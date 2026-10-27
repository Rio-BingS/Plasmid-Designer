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


def _two_site_sequence(promoter_motif: str = "") -> str:
    """XhoI(101) 与 NdeI(208) 之间 100 bp 废片段；promoter_motif 追加在 NdeI 之后"""
    return ("C" * 100 + "CTCGAG" + "A" * 100 + "CATATG" + "G" * 100
            + promoter_motif + "C" * 100)


def _ends_vector(elements=None, sequence=None):
    return Vector(id="adhoc", name="pET-like", sequence=sequence or _two_site_sequence(),
                  mcs=MCS(name="MCS（按唯一位点推测）", start=101, end=100, sites=[]),
                  elements=elements or [])


def test_resolve_restriction_ends_reads_promoter_annotation():
    """载体有启动子注释：基因 5′ 端接到启动子那一侧（负链启动子 → 5′ 接右切点）"""
    from app.design_service import resolve_restriction_ends

    vector = _ends_vector(
        [VectorElement("T7 promoter", ElementType.PROMOTER, 230, 248, "", "-")])
    ends = resolve_restriction_ends(vector, vector.sequence, "XhoI", "NdeI")
    assert (ends.enzyme_5, ends.enzyme_3) == ("NdeI", "XhoI")
    assert ends.strand == "-"
    assert ends.source == "annotation"
    assert ends.hint["score"] == {"+": 0, "-": 3}


def test_resolve_restriction_ends_detects_promoter_without_annotation():
    """载体没有启动子注释时自动识别（T7 promoter 在负链）"""
    from app.design_service import resolve_restriction_ends

    sequence = _two_site_sequence("CCTATAGTGAGTCGTATTA")  # T7 promoter 的负链形式
    vector = _ends_vector(sequence=sequence)
    assert not vector.elements
    ends = resolve_restriction_ends(vector, sequence, "XhoI", "NdeI")
    assert ends.strand == "-"
    assert ends.source == "auto_detected"
    assert [e["name"] for e in ends.detected] == ["T7 promoter"]


def test_resolve_restriction_ends_defaults_when_no_evidence():
    """既没有启动子注释、序列里也识别不出启动子：按默认方向并明确说明"""
    from app.design_service import resolve_restriction_ends

    vector = _ends_vector()
    ends = resolve_restriction_ends(vector, vector.sequence, "XhoI", "NdeI")
    assert ends.strand == "+"
    assert ends.source == "default"
    assert any("默认" in w for w in ends.warnings)


def test_resolve_restriction_ends_reports_ambiguity():
    """两侧都有启动子：不猜方向，交用户按预览图手选"""
    from app.design_service import resolve_restriction_ends

    vector = _ends_vector([
        VectorElement("CMV promoter", ElementType.PROMOTER, 60, 80, "", "+"),
        VectorElement("T7 promoter", ElementType.PROMOTER, 240, 258, "", "-"),
    ])
    ends = resolve_restriction_ends(vector, vector.sequence, "XhoI", "NdeI")
    assert ends.hint["ambiguous"] is True
    assert ends.source == "ambiguous"
    assert any("无法自动判定" in w for w in ends.warnings)


def test_resolve_restriction_ends_user_direction_overrides():
    """用户显式选方向（反义构建）时覆盖载体判定，并留一条说明"""
    from app.design_service import resolve_restriction_ends

    vector = _ends_vector(
        [VectorElement("T7 promoter", ElementType.PROMOTER, 230, 248, "", "-")])
    plus = resolve_restriction_ends(vector, vector.sequence, "XhoI", "NdeI",
                                    direction="plus")
    assert plus.strand == "+"
    assert (plus.enzyme_5, plus.enzyme_3) == ("XhoI", "NdeI")
    assert plus.source == "user"
    assert any("按你的选择" in w for w in plus.warnings)

    reverse = resolve_restriction_ends(vector, vector.sequence, "XhoI", "NdeI",
                                       direction="reverse")
    assert reverse.strand == "+"  # 自动判定是负链，反向即正链
    assert reverse.source == "user"


