"""核心设计服务测试：密码子表、optimize 开关、组装、统一流水线。"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "src" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.routes.models import CloningMethod, DesignRequest, DesignStatus, SequenceType
from app.design_service import (
    assemble_construct,
    process_sequence,
    run_design,
    generate_genbank_from_result,
    derive_gg_overhangs,
    get_vector_library,
    ensure_vector_backbone,
)
from core.codon_optimizer import CodonOptimizer
from core.vector_library import Vector, MCS, CloningSite, VectorElement, ElementType


def test_back_translate_does_not_call_optimize_path():
    opt = CodonOptimizer(species="ecoli")
    dna = opt.back_translate("MKLV")
    assert len(dna) == 12
    assert set(dna) <= set("ATGC")


def test_process_sequence_optimize_false_differs_from_flag_true_path():
    aa = "MKVLWAALLTFLGCAATSGSQAPDRRNRLALASLLRLQGVSSVQIRCRDSDMNADADATIRR"
    dna_opt, cai_opt, _, _ = process_sequence(
        aa, SequenceType.AMINO_ACID, True, "ecoli", 40, 60
    )
    dna_raw, cai_raw, _, warns = process_sequence(
        aa, SequenceType.AMINO_ACID, False, "ecoli", 40, 60
    )
    assert len(dna_opt) == len(aa) * 3
    assert len(dna_raw) == len(aa) * 3
    assert any("未启用" in w for w in warns)
    assert cai_raw is not None


def test_assemble_construct_inserts_at_mcs():
    vector = Vector(
        id="test",
        name="testV",
        sequence="A" * 50 + "GGATCC" + "C" * 50,
        mcs=MCS(name="MCS", start=51, end=56, sites=[
            CloningSite("BamHI", "GGATCC", 51, 57, "GATC", True)
        ]),
        elements=[
            VectorElement("ori", ElementType.ORIGIN, 1, 20, "A" * 20),
            VectorElement("res", ElementType.RESISTANCE, 80, 100, "C" * 21),
        ],
    )
    insert = "ATG" * 10
    asm = assemble_construct(vector, insert, "geneX")
    assert insert in asm.sequence
    assert asm.insert_start == 51
    assert asm.insert_end == 50 + len(insert)
    assert any(f["name"] == "geneX" for f in asm.features)
    # 下游元件应右移
    res = next(f for f in asm.features if f["name"] == "res")
    assert res["start"] > 80 or res["start"] == 80 + (len(insert) - 6)


def test_assemble_construct_keeps_cut_pair_when_mcs_point_sits_on_a_cut():
    """一次性载体的 MCS 是「推测点」，点落在切点上时不能退回整段替换

    pET-30a 实测：XhoI 切点 158、NdeI 切点 346（废片段 188 bp），推测的 MCS
    点正好取在 158。旧口径按「切除区间是否包含该点」打分，于是选中反方向的
    「整圈 5234 bp」→ 判「切点跨原点」→ 退回 MCS 整段替换：图谱上废片段
    没被切走、基因落在两个位点之外。这里的载体只有一对位点，点压在左切点上。
    """
    sequence = "G" * 40 + "CTCGAG" + "T" * 34 + "GGATCC" + "C" * 40
    vector = Vector(
        id="adhoc",
        name="uploaded",
        sequence=sequence,
        # 推测 MCS = 纯插入点（start=point, end=point-1），点压在 XhoI 切点上
        mcs=MCS(name="MCS（按唯一位点推测）", start=41, end=40, sites=[]),
    )
    gene = "ATG" + "GAA" * 20 + "TAA"
    asm = assemble_construct(vector, gene, "ins", method="restriction",
                             enzyme_5="XhoI", enzyme_3="BamHI")
    assert not [w for w in asm.warnings if "已按 MCS 区间替换" in w]
    assert not [w for w in asm.warnings if "跨越载体原点" in w]
    # 两个切点之间的废片段被切走，两端补回位点残留：XhoI 的 TCGAG / BamHI 的 G
    from core.construct_assembly import physical_sites

    xhoi = physical_sites(sequence, "XhoI")[0]["cut_fwd"]
    bamhi = physical_sites(sequence, "BamHI")[0]["cut_fwd"]
    assert asm.insert_start == xhoi + 1 + 5
    assert asm.insert_end - asm.insert_start + 1 == len(gene)
    assert len(asm.sequence) == len(sequence) - (bamhi - xhoi) + 5 + len(gene) + 1
    assert asm.features[0]["strand"] == "+"
    assert sequence[40:46] in asm.sequence and sequence[80:86] in asm.sequence


def test_run_design_end_to_end_minimal():
    req = DesignRequest(
        sequence="MKLVWAALLTF",
        sequence_type=SequenceType.AMINO_ACID,
        sequence_name="demo",
        vector_id="pET-28a",
        cloning_method=CloningMethod.GIBSON,
        optimize_codons=True,
        target_species="ecoli",
    )
    result = run_design("design_test001", req)
    assert result.status == DesignStatus.COMPLETED
    assert result.optimized_sequence
    assert result.construct_sequence
    assert result.final_length == len(result.construct_sequence)
    assert len(result.primers) >= 2
    assert result.insert_start and result.insert_end

    gb = generate_genbank_from_result(result)
    assert "LOCUS" in gb and "ORIGIN" in gb
    import io
    from Bio import SeqIO
    rec = SeqIO.read(io.StringIO(gb), "genbank")  # 解析告警在套件中即为错误
    assert len(rec) == result.final_length
    assert rec.annotations["topology"] == "circular"
    assert result.optimized_sequence in gb.replace(" ", "").replace("\n", "") or "insert" in gb

    from app.map_documents import design_map_document
    m = design_map_document(result)
    assert m["schema"] == "plasmid-map/1" and m["topology"] == "circular"
    assert m["length"] == result.final_length == len(m["sequence"])
    sources = {f["source"] for f in m["features"]}
    assert sources == {"design", "vector"}
    insert = [f for f in m["features"] if f["source"] == "design"]
    assert len(insert) == 1 and insert[0]["type"] == "cds"
    assert (insert[0]["start"], insert[0]["end"]) == (result.insert_start, result.insert_end)


def test_run_design_optimize_false():
    req = DesignRequest(
        sequence="MKLV",
        sequence_type=SequenceType.AMINO_ACID,
        optimize_codons=False,
        vector_id="pET-28a",
        cloning_method=CloningMethod.RESTRICTION,
        enzyme="BamHI",
    )
    result = run_design("design_test002", req)
    assert result.status == DesignStatus.COMPLETED
    assert any("未启用" in w for w in result.warnings)


def test_golden_gate_completes():
    req = DesignRequest(
        sequence="ATGAAAGTGCTG",
        sequence_type=SequenceType.DNA,
        cloning_method=CloningMethod.GOLDEN_GATE,
        enzyme="BsaI",
        vector_id="pET-28a",
    )
    result = run_design("design_test003", req)
    assert result.status == DesignStatus.COMPLETED
    assert len(result.primers) == 2

def _rc(s: str) -> str:
    return s.translate(str.maketrans("ATGC", "TACG"))[::-1]


def test_design_restriction_primers_double_digest():
    """双酶切引物：5' 端加 enzyme_5 位点、3' 端酶位点反向互补在反向引物 5' 端"""
    from core.primer_designer import PrimerDesigner

    designer = PrimerDesigner()
    seq = ("ATGAAAGGTTTTGGTAAACCGTTTCCCGGGAAATTTCCCGGTAAGGTTCCAAAGGGTTT"
           "AAACCCGGGATTTAAAGGGCCCTTTAAAGGGCCCAAATTTGGGCCCCTAG")
    pair = designer.design_restriction_primers(seq, "BamHI", "EcoRI", "t")

    spacer = PrimerDesigner.PROTECTIVE_SPACER
    # 5' 端保护碱基 + BamHI 位点（末端切割效率低，位点前必须留碱基）
    assert pair.forward.sequence.startswith(spacer + "GGATCC")
    assert pair.forward.sequence.endswith(seq[:20])            # 后接插入片段 5' 退火区
    assert pair.reverse.sequence.startswith(spacer + "GAATTC")  # EcoRI 回文，rc 即本身
    assert pair.reverse.sequence.endswith(_rc(seq[-20:]))      # 后接插入片段 3' 端反向互补
    assert pair.product_size == len(seq)


