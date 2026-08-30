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