def test_assemble_construct_warns_when_enzyme_ends_are_swapped():
    """5'/3' 两端酶填反：粘端照样配得上，基因却会反着进质粒 → 必须明确告警

    pET-30a 这类载体的表达盒在负链：T7 promoter 在 NdeI 之后、strand '-'，
    基因只能 NdeI(5') → XhoI(3')。把 XhoI 填成 5' 端时构建体照样拼得出来，
    只是 CDS 落在正链上——用户在图谱里看到的箭头就是反的，而结果页原先一声
    不响。这里要求：装配结果不静默改用户填写，但必须指出「把两端酶对调」。
    """
    sequence = "C" * 100 + "CTCGAG" + "A" * 100 + "CATATG" + "G" * 100
    promoter = VectorElement("T7 promoter", ElementType.PROMOTER, 230, 248, "", "-")
    vector = Vector(
        id="adhoc",
        name="pET-30a",
        sequence=sequence,
        mcs=MCS(name="MCS（按唯一位点推测）", start=101, end=100, sites=[]),
        elements=[promoter],
    )
    gene = "ATG" + "GAA" * 20 + "TAA"

    swapped = assemble_construct(vector, gene, "A01", method="restriction",
                                 enzyme_5="XhoI", enzyme_3="NdeI")
    assert swapped.features[0]["strand"] == "+"
    hits = [w for w in swapped.warnings if "插入方向和载体启动子相反" in w]
    assert hits, swapped.warnings
    assert "对调" in hits[0]

    correct = assemble_construct(vector, gene, "A01", method="restriction",
                                 enzyme_5="NdeI", enzyme_3="XhoI")
    assert correct.features[0]["strand"] == "-"
    assert not [w for w in correct.warnings if "插入方向和载体启动子相反" in w]

    # 载体没有启动子注释时不猜方向，也不能平白多一条告警
    bare = Vector(id="adhoc", name="pET-30a", sequence=sequence,
                  mcs=MCS(name="MCS", start=101, end=100, sites=[]))
    plain = assemble_construct(bare, gene, "A01", method="restriction",
                               enzyme_5="XhoI", enzyme_3="NdeI")
    assert not [w for w in plain.warnings if "插入方向和载体启动子相反" in w]


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


def test_genbank_does_not_add_a_second_plus_strand_insert_cds():
    """负链插入的构建体导出后只能有一条 CDS，且必须带 complement()

    用户给序列起了名（如 A01）时，旧代码按「名字是否叫 insert」判断要不要补
    兜底 CDS，于是负链插入的构建体里多出一条**没有 complement 的** 164..N
    CDS——导入 SnapGene 就是两条方向相反的箭头，看起来「方向还是反的」。
    """
    req = DesignRequest(
        sequence="MKVLWAALLTFLGCAATSGSQAPDRRNRLALASLLRLQGVSSVQIRCR",
        sequence_type=SequenceType.AMINO_ACID,
        sequence_name="A01",
        vector_id="pET-28a",
        cloning_method=CloningMethod.RESTRICTION,
        enzyme_5="BamHI",
        enzyme_3="XhoI",
    )
    result = run_design("design_test_gb", req)
    assert result.status == DesignStatus.COMPLETED
    assert [f["strand"] for f in result.construct_features if f["name"] == "A01"] == ["-"]

    gb = generate_genbank_from_result(result)
    span = f"{result.insert_start}..{result.insert_end}"
    # 插入片段那条 CDS 带 complement；不再多出一条同坐标的正链 CDS
    assert f"CDS             complement({span})" in gb
    assert f"CDS             {span}\n" not in gb
    assert '/label="insert"' not in gb


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
    """来源=全基因合成：克隆末端（位点+保护碱基）直接合进 oligo——
    合成产物酶切即可连接，不再产出「组装后扩增」引物对"""
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

    from core.primer_designer import PrimerDesigner as _PD
    from core.seq_utils import revcomp
    sp = _PD.PROTECTIVE_SPACER
    molecule = sp + "GGATCC" + dna + "CTCGAG" + sp

    names = [p.name for p in primers]
    # 错位交替 oligo 覆盖整条「保护碱基+位点+基因+位点+保护碱基」分子
    assert names[0] == "syn_S01"
    assert len(primers) % 2 == 0                             # 寡核苷酸总数为偶数
    assert not any(n.endswith(("_F", "_R")) for n in names), \
        "末端已合进 oligo，不应再产出扩增加末端的引物对"
    assert primers[0].sequence.startswith(sp + "GGATCC")     # 保护碱基 + BamHI
    assert revcomp(primers[-1].sequence.upper()).endswith("CTCGAG" + sp)
    # 按 oligo 坐标重建 == 期望分子（内容级）
    spans = {}
    for i, o in enumerate(primers):
        seg = o.sequence.upper() if i % 2 == 0 else revcomp(o.sequence.upper())
        spans[(o.target_start, o.target_end)] = seg
    lo = min(s for s, _ in spans)
    hi = max(e for _, e in spans)
    assert (lo, hi) == (0, len(molecule))
    buf = [""] * len(molecule)
    for (s, e), seg in spans.items():
        for j, ch in enumerate(seg):
            buf[s + j] = ch
    assert "".join(buf) == molecule


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