def test_design_primers_gene_synthesis_with_cloning_pair():
    """来源=全基因合成：产出组装 oligo 组 + 带双酶切末端的组装后扩增引物"""
    from app.routes.models import CloningMethod, DesignRequest, SequenceType
    from app.design_service import design_primers_for_method

    dna = ("ATGAAAGGTTTTGGTAAACCGTTTCCCGGGAAATTTCCCGGTAAGGTTCCAAAGGGTTT"
           "AAACCCGGGATTTAAAGGGCCCTTTAAAGGGCCCAAATTTGGGCCCCTAG")
    req = DesignRequest(
        sequence=dna,
        sequence_type=SequenceType.DNA,
        sequence_name="syn",
        cloning_method=CloningMethod.RESTRICTION,
        insert_source="gene_synthesis",
        enzyme_5="BamHI",
        enzyme_3="XhoI",
    )
    primers = design_primers_for_method(req, dna, vector=None, backbone="")

    names = [p.name for p in primers]
    # 87bp、默认长度范围（60/60）→ 2 条错位交替 oligo + 1 对克隆引物
    assert names[:2] == ["syn_S01", "syn_AS02"]
    assert names[-2:] == ["syn_F", "syn_R"]                  # 末端为克隆引物对
    from core.primer_designer import PrimerDesigner as _PD
    sp = _PD.PROTECTIVE_SPACER
    assert primers[-2].full_sequence.startswith(sp + "GGATCC")  # 保护碱基 + BamHI
    assert primers[-1].full_sequence.startswith(sp + "CTCGAG")  # 保护碱基 + XhoI（回文）
    assert len(primers) % 2 == 0                             # 寡核苷酸总数为偶数


