#!/usr/bin/env python3
"""防回潮守卫：真实测序数据及其衍生信息一律不入 git 仓库（阻断）。

2026-11-21 政策（用户定策）：真实测序数据**含脱敏件**同属真实数据，
连同其衍生测试信息（真实样本代号、真实位点/基线）一律不进 git 仓库；
真实数据回归在本机专用目录跑（tests/data/real_ab1/，gitignored，随数据
放测试文件，pytest 自动收集），GitHub CI 不跑真实数据回归。

两种模式：

1. 无参数——扫描当前工作树的 git 跟踪文件（CI 与本地门禁用）。
2. ``--tree COMMIT [COMMIT ...]``——扫描指定提交树：**路径规则覆盖全部
   给定树**（历史中间提交混入数据文件也算违规），**内容规则只查第一棵**
   （tip；泄漏内容若滞留必然滞留到 tip，中途出现又被删属自净历史，
   逐树内容扫描在百级提交上不可承受）。githooks/pre-push 用此模式在
   推送前逐新提交拦截。

发现以下任一情况退出码 1：

1. 路径：`tests/data/real_ab1/` 下的任何文件；`tests/data/sanger_bench/`
   之外的 `*.ab1` / `*.dna`（sanger_bench 的 310/3100/3730.ab1 是
   Biopython 官方公开测试集，Biopython License 允许再分发，允许入库）。
2. 内容：真实样本/参考标识与真实位点标识（词边界匹配——requirements
   哈希串、公共载体 pET-28a 坐标等数字误报由 `\\b` 规避）。

白名单：本脚本自身（含 token 字面量）。

用法：
    python scripts/check_real_data_absence.py                  # 工作树模式
    python scripts/check_real_data_absence.py --tree HEAD B C  # 树模式
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SELF = "scripts/check_real_data_absence.py"

# 路径规则
BANNED_PATH_PARTS = ("tests/data/real_ab1/", "tests\\data\\real_ab1\\")
BANNED_SUFFIXES = (".ab1", ".dna")
SUFFIX_ALLOWLIST_DIRS = ("tests/data/sanger_bench/", "tests\\data\\sanger_bench\\")

# 内容规则：真实样本代号 / 真实参考名 / 真实位点标识（词边界）
BANNED_STRINGS = (
    "S99676", "S00001", "S00024", "S00025", "S00067",
    "SZ00002", "SZ15674",
    "MUTPK", "DEMO1", "DEMO6", "DEMO7",
    "ins335", "ins440", "del4081",
)
BANNED_PATTERNS = (
    re.compile(r"\b4081\b"),
    re.compile(r"\b4187\b"),
)
# 内容白名单：公共参考数据（pET-28a 等公共载体的坐标是公开信息）
CONTENT_ALLOWLIST_DIRS = ("src/frontend/tests/fixtures/map/",)


def working_tree_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True)
    return [f for f in out.stdout.decode("utf-8", "replace").split("\0") if f]


def tree_files(treeish: str) -> list[str]:
    """列出提交树内全部 blob 路径（ls-tree 单子进程，不读内容）。"""
    out = subprocess.run(
        ["git", "ls-tree", "-r", "-z", treeish],
        cwd=REPO, capture_output=True, check=True)
    files = []
    for entry in out.stdout.decode("utf-8", "replace").split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == "blob":
            files.append(path)
    return files


def tree_blobs(treeish: str) -> dict[str, bytes]:
    """读提交树内全部 blob 内容（cat-file --batch 单进程流式，tip 树用）。"""
    out = subprocess.run(
        ["git", "ls-tree", "-r", "-z", treeish],
        cwd=REPO, capture_output=True, check=True)
    entries = []
    for entry in out.stdout.decode("utf-8", "replace").split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == "blob":
            entries.append((path, parts[2]))
    blobs: dict[str, bytes] = {}
    if not entries:
        return blobs
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"], cwd=REPO,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert proc.stdin is not None and proc.stdout is not None
    try:
        for path, sha in entries:
            proc.stdin.write(sha.encode() + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline().decode().split()
            size = int(header[2])
            blobs[path] = proc.stdout.read(size)
            assert proc.stdout.read(1) == b"\n"  # 尾分隔
    finally:
        proc.stdin.close()
        proc.wait()
    return blobs


def path_violations(files: list[str]) -> list[str]:
    bad = []
    for f in files:
        norm = f.replace("\\", "/")
        if any(p.replace("\\", "/") in norm for p in BANNED_PATH_PARTS):
            bad.append(f"路径（真实数据目录）: {f}")
            continue
        low = norm.lower()
        if low.endswith(BANNED_SUFFIXES) and not any(
                d in norm for d in SUFFIX_ALLOWLIST_DIRS):
            bad.append(f"路径（真实测序文件格式）: {f}")
    return bad


def content_violations(files, read_blob) -> list[str]:
    """read_blob(path) -> bytes | None：按数据源读文件内容。"""
    bad = []
    for f in sorted(set(files)):
        norm = f.replace("\\", "/")
        if norm == SELF:
            continue
        if any(d in norm for d in CONTENT_ALLOWLIST_DIRS):
            continue
        data = read_blob(f)
        if data is None:
            continue
        text = data.decode("utf-8", "replace")
        for s in BANNED_STRINGS:
            if s in text:
                bad.append(f"内容（真实标识 {s}）: {f}")
        for pat in BANNED_PATTERNS:
            if pat.search(text):
                bad.append(f"内容（真实位点 {pat.pattern}）: {f}")
    return bad


def _read_working(p: Path):
    try:
        return p.read_bytes()
    except OSError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="真实测序数据/衍生信息防回潮守卫（政策见 "
                    "docs/KNOWN_ISSUES.md 防回潮约束第 23 条）")
    ap.add_argument(
        "--tree", nargs="+", metavar="COMMIT",
        help="扫描提交树：路径规则查全部，内容规则只查第一棵（tip）")
    args = ap.parse_args()

    if args.tree:
        bad = []
        for i, t in enumerate(args.tree):
            files = tree_files(t)
            bad += [f"[{t}] {x}" for x in path_violations(files)]
            if i == 0:  # 内容规则只查 tip
                blobs = tree_blobs(t)
                bad += [f"[{t}] {x}" for x in content_violations(
                    list(blobs), blobs.get)]
        scope = "提交树 x%d" % len(args.tree)
    else:
        files = working_tree_files()
        bad = path_violations(files) + content_violations(
            files, lambda f: _read_working(REPO / f))
        scope = "工作树"

    if bad:
        print("真实测序数据/衍生信息防回潮守卫：发现违规，禁止入库：\n")
        for x in sorted(set(bad)):
            print(f"  - {x}")
        print("\n政策（2026-11-21）：真实测序数据含脱敏件及其衍生测试信息一律"
              "不入库；\n真实数据回归在本机 tests/data/real_ab1/（gitignored）跑。"
              "\n如认为命中属误报，请在 scripts/check_real_data_absence.py 的"
              "白名单/词边界\n处说明后调整，不要绕过守卫。")
        return 1
    print(f"check_real_data_absence: OK（{scope}，无真实测序数据/衍生信息入库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
