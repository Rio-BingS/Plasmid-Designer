"""Sanger 测序分析管线测试 — 合成 ab1 全链路"""

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from abif_utils import make_ab1  # noqa: E402
from core.sanger.abif_reader import extract_read, parse_abif, AbiParseError  # noqa: E402
from core.sanger.aligner import align_read, revcomp, merge_coverage  # noqa: E402
from core.sanger.pipeline import analyze, _trim_by_quality  # noqa: E402


@pytest.fixture(scope="module")
def reference() -> str:
    random.seed(42)
    return "".join(random.choice("ACGT") for _ in range(2000))


def test_abif_parser_matches_biopython():
    blob = make_ab1("ACGT" * 25, [40] * 100)
    bio = extract_read(blob)
    internal = extract_read.__globals__["_extract_read_internal"](blob)
    assert bio["bases"] == internal["bases"] == "ACGT" * 25
    assert bio["quality"] == internal["quality"] == [40] * 100
    assert set(bio["trace"].keys()) == {"A", "T", "G", "C"}


def test_abif_rejects_invalid():
    with pytest.raises((AbiParseError, ValueError, Exception)):
        extract_read(b"not an abif file at all........")


def test_align_read_exact_forward(reference):
    r = align_read(reference[500:1100], reference)
    assert r["direction"] == "+"
    assert r["ref_start"] == 501 and r["ref_end"] == 1100
    assert r["identity"] == 1.0
    assert r["variants"] == []


def test_align_read_reverse_strand(reference):
    r = align_read(revcomp(reference[700:1300]), reference)
    assert r["direction"] == "-"
    assert r["ref_start"] == 701 and r["ref_end"] == 1300
    assert r["identity"] == 1.0


def test_align_read_substitution_and_indel(reference):
    mutated = list(reference[500:1000])
    mutated[100] = "A" if mutated[100] != "A" else "G"
    mutated[250:250] = ["G", "G", "G"]  # 中段 3bp 插入（局部比对末端 clipping 不计）
    mutated = "".join(mutated)
    r = align_read(mutated, reference)
    subs = [v for v in r["variants"] if v["type"] == "substitution"]
    ins = [v for v in r["variants"] if v["type"] == "insertion"]
    assert len(subs) == 1 and subs[0]["ref_pos"] == 601
    assert len(ins) == 1 and ins[0]["length"] == 3


def test_trim_by_quality():
    bases = "ACGT" * 20
    quality = [5] * 10 + [40] * 60 + [3] * 10
    s, e = _trim_by_quality(bases, quality, 20)
    assert s == 10 and e == 70


def test_merge_coverage():
    merged = merge_coverage([(1, 100), (90, 200), (300, 350)], 1000)
    assert merged == [(1, 200), (300, 350)]


def test_analyze_perfect_read(reference):
    blob = make_ab1(reference[100:600], [40] * 500)
    result = analyze([("ok.ab1", blob)], reference, [])
    assert result["reads"], "应成功解析至少一条 read"
    assert result["variants"] == []
    assert "一致" in result["conclusion"]
    assert result["consensus"]["sequence"] == reference.upper()
    assert result["consensus"]["coverage_percent"] == pytest.approx(25.0, abs=0.5)


def test_analyze_detects_mutation_with_annotation(reference):
    features = [{"name": "GFP", "type": "CDS", "start": 501, "end": 1100, "strand": "+"}]
    seg = list(reference[500:1000])
    seg[50] = "A" if seg[50] != "A" else "G"  # ref_pos 551, CDS 内
    seg = "".join(seg) + "NNNN"  # 低质量尾部应被修剪
    blob = make_ab1(seg, [40] * 500 + [5] * 4)
    result = analyze([("mut.ab1", blob)], reference, features)
    assert len(result["variants"]) == 1
    v = result["variants"][0]
    assert v["ref_pos"] == 551 and v["type"] == "substitution"
    assert v["features"][0]["name"] == "GFP"
    assert v["aa_change"]  # 氨基酸变化已注释
    # 修剪生效：NNNN 不产生假突变
    assert all(v2["ref_pos"] < 1000 for v2 in result["variants"])


def test_analyze_multi_read_consensus(reference):
    """两条不同区段的 read 拼接后覆盖范围合并"""
    b1 = make_ab1(reference[200:700], [40] * 500)
    b2 = make_ab1(revcomp(reference[600:1100]), [40] * 500)
    result = analyze([("r1.ab1", b1), ("r2.ab1", b2)], reference, [])
    assert result["consensus"]["coverage_percent"] == pytest.approx(45.0, abs=1.0)
    assert result["reads"][0]["alignment"]["direction"] == "+"
    assert result["reads"][1]["alignment"]["direction"] == "-"


