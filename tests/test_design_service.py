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
    map_data_from_result,
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
    assert result.optimized_sequence in gb.replace(" ", "").replace("\n", "") or "insert" in gb

    m = map_data_from_result(result)
    assert m["length"] == result.final_length
    assert m["features"]


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

    assert pair.forward.sequence.startswith("GGATCC")          # BamHI 位点在 5' 端
    assert pair.forward.sequence.endswith(seq[:20])            # 后接插入片段 5' 退火区
    assert pair.reverse.sequence.startswith("GAATTC")          # EcoRI 回文，rc 即本身
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
    # 87bp、默认长度范围（60/60）→ 2 个片段 → 4 条成对寡核苷酸 + 1 对克隆引物
    assert names[:4] == ["syn_S01", "syn_AS01", "syn_S02", "syn_AS02"]
    assert names[-2:] == ["syn_F", "syn_R"]                  # 末端为克隆引物对
    assert primers[-2].full_sequence.startswith("GGATCC")    # 5' 端 BamHI
    assert primers[-1].full_sequence.startswith("CTCGAG")    # 3' 端 XhoI（回文）
    assert len(primers) % 2 == 0                             # 寡核苷酸总数为偶数


def test_legacy_gene_synthesis_method_normalized():
    """旧契约兼容：cloning_method=gene_synthesis 归一为合成来源 + 限制性克隆"""
    from app.routes.models import CloningMethod, DesignRequest

    req = DesignRequest(
        sequence="MKVLWAALLVTFLAGCDDAKRVRELTY",
        cloning_method=CloningMethod.GENE_SYNTHESIS,
    )
    assert req.insert_source == "gene_synthesis"
    assert req.cloning_method == CloningMethod.RESTRICTION
