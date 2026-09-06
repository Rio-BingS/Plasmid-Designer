"""Sanger 测序分析管线测试 — 合成 ab1 全链路"""

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from abif_utils import make_ab1  # noqa: E402
from core.sanger.abif_reader import extract_read, parse_abif, AbiParseError  # noqa: E402
from core.sanger.annotator import annotate_variant, summarize_severity  # noqa: E402
from core.sanger.aligner import align_read, revcomp, merge_coverage  # noqa: E402
from core.sanger.pipeline import (  # noqa: E402
    analyze, _trim_by_quality, _build_consensus, _build_cds_reports,
    _read_grade, _variant_confidence, _coverage_gaps,
)


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


def test_analyze_over_aggressive_trim_reports_error():
    """阈值高于全部碱基 Q 值：修剪后为空，报错误条目而非比对崩溃"""
    blob = make_ab1("ACGT" * 50, [40] * 200)
    result = analyze([("q.ab1", blob)], "ACGT" * 100, [], min_q=50)
    assert result["reads"] == []
    assert result["errors"] and "修剪后仅 0bp" in result["errors"][0]["error"]


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


# ==================== 酶切位点注释（indel 位移配对） ====================

def _base_variant(**kw):
    v = {"ref_pos": 1, "read_pos": 1, "type": "substitution",
         "ref_base": "A", "alt_base": "T", "length": 1}
    v.update(kw)
    return v


def test_deletion_shifts_downstream_site_not_reported():
    """缺失使下游 BsaHI(GAYG) 位点整体平移：不应误报为 破坏+新增"""
    ref = "A" * 15 + "GACG" + "C" * 15   # BsaHI 位于 16-19
    v = _base_variant(ref_pos=13, type="deletion", length=3, ref_base="AAA", alt_base="-")
    annotate_variant(v, [], ref)
    assert v["enzyme_sites_lost"] == []
    assert v["enzyme_sites_gained"] == []


def test_deletion_spanning_site_reported_lost():
    """删除覆盖识别序列本体：正确报破坏，且不误报新增"""
    ref = "A" * 15 + "GACG" + "C" * 15
    v = _base_variant(ref_pos=17, type="deletion", length=3, ref_base="ACG", alt_base="-")
    annotate_variant(v, [], ref)
    assert v["enzyme_sites_lost"] == ["BsaHI"]
    assert v["enzyme_sites_gained"] == []


# ==================== 共识插入保留右侧碱基 ====================

def test_consensus_insertion_keeps_following_reference_base():
    """插入位点的共识序列必须保留紧跟其后的参考碱基（长度 = 参考长度 + 插入长度）"""
    ref = "ACGTACGTAC" * 5
    read = {
        "mean_q": 40,
        "alignment": {
            "ref_start": 5, "ref_end": 20,
            "variants": [{"ref_pos": 7, "type": "insertion", "ref_base": "-", "alt_base": "GGG", "length": 3}],
        },
    }
    c = _build_consensus(ref, [read])
    assert len(c["sequence"]) == len(ref) + 3
    d = c["diffs"][0]
    assert d["cons_base"] == "GGG"
    assert c["sequence"][d["cons_index"]:d["cons_index"] + 3] == "GGG"
    assert c["sequence"][d["cons_index"] + 3] == ref[7]  # 参考位置 8 的碱基仍在


# ==================== CDS 级别测序结论 ====================

def _cds_reference():
    """40bp 前导 + 150bp CDS（ATG + 48 个无终止密码子 + TAA）+ 40bp 尾部"""
    cds = "ATG" + "GCTTTCGGATAC" * 12 + "TAA"
    return "ACGT" * 10 + cds + "ACGT" * 10, 41, 190


def test_cds_report_identical(reference):
    ref, start, end = _cds_reference()
    feats = [{"name": "MX", "type": "CDS", "start": start, "end": end, "strand": "+"}]
    result = analyze([("f.ab1", make_ab1(ref, [40] * len(ref)))], ref, feats)
    cr = result["cds_reports"][0]
    assert cr["coverage_status"] == "full"
    assert cr["protein_identical"] is True
    assert cr["ref_protein_length"] == 49  # ATG..TAA 去掉终止后 49 aa
    assert cr["frameshift_count"] == 0
    assert "翻译产物与参考一致" in cr["verdict"]