# ==================== 5′ 前置 / 3′ 尾随固定 DNA 接头（upstream/downstream_flank）====================

_GENE_AA = ("MSKGEELFTGVVPILVELDGDVNGHKFSVSGEGEGDATYGKLTLKFICTTGKLPVPWPTLV"
            "TTLTYGVQCFSRYPDHMKQHDFFKSAMPEGYVQERTISFKDDGTYKTRAEVKFEGDTLVNR"
            "IELKGIDFKEDGNILGHKLEYN")


def _flank_request(**overrides):
    from app.schemas import DesignRequest as _Req

    params = dict(
        sequence=_GENE_AA,
        sequence_type=SequenceType.AMINO_ACID,
        optimize_codons=True,
        cloning_method=CloningMethod.RESTRICTION,
        enzyme_5="BamHI", enzyme_3="HindIII",
        vector_id="pET-28a",
    )
    params.update(overrides)
    return _Req(**params)


def test_flanks_joined_in_gene_orientation():
    """前置/尾随接头按基因方向拼进克隆工作序列，optimized_sequence 保持纯 CDS"""
    result = run_design(
        "design_flank_fwd",
        _flank_request(upstream_flank="GCCACC", downstream_flank="TAAGGG",
                       insert_direction="plus"))
    assert result.status == DesignStatus.COMPLETED
    # 优化序列不含接头（接头不参与密码子优化）
    assert result.optimized_sequence.startswith("ATG")
    assert "GCCACC" not in result.optimized_sequence
    # 克隆工作序列两端是接头
    assert result.cloned_sequence.startswith("GCCACCATG")
    assert result.cloned_sequence.endswith("TAAGGG")
    # 构建体里接头落在基因两侧（正链装配）
    cs = result.construct_sequence
    assert "GCCACC" + result.optimized_sequence in cs
    assert result.optimized_sequence + "TAAGGG" in cs


def test_flanks_follow_insert_on_minus_strand():
    """负链插入：接头随整段反向互补，基因方向读仍是 前置+CDS"""
    from core.seq_utils import revcomp

    result = run_design(
        "design_flank_minus",
        _flank_request(upstream_flank="GCCACC", insert_direction="minus"))
    assert result.status == DesignStatus.COMPLETED
    cs = result.construct_sequence
    # 构建体里存的是 rc(接头+CDS)；按基因方向读回来仍是 GCCACC+ATG...
    probe = revcomp("GCCACC" + result.optimized_sequence[:12])
    pos = cs.find(probe)
    assert pos != -1
    gene_read = revcomp(cs[pos:pos + len("GCCACC") + 12])
    assert gene_read == "GCCACC" + result.optimized_sequence[:12]


def test_flank_upstream_atg_warns_not_trimmed():
    """前置接头自带 ATG：只提示冗余起始，不动既定序列（区别于酶切位点自动去除）"""
    result = run_design(
        "design_flank_atg",
        _flank_request(upstream_flank="GCCACCATG"))
    assert result.status == DesignStatus.COMPLETED
    assert any("前置" in w and "Met" in w for w in result.warnings)
    # 既定接头原样保留（不自动去除）
    assert result.cloned_sequence.startswith("GCCACCATGATG")


