# -*- coding: utf-8 -*-
"""防回潮守卫：生产路径不得回退到裁参考的旧口径（goal v3.1 X6 / RB P2-2）

W8 把「目的基因模式」从「裁参考取 CDS ± 侧翼」改成「全质粒 + scope」
（`cds_extract.prepare_scope_mode`）。旧路径 `resolve_reference` 保留只为
对照（它有已知的窗口边缘问题：贴着 CDS 首端的插入会被软剪切吞掉），
**任何 src/ 代码都不许再调用它**——一旦被调用，就是静默退回旧实现。

本测试直接扫源码文本，比「跑一遍看结果」更能挡住悄悄接线。
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "backend"

# 旧口径函数名；只认「被调用」的形态（`resolve_reference(` 或 `ext.resolve_reference(`），
# docstring / 注释里不带括号的提及是说明，不算调用
_LEGACY_CALL = re.compile(r"(?<![\w.])(?:\w+\.)?resolve_reference\s*\(")


def test_no_production_caller_of_legacy_resolve_reference():
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            # 允许：定义行、注释
            if stripped.startswith("#") or stripped.startswith("def "):
                continue
            if _LEGACY_CALL.search(line):
                offenders.append(f"{path.relative_to(SRC).as_posix()}:{lineno}: {stripped}")
    assert not offenders, (
        "生产代码不得调用裁参考的旧口径 resolve_reference（W8 起走 "
        "prepare_scope_mode）：\n" + "\n".join(offenders))


def test_scope_mode_is_the_only_entry():
    """prepare_scope_mode 必须存在且仍是 cds_extract 的公开入口"""
    text = (SRC / "core" / "sanger" / "cds_extract.py").read_text(encoding="utf-8")
    assert re.search(r"^def prepare_scope_mode\(", text, re.M), \
        "prepare_scope_mode 不见了——目的基因模式的唯一入口"