def test_cds_report_frameshift(reference):
    ref, start, end = _cds_reference()
    feats = [{"name": "MX", "type": "CDS", "start": start, "end": end, "strand": "+"}]
    seg = ref[40:190]
    mutated = seg[:29] + seg[30:]  # 删 CDS 内 1bp（ref pos 70）→ 移码
    result = analyze([("f.ab1", make_ab1(mutated, [40] * len(mutated)))], ref, feats)
    cr = result["cds_reports"][0]
    assert cr["protein_identical"] is False
    assert cr["frameshift_count"] == 1
    # HGVS 风格：一致前缀 + 第一个受影响氨基酸的 fs 描述
    assert "前 10 aa 与参考一致" in cr["verdict"]
    assert "自第 11 aa 起阅读框改变" in cr["verdict"]
    assert "fs" in cr["verdict"]
    # 移码区的“替换”属于噪声，不应列出
    assert cr["aa_changes"] == []


def test_cds_report_insertion_boundaries():
    """边界插入语义：CDS 起点前的插入不改变 CDS 自身（判一致）；CDS 内部 1bp 插入为移码"""
    ref, start, end = _cds_reference()
    feats = [{"name": "MX", "type": "CDS", "start": start, "end": end, "strand": "+"}]

    # 紧贴起点之前的插入：CDS 序列不变，翻译产物一致
    seg_before = ref[:40] + "T" + ref[40:190]
    cr1 = analyze([("f.ab1", make_ab1(seg_before, [40] * len(seg_before)))], ref, feats)["cds_reports"][0]
    assert cr1["frameshift_count"] == 0
    assert cr1["protein_identical"] is True
    assert "翻译产物与参考一致" in cr1["verdict"]

    # CDS 首碱基之后的 1bp 插入：移码，HGVS fs 描述
    seg_in = ref[:41] + "T" + ref[41:190]
    cr2 = analyze([("f.ab1", make_ab1(seg_in, [40] * len(seg_in)))], ref, feats)["cds_reports"][0]
    assert cr2["frameshift_count"] == 1
    assert cr2["protein_identical"] is False
    assert "移码" in cr2["verdict"]
    assert "fs" in cr2["verdict"]
    assert cr2["aa_changes"] == []  # 移码区不列错义清单


def test_cds_report_premature_stop(reference):
    ref, start, end = _cds_reference()
    feats = [{"name": "MX", "type": "CDS", "start": start, "end": end, "strand": "+"}]
    seg = list(ref)
    seg[54] = "G"  # 第 5 个编码子 TAC→TAG（ref pos 55）：无义突变
    result = analyze([("f.ab1", make_ab1("".join(seg), [40] * len(seg)))], ref, feats)
    cr = result["cds_reports"][0]
    assert cr["protein_identical"] is False
    assert cr["premature_stop_aa"] == 5
    assert "提前终止" in cr["verdict"]


def test_cds_report_partial_coverage(reference):
    ref, start, end = _cds_reference()
    feats = [{"name": "MX", "type": "CDS", "start": start, "end": end, "strand": "+"}]
    result = analyze([("f.ab1", make_ab1(ref[:120], [40] * 120))], ref, feats)
    cr = result["cds_reports"][0]
    assert cr["coverage_status"] == "partial"
    assert "未验证" in cr["verdict"]


def test_cds_report_uncovered_and_reverse_strand():
    ref, start, end = _cds_reference()
    # 未覆盖：CDS 无 read 覆盖
    feats = [{"name": "MX", "type": "CDS", "start": start, "end": end, "strand": "+"}]
    result = analyze([("f.ab1", make_ab1(ref[:40], [40] * 40))], ref, feats)
    assert result["cds_reports"][0]["coverage_status"] == "uncovered"

    # 反向链 CDS：read 覆盖且干净 → 翻译一致
    feats_rev = [{"name": "R", "type": "CDS", "start": start, "end": end, "strand": "-"}]
    result2 = analyze([("f.ab1", make_ab1(ref, [40] * len(ref)))], ref, feats_rev)
    cr = result2["cds_reports"][0]
    assert cr["protein_identical"] is True
    assert cr["verdict"].startswith("CDS 完整覆盖")


