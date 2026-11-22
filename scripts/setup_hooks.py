#!/usr/bin/env python3
"""启用本仓库的 git 钩子（真实数据防推送守卫，githooks/pre-push）。

新克隆 / 新机器初始化步骤之一（AGENTS.md「新克隆/换机器初始化」）：

    python scripts/setup_hooks.py

实现：仓库级设置 ``git config core.hooksPath githooks``（只影响本克隆，
不动全局配置）。幂等，重复运行无害。钩子本体 githooks/pre-push 在
每次 git push 前用 scripts/check_real_data_absence.py 扫描待推提交，
违规即取消推送；政策与白名单见 docs/KNOWN_ISSUES.md 约束第 23 条。

事后兜底（不依赖本机是否启用钩子）：.github/workflows/guard.yml 在
远端对所有分支/tag 推送再跑一遍同一守卫。
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "githooks" / "pre-push"


def main() -> int:
    if not HOOK.is_file():
        print(f"✗ 未找到 {HOOK.relative_to(REPO)}——仓库不完整？", file=sys.stderr)
        return 1
    subprocess.run(
        ["git", "config", "core.hooksPath", "githooks"],
        cwd=REPO, check=True)
    # 自检：钩子守卫本体跑一遍（顺带确认 python 可用、工作树干净）
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_real_data_absence.py")],
        cwd=REPO)
    if r.returncode != 0:
        print("✗ 守卫自检未通过，钩子启用但工作树已有违规。", file=sys.stderr)
        return r.returncode
    print("已启用 githooks/pre-push（真实数据防推送守卫）。")
    print("每次 git push 前自动扫描待推提交；政策见 docs/KNOWN_ISSUES.md 约束第 23 条。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
