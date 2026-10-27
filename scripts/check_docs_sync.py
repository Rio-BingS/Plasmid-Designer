"""文档同步检查：把「文档脱轨」里能机械校验的事实变成门禁

背景（2026-10-27 修账）：CHANGELOG 从 v2.18.0 起漏加 compare 链接引用断链 14 个
版本；AGENTS.md 测试基线停在 v2.17.1 口径；工作区外壳 AGENTS.md 硬编码过期版本号。
本脚本检查四类「改代码时该顺手带上、最容易漏」的事实，文档质量的其余部分
（部署经验、留档）无法机械校验，仍靠 HANDOVER §5 发布清单与 /handoff。

检查项：
1. CHANGELOG 每个版本标题都有 compare 链接引用；[Unreleased] 指向最新 tag
2. 三个缓存版本常量（CODON_OPT_ALGO / ANALYSIS_ENZYMES_VERSION / MAP_ALGO_VERSION）
   的代码值与 HANDOVER §2「缓存版本常量」行一致（文档错 = 接手人递增加错基准）
3. AGENTS.md 测试基线与 HANDOVER §1 口径一致（改测试基线要两处同改）
4. 仓库内文档不再硬编码版本号（版本事实只收敛在 core/_version.py + HANDOVER；
   CHANGELOG / HANDOVER 自身除外）

用法：python scripts/check_docs_sync.py（CI 阻断、pytest 也调同一实现）
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = "https://github.com/Rio-BingS/Plasmid-Designer"


def latest_tag() -> str:
    tags = subprocess.run(
        ["git", "tag", "-l", "v*", "--sort=v:refname"],
        capture_output=True, text=True, check=True, cwd=ROOT,
    ).stdout.split()
    if not tags:
        raise AssertionError("仓库没有任何 v* tag")
    return tags[-1]


def check_changelog_links(text: str) -> list[str]:
    errors = []
    # 版本标题 `## [x.y.z]`；链接引用行 `[x.y.z]: <compare url>`
    titled = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", text, re.M)
    linked = set(re.findall(r"^\[(\d+\.\d+\.\d+)\]: \S+", text, re.M))
    for version in titled:
        if version not in linked:
            errors.append(f"CHANGELOG 缺链接引用：版本 {version} 有标题无 [x.y.z]: compare 行")
    # [Unreleased] 指向最新 tag（tag 打错或漏改 compare 目标即红）
    unreleased = re.search(r"^\[Unreleased\]: \S+/compare/(\S+)\.\.\.HEAD", text, re.M)
    if not unreleased:
        errors.append("CHANGELOG 缺 [Unreleased]: compare/<tag>...HEAD 行")
    elif unreleased.group(1) != latest_tag():
        errors.append(
            f"CHANGELOG [Unreleased] 指向 {unreleased.group(1)}，但最新 tag 是 {latest_tag()}"
            "（发版时 compare 目标要一并改）"
        )
    # 引用行的 URL 域名防漂移（用户名改过一次）
    bad_url = re.findall(r"^\[[^\]]+\]: (?!https://github\.com/Rio-BingS/)\S+", text, re.M)
    for url in bad_url:
        errors.append(f"CHANGELOG 链接域名/用户名不符：{url}")
    return errors


def check_cache_constants(text: str) -> list[str]:
    # 代码侧单一来源
    cache_py = (ROOT / "src/backend/app/cache.py").read_text(encoding="utf-8")
    map_py = (ROOT / "src/backend/app/routes/map_routes.py").read_text(encoding="utf-8")
    actual = {
        "CODON_OPT_ALGO": re.search(r'CODON_OPT_ALGO\s*=\s*"([^"]+)"', cache_py).group(1),
        "ANALYSIS_ENZYMES_VERSION": re.search(
            r'ANALYSIS_ENZYMES_VERSION\s*=\s*"([^"]+)"', cache_py
        ).group(1),
        "MAP_ALGO_VERSION": re.search(r'MAP_ALGO_VERSION\s*=\s*"([^"]+)"', map_py).group(1),
    }
    # HANDOVER §2 缓存版本常量行：`CODON_OPT_ALGO="v2.2"、ANALYSIS_ENZYMES_VERSION="v3"`、
    # `MAP_ALGO_VERSION="4"`（`app/cache.py`）……
    row = re.search(r"^\| 缓存版本常量 \| (.+?) \|$", text, re.M)
    if not row:
        return ["HANDOVER §2 找不到「缓存版本常量」行（表格行被改掉或移位）"]
    documented = dict(
        re.findall(r'(CODON_OPT_ALGO|ANALYSIS_ENZYMES_VERSION|MAP_ALGO_VERSION)="([^"]+)"', row.group(1))
    )
    errors = []
    for name, value in actual.items():
        if name not in documented:
            errors.append(f"HANDOVER §2 缓存版本常量行缺 {name}")
        elif documented[name] != value:
            errors.append(
                f"缓存版本常量 {name} 代码={value}，HANDOVER §2 写的是 {documented[name]}"
                "——改口径递增版本后要同步该行"
            )
    return errors


def check_test_baseline(text: str) -> list[str]:
    # HANDOVER §1 的「N passed」数字与 AGENTS.md 测试基线行必须一致
    handover_passed = re.findall(r"\*\*(\d+) passed", text)
    agents_md = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    agents_passed = re.search(r"pytest\s*\n?\s*\*\*(\d+) passed", agents_md)
    if not handover_passed or not agents_passed:
        return ["HANDOVER §1 或 AGENTS.md 找不到「N passed」测试基线数字（写法变了就同步本检查）"]
    if handover_passed[0] != agents_passed.group(1):
        errors = [
            f"HANDOVER §1 与 AGENTS.md 测试基线不一致：{handover_passed[0]} vs {agents_passed.group(1)}"
            " passed——改基线要两处同改"
        ]
        return errors
    return []


def check_no_hardcoded_versions(text: str) -> list[str]:
    # 仓库内 AGENTS.md/README 不再声明「当前版本 v…」「截至 v…」（CHANGELOG/HANDOVER
    # 记录历史事实，豁免；教训引用如「v2.18.0→v2.29.2 红灯链」不拦）
    errors = []
    patterns = re.compile(r"(?:当前版本|截至)\s*v\d+\.\d+(?:\.\d+)?")
    for rel in ("AGENTS.md", "README.md"):
        body = (ROOT / rel).read_text(encoding="utf-8")
        for match in patterns.findall(body):
            errors.append(
                f"{rel} 硬编码版本声明「{match}」——版本事实只写在 core/_version.py 与 HANDOVER"
            )
    return errors


def main() -> int:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    handover = (ROOT / "docs/HANDOVER.md").read_text(encoding="utf-8")
    errors = [
        *check_changelog_links(changelog),
        *check_cache_constants(handover),
        *check_test_baseline(handover),
        *check_no_hardcoded_versions(handover),
    ]
    if errors:
        print("文档同步检查未通过：")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"文档同步检查通过（最新 tag {latest_tag()}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