def test_cds_report_synonymous_mutation(reference):
    """CDS 内同义突变：蛋白一致但要点名 synonymous_variant 与同义计数"""
    ref, start, end = _cds_reference()
    feats = [{"name": "MX", "type": "CDS", "start": start, "end": end, "strand": "+"}]
    seg = list(ref)
    seg[45] = "C"  # GCT→GCC（codon2）：Ala→Ala 同义
    result = analyze([("f.ab1", make_ab1("".join(seg), [40] * len(seg)))], ref, feats)
    cr = result["cds_reports"][0]
    assert cr["protein_identical"] is True
    assert "synonymous_variant" in cr["consequences"]
    assert cr["synonymous_count"] == 1
    assert "同义突变" in cr["verdict"]


def test_cds_report_inframe_deletion(reference):
    """整密码子 3bp 缺失：inframe_deletion，不移码，蛋白短一个氨基酸"""
    ref, start, end = _cds_reference()
    feats = [{"name": "MX", "type": "CDS", "start": start, "end": end, "strand": "+"}]
    mutated = ref[:46] + ref[49:190]  # 删 codon3 TTC（pos 47-49）
    result = analyze([("f.ab1", make_ab1(mutated, [40] * len(mutated)))], ref, feats)
    cr = result["cds_reports"][0]
    assert "inframe_deletion" in cr["consequences"]
    assert cr["frameshift_count"] == 0
    assert cr["protein_identical"] is False
    assert cr["alt_protein_length"] == cr["ref_protein_length"] - 1
    assert "框内插入/缺失 3 bp" in cr["verdict"]


def test_cds_report_start_lost(reference):
    """起始密码子 ATG→ACG：start_lost"""
    ref, start, end = _cds_reference()
    feats = [{"name": "MX", "type": "CDS", "start": start, "end": end, "strand": "+"}]
    seg = list(ref)
    seg[41] = "C"  # ATG→ACG（codon1）
    result = analyze([("f.ab1", make_ab1("".join(seg), [40] * len(seg)))], ref, feats)
    cr = result["cds_reports"][0]
    assert "start_lost" in cr["consequences"]
    assert "起始密码子改变" in cr["verdict"]


def test_cds_report_stop_lost(reference):
    """终止密码子 TAA→CAA：stop_lost，翻译读穿"""
    ref, start, end = _cds_reference()
    feats = [{"name": "MX", "type": "CDS", "start": start, "end": end, "strand": "+"}]
    seg = list(ref)
    seg[187] = "C"  # TAA→CAA（codon50，pos 188-190）
    result = analyze([("f.ab1", make_ab1("".join(seg), [40] * len(seg)))], ref, feats)
    cr = result["cds_reports"][0]
    assert "stop_lost" in cr["consequences"]
    assert "终止密码子丢失" in cr["verdict"]


# ==================== read 质量评级 / 变异置信度 / 覆盖缺口 ====================

def test_read_grade_levels():
    q40 = [40] * 500
    assert _read_grade("ACGT" * 125, q40)[0] == "A"
    seg = "ACGT" * 30 + "N" * 4           # 含 N：降为 B
    grade, _ = _read_grade(seg, [40] * len(seg))
    assert grade == "B"
    low_q = [18] * 100                     # Q20 比例 0 → C
    assert _read_grade("ACGT" * 25, low_q)[0] == "C"
    assert _read_grade("ACGT" * 5, [40] * 20)[0] == "C"  # 过短
    assert _read_grade("ACGT" * 25, [40] * 80 + [15] * 20)[0] == "B"  # Q20 比例 0.8


def test_variant_confidence_levels():
    base = {"read_pos": 10, "read_q": 42, "support_reads": 1}
    assert _variant_confidence(dict(base), set()) == "high"
    assert _variant_confidence(dict(base, read_q=30), set()) == "medium"
    assert _variant_confidence(dict(base, read_q=15), set()) == "low"
    assert _variant_confidence(dict(base, support_reads=2, read_q=30), set()) == "high"
    assert _variant_confidence(dict(base, support_reads=2, read_q=15), set()) == "medium"
    # 变异位有混合峰信号：无条件降为低
    assert _variant_confidence(dict(base), {10}) == "low"