def test_legacy_gene_synthesis_method_normalized():
    """旧契约兼容：cloning_method=gene_synthesis 归一为合成来源 + 限制性克隆"""
    from app.routes.models import CloningMethod, DesignRequest

    req = DesignRequest(
        sequence="MKVLWAALLVTFLAGCDDAKRVRELTY",
        cloning_method=CloningMethod.GENE_SYNTHESIS,
        enzyme_5="EcoRI",
        enzyme_3="HindIII",
    )
    assert req.insert_source == "gene_synthesis"
    assert req.cloning_method == CloningMethod.RESTRICTION


def test_exclude_enzymes_avoided_in_optimization():
    """排除限制酶位点：氨基酸 + 密码子优化时，位点经同义替换消失且长度不变"""
    from app.routes.models import SequenceType
    from app.design_service import process_sequence

    # 该蛋白的优化基线含 PstI 位点 CTGCAG（随机种子固定，见测试历史）
    aa = "MFPCDVENWCTHCDQQDIDVQCWEIWCWWPCICVFLQFVEWLVGEWWHNEVDWCYHSVQM"
    dna_raw, _, _, _ = process_sequence(aa, SequenceType.AMINO_ACID, True, "ecoli", 40, 60)
    assert "CTGCAG" in dna_raw, "基线应含 PstI 位点（测试靶点校验）"

    dna_excl, _, _, warns = process_sequence(
        aa, SequenceType.AMINO_ACID, True, "ecoli", 40, 60, exclude_enzymes=["PstI"]
    )
    assert len(dna_excl) == len(aa) * 3          # 沉默突变不改变长度
    assert "CTGCAG" not in dna_excl, "优化序列不应含 PstI 位点"
    assert dna_raw != dna_excl                   # 发生了同义替换
    assert not any("无法完全排除" in w for w in warns)