def test_analyze_reports_errors():
    result = analyze([("bad.ab1", b"junk")], "ACGT" * 100, [])
    assert result["reads"] == []
    assert result["errors"][0]["filename"] == "bad.ab1"


# ==================== 逐列对齐视图（人工核对证据） ====================

def test_aligner_aligned_strings_forward(reference):
    """正向 read：逐列字符串无 gap，Q 与原始质量一一对应"""
    seg = list(reference[500:1100])
    seg[20] = "A" if seg[20] != "A" else "G"
    q = list(range(10, 10 + 600))
    r = align_read("".join(seg), reference, q)
    a = r["aligned"]
    assert a["ref_start"] == 501
    assert "-" not in a["ref_aligned"] and "-" not in a["read_aligned"]
    assert len(a["ref_aligned"]) == len(a["read_aligned"]) == len(a["q_aligned"]) == 600
    assert a["ref_aligned"] == reference[500:1100]
    mismatches = [i for i, (rb, qb) in enumerate(zip(a["ref_aligned"], a["read_aligned"])) if rb != qb]
    assert len(mismatches) == 1
    # 正向：Q 逐列对应原始质量
    assert a["q_aligned"] == q


def test_aligner_aligned_strings_reverse(reference):
    """反向 read：以参考方向展示（反向互补），Q 随碱基一起反转"""
    seg = reference[700:1300]
    q = list(range(10, 10 + len(seg)))
    r = align_read(revcomp(seg), reference, q)
    assert r["direction"] == "-"
    a = r["aligned"]
    assert a["read_aligned"] == seg  # 完美匹配：展示串即参考片段
    assert a["q_aligned"] == q[::-1]


def test_aligner_aligned_strings_deletion_gap(reference):
    """缺失列：read 侧为 gap，Q = 0"""
    seg = reference[500:1100]
    mutated = seg[:100] + seg[105:]  # 删除参考 601-605
    r = align_read(mutated, reference, [40] * len(mutated))
    dels = [v for v in r["variants"] if v["type"] == "deletion"]
    assert len(dels) == 1 and dels[0]["length"] == 5
    a = r["aligned"]
    assert a["ref_aligned"].replace("-", "") == reference[500:1100]
    gaps = [i for i, (rb, qb) in enumerate(zip(a["ref_aligned"], a["read_aligned"])) if qb == "-"]
    assert len(gaps) == 5
    assert all(a["q_aligned"][i] == 0 for i in gaps)


def test_aligner_aligned_strings_insertion_gap(reference):
    """插入列：参考侧为 gap"""
    seg = list(reference[500:1100])
    seg[250:250] = ["T", "T", "T"]
    r = align_read("".join(seg), reference, [40] * 603)
    a = r["aligned"]
    gaps = [i for i, (rb, qb) in enumerate(zip(a["ref_aligned"], a["read_aligned"])) if rb == "-"]
    assert len(gaps) == 3
    assert all(a["read_aligned"][i] == "T" for i in gaps)


def test_pipeline_alignment_view_and_consensus_diffs(reference):
    """端到端：每条 read 带对齐视图；共识差异位恰好等于真突变"""
    features = [{"name": "GFP", "type": "CDS", "start": 501, "end": 1100, "strand": "+"}]
    seg = list(reference[500:1000])
    seg[50] = "A" if seg[50] != "A" else "G"  # ref_pos 551
    b1 = make_ab1("".join(seg), [40] * 500)
    b2 = make_ab1(revcomp(reference[900:1400]), [40] * 500)
    result = analyze([("f.ab1", b1), ("r.ab1", b2)], reference, features)
    assert len(result["variants"]) == 1

    for r in result["reads"]:
        a = r["alignment"]["aligned"]
        assert len(a["ref_aligned"]) == len(a["read_aligned"]) == len(a["q_aligned"])
        assert a["ref_aligned"].replace("-", "") == reference[a["ref_start"] - 1:r["alignment"]["ref_end"]]

    diffs = result["consensus"]["diffs"]
    assert len(diffs) == 1
    d = diffs[0]
    assert d["ref_pos"] == 551
    assert d["cons_base"] == result["variants"][0]["alt_base"]
    assert result["consensus"]["sequence"][d["cons_index"]] == d["cons_base"]
