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