def test_variant_confidence_indel_floor():
    """indel 假阳性率高于替换（同聚物滑移/错配区补偿）：单 read 低质量
    indel 的置信门槛为 Q30（替换为 Q25），多 read 支持可托底"""
    base = {"read_pos": 10, "read_q": 42, "support_reads": 1, "type": "deletion"}
    assert _variant_confidence(dict(base, read_q=28), set()) == "low"
    assert _variant_confidence(dict(base, read_q=30), set()) == "medium"
    ins = {"read_pos": 10, "read_q": 42, "support_reads": 1, "type": "insertion"}
    assert _variant_confidence(dict(ins, read_q=25), set()) == "low"
    assert _variant_confidence(dict(ins, read_q=15, support_reads=2), set()) == "medium"


def test_coverage_gaps_complement():
    gaps = _coverage_gaps([(150, 300), (350, 500)], 700, min_len=50)
    spans = [(g["start"], g["end"]) for g in gaps]
    # 301-349 仅 49bp < min_len 50，被过滤
    assert (1, 149) in spans and (501, 700) in spans and (301, 349) not in spans
    assert all(g["length"] >= 50 for g in gaps)
    # 不足 min_len 的缺口被过滤，且按长度降序
    assert gaps[0]["length"] >= gaps[-1]["length"]


def _real_traces(bases, main=1000, minor=30):
    """带主次峰落差的四通道信号，避免平峰被混合检测整条标记"""
    return [[main if b == ch else minor for b in bases] for ch in "ATGC"]


def test_analyze_reports_grade_confidence_and_gaps(reference):
    seg = list(reference[99:499])
    seg[50] = "A" if seg[50] != "A" else "G"
    seq = "".join(seg)
    result = analyze([("f.ab1", make_ab1(seq, [40] * 400, _real_traces(seq)))], reference, [])
    r = result["reads"][0]
    assert r["grade"] in ("A", "B", "C")
    assert 0 <= r["q20_ratio"] <= 1
    # 合成平峰 trace 会把大量位置标记为混合峰，这里只验证字段与分级合法性
    assert result["variants"][0]["confidence"] in ("high", "medium", "low")
    # 参考 2000bp 只测了 100-500：应有长缺口
    assert result["coverage_gaps"]
    assert result["coverage_gaps"][0]["length"] >= 100


# ---------- 峰级证据与置信度融合（Mutation Surveyor 要素） ----------

def test_peak_window_coordinate_semantics():
    """PLOC 峰坐标是 trace 采样点坐标：稀疏峰取半间距窗口，单点峰退化单点窗口"""
    from core.sanger.pipeline import _peak_window
    dense = list(range(50))                       # 每碱基 1 个采样点（合成数据）
    assert _peak_window(dense, 10, 50) == (10, 11)
    sparse = [4 * i for i in range(50)]           # 每碱基 4 个采样点（真实 ab1）
    lo, hi = _peak_window(sparse, 10, 200)
    assert (lo, hi) == (38, 43)                   # 半峰间距 2 点
    assert _peak_window(sparse, 0, 200)[0] == 0   # 首峰不越界


def test_variant_peak_evidence_and_confidence_fusion():
    """峰强比 mutant_pct / 信噪比 snr 计算及其对置信度分级的融合"""
    from core.sanger.pipeline import _variant_peak_evidence
    bases = "ACGTTACGTAGCAATGGTCA"   # index10='G'（1-based 位 11）
    pos = 11
    read = extract_read(make_ab1(bases, [45] * len(bases)))  # 主通道 100 / 本底 4
    # 真实替换的峰形：alt 通道为主峰、参考通道仅剩本底 → mutant_pct ≈100%
    tr = {b: list(v) for b, v in read["trace"].items()}
    tr["A"][10], tr["G"][10] = 1000, 4
    ev = _variant_peak_evidence(tr, read["peak_indices"], pos, "G", "A")
    assert ev["mutant_pct"] == pytest.approx(100 * 1000 / 1004, abs=0.5)
    assert ev["snr"] is not None and ev["snr"] >= 10
    assert _variant_confidence({"read_pos": pos, "read_q": 45, "support_reads": 1}, set(), ev) == "high"
    # 双峰：alt 与 ref 通道各占一半 → 疑似混合 → 低置信
    tr2 = {b: list(v) for b, v in read["trace"].items()}
    tr2["A"][10] = tr2["G"][10] = 500
    ev2 = _variant_peak_evidence(tr2, read["peak_indices"], pos, "G", "A")
    assert ev2["mutant_pct"] == 50.0
    assert _variant_confidence({"read_pos": pos, "read_q": 45, "support_reads": 1}, set(), ev2) == "low"
    # 突变通道远弱于参考通道（疑似误读）→ 低置信
    assert _variant_confidence(
        {"read_pos": pos, "read_q": 45, "support_reads": 1}, set(), {"mutant_pct": 10.0, "snr": 50.0}
    ) == "low"
    # 突变峰淹没在本底附近（低信噪比）→ 低置信
    assert _variant_confidence(
        {"read_pos": pos, "read_q": 45, "support_reads": 1}, set(), {"mutant_pct": 98.0, "snr": 2.0}
    ) == "low"
    # 峰证据干净但 Q 中等 → 中置信
    assert _variant_confidence(
        {"read_pos": pos, "read_q": 30, "support_reads": 1}, set(), {"mutant_pct": 98.0, "snr": 50.0}
    ) == "medium"
    # indel / 缺 trace 无峰级证据
    assert _variant_peak_evidence(read["trace"], read["peak_indices"], pos, "A", "-") is None
    assert _variant_peak_evidence(read["trace"], [], pos, "G", "A") is None


