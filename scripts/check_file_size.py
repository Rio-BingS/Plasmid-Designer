#!/usr/bin/env python3
"""源码文件尺寸预算检查（防回潮）

审查后的约定：单文件默认预算 **900 行**；拆分前已超限的历史大文件
（SIZE_BUDGETS 白名单）按其入库时的行数封顶——白名单文件只许变小、
不许增长，新文件一律按默认预算。超限即退出码 1。

用法：
    python scripts/check_file_size.py            # 检查
    python scripts/check_file_size.py --refresh  # 把当前超限文件写入白名单
                                                 # （拆分收敛后白名单应只减不增）
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUDGET_FILE = REPO / "scripts" / "size_budgets.json"
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
    budgets = {}
    if BUDGET_FILE.exists():
        budgets = json.loads(BUDGET_FILE.read_text(encoding="utf-8"))

    if "--refresh" in sys.argv:
        over = {p: n for p, n in sizes.items() if n > DEFAULT_BUDGET}
        BUDGET_FILE.write_text(
            json.dumps(dict(sorted(over.items())), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"白名单已刷新：{len(over)} 个文件（{BUDGET_FILE.relative_to(REPO)}）")
        return 0

    violations = []
    for path, n in sorted(sizes.items()):
        cap = budgets.get(path, DEFAULT_BUDGET)
        if n > cap:
            origin = "（白名单文件不得增长）" if path in budgets else ""
            violations.append(f"  {path}: {n} 行 > 预算 {cap} 行{origin}")
    if violations:
        print("文件超出尺寸预算：")
        print("\n".join(violations))
        print("\n拆分该文件（参考 docs 与既有拆分先例），"
              "确属新的历史包袱可用 scripts/check_file_size.py --refresh 记账。")
        return 1
    print(f"尺寸预算通过：{len(sizes)} 个源码文件，白名单 {len(budgets)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
