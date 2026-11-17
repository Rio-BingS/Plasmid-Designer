"""goal v3 W10 范围输入单测：infer_scope 来源优先级（PL §C1/§C2）与
parse_scope_cell 解析口径（元件名/数字区间/多段/关键字/失败不抛异常）。

SC2（filename_match → pass）经场景测试覆盖；本文件锁解析与推断的单元行为。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.sanger.scope import infer_scope, parse_scope_cell  # noqa: E402

_FEATS = [
    dict(name="GeneX", type="CDS", start=301, end=600, strand="+"),
    dict(name="AmpR", type="gene", start=1501, end=2100, strand="+"),
]


# ---------- infer_scope（§3.1 来源优先级的中间两级） ----------


def test_infer_scope_design_insert_with_junctions():
    """design_insert：插入边界 ±30 接合区另算（junctions 字段，W8 口径）"""
    scope = infer_scope("GeneX.gb", ["GeneX_F.ab1"], _FEATS,
                        insert_range=(801, 900), ref_length=3000)
    assert scope.source == "design_insert" and scope.declared
    assert scope.ranges == [(801, 900)]
    assert (771, 831) in scope.junctions and (870, 930) in scope.junctions


def test_infer_scope_filename_match():
    """filename_match：参考名命中 GeneX → 以该 CDS+接合区为范围"""
    scope = infer_scope("GeneX.gb", ["S1-GeneX-F.ab1"], _FEATS)
    assert scope.source == "filename_match" and scope.declared
    assert scope.ranges == [(301, 600)]
    assert scope.elements == ["GeneX"]
    assert (271, 331) in scope.junctions


def test_infer_scope_no_hit_returns_none():
    """文件名无命中 → None（调用方退回未声明 measured 口径）"""
    assert infer_scope("nothing", ["x.ab1"], _FEATS) is None
    assert infer_scope("", [], []) is None


def test_infer_scope_design_insert_beats_filename():
    """来源优先级：design_insert > filename_match（插入边界存在时优先）"""
    scope = infer_scope("GeneX.gb", ["S1-GeneX-F.ab1"], _FEATS,
                        insert_range=(801, 900))
    assert scope.source == "design_insert"


# ---------- resolve_scope 唯一入口（推断并入其内部一步，2026-11-18） ----------


def test_resolve_scope_infer_matches_infer_scope():
    """resolve_scope(None, infer=True) 与兼容入口 infer_scope 逐字段一致"""
    from core.sanger.scope import resolve_scope
    cases = [
        ("GeneX.gb", ["GeneX_F.ab1"], (801, 900), 3000),
        ("GeneX.gb", ["S1-GeneX-F.ab1"], None, 0),
        ("GeneX.gb", ["S1-GeneX-F.ab1"], None, 3000),
        ("nothing", ["x.ab1"], None, 3000),
    ]
    for stem, reads, ins, n in cases:
        got, err = resolve_scope(None, _FEATS, n, insert_range=ins, infer=True,
                                 ref_stem=stem, read_names=reads)
        old = infer_scope(stem, reads, _FEATS, insert_range=ins, ref_length=n)
        assert err is None
        assert (got.to_dict() if got else None) == (old.to_dict() if old else None)


def test_resolve_scope_no_infer_by_default():
    """不带 infer 时 None 仍是 None（显式入参路径行为不变）"""
    from core.sanger.scope import resolve_scope
    assert resolve_scope(None, _FEATS, 3000, insert_range=(801, 900)) == (None, None)


def test_resolve_scope_explicit_beats_infer():
    """显式范围优先：scope 非空时不推断"""
    from core.sanger.scope import resolve_scope
    sc, err = resolve_scope("AmpR", _FEATS, 3000, insert_range=(801, 900), infer=True)
    assert err == "" and sc.source == "user" and sc.ranges == [(1501, 2100)]


# ---------- parse_scope_cell（W1 实现，W10 锁关键口径） ----------


def test_parse_scope_cell_element_number_multi():
    sc, err = parse_scope_cell("GeneX; 701-800", _FEATS, 3000)
    assert err == ""
    assert sc.ranges == [(301, 600), (701, 800)]
    assert sc.elements == ["GeneX"]
    assert sc.source == "user" and sc.declared


def test_parse_scope_cell_keywords():
    sc, err = parse_scope_cell("全质粒", _FEATS, 3000)
    assert err == "" and sc.ranges == [(1, 3000)]
    sc2, err2 = parse_scope_cell("插入片段", _FEATS, 3000, insert_range=(801, 900))
    assert err2 == "" and sc2.ranges == [(801, 900)]


def test_parse_scope_cell_failure_returns_error_not_raise():
    """解析失败返回 (None, 错误说明)，不抛异常——批量回填口径的前提"""
    bad, err = parse_scope_cell("不存在的元件", _FEATS, 3000)
    assert bad is None and "无法识别" in err
    bad2, err2 = parse_scope_cell("301-99999", _FEATS, 3000)
    assert bad2 is None and err2
    empty, err3 = parse_scope_cell("", _FEATS, 3000)
    assert empty is None and err3 == ""     # 空文本 → 走来源推断
