"""人工验证反馈（v2.21.1 之后）五项修复的内容级回归：

1. 结果图谱名 = plasmid_name（载体名-序列名），不再硬编码「载体名 + insert」；
2. 优化序列开头 ATG 去除有结构化标志（start_codon_removed），构建体编码区完整；
3. 组装可行性自检：粘端兼容（SalI/XhoI 同为 TCGA）必须告警「方向不唯一」；
   位点缺失走 MCS 回退时必须明确告警「酶切-连接无法复现该构建体」；
4. 方向自动判定成功时结果层有一条用户能看见的「方向判定」说明；
5. Gibson 双酶切线性化预览（保留位点 / 无缝两种模式）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.design_service import (  # noqa: E402
    CloningMethod, build_restriction_preview, get_vector_library, run_design,
)
from app.map_documents import design_map_document  # noqa: E402
from app.routes.models import DesignStatus  # noqa: E402
from app.schemas import RestrictionPreviewRequest  # noqa: E402
from core.construct_assembly import sticky_overhang  # noqa: E402
from core.seq_utils import revcomp  # noqa: E402

AA = "M" + "EKT" * 12   # 37 aa，无终止符


def _request(**over):
    payload = {
        "sequence": AA, "sequence_type": "amino_acid", "vector_id": "pET-28a",
        "cloning_method": CloningMethod.RESTRICTION,
        "enzyme_5": "NdeI", "enzyme_3": "XhoI", "sequence_name": "myGene",
    }
    payload.update(over)
    from app.routes.models import DesignRequest
    return DesignRequest(**payload)


# ---------- 1. 图谱命名 ----------

def test_map_document_uses_plasmid_name():
    """开了载体名前缀后，结果图谱名必须是「载体名-序列名」。
    修复前 map_documents 硬编码「载体名 + insert」，图谱名不随命名规则走"""
    result = run_design("uvf_map", _request(vector_name_prefix=True))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert result.plasmid_name == "pET-28a(+)-myGene"
    doc = design_map_document(result)
    assert doc["name"] == "pET-28a(+)-myGene"


def test_map_document_without_prefix_uses_sequence_name():
    result = run_design("uvf_map2", _request())
    doc = design_map_document(result)
    assert doc["name"] == "myGene"


# ---------- 2. 优化序列少 3 bp 的透明化 ----------

def test_start_codon_removed_flag_and_full_protein_in_construct():
    """NdeI 位点自带 ATG：克隆工作序列少 3bp 且置 start_codon_removed；
    「优化后序列」保持完整 CDS 语义（含 ATG），构建体负链 CDS 翻译回
    完整输入蛋白（一个氨基酸都不缺）"""
    from core.seq_utils import CODON_TO_AA
    result = run_design("uvf_trim", _request(optimize_codons=True))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert result.start_codon_removed is True
    # 优化后序列 = 完整 CDS：37 个密码子、以 ATG 开头，与输入蛋白对应
    assert result.optimized_sequence.upper().startswith("ATG")
    assert len(result.optimized_sequence) == 3 * len(AA)
    # 克隆工作序列 = 组装/引物退火用的：去掉开头 ATG，短 3bp
    assert result.cloned_sequence is not None
    assert not result.cloned_sequence.upper().startswith("ATG")
    assert len(result.cloned_sequence) == 3 * (len(AA) - 1)
    cds = next(f for f in result.construct_features
               if "designed insert" in (f.get("description") or "").lower())
    lo, hi = cds["start"], cds["end"]
    region = result.construct_sequence[lo - 1:hi]
    minus = revcomp(region)
    prot = "".join(CODON_TO_AA.get(minus[i:i + 3], "?")
                   for i in range(0, len(minus) - 2, 3))
    assert prot == AA   # 构建体蛋白完整（含位点提供的 M）


# ---------- 3. 组装可行性自检 ----------

def test_sticky_overhang_values():
    assert sticky_overhang("NdeI") == "TA"
    assert sticky_overhang("XhoI") == "TCGA"
    assert sticky_overhang("SalI") == "TCGA"    # 与 XhoI 兼容
    assert sticky_overhang("EcoRI") == "AATT"
    assert sticky_overhang("EcoRV") == ""       # 平末端


def test_compatible_overhangs_trigger_direction_warning():
    """SalI/XhoI 粘端同为 TCGA：连接方向不唯一，必须告警"""
    result = run_design("uvf_sal", _request(enzyme_5="SalI", enzyme_3="XhoI",
                                            optimize_codons=False))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert any("粘端相同" in w and "TCGA" in w and "测序确认" in w
               for w in result.warnings)


def test_missing_sites_fallback_warns_unreproducible():
    """KpnI/PstI 不在 pET-28a 上：回退 MCS 替换后必须明确说明
    「酶切-连接无法复现该构建体」——这正是用户人工验证踩到的
    「限制性酶切生成的序列无法完成组装」场景"""
    result = run_design("uvf_fb", _request(enzyme_5="KpnI", enzyme_3="PstI",
                                           optimize_codons=False))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert any("无法" in w and "复现该构建体" in w for w in result.warnings)
    assert any("MCS 区间替换" in w for w in result.warnings)


def test_standard_digest_design_has_no_feasibility_warnings():
    """常规 NdeI/XhoI：位点复原、引物带位点、粘端不同——不得有自检告警"""
    result = run_design("uvf_ok", _request(optimize_codons=True))
    assert result.status == DesignStatus.COMPLETED, result.errors
    for w in result.warnings:
        assert "复现该构建体" not in w
        assert "粘端相同" not in w
        assert "引物 5′ 端未携带" not in w
    # 位点复原（内容级）：XhoI 在基因 5′ 连接处、NdeI 在 3′ 连接处
    assert result.construct_sequence[result.insert_start - 6:result.insert_start - 1] == "TCGAG"
    assert result.construct_sequence[result.insert_end:result.insert_end + 6] == "CATATG"


# ---------- 4. 方向判定可见 ----------

def test_auto_direction_summary_is_visible():
    result = run_design("uvf_dir", _request(optimize_codons=True))
    summary = [w for w in result.warnings if w.startswith("方向判定")]
    assert summary, "自动判定成功时结果层必须有方向判定说明"
    assert "T7 promoter" in summary[0]
    assert "负链" in summary[0]
    assert summary[0].startswith("方向判定：基因 5′ 端接 NdeI、3′ 端接 XhoI")


# ---------- 4b. 引物 5′ 保护碱基（PCR 产物末端位点等效平末端） ----------

def test_restriction_primers_carry_protective_spacer():
    """限制性引物 5′ 端必须带保护碱基（6nt）：位点贴着 PCR 产物末端时
    酶几乎切不动；保护碱基在酶切后随小片段去除、不进构建体"""
    from core.primer_designer import PrimerDesigner
    result = run_design("uvf_pad", _request(optimize_codons=True))
    assert result.status == DesignStatus.COMPLETED, result.errors
    fwd, rev = result.primers[0], result.primers[1]
    assert fwd.sequence.upper().startswith("TTTTTTCATATG")   # 6nt 保护碱基 + NdeI
    assert rev.sequence.upper().startswith("TTTTTTCTCGAG")   # 6nt 保护碱基 + XhoI(rc)
    # 拼在引物上的直接设计路径同样 6nt
    pair = PrimerDesigner().design_restriction_primers(
        result.cloned_sequence or result.optimized_sequence, "NdeI", "XhoI")
    assert pair.forward.sequence.startswith("TTTTTTCATATG")


# ---------- 5. Gibson 双酶切预览 ----------

def test_gibson_preview_keep_sites():
    pv = build_restriction_preview(RestrictionPreviewRequest(
        enzymes=["NdeI", "XhoI"], vector_id="pET-28a", insert_length=111,
        cloning_method="gibson", keep_restriction_sites=True))
    assert pv["strand"] == "-"
    assert len(pv["sites"]) == 2
    assert pv["removed_bp"] == 80
    assert pv["junctions"]["five"].startswith("CTCGAG")


def test_gibson_preview_seamless_has_no_sites():
    """无缝（保留位点关）：位点碱基被重组去除，预览图上不应再画切点"""
    pv = build_restriction_preview(RestrictionPreviewRequest(
        enzymes=["NdeI", "XhoI"], vector_id="pET-28a", insert_length=111,
        cloning_method="gibson", keep_restriction_sites=False))
    assert pv["sites"] == []
    assert pv["removed_bp"] == 85   # 无缝外扩：80bp 废片段 + 位点边界 5bp
    construct = pv["construct_length"]
    # 无缝构建体不再含位点序列（用方向判定确认同一几何，removed 一致）
    assert construct == 5369 - 85 + 111


def test_restriction_preview_default_unchanged():
    """不传 cloning_method 时保持旧行为（限制性克隆，保留位点）"""
    pv = build_restriction_preview(RestrictionPreviewRequest(
        enzymes=["NdeI", "XhoI"], vector_id="pET-28a", insert_length=111))
    assert len(pv["sites"]) == 2
    assert pv["strand"] == "-"


# ---------- 6. 自定义同源臂：预览与拼接定位判定 ----------

ARM5 = "GGTGGTGGTACGTACTG"      # 17bp，锚在载体 21..37
ARM3 = "TTATCCACAGGACTCAGT"     # 18bp，锚在载体 68..85
ARMS_BB = "A" * 20 + ARM5 + "C" * 30 + ARM3 + "T" * 20


def _arms_inline_vector():
    from app.schemas import DesignVectorInput
    return DesignVectorInput(id="armtest", name="armtest", sequence=ARMS_BB,
                             topology="circular")


def test_arms_preview_locates_and_shows_strand():
    """同源臂预览：基因落在两臂之间（30bp C 区被替换），正链装配"""
    pv = build_restriction_preview(RestrictionPreviewRequest(
        vector=_arms_inline_vector(), insert_length=120,
        cloning_method="custom_arms", homology_arm_5=ARM5, homology_arm_3=ARM3))
    assert pv["strand"] == "+"
    assert pv["gene"]["start"] == 38          # 臂5 末端 37 之后
    assert pv["removed_bp"] == 30             # 中间的 C*30 被替换
    assert pv["construct_length"] == len(ARMS_BB) - 30 + 120
    assert pv["sites"] == []                  # 无切点几何，不画酶切线
    assert pv["gene_5prime_enzyme"].startswith("臂5")
    assert pv["gene_3prime_enzyme"].startswith("臂3")


def test_arms_preview_reverse_orientation():
    """臂按负链取向（反向互补匹配）：预览判为负链装配。
    负链装配要求臂5 的锚点在右：镜像骨架（臂3 在左、臂5 在右）+ 臂取反向互补"""
    from core.seq_utils import revcomp
    mirrored = "A" * 20 + ARM3 + "C" * 30 + ARM5 + "T" * 20
    from app.schemas import DesignVectorInput
    pv = build_restriction_preview(RestrictionPreviewRequest(
        vector=DesignVectorInput(id="mir", name="mir", sequence=mirrored,
                                 topology="circular"),
        insert_length=120, cloning_method="custom_arms",
        homology_arm_5=revcomp(ARM5), homology_arm_3=revcomp(ARM3)))
    assert pv["strand"] == "-"


def test_arms_preview_swapped_fields_auto_swap():
    """两臂栏位贴反：不再报错——按载体位置自动对调并说明（顺序无关）"""
    pv = build_restriction_preview(RestrictionPreviewRequest(
        vector=_arms_inline_vector(), insert_length=120,
        cloning_method="custom_arms", homology_arm_5=ARM3,
        homology_arm_3=ARM5))
    assert pv["strand"] == "+"
    assert pv["gene"]["start"] == 38 and pv["removed_bp"] == 30
    assert any("自动对调" in w for w in pv["warnings"])


def test_arms_preview_direction_follows_promoter():
    """负链启动子（表达盒读向右→左）+ 正向粘贴的臂：不再「生硬拼接」，
    装配链向自动按启动子判成负链（臂取右端同源区的反向互补）"""
    pv = build_restriction_preview(RestrictionPreviewRequest(
        vector=_arms_vector_with_promoter(
            {"name": "T7 promoter", "type": "promoter",
             "start": 90, "end": 95, "strand": "-"}),
        insert_length=120, cloning_method="custom_arms",
        homology_arm_5=ARM5, homology_arm_3=ARM3))
    assert pv["strand"] == "-"
    notes = " ".join(pv["warnings"])
    assert "装配方向按载体启动子判定" in notes
    assert "负链" in notes
    assert "自动纠正" in notes


def test_arms_design_direction_follows_promoter():
    """正式设计同样按启动子自动定链向：CDS 落负链、与启动子一致"""
    from app.routes.models import CloningMethod, DesignRequest, DesignStatus
    result = run_design("uvf_arms_dir", DesignRequest(
        sequence=AA, sequence_type="amino_acid",
        vector=_arms_vector_with_promoter(
            {"name": "T7 promoter", "type": "promoter",
             "start": 90, "end": 95, "strand": "-"}),
        cloning_method=CloningMethod.GIBSON,
        gibson_linearization="custom_arms",
        homology_arm_5=ARM5, homology_arm_3=ARM3,
        sequence_name="myGene"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    cds = next(f for f in result.construct_features
               if "designed insert" in (f.get("description") or "").lower())
    assert cds["strand"] == "-"
    assert any("CDS 方向与启动子一致" in w and "负链" in w
               for w in result.warnings)


# ---------- 8. 全基因合成：oligo 直接包含克隆末端 ----------

def _reconstruct_molecule(oligos):
    """按 oligo 坐标与链向重建合成 oligo 覆盖的全长分子"""
    from core.seq_utils import revcomp
    spans = {}
    for i, o in enumerate(oligos):
        seg = o.sequence.upper() if i % 2 == 0 else revcomp(o.sequence.upper())
        spans[(o.target_start, o.target_end)] = seg
    lo = min(s for s, _ in spans)
    hi = max(e for _, e in spans)
    buf = [""] * (hi - lo)
    for (s, e), seg in spans.items():
        for j, ch in enumerate(seg):
            buf[s - lo + j] = ch
    return "".join(buf)


def test_synthesis_restriction_oligos_include_sites_and_spacer():
    """全基因合成 + 限制性克隆：oligo 覆盖的分子 = 载体连接点侧翼+位点+基因
    +位点+侧翼（侧翼直接取载体序列，全序列可在图谱核对）；不再产出
    「组装后扩增」引物对——合成产物酶切即可连接，无需再扩一轮"""
    from app.routes.models import DesignRequest
    result = run_design("uvf_syn_res", DesignRequest(
        sequence=AA, sequence_type="amino_acid", vector_id="pET-28a",
        cloning_method=CloningMethod.RESTRICTION,
        enzyme_5="NdeI", enzyme_3="XhoI",
        insert_source="gene_synthesis", sequence_name="myGene",
        optimize_codons=True))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert not any(p.name.endswith(("_F", "_R")) for p in result.primers), \
        "克隆末端已含在合成分子里，不应再产出扩增加末端的引物对"
    oligos = [p for p in result.primers if p.name.startswith("myGene")]
    assert len(oligos) >= 2
    molecule = _reconstruct_molecule(oligos)
    cloned = result.cloned_sequence or result.optimized_sequence
    # 结构：两端各 6nt（载体连接点侧翼）+ NdeI 位点 + 克隆工作序列 + XhoI
    assert molecule[6:12] == "CATATG" and molecule[-12:-6] == "CTCGAG"
    assert molecule[12:12 + len(cloned)] == cloned
    assert len(molecule) == 12 + 6 + len(cloned) + 6
    # 侧翼必须是构建体上的真实序列：正链装配时整条分子原样落在构建体上，
    # 负链装配时落在构建体的反向互补上——这正是「全序列可核对」的要求
    cons = (result.construct_sequence or "").upper()
    assert molecule in cons or molecule in revcomp(cons), \
        "合成分子（含两端载体侧翼）必须整体落在构建体序列上"
    assert any("已包含克隆末端" in w for w in result.warnings)


def test_synthesis_gibson_arms_oligos_include_arms():
    """全基因合成 + 同源臂：oligo 覆盖的分子 = 臂5+基因+臂3（解析后的臂），
    不再产出扩增加臂的引物对"""
    from app.routes.models import CloningMethod as CM, DesignRequest, DesignStatus
    result = run_design("uvf_syn_arm", DesignRequest(
        sequence=AA, sequence_type="amino_acid",
        vector=_arms_inline_vector(),
        cloning_method=CM.GIBSON,
        gibson_linearization="custom_arms",
        homology_arm_5=ARM5, homology_arm_3=ARM3,
        insert_source="gene_synthesis", sequence_name="myGene"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert not any(p.name.endswith(("_F", "_R")) for p in result.primers)
    oligos = [p for p in result.primers if p.name.startswith("myGene")]
    molecule = _reconstruct_molecule(oligos)
    cloned = result.cloned_sequence or result.optimized_sequence
    assert molecule == ARM5 + cloned + ARM3


def test_synthesis_golden_gate_keeps_cloning_pair():
    """Golden Gate 末端语义不同（BsaI 位点+overhang）：保留合成后扩增的旧路径"""
    from app.routes.models import CloningMethod as CM, DesignRequest, DesignStatus
    result = run_design("uvf_syn_gg", DesignRequest(
        sequence=AA, sequence_type="amino_acid", vector_id="pET-28a",
        cloning_method=CM.GOLDEN_GATE, enzyme="BsaI",
        insert_source="gene_synthesis", sequence_name="myGene"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert any(p.name.endswith("_F") for p in result.primers), \
        "Golden Gate 未把末端合进 oligo，应保留组装后扩增加末端的引物对"


def test_synthesis_end_oligos_annotate_vector_flanks():
    """首/尾 oligo 的 notes 写明两端成分：默认取载体连接点侧翼（与图谱
    一致、可全序列核对），酶名一并写明——用户核对合成订单不用再猜"""
    from app.routes.models import DesignRequest
    result = run_design("uvf_syn_note", DesignRequest(
        sequence=AA, sequence_type="amino_acid", vector_id="pET-28a",
        cloning_method=CloningMethod.RESTRICTION,
        enzyme_5="NdeI", enzyme_3="XhoI",
        insert_source="gene_synthesis", sequence_name="myGene"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    first, last = result.primers[0], result.primers[-1]
    assert "载体连接点侧翼" in (first.notes or "") and "NdeI" in (first.notes or ""), first.notes
    assert "可在图谱核对" in (first.notes or "")
    assert "载体连接点侧翼" in (last.notes or "") and "XhoI" in (last.notes or ""), last.notes
    assert "可在图谱核对" in (last.notes or "")


def test_synthesis_end_oligos_fall_back_to_protective_spacer():
    """载体侧翼取不到（位点不在载体上、回退 MCS 区间替换）时回退固定
    保护碱基，notes 与结果层说明必须如实写明「不在构建体中」"""
    from app.routes.models import DesignRequest
    result = run_design("uvf_syn_pad", DesignRequest(
        sequence=AA, sequence_type="amino_acid", vector_id="pET-28a",
        cloning_method=CloningMethod.RESTRICTION,
        enzyme_5="KpnI", enzyme_3="PstI",
        insert_source="gene_synthesis", sequence_name="myGene"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    first, last = result.primers[0], result.primers[-1]
    assert "保护碱基" in (first.notes or "") and "不在构建体中" in (first.notes or ""), first.notes
    assert "保护碱基" in (last.notes or "") and "不在构建体中" in (last.notes or ""), last.notes
    molecule = _reconstruct_molecule(
        [p for p in result.primers if p.name.startswith("myGene")])
    assert molecule.startswith("TTTTTT") and molecule.endswith("TTTTTT"), \
        "侧翼不可用时应回退 6nt 固定保护碱基"


def test_synthesis_gibson_end_oligos_annotate_arms():
    """同源臂合成 oligo 的首尾 notes 写明「载体同源臂，组装后与载体序列
    一致」——臂在图谱里，与限制性的保护碱基（不在图谱里）口径不同"""
    from app.routes.models import CloningMethod as CM, DesignRequest
    result = run_design("uvf_syn_arm_note", DesignRequest(
        sequence=AA, sequence_type="amino_acid",
        vector=_arms_inline_vector(),
        cloning_method=CM.GIBSON,
        gibson_linearization="custom_arms",
        homology_arm_5=ARM5, homology_arm_3=ARM3,
        insert_source="gene_synthesis", sequence_name="myGene"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    first, last = result.primers[0], result.primers[-1]
    assert "同源臂" in (first.notes or "") and "一致" in (first.notes or ""), first.notes
    assert "同源臂" in (last.notes or "") and "一致" in (last.notes or ""), last.notes


def test_synthesis_oligos_all_locate_on_construct():
    """oligo↔构建体一致性：两端取载体侧翼后，每条合成 oligo 的**全序列**
    （不用扣任何碱基）都必须能在构建体序列（顶链或反向互补）上定位；
    正常设计不允许出现「无法在构建体序列上定位」告警"""
    from app.routes.models import DesignRequest
    result = run_design("uvf_syn_loc", DesignRequest(
        sequence=AA, sequence_type="amino_acid", vector_id="pET-28a",
        cloning_method=CloningMethod.RESTRICTION,
        enzyme_5="NdeI", enzyme_3="XhoI",
        insert_source="gene_synthesis", sequence_name="myGene"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert not any("无法在构建体序列上定位" in w for w in result.warnings), result.warnings
    cons = (result.construct_sequence or "").upper()
    cons_rc = revcomp(cons)
    for p in (x for x in result.primers if x.target_start is not None):
        s = p.full_sequence.upper()
        assert s in cons or s in cons_rc, \
            f"{p.name} 全序列应可定位（载体侧翼核对）: {s[:30]}…"


def test_arms_preview_unknown_arm_fails_loudly():
    import pytest
    with pytest.raises(ValueError) as exc:
        build_restriction_preview(RestrictionPreviewRequest(
            vector=_arms_inline_vector(), insert_length=120,
            cloning_method="custom_arms", homology_arm_5="ACGT" * 5,
            homology_arm_3=ARM3))
    assert "无精确匹配" in str(exc.value)


def test_arms_design_reports_splice_position():
    """正式设计结果里也有拼接定位说明（锚点、替换量、装配链向）"""
    from app.routes.models import CloningMethod, DesignRequest, DesignStatus
    result = run_design("uvf_arms", DesignRequest(
        sequence=AA, sequence_type="amino_acid",
        vector=_arms_inline_vector(),
        cloning_method=CloningMethod.GIBSON,
        gibson_linearization="custom_arms",
        homology_arm_5=ARM5, homology_arm_3=ARM3,
        sequence_name="myGene"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    splice = [w for w in result.warnings if w.startswith("拼接定位")]
    assert splice, "同源臂设计必须给出拼接定位说明"
    assert "臂5 末端 37" in splice[0]
    assert "正链" in splice[0]


# ---------- 7. CDS 方向 = 启动子方向（同源臂预览/设计的判定） ----------

def test_cassette_hint_promoter_gap_extended():
    """启动子距切点 1600bp（>旧上限 1500）：必须仍能判定方向——
    大标签/MCS 隔开启动子的载体此前方向判定失效、回落默认正链"""
    from core.construct_assembly import cassette_hint
    elements = [{"name": "T7 promoter", "type": "promoter",
                 "start": 1, "end": 18, "strand": "+"}]
    hint = cassette_hint(elements, 1700, 1750)   # gap = 1682
    assert hint["strand"] == "+"
    hint_far = cassette_hint(elements, 5000, 5050)   # 超过 3000：判不出
    assert hint_far["strand"] is None


def test_cassette_hint_nearby_promoter_beats_distant_opposite():
    """邻近度加权：近处真表达盒（gap 100）必须压过 2000bp 外恰好朝插入区
    读的无关反向启动子（否则窗口放宽后被别的表达盒带偏方向）"""
    from core.construct_assembly import cassette_hint
    elements = [
        {"name": "T7 promoter", "type": "promoter",
         "start": 100, "end": 118, "strand": "+"},          # 近处，gap 82 → 权重 3
        {"name": "other cassette promoter", "type": "promoter",
         "start": 4800, "end": 4818, "strand": "-"},        # 远处反向，gap 50 → 负链权重 3?
    ]
    # 远处启动子在 right_cut=1750 右侧 → gap = 4800-1750 = 3050 > 3000，不计入；
    # 把它挪到 3700（gap 1950，权重 1）
    elements[1]["start"] = 3700
    elements[1]["end"] = 3718
    hint = cassette_hint(elements, 200, 1750)
    assert hint["strand"] == "+"
    assert not hint["ambiguous"]
    assert hint["promoter"]["name"] == "T7 promoter"


def test_cassette_hint_equal_proximity_still_ambiguous():
    """两侧都是贴邻的对立启动子（3 对 3）：仍判歧义，交给用户手选"""
    from core.construct_assembly import cassette_hint
    elements = [
        {"name": "P_left", "type": "promoter", "start": 50, "end": 60, "strand": "+"},
        {"name": "P_right", "type": "promoter", "start": 210, "end": 220, "strand": "-"},
    ]
    hint = cassette_hint(elements, 100, 180)   # 左 gap 40（权3），右 gap 30（权3）
    assert hint["strand"] is None and hint["ambiguous"]


def _arms_vector_with_promoter(feature: dict):
    from app.schemas import DesignVectorInput
    return DesignVectorInput(id="pdir", name="pdir", sequence=ARMS_BB,
                             topology="circular", features=[feature])


def test_arms_preview_direction_agreement_is_stated():
    """正链启动子在插入区间上游 + 正向臂：预览明确说「方向一致」"""
    pv = build_restriction_preview(RestrictionPreviewRequest(
        vector=_arms_vector_with_promoter(
            {"name": "T7 promoter", "type": "promoter",
             "start": 1, "end": 18, "strand": "+"}),
        insert_length=120, cloning_method="custom_arms",
        homology_arm_5=ARM5, homology_arm_3=ARM3))
    assert pv["strand"] == "+"
    assert any("CDS 方向与启动子一致" in w and "正链" in w
               for w in pv["warnings"])