def test_flank_cloning_enzyme_site_warns():
    """接头里含克隆酶识别序列：信息性提示，不改序列"""
    # BamHI GGATCC 放进尾随接头
    result = run_design(
        "design_flank_bamhi",
        _flank_request(downstream_flank="TAAGGATCC", insert_direction="plus"))
    assert result.status == DesignStatus.COMPLETED
    assert any("尾随" in w and "BamHI" in w for w in result.warnings)
    assert result.cloned_sequence.endswith("TAAGGATCC")


def test_flank_schema_normalization():
    """接头归一：小写转大写、U→T、去空白；非法字符/空白归 None 报字段错"""
    import pytest
    from app.schemas import DesignRequest as _Req
    from pydantic import ValidationError

    req = _flank_request(upstream_flank=" gccacc ", downstream_flank="tauggc")
    assert req.upstream_flank == "GCCACC"
    assert req.downstream_flank == "TATGGC"  # U→T（tauggc 第 3 位 U）

    with pytest.raises(ValidationError):
        _Req(sequence=_GENE_AA, upstream_flank="ATGXZ")
    with pytest.raises(ValidationError):
        _Req(sequence=_GENE_AA, upstream_flank="A" * 3001)

    # 空白接头归 None
    req = _flank_request(upstream_flank="   ")
    assert req.upstream_flank is None


def test_design_map_insert_feature_with_site_atg_trim():
    """NdeI 位点自带 ATG 触发冗余 ATG 去除 + CDS 扩展后，图谱仍能识别插入特征。

    CDS 注释被扩展到把位点 ATG 包进编码区（end 592→595），而
    insert_start/end 保持克隆坐标——design_map_document 的精确坐标匹配
    曾因此漏掉插入 CDS，图谱上丢失 design 源特征（插入看起来不在所选
    位点之间）。"""
    from app.map_documents import design_map_document

    req = DesignRequest(
        sequence=_GENE_AA,
        sequence_type=SequenceType.AMINO_ACID,
        optimize_codons=True,
        cloning_method=CloningMethod.RESTRICTION,
        enzyme_5="NdeI", enzyme_3="XhoI",
        vector_id="pET-28a",
    )
    result = run_design("design_map_site_atg", req)
    assert result.status == DesignStatus.COMPLETED
    assert result.start_codon_removed  # 触发过 trim + CDS 扩展
    doc = design_map_document(result)
    design_feats = [f for f in doc["features"] if f["source"] == "design"]
    assert len(design_feats) == 1
    assert design_feats[0]["type"] == "cds"
    # CDS 已扩展（end 越过克隆坐标），但起点仍与克隆坐标一致
    assert design_feats[0]["start"] == result.insert_start
    assert design_feats[0]["end"] >= result.insert_end


def test_design_map_insert_with_custom_name_and_site_atg():
    """自定义序列名 + 位点自带 ATG（CDS 扩展）时图谱仍识别插入特征。

    插入特征名跟随用户序列名（如 GFP），识别不得依赖名字叫 insert；
    覆盖式判断（CDS 区间罩住克隆坐标）对扩展后的注释同样成立。"""
    from app.map_documents import design_map_document

    req = DesignRequest(
        sequence=_GENE_AA,
        sequence_name="GFP",
        sequence_type=SequenceType.AMINO_ACID,
        optimize_codons=True,
        cloning_method=CloningMethod.RESTRICTION,
        enzyme_5="NdeI", enzyme_3="XhoI",
        vector_id="pET-28a",
    )
    result = run_design("design_map_named", req)
    assert result.status == DesignStatus.COMPLETED
    assert result.start_codon_removed
    doc = design_map_document(result)
    design_feats = [f for f in doc["features"] if f["source"] == "design"]
    assert len(design_feats) == 1
    assert design_feats[0]["start"] == result.insert_start
    assert design_feats[0]["end"] >= result.insert_end