def test_exclude_enzymes_ignored_for_dna_input():
    """DNA 输入根本不适用排除：原样返回，不校验酶名、也不产生位点告警"""
    from app.routes.models import SequenceType
    from app.design_service import process_sequence

    dna = "ATG" + "GAATTC" + "TAA"
    for optimize in (False, True):
        out, cai, gc, warns = process_sequence(
            dna, SequenceType.DNA, optimize, "ecoli", 40, 60,
            exclude_enzymes=["EcoRI", "NotAnEnzyme"],
        )
        assert out == dna
        assert cai is None and gc is None
        # 不应出现「仍包含需排除的位点」或「忽略未知限制酶」这类看似生效的提示
        assert not any("排除" in w or "限制酶" in w for w in warns), warns


def test_exclude_enzymes_ignored_without_codon_optimization():
    """未启用密码子优化时只是反翻译：排除参数不参与、不产生避让告警"""
    from app.routes.models import SequenceType
    from app.design_service import process_sequence

    aa = "MDP"
    dna_raw, _, _, _ = process_sequence(aa, SequenceType.AMINO_ACID, False, "ecoli", 40, 60)
    dna_excl, _, _, warns = process_sequence(
        aa, SequenceType.AMINO_ACID, False, "ecoli", 40, 60, exclude_enzymes=["BamHI"]
    )
    assert dna_excl == dna_raw
    assert any("未启用密码子优化" in w for w in warns)
    assert not any("避让" in w or "排除" in w for w in warns), warns


def test_exclude_enzymes_do_not_touch_vector_or_assembly():
    """排除位点只作用于插入片段的密码子优化，不影响载体/组装结果

    DNA 输入 + 全基因合成 + 双酶切：即便请求里带了排除酶（含克隆酶本身），
    optimized_sequence / construct_sequence / 载体注释都必须与不带排除时完全一致。
    """
    from app.routes.models import CloningMethod, DesignRequest, SequenceType
    from app.design_service import run_design

    insert = "ATG" + "GAATTC" + "GGATCC" + "GCT" * 80 + "TAA"
    kwargs = dict(
        sequence=insert,
        sequence_type=SequenceType.DNA,
        sequence_name="syn",
        vector_id="pET-28a",
        cloning_method=CloningMethod.RESTRICTION,
        insert_source="gene_synthesis",
        enzyme_5="BamHI",
        enzyme_3="XhoI",
    )
    base = run_design("design_excl_base", DesignRequest(**kwargs), store_result=False)
    excl = run_design(
        "design_excl_off",
        DesignRequest(**kwargs, exclude_enzymes=["BamHI", "XhoI", "EcoRI"]),
        store_result=False,
    )
    assert base.status == excl.status
    assert excl.optimized_sequence == insert
    assert excl.construct_sequence == base.construct_sequence
    assert excl.construct_features == base.construct_features
    assert excl.vector_name == base.vector_name
    assert not any("排除" in w or "限制酶" in w for w in excl.warnings), excl.warnings


def test_unknown_enzyme_warning_only_when_exclusion_applies():
    """终审 D-04 回归锁：优化路径的未知限制酶告警必须透出

    排除参数只对「氨基酸 + 密码子优化」生效；未优化路径现在直接忽略该参数，
    不应再出现「忽略未知限制酶」这种让用户以为排除已处理的提示。
    """
    from app.routes.models import SequenceType
    from app.design_service import process_sequence

    aa = "MKVLWAALLTFLGCAATSGSQAPDRRNRLALASLLRLQGVSSVQIRCRDSDMNADADATIRR"
    _, _, _, warns = process_sequence(
        aa, SequenceType.AMINO_ACID, True, "ecoli", 40, 60,
        exclude_enzymes=["NotAnEnzyme"],
    )
    assert any("忽略未知限制酶" in w for w in warns), warns

    _, _, _, warns_no_opt = process_sequence(
        aa, SequenceType.AMINO_ACID, False, "ecoli", 40, 60,
        exclude_enzymes=["NotAnEnzyme"],
    )
    assert not any("忽略未知限制酶" in w for w in warns_no_opt), warns_no_opt


