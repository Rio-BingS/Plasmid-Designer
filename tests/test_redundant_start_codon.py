"""限制性克隆起始密码子一致性（NdeI 位点自带 ATG，第二轮审计后用户反馈）

场景：pET 类载体用 NdeI（CATATG）克隆时，位点复原后末尾 ATG 紧贴基因、
通常充当起始密码子；插入序列（氨基酸输入经密码子优化）又以 ATG 开头时，
成品蛋白 N 端出现连续两个 Met。行为：
- trim_redundant_start=True（默认）→ 去掉插入序列开头的 ATG，第一个甲硫氨酸
  由位点提供，蛋白与输入氨基酸一致；
- False → 只告警不动序列；
- 位点方案落空（载体缺位点回退 MCS 替换）→ 绝不动插入序列；
- CDS 注释把位点里的起始密码子包进来（CDS 完整性）。
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.design_service import (  # noqa: E402
    CloningMethod, _resolve_redundant_start_codon, run_design,
)
from app.routes.models import DesignRequest, DesignStatus  # noqa: E402

AA_SEQ = "M" + "EKT" * 20   # 以 M 开头的氨基酸输入（密码子优化产出 ATG 开头）


def _restriction_request(**over) -> DesignRequest:
    payload = {
        "sequence": AA_SEQ,
        "sequence_type": "amino_acid",
        "vector_id": "pET-28a",
        "cloning_method": "restriction",
        "enzyme_5": "NdeI",
        "enzyme_3": "XhoI",
    }
    payload.update(over)
    return DesignRequest(**payload)


def _insert_cds(result):
    return next(f for f in result.construct_features
                if f["type"] == "CDS" and "designed insert" in (f.get("description") or "").lower())


def _start_codon_of(result, feat) -> str:
    """按链取向取出 CDS 注释起点处的前三个碱基（基因读法方向）"""
    from core.seq_utils import revcomp
    construct = result.construct_sequence
    if feat["strand"] == "-":
        return revcomp(construct[feat["end"] - 3:feat["end"]])
    return construct[feat["start"] - 1:feat["start"] + 2]


def test_ndei_default_trims_and_keeps_cds_complete():
    """默认（去除开启）：插入序列开头的 ATG 被去掉，CDS 包含位点起始密码子"""
    result = run_design("d_trim", _restriction_request())
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert any(w.startswith("已自动去除冗余起始密码子") for w in result.warnings)
    # 去除后优化序列不再以 ATG 开头（第一个 M 由位点提供）
    assert not result.optimized_sequence.upper().startswith("ATG")
    feat = _insert_cds(result)
    assert _start_codon_of(result, feat) == "ATG"  # 起始密码子来自位点
    # pET-28a 表达盒在负链：CDS 注释终点向位点扩 3bp
    assert feat["strand"] == "-"
    assert feat["end"] == result.insert_end + 3
    assert "起始密码子来自 NdeI" in feat["description"]


def test_ndei_option_off_warns_but_keeps_duplicate():
    """关闭去除：只告警，基因自带 ATG 保留（Met-Met），CDS 仍扩展到位点"""
    result = run_design("d_keep", _restriction_request(trim_redundant_start=False))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert any(w.startswith("检测到冗余起始密码子") for w in result.warnings)
    assert result.optimized_sequence.upper().startswith("ATG")
    feat = _insert_cds(result)
    construct = result.construct_sequence
    # 基因自带的 ATG 还在（用户选择保留）：负链基因 5′ 端 = 片段右侧
    from core.seq_utils import revcomp
    assert revcomp(construct[result.insert_end - 3:result.insert_end]) == "ATG"
    # 但 CDS 注释仍从位点的 ATG 开始（真实起始密码子在位点里）
    assert _start_codon_of(result, feat) == "ATG"
    assert feat["end"] == result.insert_end + 3


def test_ndei_reverse_strand_on_pet28a_trims_at_gene_5_end():
    """pET-28a 表达盒在负链：NdeI 是基因 5′ 端，去除与 CDS 扩展在另一端坐标"""
    result = run_design("d_rev", _restriction_request(insert_direction="minus"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert any(w.startswith("已自动去除冗余起始密码子") for w in result.warnings)
    feat = _insert_cds(result)
    assert feat["strand"] == "-"
    construct = result.construct_sequence
    # 基因 5′ 端在片段右侧：位点前 3bp（CAT）的反向互补是起始密码子
    assert feat["end"] == result.insert_end + 3
    from core.seq_utils import revcomp
    assert revcomp(construct[result.insert_end:result.insert_end + 3]) == "ATG"


def test_bamhi_xhoi_no_detection():
    """BamHI（GGATCC）位点不含贴基因的 ATG：不告警、不去除"""
    result = run_design("d_bam", _restriction_request(
        enzyme_5="BamHI", enzyme_3="XhoI", trim_redundant_start=False))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert not any("起始密码子" in w for w in result.warnings)
    assert result.optimized_sequence.upper().startswith("ATG")


def test_site_missing_fallback_never_trims():
    """载体上没有 NdeI（回退 MCS 整段替换，位点不在构建体里）：绝不动插入序列"""
    from core.vector_library import Vector  # noqa: F401  确认可导入
    from app import design_service

    class _FakeLib:
        def get_vector(self, vector_id):
            from core.vector_library import CloningSite, MCS, Vector
            return Vector(
                id="synth", name="noNdeI",
                sequence="A" * 50 + "GGATCC" + "C" * 50,
                mcs=MCS(name="MCS", start=51, end=56, sites=[
                    CloningSite("BamHI", "GGATCC", 51, 57, "GATC", True)]),
                elements=[],
            )

    request = SimpleNamespace(
        cloning_method=CloningMethod.RESTRICTION,
        enzyme_5="NdeI", enzyme_3="XhoI", enzyme=None,
        trim_redundant_start=True,
    )
    dna = "ATG" + "AAAGGGTTT" * 5
    out, warnings = _resolve_redundant_start_codon(
        request, _FakeLib().get_vector("x"), _FakeLib().get_vector("x").sequence,
        dna, resolved_ends=None)
    assert out == dna, "位点方案落空时不得改写插入序列"
    assert warnings == []


def test_golden_gate_out_of_scope():
    """Golden Gate overhang（如 AATG）是 MoClo 惯例，不在本检测范围"""
    from app import design_service
    request = SimpleNamespace(
        cloning_method=CloningMethod.GOLDEN_GATE,
        enzyme_5=None, enzyme_3=None, enzyme="BsaI",
        trim_redundant_start=True,
    )
    out, warnings = _resolve_redundant_start_codon(
        request, None, "N" * 100, "ATGAAAGGG", resolved_ends=None)
    assert out == "ATGAAAGGG" and warnings == []


def test_forward_synthetic_vector_trims_and_extends_start(monkeypatch):
    """正链合成载体（NdeI 在左）：去除后 CDS 起点向位点残留扩 3bp"""
    from core.vector_library import CloningSite, MCS, Vector
    from app import design_service

    seq = "A" * 50 + "CATATG" + "G" * 40 + "CTCGAG" + "C" * 50
    vector = Vector(
        id="synth", name="synth", sequence=seq,
        mcs=MCS(name="MCS", start=51, end=56, sites=[
            CloningSite("NdeI", "CATATG", 51, 57, "TA", True)]),
        elements=[],
    )

    class _Lib:
        def get_vector(self, vector_id):
            return vector

    monkeypatch.setattr(design_service, "get_vector_library", lambda: _Lib())
    result = run_design("d_fwd", _restriction_request(vector_id="synth"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert any(w.startswith("已自动去除冗余起始密码子") for w in result.warnings)
    feat = _insert_cds(result)
    assert feat["strand"] == "+"
    # CDS 起点向前扩 3bp：TATG 的末尾 ATG 是起始密码子
    assert feat["start"] == result.insert_start - 3
    assert result.construct_sequence[feat["start"] - 1:feat["start"] + 2] == "ATG"


# ==================== Gibson ====================

def _gibson_request(**over):
    """单点位 Gibson（旧模型）请求：不带酶、显式声明 single_site。
    双酶切线性化测试用 _gibson_double_request。"""
    over.setdefault("gibson_linearization", "single_site")
    over.setdefault("enzyme_5", None)
    over.setdefault("enzyme_3", None)
    return _restriction_request(cloning_method="gibson", **over)


def _gibson_double_request(**over):
    """双酶切线性化 Gibson 请求（默认模式，pET-28a NdeI/XhoI）"""
    over.setdefault("gibson_linearization", "double_digest")
    over.setdefault("enzyme_5", "NdeI")
    over.setdefault("enzyme_3", "XhoI")
    return _restriction_request(cloning_method="gibson", **over)


def test_gibson_standalone_trims_and_extends_cds(monkeypatch):
    """线性化点前 3bp 为 ATG（独立表达）：默认去除 + CDS 向前扩 3bp"""
    from core.vector_library import MCS, Vector
    from app import design_service

    # "A"*47 + "ATG"：MCS 起点 51 → 线性化 pos=50（0-based），backbone[:50] 以 ATG 结尾
    vector = Vector(
        id="synth", name="synth",
        sequence="A" * 47 + "ATG" + "C" * 52,
        mcs=MCS(name="MCS", start=51, end=56, sites=[]),
        elements=[],
    )

    class _Lib:
        def get_vector(self, vector_id):
            return vector

    monkeypatch.setattr(design_service, "get_vector_library", lambda: _Lib())
    result = run_design("g_trim", _gibson_request(vector_id="synth"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert any(w.startswith("已自动去除冗余起始密码子") for w in result.warnings)
    assert not result.optimized_sequence.upper().startswith("ATG")
    feat = _insert_cds(result)
    assert feat["strand"] == "+"
    assert feat["start"] == result.insert_start - 3
    assert result.construct_sequence[feat["start"] - 1:feat["start"] + 2] == "ATG"
    assert "线性化点前的载体碱基" in feat["description"]


def test_gibson_option_off_only_warns(monkeypatch):
    from core.vector_library import MCS, Vector
    from app import design_service

    vector = Vector(
        id="synth", name="synth",
        sequence="A" * 47 + "ATG" + "C" * 52,
        mcs=MCS(name="MCS", start=51, end=56, sites=[]),
        elements=[],
    )

    class _Lib:
        def get_vector(self, vector_id):
            return vector

    monkeypatch.setattr(design_service, "get_vector_library", lambda: _Lib())
    result = run_design("g_keep", _gibson_request(
        vector_id="synth", trim_redundant_start=False))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert any(w.startswith("检测到冗余起始密码子") for w in result.warnings)
    assert result.optimized_sequence.upper().startswith("ATG")


def test_gibson_fusion_in_frame_warns_internal_met(monkeypatch):
    """线性化点紧贴正链 tag CDS 末端且同框：不去除，提示内部 Met"""
    from core.vector_library import ElementType, MCS, Vector, VectorElement
    from app import design_service

    # tag 40..48（9bp 同框）+ C 区；MCS 起点 49 → pos=48，紧贴 tag 末端
    vector = Vector(
        id="synth", name="synth",
        sequence="A" * 39 + "GGTGGTGGT" + "C" * 52,
        mcs=MCS(name="MCS", start=49, end=54, sites=[]),
        elements=[VectorElement("6xHis", ElementType.TAG, 40, 48, "GGTGGTGGT", strand="+")],
    )

    class _Lib:
        def get_vector(self, vector_id):
            return vector

    monkeypatch.setattr(design_service, "get_vector_library", lambda: _Lib())
    result = run_design("g_fuse", _gibson_request(vector_id="synth"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert any("融合克隆" in w and "内部甲硫氨酸" in w for w in result.warnings)
    assert result.optimized_sequence.upper().startswith("ATG")  # 不动序列


def test_gibson_fusion_frameshift_warns(monkeypatch):
    """线性化点在 tag CDS 内部且碱基数非 3 的倍数：移码告警"""
    from core.vector_library import ElementType, MCS, Vector, VectorElement
    from app import design_service

    # tag 41..49（9bp，插入点前占 8bp，非 3 倍数）+ C 区；MCS 起点 49 → pos=48 在 tag 内
    vector = Vector(
        id="synth", name="synth",
        sequence="A" * 40 + "GGTGGTGGT" + "C" * 51,
        mcs=MCS(name="MCS", start=49, end=54, sites=[]),
        elements=[VectorElement("6xHis", ElementType.TAG, 41, 49, "GGTGGTGGT", strand="+")],
    )

    class _Lib:
        def get_vector(self, vector_id):
            return vector

    monkeypatch.setattr(design_service, "get_vector_library", lambda: _Lib())
    result = run_design("g_shift", _gibson_request(vector_id="synth"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert any("读码框不匹配" in w and "移位" in w for w in result.warnings)


def test_gibson_direction_warning_on_pet28a():
    """pET-28a 表达盒在负链：Gibson 正链组装必须给出方向相反告警"""
    result = run_design("g_dir", _gibson_request())
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert any("反义 RNA" in w for w in result.warnings)
    # 默认线性化点（MCS 起点）前不是 ATG：不触发去除
    assert not any("已自动去除冗余起始密码子" in w for w in result.warnings)


# ==================== Gibson 双酶切线性化（默认模式）====================

def test_gibson_double_digest_matches_restriction_semantics():
    """双酶切线性化 Gibson = restriction 几何：去除、CDS 扩展、方向全套适用"""
    result = run_design("gd_trim", _gibson_double_request())
    assert result.status == DesignStatus.COMPLETED, result.errors
    # 位点残留起始密码子：与 restriction 同一套去除逻辑
    assert any(w.startswith("已自动去除冗余起始密码子") for w in result.warnings)
    assert not result.optimized_sequence.upper().startswith("ATG")
    feat = _insert_cds(result)
    assert _start_codon_of(result, feat) == "ATG"
    assert "起始密码子来自 NdeI" in feat["description"]
    # pET-28a 表达盒负链 → 反向排布告警应为 Gibson 文案（同源臂，非粘端）
    assert any("同源臂" in w and "粘端" not in w for w in result.warnings
               if "切点在前" in w)


def test_gibson_double_digest_primers_use_homology_arms():
    """双酶切 Gibson 引物：同源臂覆盖线性化末端，基因段含位点残留"""
    result = run_design("gd_primer", _gibson_double_request())
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert len(result.primers) >= 2
    fwd, rev = result.primers[0], result.primers[1]
    # 同源臂（overhang）非空且来自线性化末端
    assert fwd.overhang and rev.overhang
    # 基因段（引物主体）以位点残留开头：pET-28a 反向排布下 PCR 分子
    # = XhoI 残留(TCGAG) + 基因反向互补 + NdeI 残留，正向引物锚定分子 5′ 端
    assert fwd.sequence.upper().startswith("TCGAG")


def test_gibson_double_digest_protocol_mentions_digest():
    """方案文本：双酶切线性化步骤写明两把酶"""
    result = run_design("gd_proto", _gibson_double_request())
    assert result.status == DesignStatus.COMPLETED, result.errors
    text = json.dumps(result.clone_protocol, ensure_ascii=False) if not isinstance(
        result.clone_protocol, str) else result.clone_protocol
    assert "NdeI/XhoI 双酶切线性化" in text


def test_gibson_double_digest_without_sites_falls_back(monkeypatch):
    """双酶切模式但载体缺位点：回退 MCS 替换，绝不动插入序列"""
    from core.vector_library import MCS, Vector
    from app import design_service

    vector = Vector(
        id="synth", name="synth",
        sequence="A" * 47 + "ATG" + "C" * 52,   # 无 NdeI/XhoI 位点
        mcs=MCS(name="MCS", start=51, end=56, sites=[]),
        elements=[],
    )

    class _Lib:
        def get_vector(self, vector_id):
            return vector

    monkeypatch.setattr(design_service, "get_vector_library", lambda: _Lib())
    result = run_design("gd_fallback", _gibson_double_request(vector_id="synth"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    # 位点方案落空 → 插入序列的 ATG 是唯一起始密码子，不能去
    assert result.optimized_sequence.upper().startswith("ATG")
    assert not any(w.startswith("已自动去除冗余起始密码子") for w in result.warnings)


def test_gibson_double_digest_without_enzymes_falls_back_to_single(monkeypatch):
    """双酶切模式但位点不全：告警并按单位点线性化处理（旧模型仍可用）"""
    from core.vector_library import MCS, Vector
    from app import design_service

    vector = Vector(
        id="synth", name="synth",
        sequence="A" * 47 + "ATG" + "C" * 52,
        mcs=MCS(name="MCS", start=51, end=56, sites=[]),
        elements=[],
    )

    class _Lib:
        def get_vector(self, vector_id):
            return vector

    monkeypatch.setattr(design_service, "get_vector_library", lambda: _Lib())
    result = run_design("gd_partial", _restriction_request(
        cloning_method="gibson", vector_id="synth",
        gibson_linearization="double_digest", enzyme_5="NdeI", enzyme_3=None))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert any("已按单位点线性化处理" in w for w in result.warnings)
    # 单点位模型照常工作
    assert any(w.startswith("已自动去除冗余起始密码子") for w in result.warnings)

# ==================== Gibson 保留/去除酶切位点 ====================

def test_gibson_double_digest_scarless_removes_sites():
    """关闭保留位点（无缝连接）：构建体不含位点序列、不去起始 ATG、CDS 不扩展"""
    result = run_design("gd_scarless", _gibson_double_request(
        keep_restriction_sites=False))
    assert result.status == DesignStatus.COMPLETED, result.errors
    construct = result.construct_sequence.upper()
    # 位点整体从构建体去除
    assert "CATATG" not in construct and "CTCGAG" not in construct
    assert any("无缝连接" in w for w in result.warnings)
    # 位点不在构建体里 → 插入序列自己的 ATG 是唯一起始密码子，不能去
    assert result.optimized_sequence.upper().startswith("ATG")
    # CDS 注释不扩展（起点 = 基因自身起点）
    feat = _insert_cds(result)
    assert feat["start"] == result.insert_start
    # 引物：插入分子 = 基因本身（无位点残留）
    assert result.primers and not result.primers[0].sequence.upper().startswith("TCGAG")


def test_gibson_double_digest_keep_sites_default_restores():
    """默认保留位点：构建体连接处位点完整（回归对照）"""
    result = run_design("gd_keep", _gibson_double_request())
    assert result.status == DesignStatus.COMPLETED, result.errors
    construct = result.construct_sequence.upper()
    assert "CATATG" in construct and "CTCGAG" in construct


# ==================== 质粒命名 ====================

def test_plasmid_name_defaults_to_sequence_name():
    """默认质粒名 = 序列名"""
    result = run_design("n_base", _restriction_request(sequence_name="GFP"))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert result.plasmid_name == "GFP"


def test_plasmid_name_with_vector_prefix():
    """开启前缀：质粒名 = 载体名-序列名，GenBank LOCUS 以它为准"""
    result = run_design("n_prefix", _restriction_request(
        sequence_name="GFP", vector_name_prefix=True))
    assert result.status == DesignStatus.COMPLETED, result.errors
    assert result.plasmid_name == "pET-28a(+)-GFP"  # 载体全名含 (+)
    # GenBank 导出 LOCUS 用质粒名（format_locus_line 会做字符清理）
    from app.design_service import generate_genbank_from_result
    gb = generate_genbank_from_result(result)
    assert gb.splitlines()[0].startswith("LOCUS")
    locus = gb.splitlines()[0].replace("-", "_").replace("(", "_").replace(")", "_")
    assert "pET_28a" in locus and "GFP" in locus
