"""Gibson 双酶切线性化引物的内容级正确性（第三轮审计 P1）

修复前反向引物同源臂取自左切点下游（被切除的废片段内部），在线性化
载体上无处退火；负链装配（pET-28a 表达盒在负链）时片段基因还是正向的，
照引物做出来的构建体与设计图（负链）矛盾。本文件用**内容级**断言锁死：

- 两臂分别锚定左切点上游 / 右切点下游（跨原点时绕回）；
- 片段基因取向与构建体 CDS 链向一致（负链装配 = head + revcomp(基因) + tail）；
- 无缝（keep_sites=False）时臂锚定外扩后的切点、无位点残留。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.design_service import (  # noqa: E402
    CloningMethod, _mcs_window, ensure_vector_backbone, get_vector_library,
    run_design,
)
from app.routes.models import DesignRequest, DesignStatus  # noqa: E402
from core.construct_assembly import plan_restriction  # noqa: E402
from core.primer_designer import PrimerDesigner  # noqa: E402
from core.seq_utils import revcomp  # noqa: E402

AA = "M" + "EKT" * 20
ARM = 20
FILLER = "ACGTTGCAAGGCTATCCTAGG"
GENE = "ATGGGCTAATTCCGGATCAAAGTCCATTTGAC"


def _request(**over) -> DesignRequest:
    payload = {
        "sequence": AA, "sequence_type": "amino_acid", "vector_id": "pET-28a",
        "cloning_method": CloningMethod.GIBSON, "enzyme_5": "NdeI",
        "enzyme_3": "XhoI",
    }
    payload.update(over)
    return DesignRequest(**payload)


def _pet28a():
    vector = get_vector_library().get_vector("pET-28a")
    return vector, ensure_vector_backbone(vector)


def _pet28a_plan(backbone, vector, keep_sites=True):
    """与 run_design 内部重算完全同参的 plan（pET-28a 上解析后酶 == 原始酶）"""
    return plan_restriction(
        backbone, "NdeI", "XhoI", window=_mcs_window(vector), circular=True,
        warnings=[], join="gibson", keep_sites=keep_sites)


def _insert_cds(result):
    return next(f for f in result.construct_features if f["type"] == "CDS"
                and "designed insert" in (f.get("description") or "").lower())


def test_arms_anchor_both_junctions_on_pet28a():
    """双酶切引物：F 臂 = 左切点上游、R 臂 = 右切点下游的互补链。
    修复前 R 臂错取左切点下游（被切除区域），实测值为 AAGCTTGCGGCCGCACTCGA"""
    vector, backbone = _pet28a()
    plan = _pet28a_plan(backbone, vector)
    assert plan.left_cut < plan.right_cut          # 确实切走了一段（非单点）
    result = run_design("gdp_arm", _request())
    assert result.status == DesignStatus.COMPLETED, result.errors
    fwd, rev = result.primers[0], result.primers[1]
    L, R = plan.left_cut, plan.right_cut
    assert fwd.overhang.upper() == backbone[L - ARM:L]
    assert rev.overhang.upper() == revcomp(backbone[R:R + ARM])
    # 审计实证的错误来源（左切点下游 = 废片段内部）绝不允许再出现
    assert rev.overhang.upper() != revcomp(backbone[L:L + ARM])
    assert rev.overhang.upper() == "GGTGCCGCGCGGCAGCCATA"   # 探针推导的精确值


def test_minus_strand_fragment_matches_construct_strand():
    """pET-28a 负链装配：构建体 CDS 在负链，PCR 片段基因必须是反向互补
    （正向引物退火区 = 位点残留 + revcomp(基因) 前缀，反向引物贴片段尾部）"""
    vector, backbone = _pet28a()
    plan = _pet28a_plan(backbone, vector)
    assert plan.insert_reverse is True
    result = run_design("gdp_minus", _request())
    assert result.status == DesignStatus.COMPLETED, result.errors
    cds = _insert_cds(result)
    assert cds["strand"] == "-"
    gene_rc = revcomp(result.cloned_sequence)   # 克隆工作序列（位点 ATG 已裁）
    fwd, rev = result.primers[0], result.primers[1]
    # 片段 = head(TCGAG) + revcomp(基因) + tail(CA)
    assert fwd.sequence.upper().startswith(plan.head)
    assert gene_rc.startswith(fwd.sequence.upper()[len(plan.head):])
    rc_rev = revcomp(rev.sequence.upper())
    assert rc_rev.endswith(plan.tail)
    assert gene_rc.endswith(rc_rev[:max(0, len(rc_rev) - len(plan.tail))])


def test_forward_assembly_arms_and_body_unit():
    """正链装配（合成载体，NdeI 切点在前）：臂锚定两侧切点、基因正向进片段"""
    bb = FILLER * 5 + "CATATG" + FILLER * 3 + "CTCGAG" + FILLER * 5
    plan = plan_restriction(bb, "NdeI", "XhoI", window=None, circular=True,
                            warnings=[], join="gibson")
    assert plan is not None and plan.insert_reverse is False
    fragment = plan.fragment(GENE)          # head + 基因 + tail
    pair = PrimerDesigner().design_gibson_primers(
        fragment, bb, plan.left_cut, plan.right_cut,
        homology_arm=ARM, primer_name="g")
    assert pair.forward.overhang == bb[plan.left_cut - ARM:plan.left_cut]
    assert pair.reverse.overhang == revcomp(bb[plan.right_cut:plan.right_cut + ARM])
    assert pair.forward.sequence.startswith(plan.head)
    assert GENE.startswith(pair.forward.sequence[len(plan.head):])


def test_seamless_double_digest_arms_anchor_expanded_cuts():
    """无缝（keep_sites=False）：臂锚定外扩后的切点，片段无位点残留"""
    vector, backbone = _pet28a()
    plan = _pet28a_plan(backbone, vector, keep_sites=False)
    assert plan.head == "" and plan.tail == ""
    result = run_design("gdp_seam", _request(keep_restriction_sites=False))
    assert result.status == DesignStatus.COMPLETED, result.errors
    fwd, rev = result.primers[0], result.primers[1]
    assert fwd.overhang.upper() == backbone[plan.left_cut - ARM:plan.left_cut]
    assert rev.overhang.upper() == revcomp(
        backbone[plan.right_cut:plan.right_cut + ARM])
    # 无缝 + 负链：片段直接以 revcomp(基因) 开头
    assert fwd.sequence.upper().startswith(
        revcomp(result.cloned_sequence)[:8])


def test_homology_arm_wraps_origin():
    """左切点靠近原点时正向臂绕回载体尾部（环状切片，不再截短）。
    NdeI 切点 2 在前、XhoI 在 ~28：短方向为 2→28，左切点 = 2"""
    bb = "CATATG" + FILLER + "CTCGAG" + FILLER * 4 + "TTACC"
    plan = plan_restriction(bb, "NdeI", "XhoI", window=None, circular=True,
                            warnings=[], join="gibson")
    assert plan is not None and plan.left_cut == 2
    pair = PrimerDesigner().design_gibson_primers(
        GENE, bb, plan.left_cut, plan.right_cut, homology_arm=ARM,
        primer_name="g", circular=True)
    assert pair.forward.overhang == bb[-18:] + bb[:2]   # 20bp 跨原点
