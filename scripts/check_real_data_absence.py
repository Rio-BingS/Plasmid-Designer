#!/usr/bin/env python3
"""防回潮守卫：真实测序数据及其衍生信息一律不入 git 仓库（阻断）。

2026-11-21 政策（用户定策）：真实测序数据**含脱敏件**同属真实数据，
连同其衍生测试信息（真实样本代号、真实位点/基线）一律不进 git 仓库；
真实数据回归在本机专用目录跑（tests/data/real_ab1/，gitignored，随数据
放测试文件，pytest 自动收集），GitHub CI 不跑真实数据回归。

本脚本扫描 git 跟踪的文件，发现以下任一情况退出码 1（CI 阻断）：

1. 路径：`tests/data/real_ab1/` 下的任何文件；`tests/data/sanger_bench/`
   之外的 `*.ab1` / `*.dna`（sanger_bench 的 310/3100/3730.ab1 是
   Biopython 官方公开测试集，Biopython License 允许再分发，允许入库）。
2. 内容：真实样本/参考标识与真实位点标识（词边界匹配——requirements
   哈希串、公共载体 pET-28a 坐标等数字误报由 `\\b` 规避）。

白名单：本脚本自身（含 token 字面量）。

用法：
    python scripts/check_real_data_absence.py        # 违规即退出码 1
"""

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


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True)
    return [f for f in out.stdout.decode("utf-8", "replace").split("\0") if f]


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


def content_violations(files: list[str]) -> list[str]:
    bad = []
    for f in files:
        norm = f.replace("\\", "/")
        if norm == SELF:
            continue
        if any(d in norm for d in CONTENT_ALLOWLIST_DIRS):
            continue
        p = REPO / f
        if not p.is_file():
            continue
        try:
            text = p.read_text("utf-8", "replace")
        except OSError:
            continue
        for s in BANNED_STRINGS:
            if s in text:
                bad.append(f"内容（真实标识 {s}）: {f}")
        for pat in BANNED_PATTERNS:
            if pat.search(text):
                bad.append(f"内容（真实位点 {pat.pattern}）: {f}")
    return bad


def main() -> int:
    files = tracked_files()
    bad = path_violations(files) + content_violations(files)
    if bad:
        print("真实测序数据/衍生信息防回潮守卫：发现违规，禁止入库：\n")
        for x in sorted(set(bad)):
            print(f"  - {x}")
        print("\n政策（2026-11-21）：真实测序数据含脱敏件及其衍生测试信息一律"
              "不入库；\n真实数据回归在本机 tests/data/real_ab1/（gitignored）跑。"
              "\n如认为命中属误报，请在 scripts/check_real_data_absence.py 的"
              "白名单/词边界\n处说明后调整，不要绕过守卫。")
        return 1
    print("check_real_data_absence: OK（无真实测序数据/衍生信息入库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