def test_unknown_enzyme_warning_survives_cache_roundtrip():
    """终审 D-04 回归锁：局部告警必须进缓存写回并在命中路径返回
    （键含 exclude_enzymes，告警与该输入绑定）"""
    from app.routes.models import SequenceType
    from app.design_service import process_sequence

    aa = "MKVL"
    for _ in range(2):  # 第一次写缓存，第二次命中
        _, _, _, warns = process_sequence(
            aa, SequenceType.AMINO_ACID, True, "ecoli", 40, 60,
            exclude_enzymes=["NotAnEnzyme"],
        )
        assert any("忽略未知限制酶" in w for w in warns), warns


def test_residual_site_warning_not_dropped(monkeypatch):
    """终审 D-04 回归锁：经优化仍无法排除的位点告警曾被丢弃——
    固定优化器输出含 HindIII 位点，process_sequence 必须产出残留告警"""
    from app.routes.models import SequenceType
    from app import design_service
    from core.codon_optimizer import CodonOptimizer

    class FakeResult:
        dna_sequence = "ATG" + "AAGCTT" + "GGC"  # 含 HindIII (AAGCTT)
        cai = 0.8
        gc_content = 0.5
        warnings = []

    monkeypatch.setattr(CodonOptimizer, "optimize", lambda self, seq, **kw: FakeResult())
    _, _, _, warns = design_service.process_sequence(
        "MKL", SequenceType.AMINO_ACID, True, "ecoli", 40, 60,
        exclude_enzymes=["HindIII"],
    )
    assert any("无法完全排除" in w and "AAGCTT" in w for w in warns), warns


def test_gibson_site_anchor_resolves_cut_position():
    """Gibson 定位位点：解析为该酶切位点的切割位置（0-based）"""
    from app.routes.models import CloningMethod, DesignRequest
    from app.design_service import resolve_gibson_insert_pos, get_vector_library

    vector = get_vector_library().get_vector("pET-28a")
    assert vector and vector.mcs and vector.mcs.sites, "pET-28a 应含 MCS 位点"

    target = next(s for s in vector.mcs.sites if s.cut_position_5)
    req = DesignRequest(
        sequence="MKVLWAALLVTFLAGCDDAKRVRELTY",
        cloning_method=CloningMethod.GIBSON,
        gibson_site=target.enzyme_name,
    )
    assert resolve_gibson_insert_pos(req, vector) == target.cut_position_5 - 1

    req2 = DesignRequest(sequence="MKVLWAALLVTFLAGCDDAKRVRELTY",
                         cloning_method=CloningMethod.GIBSON)
    # 未指定位点时回落到 MCS 起点
    assert resolve_gibson_insert_pos(req2, vector) == vector.mcs.start - 1


