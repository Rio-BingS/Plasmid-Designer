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
