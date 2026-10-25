"""起始密码子一致性的边界场景（第三轮审计 P3 小修）

- custom_arms 正链装配加融合克隆检查：臂连接点落在正链 CDS/tag 末端时
  与 Gibson 单点同口径（只告警读码框/内部 Met，不自动去除 ATG），修复前
  会静默删 ATG、与单点模式行为不一致；
- 同酶双填（enzyme_5 == enzyme_3）回退单位点的文案写明原因，且线性化点
  落在这把酶自己的切点上（而不是笼统的 MCS 起点）；
- Gibson 单点跨原点的连接点 ATG（pos < 3）不再漏检（环状绕回窗口）。
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.design_service import (  # noqa: E402
    CloningMethod, _mcs_window, _resolve_custom_arms_start_codon,
    _resolve_gibson_start_codon, ensure_vector_backbone, get_vector_library,
    run_design,
)
from app.routes.models import DesignRequest, DesignStatus  # noqa: E402
from core.construct_assembly import physical_sites, plan_custom_arms  # noqa: E402
from core.seq_utils import revcomp  # noqa: E402

AA = "M" + "EKT" * 20
DNA = "ATG" + "AAG" * 8                      # 以 ATG 开头，长度 27
FILLER = "ACGTTGCAAGGCTATCCTAGG"


def _el(name, start, end, kind="CDS", strand="+"):
    return SimpleNamespace(
        name=name, element_type=SimpleNamespace(value=kind),
        start=start, end=end, strand=strand, description="")


def _fake_vector(backbone, elements, mcs=None, topology="circular"):
    return SimpleNamespace(
        name="fakev", sequence=backbone, topology=topology, mcs=mcs,
        elements=elements, unique_site_cuts={})


# ==================== custom_arms 融合克隆检查 ====================

def _arms_backbone():
    """pre(19) + ATG[19:22] + 正链 CDS[22:46]（以 ATG 结尾，1-based 23..46）
    + CCATG[46:51] + FILLER[51:72] + 尾巴"""
    cds = "ATG" + "AAA" * 6 + "ATG"           # 24bp，以 ATG 结尾
    return ("ACGTTGCAAGGCTTCCTAG" + "ATG" + cds + "CCATG"
            + FILLER + "GGTTACCAAGG")


def test_custom_arms_fusion_does_not_trim():
    """臂连接点贴正链 CDS 末端：融合场景只告警（同框提示），不删 ATG"""
    bb = _arms_backbone()
    assert bb[19:22] == "ATG" and bb[43:46] == "ATG"   # 前置自检
    arm5 = bb[26:44 + 2]    # CDS 的最后 20bp → left_cut = 46 = CDS 末端
    arm3 = bb[51:72]        # FILLER（21bp，全骨架唯一）
    plan = plan_custom_arms(bb, arm5, arm3, circular=True)
    assert not plan.insert_reverse and plan.left_cut == 46
    vector = _fake_vector(bb, [_el("HisTag", 23, 46, kind="tag")])
    out, warns = _resolve_custom_arms_start_codon(
        SimpleNamespace(trim_redundant_start=True), vector, bb, plan, DNA)
    assert out == DNA                          # 融合：绝不动序列
    assert len(warns) == 1 and "融合克隆提示" in warns[0]
    assert "HisTag" in warns[0] and "同框" in warns[0]
    assert not any("已自动去除" in w for w in warns)


def test_custom_arms_independent_expression_trims():
    """对照：臂连接点在 CDS 之外、连接点前 3bp 为 ATG → 正常去除"""
    bb = _arms_backbone()
    arm5 = bb[0:22]          # pre + ATG → left_cut = 22，前置 3bp 就是 ATG
    arm3 = bb[51:72]
    plan = plan_custom_arms(bb, arm5, arm3, circular=True)
    assert plan.left_cut == 22 and bb[19:22] == "ATG"
    vector = _fake_vector(bb, [_el("HisTag", 23, 46, kind="tag")])
    out, warns = _resolve_custom_arms_start_codon(
        SimpleNamespace(trim_redundant_start=True), vector, bb, plan, DNA)
    assert out == DNA[3:]
    assert any("已自动去除冗余起始密码子" in w for w in warns)


# ==================== 同酶双填回退单位点 ====================

def test_same_enzyme_falls_back_to_its_own_cut_site():
    """enzyme_5 == enzyme_3：文案写明「两把酶相同」，线性化点落在该酶
    自己的切点上（引物臂锚定 NdeI 切点边界 CA|TATG）"""
    result = run_design("same_enz", DesignRequest(
        sequence=AA, sequence_type="amino_acid", vector_id="pET-28a",
        cloning_method=CloningMethod.GIBSON, enzyme_5="NdeI", enzyme_3="NdeI"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert any("两把酶相同（NdeI）" in w for w in result.warnings)
    vector = get_vector_library().get_vector("pET-28a")
    backbone = ensure_vector_backbone(vector)
    sites = physical_sites(backbone, "NdeI", circular=True)
    assert sites, "pET-28a 应含 NdeI 位点"
    lo, hi = _mcs_window(vector) or (0, len(backbone))
    pick = next((s for s in sites if lo <= int(s["position"]) <= hi), sites[0])
    cut = int(pick["cut_fwd"])
    assert backbone[cut - 2:cut] == "CA" and backbone[cut:cut + 4] == "TATG"
    fwd, rev = result.primers[0], result.primers[1]
    assert fwd.overhang.upper() == backbone[cut - 20:cut]
    assert rev.overhang.upper() == revcomp(backbone[cut:cut + 20])


# ==================== 跨原点连接点 ATG ====================

def test_gibson_origin_spanning_atg_detected_on_circular():
    """环状载体、线性化点 pos=1：连接点前 3bp 绕回载体尾部（…AT|G），
    为 ATG 时照常去除；修复前 pos<3 直接返回、漏检"""
    bb = "G" + FILLER * 2 + "AT"              # 尾部 AT + 开头 G = ATG
    vector = _fake_vector(
        bb, [], mcs=SimpleNamespace(start=2, end=9, sites=[]))
    request = SimpleNamespace(gibson_site=None, trim_redundant_start=True)
    out, warns = _resolve_gibson_start_codon(request, vector, bb, DNA)
    assert out == DNA[3:]
    assert any("已自动去除冗余起始密码子" in w for w in warns)


def test_gibson_origin_spanning_atg_skipped_on_linear():
    """线性载体 pos<3 没有完整的 3bp 前置碱基：不去除、不告警"""
    bb = "G" + FILLER * 2 + "AT"
    vector = _fake_vector(
        bb, [], mcs=SimpleNamespace(start=2, end=9, sites=[]), topology="linear")
    request = SimpleNamespace(gibson_site=None, trim_redundant_start=True)
    out, warns = _resolve_gibson_start_codon(request, vector, bb, DNA)
    assert out == DNA and warns == []