# ---------- CDS 编码区自动校正（ORF 对齐）与嵌套去重 ----------

def test_find_orf_realignment_forward():
    """特征起点多含上游碱基（MX.dna 场景）：ORF 对齐回真实编码区"""
    from core.sanger.pipeline import _find_orf
    ref, start, end = _cds_reference()  # CDS 41..190
    orf = _find_orf(ref, start - 1, end, "+")   # 特征 40..190
    assert (orf["orf_start"], orf["orf_end"]) == (start, end)
    # 特征与编码区一致时原样返回
    orf2 = _find_orf(ref, start, end, "+")
    assert (orf2["orf_start"], orf2["orf_end"]) == (start, end)


def test_find_orf_realignment_reverse():
    """负链特征边界偏差同样按 ORF 对齐（坐标换算回参考方向）"""
    from core.sanger.pipeline import _find_orf
    from core.sanger.aligner import revcomp
    cds_fwd = "ATG" + "GCTTTCGGATAC" * 12 + "TAA"
    ref_neg = "ACGT" * 10 + revcomp(cds_fwd) + "ACGT" * 10  # 负链 CDS 位于 41..190
    # 特征两端各多含 1bp：仍应对齐回真实编码区
    orf = _find_orf(ref_neg, 40, 191, "-")
    assert (orf["orf_start"], orf["orf_end"]) == (41, 190)


def test_cds_report_orf_realignment_end_to_end():
    ref, start, end = _cds_reference()
    feats = [{"name": "MX", "type": "CDS", "start": start - 1, "end": end, "strand": "+"}]
    result = analyze([("f.ab1", make_ab1(ref, [40] * len(ref)))], ref, feats)
    cr = result["cds_reports"][0]
    assert cr["start"] == start and cr["end"] == end
    assert cr["ref_protein_length"] == 49
    assert cr["protein_identical"] is True
    assert "已按编码区翻译" in cr["verdict"]


def test_cds_report_skips_nested_cds():
    """被更大 CDS 完全包含的小特征（如 6xHis 标签）不单独出结论"""
    ref, start, end = _cds_reference()
    feats = [
        {"name": "MX", "type": "CDS", "start": start, "end": end, "strand": "+"},
        {"name": "6xHis", "type": "CDS", "start": start + 3, "end": start + 20, "strand": "+"},
    ]
    consensus = {"diffs": [], "covered_ranges": [(start, end)], "coverage_percent": 100.0}
    reports = _build_cds_reports(ref, feats, [], consensus)
    assert [r["name"] for r in reports] == ["MX"]


def test_cds_report_ignores_low_confidence_variants():
    """低置信 indel（测序噪声）不推翻 CDS 结论；verdict 附待复核提示"""
    ref, start, end = _cds_reference()
    feats = [{"name": "MX", "type": "CDS", "start": start, "end": end, "strand": "+"}]
    variants = [
        {"ref_pos": 60, "type": "deletion", "ref_base": "C", "alt_base": "-",
         "length": 1, "read_q": 9, "confidence": "low"},
        {"ref_pos": 70, "type": "insertion", "ref_base": "-", "alt_base": "G",
         "length": 1, "read_q": 3, "confidence": "low"},
        {"ref_pos": 53, "type": "substitution", "ref_base": "T", "alt_base": "C",
         "length": 1, "read_q": 59, "confidence": "high"},  # TAC→CAC 错义（Tyr→His）
    ]
    consensus = {"diffs": [], "covered_ranges": [(start, end)], "coverage_percent": 100.0}
    cr = _build_cds_reports(ref, feats, variants, consensus)[0]
    assert cr["protein_identical"] is False
    assert cr["frameshift_count"] == 0
    assert cr["pending_low_confidence"] == 2
    assert "未计入判定" in cr["verdict"] and "人工核对峰图" in cr["verdict"]
    assert "Y5H" in "".join(cr["aa_changes"])  # 错义来自确证替换（第 5 aa Tyr→His）