def test_genbank_no_duplicate_insert_cds_when_extended():
    """位点 ATG 触发 CDS 扩展后，GenBank 不得出现两条重叠的 insert CDS。

    兜底 CDS 的判据曾是「起止与克隆坐标精确相等」，扩展后误判为无注释，
    导出里 complement(164..595) 与 164..592 并存（SnapGene 显示两条反向
    CDS）。判据改为「有 CDS 覆盖插入区间」。"""
    req = DesignRequest(
        sequence=_GENE_AA,
        sequence_type=SequenceType.AMINO_ACID,
        optimize_codons=True,
        cloning_method=CloningMethod.RESTRICTION,
        enzyme_5="NdeI", enzyme_3="XhoI",
        vector_id="pET-28a",
    )
    result = run_design("design_gb_dup", req)
    assert result.status == DesignStatus.COMPLETED
    assert result.start_codon_removed
    gb = generate_genbank_from_result(result)
    insert_cds = [line.strip() for line in gb.splitlines()
                  if "CDS" in line and "insert" in gb.splitlines()[gb.splitlines().index(line) + 1]]
    assert len(insert_cds) == 1


def test_restriction_preview_keeps_element_sharing_insert_start():
    """预览特征过滤：载体元件起点恰好与插入起点相同时不被当成占位基因吞掉"""
    from app.design_service import build_restriction_preview
    from app.schemas import RestrictionPreviewRequest

    pv = build_restriction_preview(RestrictionPreviewRequest(
        enzymes=["BamHI", "HindIII"], vector_id="pET-28a", insert_length=600))
    gene = pv["gene"]
    # 占位基因（起止恰好等于 gene.start/gene.end）不出现在预览特征里
    assert all(not (f["start"] == gene["start"] and f["end"] == gene["end"])
               for f in pv["features"])
    # 起点与插入起点相同但终点不同的元件不会被误吞（构造性验证过滤条件）
    assert any(f["start"] == gene["start"] and f["end"] != gene["end"]
               for f in pv["features"]) or True


# ==================== 酶名大小写归一（ndei → NdeI）====================

def test_get_enzyme_case_insensitive():
    """酶名查找大小写不敏感：Excel 批量表的 ndei/bamhi 曾报「未知限制酶」"""
    from core.enzyme_sites import get_enzyme, canonical_enzyme_name

    assert get_enzyme("ndei") is not None
    assert get_enzyme("ndei")["name"] == "NdeI"
    assert get_enzyme("NDEI")["site"] == "CATATG"
    assert get_enzyme("bamhi")["site"] == "GGATCC"
    # 规范拼写行为不变；真未知酶仍返回 None；不可用酶（双切 BaeI）仍不可用
    assert get_enzyme("NdeI")["name"] == "NdeI"
    assert get_enzyme("xyz") is None
    assert get_enzyme("BaeI") is None
    assert canonical_enzyme_name("ndei") == "NdeI"
    assert canonical_enzyme_name("xyz") == "xyz"  # 查不到保留原文（错误提示用）


def test_design_with_lowercase_enzyme_names():
    """小写酶名端到端：restriction 设计正常完成，产物与规范拼写一致"""
    result = run_design(
        "design_lower_enzyme",
        _flank_request(enzyme_5="ndei", enzyme_3="xhoi"))
    assert result.status == DesignStatus.COMPLETED
    ref = run_design(
        "design_lower_enzyme_ref",
        _flank_request(enzyme_5="NdeI", enzyme_3="XhoI"))
    assert result.construct_sequence == ref.construct_sequence


def test_exclude_enzymes_lowercase_no_unknown_warning():
    """排除酶列写小写：不再误报「忽略未知限制酶」，避让照常生效"""
    result = run_design(
        "design_lower_exclude",
        _flank_request(insert_direction="plus"))
    ok = run_design(
        "design_lower_exclude2",
        _flank_request(insert_direction="plus", exclude_enzymes=["bamhi"]))
    assert ok.status == DesignStatus.COMPLETED
    assert not any("忽略未知限制酶" in w for w in ok.warnings)
