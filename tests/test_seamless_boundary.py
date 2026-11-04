"""无缝连接（keep_sites=False）切点外扩的边界安全（第三轮审计 P2）

外扩是裸算术，而切点定义域是 [1..L]（0 ≡ L ≡ 原点）：跨原点位点扩完
会得到负数 / 超过 L 的切点，apply_plan 的 Python 切片会静默产出错误
构建体（实测分别多出 5 bp 与 210 bp）。本文件把两个实证过的越界场景
锁成精确的构建体断言（内容级，不是只看长度），并覆盖单切点纯插入的
无缝分支与 apply_plan 的越界防护。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.construct_assembly import (  # noqa: E402
    AssemblyPlan, apply_plan, plan_restriction,
)

FILLER = "ACGTTGCAAGGCTATCCTAGG"          # 21bp，不含常用位点
INSERT = "ATGGGCTAATTCCGGATCAAAGTCCATTTGAC"   # 31bp


def test_seamless_right_cut_wraps_origin():
    """右切点外扩越过载体末端（XhoI 跨原点，cut=L，外扩 +5）→ 归一到 5，
    绕原点旋转后构建体 = 填充段 + 插入片段，两个完整位点都被切掉"""
    bb = "TCGAG" + FILLER * 9 + "CATATG" + "GGAC" + "C"   # 205bp 环状
    assert len(bb) == 205
    plan = plan_restriction(bb, "NdeI", "XhoI", window=None, circular=True,
                            warnings=[], join="gibson", keep_sites=False)
    assert plan is not None
    # 外扩后的切点都归一回定义域 [1..L]
    assert 1 <= plan.left_cut <= len(bb)
    assert 1 <= plan.right_cut <= len(bb)
    assert plan.seamless is True and plan.head == "" and plan.tail == ""
    seq, feats, istart, iend, _w = apply_plan(
        bb, [], plan, insert=INSERT, insert_name="gene", circular=True)
    # 被切走的是 NdeI 整位点（CATATG + 下游 GGACC）+ 跨原点 XhoI 整位点
    # （C + TCGAG），只剩中间填充段 → 精确内容断言
    assert seq == FILLER * 9 + INSERT
    assert len(seq) == len(bb) - plan.removed + len(INSERT)
    assert "CATATG" not in seq and "CTCGAG" not in seq


def test_seamless_left_cut_wraps_origin():
    """左切点外扩成负数（跨原点 NdeI，cut=1，外扩 -2）→ 归一到 L-1，
    旋转后构建体 = 保留段 + 插入片段。修复前实测构建体多出 210 bp"""
    bb = ("ATATG" + FILLER * 3 + "TTGACCTAGCATTGCACTAGCTTACCTGAC" + "CTCGAG"
          + "GGTTACCAAGGCTTGACCTAGCTTAAGGCTTAACCGGTTAACCGGTTAAGCCTTAAGGCC"
          + "TTAACCGGTTAAGCCTTAAGGCCTTAAGGCCTTAAGGCCTTAAGG" + "C")  # 210bp
    assert len(bb) == 210
    plan = plan_restriction(bb, "NdeI", "XhoI", window=None, circular=True,
                            warnings=[], join="gibson", keep_sites=False)
    assert plan is not None
    assert 1 <= plan.left_cut <= len(bb)
    assert 1 <= plan.right_cut <= len(bb)
    assert plan.seamless is True
    seq, feats, istart, iend, _w = apply_plan(
        bb, [], plan, insert=INSERT, insert_name="gene", circular=True)
    # 语义自证的期望构建体：非绕原点 = bb[:L]+ins+bb[R:]；
    # 绕原点（归一后 right<left）= 保留段 bb[R:L]+ins（apply_plan 旋转等价）
    if plan.right_cut >= plan.left_cut:
        expected = bb[:plan.left_cut] + INSERT + bb[plan.right_cut:]
    else:
        expected = bb[plan.right_cut:plan.left_cut] + INSERT
    assert seq == expected
    assert len(seq) == len(bb) - plan.removed + len(INSERT)
    # 生物学断言：跨原点 NdeI（末位 C + 开头 ATATG）与 XhoI 整位点都消失，
    # 插入片段完整保留
    assert "CATATG" not in seq and "CTCGAG" not in seq
    assert INSERT in seq and len(seq) < len(bb)


def test_seamless_single_cut_pure_insertion_removes_site():
    """两把酶同切点（这里同酶双填）纯插入 + keep_sites=False：
    单切点分支也要走无缝——位点两侧整段去除，不再保留 head/tail"""
    bb = FILLER * 5 + "CATATG" + FILLER * 5 + "GGA"      # 114bp，单个 NdeI
    plan = plan_restriction(bb, "NdeI", "NdeI", window=None, circular=True,
                            warnings=[], join="gibson", keep_sites=False)
    assert plan is not None
    assert plan.seamless is True
    assert plan.head == "" and plan.tail == ""
    assert plan.removed == 6                       # 整个位点 6bp
    assert any("位点已从构建体去除" in w for w in plan.warnings)
    seq, *_rest = apply_plan(bb, [], plan, insert=INSERT,
                             insert_name="gene", circular=True)
    cut = plan.right_cut                           # 归一后 right = 原切点 +4
    assert "CATATG" not in seq
    assert len(seq) == len(bb) - 6 + len(INSERT)
    # 默认（keep_sites=True）仍是位点复原的纯插入：head/tail 回补
    plan_kept = plan_restriction(bb, "NdeI", "NdeI", window=None,
                                 circular=True, warnings=[], join="gibson")
    assert plan_kept.head == "TATG" and plan_kept.tail == "CA"
    assert plan_kept.seamless is False


def test_apply_plan_rejects_out_of_domain_cuts():
    """越域切点（负数 / 超长）必须显式失败，绝不静默切错"""
    bb = "ACGT" * 10
    with pytest.raises(ValueError):
        apply_plan(bb, [], AssemblyPlan("restriction", -1, 20, removed_bp=21),
                   insert="ATG", circular=True)
    with pytest.raises(ValueError):
        apply_plan(bb, [], AssemblyPlan("restriction", 5, 999, removed_bp=10),
                   insert="ATG", circular=True)


def test_seamless_normal_case_unchanged():
    """常规无缝（切点远离原点）行为与修复前一致：内容级对照"""
    bb = FILLER * 4 + "CATATG" + FILLER * 2 + "CTCGAG" + FILLER * 3
    plan = plan_restriction(bb, "NdeI", "XhoI", window=None, circular=True,
                            warnings=[], join="gibson", keep_sites=False)
    assert plan is not None and plan.seamless is True
    seq, *_rest = apply_plan(bb, [], plan, insert=INSERT,
                             insert_name="gene", circular=True)
    assert len(seq) == len(bb) - plan.removed + len(INSERT)
    assert "CATATG" not in seq and "CTCGAG" not in seq
    assert INSERT in seq