# ---------- 低质量 indel 全链路（共识与结论的噪声过滤） ----------

def test_low_quality_indel_not_in_verdict_nor_consensus(reference):
    """单 read 低质量 indel（Q<30）：不进入 CDS 结论、不写入共识序列，
    仅保留在变体清单并标注低置信度——高质量证据才改变结论（MX.dna 实测场景）"""
    ref, start, end = _cds_reference()
    feats = [{"name": "MX", "type": "CDS", "start": start, "end": end, "strand": "+"}]
    seg = ref[40:190]
    mutated = seg[:29] + seg[30:]  # 删 CDS 内 1bp（ref pos 70）
    # 缺失点落在 69-71 的 T 同聚区内，把可能的归因位都设为低质量（Q10）
    quality = [40] * 29 + [10] * 3 + [40] * (len(mutated) - 32)
    result = analyze([("f.ab1", make_ab1(mutated, quality))], ref, feats)

    v = result["variants"][0]
    assert v["type"] == "deletion" and v["confidence"] == "low"
    # 共识序列不固化该缺失：仍是完整参考
    assert result["consensus"]["sequence"] == ref.upper()

    cr = result["cds_reports"][0]
    assert cr["frameshift_count"] == 0
    assert cr["protein_identical"] is True
    assert cr["pending_low_confidence"] == 1
    assert "另有 1 处低置信变异" in cr["verdict"]
    assert "人工核对峰图" in cr["verdict"]
    # 自动结论的差异行也带低置信度提示
    assert any("低置信度" in line for line in result["conclusion"].splitlines())


def test_consensus_skips_low_confidence_keys():
    """skip_keys 机制：指定变体（按 ref_pos/type/alt 匹配）不写入共识"""
    ref = "ACGTACGTAC" * 5
    read = {
        "mean_q": 40,
        "alignment": {
            "ref_start": 5, "ref_end": 20,
            "variants": [{"ref_pos": 7, "type": "insertion", "ref_base": "-", "alt_base": "GGG", "length": 3}],
        },
    }
    c = _build_consensus(ref, [read])
    assert len(c["sequence"]) == len(ref) + 3
    c2 = _build_consensus(ref, [read], skip_keys={(7, "insertion", "GGG")})
    assert c2["sequence"] == ref.upper()
    assert c2["diffs"] == []


# ---------- 注释显示：同义突变与低置信度标注 ----------

def test_synonymous_substitution_not_labeled_aa_change():
    """CDS 内同义替换：不再误标为“氨基酸改变”，改标 synonymous"""
    ref = "A" * 15 + "TAT" + "C" * 15   # CDS 16-18 = TAT（Tyr）
    feats = [{"name": "X", "type": "CDS", "start": 16, "end": 18, "strand": "+"}]
    v = _base_variant(ref_pos=18, type="substitution", ref_base="T", alt_base="C")
    annotate_variant(v, feats, ref)
    assert v.get("aa_change") is None
    assert v.get("synonymous") is True
    assert v["codon_change"] == "TAT>TAC"
    notes = summarize_severity([v])
    assert "同义突变" in notes[0] and "氨基酸改变" not in notes[0]


def test_summarize_marks_low_confidence():
    """低置信度变体在结论行附人工核对提示"""
    v = _base_variant(ref_pos=5, type="deletion", ref_base="A", alt_base="-", length=1)
    v["confidence"] = "low"
    v["features"] = []
    note = summarize_severity([v])[0]
    assert "低置信度" in note and "人工核对" in note
    v2 = _base_variant(ref_pos=5, type="deletion", ref_base="A", alt_base="-", length=1)
    v2["features"] = []
    assert "低置信度" not in summarize_severity([v2])[0]