def test_digest_simulation_fragments():
    """/analysis/digest：EcoRI+HindIII 双酶切应产生 3 个片段"""
    from fastapi.testclient import TestClient
    from app.analysis_routes import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # ATG + GAATTC(EcoRI) + 中段 12bp + AAGCTT(HindIII) + TAA
    seq = "ATG" + "GAATTC" + "ACGTACGTACGT" + "AAGCTT" + "TAA"
    resp = client.post("/api/analysis/digest", json={"sequence": seq, "enzymes": ["EcoRI", "HindIII"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_fragments"] == 3
    sizes = [f["size"] for f in data["fragments"]]
    # EcoRI 切在 3+6=9（切割偏移 1，简化为识别位末端附近），片段按切割点划分
    assert sizes[0] < sizes[1] or sizes[0] < sizes[2]
    assert sum(sizes) == len(seq)  # 片段拼接应还原全长
    assert set(data["enzymes_with_sites"]) == {"EcoRI", "HindIII"}


def test_digest_no_sites_message():
    """所选酶无切割位点时返回提示"""
    from fastapi.testclient import TestClient
    from app.analysis_routes import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.post("/api/analysis/digest", json={"sequence": "ATGAAATTT", "enzymes": ["EcoRI"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_fragments"] == 0
    assert "无切割位点" in data["message"]


def test_digest_unknown_enzyme_rejected():
    """未知酶返回 400"""
    from fastapi.testclient import TestClient
    from app.analysis_routes import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    resp = client.post("/api/analysis/digest", json={"sequence": "ATG", "enzymes": ["NotAnEnzyme"]})
    assert resp.status_code == 400


def test_gg_overhang_fallback_flagged_in_warnings():
    """回归：Golden Gate overhang 无法从 MCS 推导时应回退默认值并给出警告"""
    req = DesignRequest(
        sequence="ATGAAAGTGCTG",
        sequence_type=SequenceType.DNA,
        cloning_method=CloningMethod.GOLDEN_GATE,
        enzyme="BsaI",
        vector_id="pET-28a",
    )
    result = run_design("design_test_gg_fb", req)
    assert result.status == DesignStatus.COMPLETED
    oh5, oh3, derived = derive_gg_overhangs(get_vector_library().get_vector("pET-28a"), "BsaI")
    if not derived:
        assert any("overhang" in w and "回退" in w for w in result.warnings)


def test_gg_overhang_screen_prefers_valid_pair_from_vector():
    """载体能给出合规组合时优先选它（而不是固定取首尾两个位点）"""
    from types import SimpleNamespace

    from core.primer_designer import screen_gg_overhang_pair

    def site(overhang):
        return SimpleNamespace(overhang=overhang, is_unique=True)

    # 首尾组合 GATC/TCGA 全是回文，中间的 AATG/GCTT 合规
    vector = SimpleNamespace(mcs=SimpleNamespace(
        sites=[site("GATC"), site("AATG"), site("GCTT"), site("TCGA")]
    ))
    oh5, oh3, derived = derive_gg_overhangs(vector, "BsaI")
    assert derived is True
    assert screen_gg_overhang_pair(oh5, oh3) == []
    assert (oh5, oh3) == ("AATG", "GCTT")


def test_gg_overhang_screen_degrades_to_warning_when_nothing_valid():
    """一对合规组合都挑不出来时保持原行为，只在 warnings 里说明"""
    from types import SimpleNamespace

    from core.primer_designer import screen_gg_overhang_pair

    def site(overhang):
        return SimpleNamespace(overhang=overhang, is_unique=True)

    vector = SimpleNamespace(mcs=SimpleNamespace(
        sites=[site("GATC"), site("TCGA"), site("AATT")]
    ))
    oh5, oh3, derived = derive_gg_overhangs(vector, "BsaI")
    assert (oh5, oh3, derived) == ("GATC", "AATT", True)  # 原行为：首尾两个位点
    assert screen_gg_overhang_pair(oh5, oh3)  # 但确实不合规


def test_gg_overhang_screen_warning_surfaced_in_design_result():
    """设计结果里能看到 overhang 筛查告警（pUC19 的 MCS overhang 全是回文）"""
    req = DesignRequest(
        sequence="ATGAAAGTGCTGGGTAAACCGTTTTAA",
        sequence_type=SequenceType.DNA,
        cloning_method=CloningMethod.GOLDEN_GATE,
        enzyme="BsaI",
        vector_id="pUC19",
    )
    result = run_design("design_test_gg_screen", req)
    assert result.status == DesignStatus.COMPLETED
    assert any("连接保真度筛查" in w for w in result.warnings), result.warnings


def test_gibson_placeholder_backbone_warns():
    """回归：载体骨架含 N 占位时，Gibson 同源臂不可信，应在 warnings 中明确告警"""
    from app.design_service import run_design as _rd
    req = DesignRequest(
        sequence="ATGAAAGTGCTGTAA",
        sequence_type=SequenceType.DNA,
        cloning_method=CloningMethod.GIBSON,
        vector_id="pET-28a",
    )
    result = _rd("design_test_gibson_n", req)
    assert result.status == DesignStatus.COMPLETED
    vector = get_vector_library().get_vector("pET-28a")
    backbone = ensure_vector_backbone(vector)
    if "N" in backbone:
        assert any("N 占位" in w for w in result.warnings)


def test_map_data_scans_construct_as_circular():
    """构建体为环状质粒：跨原点的 EcoRI 位点要出现在图谱数据里"""
    from datetime import datetime
    from app.routes.models import DesignResult

    construct = "TTC" + "A" * 50 + "GAA"
    res = DesignResult(
        design_id="circ", status=DesignStatus.COMPLETED, input_sequence="ATG",
        construct_sequence=construct, vector_id="v", cloning_method=CloningMethod.GIBSON,
        created_at=datetime.now(),
    )
    from app.map_documents import design_map_document
    ecori = [e for e in design_map_document(res)["enzymes"] if e["name"] == "EcoRI"]
    assert ecori and ecori[0]["cut_count"] == 1
    site = ecori[0]["sites"][0]
    assert site["position"] == len(construct) - 2
    # G^AATTC：顶链切在 G（第 L-2 位）之后，底链切点跨过原点落在第 2 位之后
    # （4 nt 突出端 AATT = L-1, L, 1, 2）
    assert (site["cut_top"], site["cut_bottom"]) == (len(construct) - 2, 2)

def test_restriction_request_requires_enzyme():
    """回归：enzyme 曾默认 BsaI，经典双酶切请求被静默套上 Type IIS 酶。
    现在缺酶必须校验失败（API 层即 422）"""
    import pytest
    from app.routes.models import CloningMethod, DesignRequest, SequenceType

    with pytest.raises(ValueError, match="限制酶"):
        DesignRequest(
            sequence="ATG" + "AAACAG" * 10 + "TAA",
            sequence_type=SequenceType.DNA,
            cloning_method=CloningMethod.RESTRICTION,
        )
    with pytest.raises(ValueError, match="Type IIS 酶"):
        DesignRequest(
            sequence="ATG" + "AAACAG" * 10 + "TAA",
            sequence_type=SequenceType.DNA,
            cloning_method=CloningMethod.GOLDEN_GATE,
        )
    # 显式给酶则通过
    req = DesignRequest(
        sequence="ATG" + "AAACAG" * 10 + "TAA",
        sequence_type=SequenceType.DNA,
        cloning_method=CloningMethod.RESTRICTION,
        enzyme_5="EcoRI", enzyme_3="XhoI",
    )
    assert req.enzyme is None


def test_restriction_primers_reject_type_iis():
    """回归：Type IIS 酶在识别序列外切割，产物末端不含位点，
    按经典双酶切流程做会得到错误构建——必须明确报错而不是照做"""
    import pytest
    from core.primer_designer import PrimerDesigner

    seq = "ATG" + "AAACAGCTTGGT" * 5 + "TAA"
    for enz in ("BsaI", "BsmBI", "BbsI"):
        with pytest.raises(ValueError, match="Type IIS"):
            PrimerDesigner().design_restriction_primers(seq, enz, "EcoRI", "t")
        with pytest.raises(ValueError, match="Type IIS"):
            PrimerDesigner().design_restriction_primers(seq, "EcoRI", enz, "t")


def test_restriction_primers_warn_on_internal_site():
    """插入片段内部含所选酶位点时必须告警：酶切会切断插入片段"""
    from core.primer_designer import PrimerDesigner

    seq = "ATGAAACAGCTT" + "GAATTC" + "GGTAAACAGCTTGGTTAA"
    pair = PrimerDesigner().design_restriction_primers(seq, "EcoRI", "XhoI", "t")
    assert "警告" in pair.forward.notes and "EcoRI" in pair.forward.notes
    assert "警告" in pair.reverse.notes

    clean = "ATG" + "AAACAGCTTGGT" * 5 + "TAA"
    ok = PrimerDesigner().design_restriction_primers(clean, "EcoRI", "XhoI", "t")
    assert "警告" not in ok.forward.notes


def test_run_design_warns_internal_restriction_site():
    req = DesignRequest(
        sequence="ATGAAACAGCTTGAATTCGGTAAACAGCTTGGTAAACAGCTTGGTTAA",
        sequence_type=SequenceType.DNA,
        optimize_codons=False,
        cloning_method=CloningMethod.RESTRICTION,
        enzyme_5="EcoRI", enzyme_3="XhoI",
        vector_id="pET-28a",
    )
    result = run_design("design_internal_site", req)
    assert result.status == DesignStatus.COMPLETED
    assert any("EcoRI" in w and "内部" in w for w in result.warnings)
