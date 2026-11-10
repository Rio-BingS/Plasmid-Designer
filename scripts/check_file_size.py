#!/usr/bin/env python3
"""源码文件尺寸统计（信息性，不阻断）

**2026-11-10 解除限制**：项目处于推进期而非维护期，行数上限频繁迫使
「为了过门禁而先拆分」，把推进期的资源花在结构调整上。大文件的重构放到
后期集中做更划算（那时没有功能压力，也能一次看清真实切分线）。

因此本脚本只统计、只提示，**永远退出码 0**，CI 不再因行数变红。
仍然保留的用途：
  - 收尾时看一眼哪些文件偏大，作为后续重构的输入；
  - `--top N` 直接给出最大的 N 个文件，便于挑重点。

用法：
    python scripts/check_file_size.py            # 列出超参考线的文件
    python scripts/check_file_size.py --top 20   # 按行数取前 20
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUDGET_FILE = REPO / "scripts" / "size_budgets.json"
# 参考线（不再是预算，只是「值得关注」的阈值）
DEFAULT_BUDGET = 900
SCAN_ROOTS = ("src/backend", "src/frontend/src")
SCAN_SUFFIXES = (".py", ".ts", ".vue")


def scan() -> dict:
    """{相对路径: 行数}，跳过 node_modules/dist/生成物"""
    out = {}
    for root in SCAN_ROOTS:
        for p in (REPO / root).rglob("*"):
            if not p.is_file() or p.suffix not in SCAN_SUFFIXES:
                continue
            rel = p.relative_to(REPO).as_posix()
            if any(part in ("node_modules", "dist", ".vite") for part in p.parts):
                continue
            out[rel] = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
    return out


def main() -> int:
    sizes = scan()

    if "--refresh" in sys.argv:
        over = {p: n for p, n in sizes.items() if n > DEFAULT_BUDGET}
        BUDGET_FILE.write_text(
            json.dumps(dict(sorted(over.items())), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"参考清单已刷新：{len(over)} 个文件（{BUDGET_FILE.relative_to(REPO)}）")
        return 0

    if "--top" in sys.argv:
        try:
            n = int(sys.argv[sys.argv.index("--top") + 1])
        except (IndexError, ValueError):
            n = 15
        print(f"最大的 {n} 个源码文件：")
        for path, lines in sorted(sizes.items(), key=lambda kv: -kv[1])[:n]:
            print(f"  {path}: {lines} 行")
        return 0

    over = [(p, n) for p, n in sorted(sizes.items()) if n > DEFAULT_BUDGET]
    if over:
        print(f"超过参考线 {DEFAULT_BUDGET} 行的文件（仅供参考，不阻断）：")
        for path, n in over:
            print(f"  {path}: {n} 行")
        print("\n项目推进期不做强制拆分；重构计划见 docs/KNOWN_ISSUES.md "
              "「大文件重构」一项。")
    print(f"尺寸统计完成：{len(sizes)} 个源码文件，"
          f"超参考线 {len(over)} 个。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
