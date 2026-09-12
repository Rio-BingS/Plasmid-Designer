"""Sanger 测序分析管线测试 — 合成 ab1 全链路"""

import logging
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from abif_utils import make_ab1  # noqa: E402
from core.sanger.abif_reader import (  # noqa: E402
    AbiParseError,
    _extract_read_internal,
    extract_read,
    parse_abif,
)
from core.sanger.annotator import annotate_variant, summarize_severity  # noqa: E402
from core.sanger.aligner import align_read, revcomp, merge_coverage  # noqa: E402
from core.sanger.signal import (  # noqa: E402
    baseline_correct,
    continuous_read_length,
    estimate_run_length,
    fit_decay,
    peak_heights,
    property_maps,
)
from core.sanger.pipeline import (  # noqa: E402
    analyze, _trim_by_quality, _build_consensus, _build_cds_reports,
    _read_grade, _variant_confidence, _coverage_gaps, _variant_peak_evidence,
    _poly_peak_amplitude_ratio, _detect_mixed_positions, _peak_count_tol,
    _annotate_homopolymer, find_homopolymers, find_repeat_runs,
)


@pytest.fixture(scope="module")
def reference() -> str:
    random.seed(42)
    return "".join(random.choice("ACGT") for _ in range(2000))


def test_find_homopolymers():
    runs = find_homopolymers("AAAAACGTTTTTTACGT", 5)
    assert runs == [
        {"base": "A", "unit": "A", "period": 1, "start": 1, "end": 5, "length": 5,
         "repeat_count": 5, "lus": 5, "tier": "poly"},
        {"base": "T", "unit": "T", "period": 1, "start": 8, "end": 13, "length": 6,
         "repeat_count": 6, "lus": 6, "tier": "poly"},
    ]


def test_find_repeat_runs_periods_and_tiers():
    """B3：二/三核苷酸重复 + 8bp 观察级；TRF 式 period 冗余去重"""
    seq = "ATATATAT" + "CAGCAGCAGCAG" + "A" * 20 + "GCA"
    runs = find_repeat_runs(seq)
    by = {(r["period"], r["unit"]): r for r in runs}
    at = by[(2, "AT")]
    assert at["start"] == 1 and at["length"] == 8 and at["repeat_count"] == 4
    cag = by[(3, "CAG")]
    assert cag["start"] == 9 and cag["repeat_count"] == 4
    a = by[(1, "A")]
    assert a["length"] == 20 and a["tier"] == "poly" and a["base"] == "A"
    # 去重：A 同聚物 span 不再冗余报出 period 2 的 AA
    assert all(not (r["period"] == 2 and r["unit"] == "AA") for r in runs)
    assert len(runs) == 3

    # 观察级：8-19bp 同聚物 tier=observed（入报告、不触发结论告警）
    seq2 = "ACGT" + "A" * 10 + "T" * 20 + "GCA"
    runs2 = find_repeat_runs(seq2)
    a10 = next(r for r in runs2 if r["base"] == "A")
    assert a10["tier"] == "observed" and a10["length"] == 10
    t20 = next(r for r in runs2 if r["base"] == "T")
    assert t20["tier"] == "poly" and t20["length"] == 20


def test_annotate_repeat_unit_insertion_and_deletion():
    """B3：插入/删除整倍重复单元归入重复结构——(CAG)n 加一个 CAG 旧逻辑
    因 set(alt)=={alt[0]} 根本归不到结构上"""
    ref = "ACGT" + "CAG" * 6 + "TTTT"
    runs = find_repeat_runs(ref)
    ins = {"ref_pos": 10, "type": "insertion", "alt_base": "CAG", "length": 3}
    _annotate_homopolymer(ref, [ins], runs)
    hp = ins["homopolymer"]
    assert hp["unit"] == "CAG" and hp["period"] == 3
    assert hp["ref_repeat_count"] == 6 and hp["observed_repeat_count"] == 7
    # 归一化最左 anchor（插入点在 run 左缘之前）同样归属
    ins2 = {"ref_pos": 4, "type": "insertion", "alt_base": "CAG", "length": 3}
    _annotate_homopolymer(ref, [ins2], runs)
    assert ins2["homopolymer"]["observed_repeat_count"] == 7
    # 缺失一个单元（anchor 在 run 左缘，相位与单元一致——与 indel 左归一化一致）
    del_v = {"ref_pos": 5, "type": "deletion", "length": 3}
    _annotate_homopolymer(ref, [del_v], runs)
    assert del_v["homopolymer"]["observed_repeat_count"] == 5


def test_poly_indel_compression_caps_confidence():
    """poly 区单 read indel：峰压缩明显时不给高置信（重复数计数不可靠）"""
    v = {"ref_pos": 10, "type": "deletion", "length": 1, "read_q": 40,
         "support_reads": 1,
         "homopolymer": {"base": "A", "peak_amplitude_ratio": 0.3}}
    assert _variant_confidence(v, set(), None, read_len=800) == "medium"
    v["homopolymer"]["peak_amplitude_ratio"] = 0.9
    assert _variant_confidence(v, set(), None, read_len=800) == "high"
    v["read_q"] = 15
    assert _variant_confidence(v, set(), None, read_len=800) == "low"


def test_homopolymer_poly_count_and_conclusion():
    """poly 区缺失：识别结构、计算参考/测得重复数、结论给出 poly 判读"""
    ref = "ACGT" * 20 + "A" * 20 + "TGCACGTT" + "ACGT" * 30  # poly-A 81-100
    read_bases = ref[20:180]
    mutated = read_bases[:60] + read_bases[61:]  # 缺 1 个 A
    blob = make_ab1(mutated, [40] * len(mutated))
    result = analyze([("f.ab1", blob)], ref, [])
    hp = next(h for h in result["homopolymers"] if h["base"] == "A")
    assert hp["ref_repeat_count"] == 20
    assert hp["observed_repeat_count"] == 19
    assert hp["count_reliable"] and hp["variant"]["type"] == "deletion"
    v = next(x for x in result["variants"] if x.get("homopolymer"))
    assert v["homopolymer"]["observed_repeat_count"] == 19
    assert "poly(A)" in result["conclusion"]


def _shaped_traces(bases, poly_span, real_peaks, channel_order="ATGC", spb=4,
                   decay=0.0, baseline_slope=0.0, crosstalk=0.0, stutter=None):
    """构造 4 采样/碱基的三角峰 trace：poly 窗口内只放 real_peaks 个 A 峰

    模拟长同聚物压缩：basecaller 按窗口碱基数调用，但峰图上只有
    real_peaks 个可分辨峰（窗口 [start, end) 为 read 上的 poly 区间）。

    注入开关（默认关闭，现有用例零改动；A2/B1/B4/B5 的验收依赖，真值已知）：
    - decay：全通道几何衰减，峰值 ×(1−decay)^(采样位置/总采样点)
    - baseline_slope：线性基线漂移（每采样点叠加 slope×位置 的本底）
    - crosstalk：通道串扰（每通道混入相邻通道该比例的信号）
    - stutter：poly 末端后的滑移 echo 序列（主峰高比例，如 (0.5, 0.2)）
    """
    traces = {ch: [] for ch in channel_order}
    for i, b in enumerate(bases):
        for ch in channel_order:
            if ch == b and not (ch == "A" and poly_span[0] <= i < poly_span[1]):
                traces[ch].extend([100, 100, 4, 4])
            else:
                traces[ch].extend([4, 4, 4, 4])
    head = [4] * (poly_span[0] * spb)
    body = [100, 100, 4, 4] * real_peaks
    tail = [4] * ((len(bases) - poly_span[1]) * spb)
    traces["A"] = head + body + tail
    n = len(bases) * spb
    if stutter:
        for j, f in enumerate(stutter):
            s0 = (poly_span[1] + j) * spb
            if s0 + 1 < n:
                traces["A"][s0] = int(round(100 * f))
                traces["A"][s0 + 1] = int(round(100 * f))
    if decay:
        for ch in channel_order:
            traces[ch] = [int(round(v * (1 - decay) ** (s / n)))
                          for s, v in enumerate(traces[ch])]
    if baseline_slope:
        for ch in channel_order:
            traces[ch] = [v + int(round(baseline_slope * s))
                          for s, v in enumerate(traces[ch])]
    if crosstalk:
        order = list(channel_order)
        mixed = {ch: [0] * n for ch in order}
        for idx, ch in enumerate(order):
            prev_ch = order[(idx - 1) % len(order)]
            mixed[ch] = [int(round((1 - crosstalk) * traces[ch][s]
                                   + crosstalk * traces[prev_ch][s]))
                         for s in range(n)]
        traces = mixed
    return [traces[ch] for ch in channel_order]


def test_poly_peak_count_and_cross_read_validation():
    """峰图独立计数 < 调用碱基数：判重复数不可靠并在结论给出峰图计数"""
    ref = "ACGT" * 20 + "A" * 30 + "TGCACGTT" + "ACGT" * 30  # poly-A 81-110
    read_bases = ref[40:170]
    spb = 4
    traces = _shaped_traces(read_bases, poly_span=(40, 70), real_peaks=27)
    blob = make_ab1(read_bases, [40] * len(read_bases), traces=traces, samples_per_base=spb)
    result = analyze([("f.ab1", blob)], ref, [])
    hp = next(h for h in result["homopolymers"] if h["base"] == "A")
    assert hp["ref_repeat_count"] == 30 and hp["observed_repeat_count"] == 30
    assert hp["peak_count_estimate"] == 27
    assert hp["count_reliable"] is False
    assert hp["read_counts"][0]["peak_count"] == 27
    # B2：宽度法交叉值同时输出（峰数法可用时 method=peaks，两者应一致）
    assert hp["length_estimate"] == 27
    assert hp["length_method"] == "peaks"
    assert "峰图计数约 27 个" in result["conclusion"]
    assert "碱基调用存在整段偏差风险" in result["conclusion"]


def test_poly_peak_count_agrees_is_reliable():
    """峰图计数与调用一致：维持可靠判定（正常 polyA）"""
    ref = "ACGT" * 20 + "A" * 30 + "TGCACGTT" + "ACGT" * 30
    read_bases = ref[40:170]
    blob = make_ab1(read_bases, [40] * len(read_bases), samples_per_base=4)
    result = analyze([("f.ab1", blob)], ref, [])
    hp = next(h for h in result["homopolymers"] if h["base"] == "A")
    assert hp["count_reliable"] is True
    assert hp["peak_count_estimate"] == 30
    assert "峰图计数约" not in result["conclusion"]


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


def test_trim_by_quality_sporadic_good_base_in_bad_ends():
    """坏末端里夹着零星高 Q 碱基：逐碱基法剪不动，Mott 累积法应整段剪掉"""
    bases = "ACGT" * 15
    quality = ([3, 3, 45, 4, 4, 4, 5]       # 坏首端，夹一个 Q45
               + [40] * 40                   # 干净中段
               + [4, 50, 4, 4, 3])           # 坏尾端，夹一个 Q50
    s, e = _trim_by_quality(bases, quality, 20)
    # 中段 40 个干净碱基整体保留；首端 Q45 零星碱基（其后紧跟 -16 分）不把保留区拖到前端
    assert (s, e) == (7, 49)
    assert quality[s] >= 20 and quality[e - 1] >= 20


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


def test_analyze_end_noise_not_confirmed(reference):
    """read 首尾 20bp 内的单 read 差异是信号爬升/下降区噪声：
    判低置信、不写入共识、不影响 CDS 结论；中段同 Q 差异照常确证"""
    seg = list(reference[100:600])
    end_noise = "A" if seg[9] != "A" else "G"      # read_pos 10 → ref_pos 110
    mid_real = "A" if seg[249] != "A" else "G"     # read_pos 250 → ref_pos 350
    seg[9], seg[249] = end_noise, mid_real
    blob = make_ab1("".join(seg), [40] * 500)
    features = [{"name": "GFP", "type": "CDS", "start": 101, "end": 700, "strand": "+"}]
    result = analyze([("end.ab1", blob)], reference, features)

    by_pos = {v["ref_pos"]: v for v in result["variants"]}
    noise, real = by_pos[110], by_pos[350]
    assert noise["confidence"] == "low"
    assert real["confidence"] != "low"
    # 噪声不进共识：consensus 该位仍是参考碱基
    assert result["consensus"]["sequence"][109] == reference[109].upper()
    assert result["consensus"]["sequence"][349] == mid_real
    # CDS 判定只计入确证变体：verdict 提到中段差异而非首端噪声
    gfp = next(cr for cr in result["cds_reports"] if cr["name"] == "GFP")
    assert "110" not in gfp["verdict"]


def test_reverse_read_variant_read_pos_in_original_coords(reference):
    """反向 read 的 read_pos 必须镜像回原始电泳顺序：Q 值/峰证据按真实位点取样，
    而不是拿到镜像位置（往往是另一端的坏区）——真实突变不被误判低置信"""
    seg = list(reference[700:1300])
    seg[50] = "A" if seg[50] != "A" else "G"   # ref_pos 751；revcomp 后位于 read 尾部附近
    rc = revcomp("".join(seg))                  # 原始 read（电泳顺序）：突变靠近 read 末端
    q = [40] * len(rc)
    q[3] = 5                                    # read 首端的坏碱基（与突变无关）
    result = analyze([("rev.ab1", make_ab1(rc, q))], reference, [])
    v = result["variants"][0]
    assert v["ref_pos"] == 751
    assert v["read_q"] == 40                   # 取突变真实位点的 Q，而非镜像位（Q5）
    assert v["confidence"] != "low"


def test_ab1_channel_autodetect_nonstandard_tag_and_order(reference):
    """真实仪器染料组各异：通道不在 DATA9-12 / 顺序非 A-T-G-C 时必须自动检测。

    映射错了，突变峰被记到错误通道（mutant_pct 假性 0/50%），真实突变被
    误判低置信（线上实例：正向 read C→A，Q31，tracy 印证，仍判 low）。
    """
    seg = list(reference[500:1000])
    seg[100] = "A" if seg[100] != "A" else "G"  # ref_pos 601
    mutated = "".join(seg)
    q = [40] * len(mutated)
    # 通道存 DATA1-4，顺序 T/A/G/C（即 DATA2 是 A 通道）
    blob = make_ab1(mutated, q, channel_start=1, channel_order="TAGC")
    r = extract_read(blob)
    expected_a0 = 100 if mutated[0] == "A" else 4
    assert r["trace"]["A"][0] == expected_a0
    result = analyze([("f.ab1", blob)], reference, [])
    v = next(x for x in result["variants"] if x["ref_pos"] == 601)
    assert v["confidence"] != "low"
    ev = v.get("peak_evidence") or {}
    assert ev.get("mutant_pct", 0) >= 80


def test_ab1_channel_default_mapping_still_works(reference):
    """标准 DATA9-12/ATGC 文件不受自动检测影响"""
    blob = make_ab1(reference[200:700], [40] * 500)
    r = extract_read(blob)
    assert r["trace"]["A"][:5] == [100 if reference[200 + i] == "A" else 4 for i in range(5)]


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
    assert "与设计一致" in cr["verdict"]


def test_cds_report_frameshift(reference):
    ref, start, end = _cds_reference()
    feats = [{"name": "MX", "type": "CDS", "start": start, "end": end, "strand": "+"}]
    seg = ref[40:190]
    mutated = seg[:29] + seg[30:]  # 删 CDS 内 1bp（ref pos 70）→ 移码
    result = analyze([("f.ab1", make_ab1(mutated, [40] * len(mutated)))], ref, feats)
    cr = result["cds_reports"][0]
    assert cr["protein_identical"] is False
    assert cr["frameshift_count"] == 1
    # 主句：变异位置 + 移码起点，一条因果链（该合成序列移码后无内部终止）
    assert "70 处的缺失" in cr["verdict"]
    assert "自第 11 位起移码" in cr["verdict"]
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
    assert "与设计一致" in cr1["verdict"]

    # CDS 首碱基之后的 1bp 插入：移码，HGVS fs 描述
    seg_in = ref[:41] + "T" + ref[41:190]
    cr2 = analyze([("f.ab1", make_ab1(seg_in, [40] * len(seg_in)))], ref, feats)["cds_reports"][0]
    assert cr2["frameshift_count"] == 1
    assert cr2["protein_identical"] is False
    assert "移码" in cr2["verdict"]
    assert "41 处的插入" in cr2["verdict"]  # 主句直接给出变异位置
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
    assert cr["verdict"].startswith("CDS 已完整覆盖")


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
    assert "框内缺失 3 bp" in cr["verdict"]


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
    grade, _, _ = _read_grade(seg, [40] * len(seg))
    assert grade == "B"
    low_q = [18] * 100                     # Q20 比例 0 → C
    assert _read_grade("ACGT" * 25, low_q)[0] == "C"
    assert _read_grade("ACGT" * 5, [40] * 20)[0] == "C"  # 过短
    assert _read_grade("ACGT" * 25, [40] * 80 + [15] * 20)[0] == "B"  # Q20 比例 0.8


def test_continuous_read_length():
    """B5：CRL = QV>20 最长连续段（Genewiz 口径）"""
    assert continuous_read_length([40, 40, 10, 40, 41, 42, 5, 40]) == 3
    assert continuous_read_length([19, 20, 21]) == 1   # Q20 本身不算（严格 >20）
    assert continuous_read_length([]) == 0


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
    assert "未计入判定" in cr["verdict"] and "核对峰图" in cr["verdict"]
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
    assert "另有 1 处低置信差异" in cr["verdict"]
    assert "核对峰图" in cr["verdict"]
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


# ---------- 插入峰强度比（真实单碱基插入 vs caller 伪影的判别） ----------

def test_insertion_peak_evidence_grades():
    """插入峰强度比：峰接近邻峰时升档（Q 在压缩区偏低不一票否决），无峰判低"""
    prefix = "ACGTTACGTAGCAATGGTCA"          # 20bp 稳定区前导
    ins_read = prefix + "C" + "CAATGGTCA"    # read_pos 21 = 插入 C（前导区外）
    read = extract_read(make_ab1(ins_read, [45] * len(ins_read)))  # 主通道 100 / 本底 4
    # 默认 trace 下插入 C 的 C 通道与邻峰同为 100 → ratio ≈ 1
    ev = _variant_peak_evidence(
        read["trace"], read["peak_indices"], 21, "-", "C", "insertion")
    assert ev is not None and ev["insertion_peak_ratio"] == pytest.approx(1.0, abs=0.15)
    v = {"read_pos": 21, "read_q": 3, "support_reads": 1}
    # 峰清晰但单 read + 极低 Q：中置信（峰压缩区 Q 偏低不否决真实峰）
    assert _variant_confidence(v, set(), ev) == "medium"
    # 多 read 支持或 Q 达标 → 高置信
    assert _variant_confidence(dict(v, read_q=30), set(), ev) == "high"
    assert _variant_confidence(dict(v, support_reads=2), set(), ev) == "high"
    # 峰很弱（<0.3）：疑似伪影 → 低
    tr = {b: list(x) for b, x in read["trace"].items()}
    tr["C"][20] = 4  # 插入位点 C 通道压到本底
    ev_weak = _variant_peak_evidence(tr, read["peak_indices"], 21, "-", "C", "insertion")
    assert ev_weak["insertion_peak_ratio"] < 0.3
    assert _variant_confidence(v, set(), ev_weak) == "low"


def test_insertion_peak_evidence_requires_stable_region():
    """read 前导 ~20bp 峰形未稳定：不产出插入峰证据（走 Q + 支持数口径）"""
    bases = "ACGT" * 10
    read = extract_read(make_ab1(bases, [45] * len(bases)))
    assert _variant_peak_evidence(read["trace"], read["peak_indices"], 10, "-", "C", "insertion") is None
    assert _variant_peak_evidence(read["trace"], read["peak_indices"], None, "-", "C", "insertion") is None


def test_mixed_signal_yields_to_clear_insertion_peak():
    """插入位点的压缩拖尾（次级峰 30-40%）不按混合样品一票否决：峰清晰时按插入规则升档"""
    ins_read = "ACGTTACGTAGCAATGGTCA" + "C" + "CAATGGTCA"  # read_pos 21 = 插入 C
    read = extract_read(make_ab1(ins_read, [45] * len(ins_read)))
    ev = _variant_peak_evidence(
        read["trace"], read["peak_indices"], 21, "-", "C", "insertion")
    v = {"read_pos": 21, "read_q": 3, "support_reads": 1}
    # 同位点被判 mixed：峰清晰（ratio≥0.6）→ 仍按插入规则给中置信而非低
    assert _variant_confidence(v, {21}, ev) == "medium"
    # 无峰证据的 mixed 位点依旧低置信
    assert _variant_confidence(v, {21}, None) == "low"


# ---------- indel 归一化（Tan 2015）与 tracy basecall 交叉印证 ----------

def test_normalize_indel_left_aligns_homopolymer():
    """同聚物/重复区的等价 indel 归一化到最左表示：不同 anchor 聚合为同一事件"""
    from core.sanger.pipeline import _normalize_indel, _variant_key
    ref = "ACGT" * 100 + "AAAA" + "ACGT" * 100          # 同聚物 AAAA 在 401-404
    base = 401
    # 同聚物内任意位置的 1bp 缺失/插入都是同一事件
    for pos in (401, 402, 403, 404):
        d = _normalize_indel(ref, {"ref_pos": pos, "type": "deletion",
                                   "ref_base": "A", "alt_base": "-", "length": 1})
        assert d["ref_pos"] == base
        i = _normalize_indel(ref, {"ref_pos": pos, "type": "insertion",
                                   "ref_base": "-", "alt_base": "A", "length": 1})
        assert i["ref_pos"] == base - 1  # 插入 anchor：最左 A 之前
        assert _variant_key(d) == _variant_key(
            _normalize_indel(ref, {"ref_pos": 403, "type": "deletion",
                                   "ref_base": "A", "alt_base": "-", "length": 1}))
    # 重复区多碱基插入：相位对齐的等价 anchor（B 后）左移到最左（T 后）并循环移位；
    # 相位错开的 anchor（A 后，如 9）不等价于 4，保持原位
    ref2 = "ACGT" + "ABABAB" + "TTTT"
    ins = _normalize_indel(ref2, {"ref_pos": 6, "type": "insertion",
                                  "ref_base": "-", "alt_base": "AB", "length": 2})
    assert (ins["ref_pos"], ins["alt_base"]) == (4, "AB")
    ins9 = _normalize_indel(ref2, {"ref_pos": 9, "type": "insertion",
                                   "ref_base": "-", "alt_base": "AB", "length": 2})
    assert ins9["ref_pos"] == 9  # 相位错开：插入 AABBA... 不等价于 T-ABABAB...，不左移
    # 非重复区变体原样保留
    same = _normalize_indel(ref, {"ref_pos": 100, "type": "substitution",
                                  "ref_base": "A", "alt_base": "G", "length": 1})
    assert same["ref_pos"] == 100


def test_tracy_basecall_corroboration(monkeypatch, reference):
    """独立 basecaller 报出同一（归一化后）变体：低置信升中并打印证标记"""
    import core.sanger.pipeline as P
    seg = list(reference[99:499])
    seg[50] = "A" if seg[50] != "A" else "G"             # read 内替换 → 低 Q 判低
    seq = "".join(seg)
    q = [45] * 400
    q[50] = 12                                            # 变异位低 Q
    result = analyze([("f.ab1", make_ab1(seq, q))], reference, [])
    v = result["variants"][0]
    assert v["confidence"] == "low" and "corroborated_by_basecall" not in v

    # tracy 重 basecall 输出同一序列 → 报出同一变体（跨 caller 印证）
    cross = P._try_tracy_basecall  # 保留引用防误删
    monkeypatch.setattr(P, "_try_tracy_basecall", lambda blob: (seq, [40] * len(seq)))
    result2 = analyze([("f.ab1", make_ab1(seq, q))], reference, [])
    v2 = result2["variants"][0]
    assert v2.get("corroborated_by_basecall") is True
    # 印证仅标记、不改置信度：两个 caller 读同一信号，错误相关不构成独立证据
    assert v2["confidence"] == "low"
    assert result2["engine"] == "internal+biopython+tracy-basecall"


def test_tracy_unavailable_degrades_gracefully(reference):
    """无 tracy 环境：不做交叉印证，管线照常（其余行为与现状一致）"""
    seg = list(reference[99:499])
    seg[50] = "A" if seg[50] != "A" else "G"
    seq = "".join(seg)
    result = analyze([("f.ab1", make_ab1(seq, [40] * 400))], reference, [])
    assert result["engine"] == "internal+biopython"
    assert result["variants"]


def test_fwo1_direct_mapping():
    """第 0 步：FWO_1 是权威通道映射，Biopython 与内置解析器都直接采用"""
    bases = "ACGT" * 8
    blob = make_ab1(bases, [40] * len(bases), channel_order="GATC", fwo="GATC")
    expected = {b: [100 if c == b else 4 for c in bases] for b in "GATC"}
    assert extract_read(blob)["trace"] == expected                 # Biopython 主路径
    assert _extract_read_internal(blob)["trace"] == expected       # 内置解析器兜底路径


def test_fwo1_overrides_content_detection(caplog):
    """第 0 步：FWO_1 与内容检测冲突时以 FWO_1 为准，并记 warning 不静默"""
    bases = "ACGT" * 8
    # 通道内容按旧惯例摆放（DATA9=A 主峰），文件却声明 GATC（DATA9=G）→ 冲突
    blob = make_ab1(bases, [40] * len(bases), channel_order="ATGC", fwo="GATC")
    with caplog.at_level(logging.WARNING):
        read = extract_read(blob)
    # FWO_1 胜出：G ← DATA9（其内容实为 A 主峰），交叉校验告警可见
    assert read["trace"]["G"] == [100 if c == "A" else 4 for c in bases]
    assert "FWO_1" in caplog.text and "不一致" in caplog.text


def test_fwo1_ignored_without_data9_12():
    """FWO_1 只描述 DATA9-12；通道不在 9-12 时回落内容检测"""
    bases = "ACGT" * 8
    blob = make_ab1(bases, [40] * len(bases), channel_start=1,
                    channel_order="TAGC", fwo="GATC")
    read = extract_read(blob)
    # 检测命中 group(1-4) 的 TAGC 排列：T ← DATA1、A ← DATA2
    assert read["trace"]["T"] == [100 if c == "T" else 4 for c in bases]
    assert read["trace"]["A"] == [100 if c == "A" else 4 for c in bases]


def test_default_fallback_gatc_convention():
    """第 0 步：无 FWO_1 且检测不可靠时，兜底为 DATA9-12 → G/A/T/C

    旧兜底 A/T/G/C 与真实文件权威顺序（FWO_1=GATC）交叉错位，本测试锁定修正。
    碱基全 N 时内容检测无采样位点，必然走兜底。
    """
    traces = [[1] * 6, [2] * 6, [3] * 6, [4] * 6]  # DATA9..12 互不相同
    blob = make_ab1("NNNNNN", [40] * 6, traces=traces)
    read = extract_read(blob)
    assert read["trace"]["G"] == [1] * 6  # DATA9
    assert read["trace"]["A"] == [2] * 6  # DATA10
    assert read["trace"]["T"] == [3] * 6  # DATA11
    assert read["trace"]["C"] == [4] * 6  # DATA12


# ==================== A1：窗口峰高（抗 PLOC 抖动） ====================

def test_poly_ratio_window_height_resists_ploc_jitter():
    """A1：峰高在峰窗内取 max——PLOC 系统性偏 1 采样点时，run 内峰全被
    单点采样读成爬坡值 4，正常 poly 被误判为压缩（4/100）；窗口取峰后为 1.0"""
    trace = {b: [4] * 64 for b in "ACGT"}
    peaks = []
    for k in range(16):
        trace["A"][4 * k + 1] = 100   # 峰顶在 PLOC+1
        peaks.append(4 * k)           # PLOC 全部偏在峰顶前 1 个采样点
    ratio = _poly_peak_amplitude_ratio(trace, peaks, 5, 8)
    assert ratio == 1.0


# ==================== A2：基线校正 ====================

def test_baseline_correction_prevents_mixed_false_positives():
    """A2：线性基线漂移抬高本底——不校正时 read 后段本底接近峰高，
    _detect_mixed_positions 大面积误报；校正后本底扣除、零误报"""
    ref = "ACGT" * 20 + "A" * 30 + "TGCACGTT" + "ACGT" * 30
    read_bases = ref[40:170]
    traces = _shaped_traces(read_bases, (40, 70), 30, baseline_slope=0.10)
    blob = make_ab1(read_bases, [40] * len(read_bases), traces=traces, samples_per_base=4)
    raw = analyze([("f.ab1", blob)], ref, [], signal_correct=False)
    assert raw["mixed_detected"] and any(raw["mixed_detected"].values()), \
        "注入应先复现误报（基线漂移 → 假混合峰）"
    corrected = analyze([("f.ab1", blob)], ref, [], signal_correct=True)
    assert corrected["mixed_detected"] == {}


def test_baseline_correct_preserves_merged_plateau():
    """A2 平台护栏：合并峰平台（信号自身就是局部最低值）不得被当本底扣平。
    本底是四通道共有的加性量，信号是通道特异的——跨通道共模判据兜底"""
    tr = {ch: [4] * 400 for ch in "ATGC"}
    tr["A"][100:300] = [100] * 200          # 200 采样点连续 A 平台
    bc = baseline_correct(tr)
    assert max(bc["A"][100:300]) >= 90, "平台被扣平"
    assert max(bc["T"]) == 0 and min(bc["A"][:100]) == 0


# ==================== A3：长度相关容差 ====================

def test_peak_count_tolerance_length_scaled():
    """A3：tol = max(1, round(1+0.05×长度))——旧口径 0.02 对 30bp 只容 ±1，
    文献口径 20bp+ 误差可达 ±2。已验算：27vs30 差 3 > 2 仍不可靠、
    0 差可靠、19vs20 差 1 ≤ 2 可靠"""
    assert _peak_count_tol(1) == 1
    assert _peak_count_tol(19) == 2
    assert _peak_count_tol(20) == 2
    assert _peak_count_tol(30) == 2
    assert _peak_count_tol(107) == 6


# ==================== A4：报告层 ====================

def test_summarize_slippage_wording():
    """A4：带滑移标记的低置信变异点名“poly 下游疑似滑移伪影”（B4 落地后接入）"""
    v = _base_variant(ref_pos=5, type="deletion", ref_base="A", alt_base="-", length=1)
    v["confidence"] = "low"
    v["features"] = []
    v["slippage_artifact"] = True
    note = summarize_severity([v])[0]
    assert "滑移伪影" in note and "混合峰" not in note
    # 无标记的低置信变异维持原口径
    v2 = _base_variant(ref_pos=5, type="deletion", ref_base="A", alt_base="-", length=1)
    v2["confidence"] = "low"
    v2["features"] = []
    assert "疑似测序噪声或混合峰" in summarize_severity([v2])[0]


def test_excel_conclusion_flags_poly_count_mismatch():
    """A4：批量 Excel 一句话结论补回 poly 区峰图计数告警（此前是真丢失）"""
    from core.sanger.batch import excel_conclusion
    res = {
        "reads": [{}], "errors": [], "variants": [],
        "consensus": {"coverage_percent": 99.0},
        "cds_reports": [],
        "homopolymers": [{"tier": "poly", "count_reliable": False}],
    }
    out = excel_conclusion("p", res, 1, True)
    assert "poly 区峰图计数与碱基调用不一致" in out
    # 观察级 run 不触发该告警
    res2 = {**res, "homopolymers": [{"tier": "observed", "count_reliable": False}]}
    assert "poly 区峰图计数" not in excel_conclusion("p", res2, 1, True)


# ==================== B1：property map 与局部压缩比 ====================

def test_property_maps_h_and_w():
    """B1：H(x)/W(x) property map——每峰 (height, width@半高) 滑动中位数插值"""
    ref = "ACGT" * 10 + "A" * 30 + "TGCACGTT" + "ACGT" * 10
    traces = _shaped_traces(ref, (40, 70), 30)
    tr = {ch: traces[i] for i, ch in enumerate("ATGC")}
    peaks = [i * 4 for i in range(len(ref))]
    maps = property_maps(tr, peaks)
    assert maps is not None
    assert abs(maps["H"][peaks[50]] - 100) < 5      # poly 区峰高包络
    assert maps["peaks_w"][0] == pytest.approx(2)   # 正常单峰半高宽 2 采样点


def test_local_ratio_removes_position_bias():
    """B1：信号沿 read 衰减时全局分母把后段正常 poly 误判压缩；
    局部分母 H(run) 下前/后段同 poly 的比值无系统性位置差"""
    bases = "ACGT" * 5 + "A" * 20 + "CGTACGTA" + "A" * 20 + "ACGT" * 5
    n = len(bases)
    ns = n * 4
    traces = {ch: [] for ch in "ATGC"}
    for i, b in enumerate(bases):
        for ch in "ATGC":
            traces[ch].extend([100 if ch == b else 4] * 4)
    for ch in "ATGC":
        traces[ch] = [int(v * (0.05 ** (s / ns))) for s, v in enumerate(traces[ch])]
    peaks = [i * 4 for i in range(n)]
    early = _poly_peak_amplitude_ratio(traces, peaks, 21, 20, local=True)
    late = _poly_peak_amplitude_ratio(traces, peaks, n - 25, 20, local=True)
    assert 0.8 <= early <= 1.2, early
    assert 0.8 <= late <= 1.2, late
    late_global = _poly_peak_amplitude_ratio(traces, peaks, n - 25, 20, local=False)
    assert late_global < 0.6, late_global   # 全局口径的位置偏差（本改动动机）


def test_fit_decay_recovers_rate():
    """B1：log 空间衰减拟合从注入的几何衰减中恢复 α（Andrade & Manolakos）"""
    import math
    bases = "ACGT" * 40
    traces = {ch: [] for ch in "ATGC"}
    for i, b in enumerate(bases):
        for ch in "ATGC":
            traces[ch].extend([100 if ch == b else 4] * 4)
    ns = len(bases) * 4
    decay_frac = 0.05
    for ch in "ATGC":
        traces[ch] = [int(v * (decay_frac ** (s / ns))) for s, v in enumerate(traces[ch])]
    peaks = [i * 4 for i in range(len(bases))]
    fd = fit_decay(traces, peaks, skip_start=5, skip_end=5)
    assert fd is not None and fd["n"] > 20
    expect_alpha = -math.log(decay_frac) / ns   # ln h = β − α·t
    assert fd["alpha"] == pytest.approx(expect_alpha, rel=0.25)


# ==================== B2：宽度法救合并峰 ====================

def test_estimate_run_length_rescues_merged_peaks():
    """B2：峰完全合并（连续高台）时峰数法失效（count≈0），宽度法给出
    长度估计 ∈ [28,32]（真值 30）；count_reliable 维持不可靠判定"""
    ref = "ACGT" * 20 + "A" * 30 + "TGCACGTT" + "ACGT" * 30
    read_bases = ref[40:170]
    traces = _shaped_traces(read_bases, (40, 70), 30)
    traces[1][40 * 4:70 * 4] = [100] * 120   # A 通道（"ATGC" 第 1 路）连续高台
    blob = make_ab1(read_bases, [40] * len(read_bases), traces=traces, samples_per_base=4)
    result = analyze([("f.ab1", blob)], ref, [])
    hp = next(h for h in result["homopolymers"] if h["base"] == "A")
    assert hp["peak_count_estimate"] == 0     # 峰数法失效（现有行为）
    assert hp["count_reliable"] is False
    assert 28 <= hp["length_estimate"] <= 32
    assert hp["length_method"] == "width"
    assert hp["length_ci"] and hp["length_ci"][0] <= 30 <= hp["length_ci"][1]


# ==================== B3：二/三核苷酸重复进报告 ====================

def test_repeat_runs_report_and_observed_tier_end_to_end():
    """B3：(CAG)6 参考进 homopolymer_report；8-20bp A run 出现在观察级；
    观察级与单元重复不触发“碱基调用偏差”结论告警"""
    ref = "ACGT" * 10 + "CAG" * 6 + "ACGT" * 5 + "A" * 12 + "TGCACGTT" + "ACGT" * 20
    read_bases = ref[20:150]
    result = analyze([("f.ab1", make_ab1(read_bases, [40] * len(read_bases)))], ref, [])
    cag = next(h for h in result["homopolymers"] if h["period"] == 3)
    assert cag["unit"] == "CAG" and cag["ref_repeat_count"] == 6
    obs = next(h for h in result["homopolymers"]
               if h["tier"] == "observed" and h["period"] == 1)
    assert obs["base"] == "A" and 8 <= obs["length"] < 20
    assert "碱基调用存在整段偏差风险" not in result["conclusion"]


# ==================== B4：滑移 echo 先于 mixed 判定 ====================

def test_slippage_echoes_not_flagged_as_mixed():
    """B4：poly 下游滑移 echo（n−1/n−2 几何衰减次峰）不进 mixed_positions，
    不再连带降级该区真实变异；P17 顺序要求 stutter 检测先于 merge"""
    ref = "ACGT" * 20 + "A" * 30 + "TGCACGTT" + "ACGT" * 30
    read_bases = ref[40:170]
    traces = _shaped_traces(read_bases, (40, 70), 30, stutter=(0.5, 0.4))
    blob = make_ab1(read_bases, [40] * len(read_bases), traces=traces, samples_per_base=4)
    read = extract_read(blob)
    unfiltered = _detect_mixed_positions(read_bases, read["trace"], read["peak_indices"])
    assert 71 in unfiltered and 72 in unfiltered, "注入应先复现误报（echo 峰 → 假混合）"
    result = analyze([("f.ab1", blob)], ref, [])
    mixed = result["mixed_detected"].get("f.ab1", [])
    assert 71 not in mixed and 72 not in mixed
    assert result["reads"][0]["slippage_positions"] == [71, 72]


def test_slippage_ignores_full_height_continuation():
    """B4 红线：poly 后真实同碱基延续（全高峰）不是 echo——不打滑移标记；
    等高次级峰（真实混合的形态，非衰减序列）也不误判为滑移"""
    ref = "ACGT" * 20 + "A" * 30 + "TGCACGTT" + "ACGT" * 30
    read_bases = ref[40:170]
    traces = _shaped_traces(read_bases, (40, 70), 30)
    blob = make_ab1(read_bases, [40] * len(read_bases), traces=traces, samples_per_base=4)
    result = analyze([("f.ab1", blob)], ref, [])
    assert result["reads"][0]["slippage_positions"] == []


# ==================== B5：poly 后骤降 + CRL ====================

def test_post_poly_dropout_reported_and_end_truncation_not_alarmed():
    """B5：read 中段 poly 下游信号骤降（塌陷后恢复）进结论并区分“未覆盖”；
    read 末端塌陷记 end_truncation、不触发骤降告警；护栏：骤降只标记不修补"""
    ref = "ACGT" * 20 + "A" * 30 + "TGCACGTT" + "ACGT" * 30
    read_bases = ref[40:170]
    traces = _shaped_traces(read_bases, (40, 70), 30)
    for i in range(70, 85):                    # 中段塌陷 15 碱基后恢复
        for ch_i in range(4):
            row = traces[ch_i]
            row[i * 4:i * 4 + 4] = [max(0, v - 90) for v in row[i * 4:i * 4 + 4]]
    blob = make_ab1(read_bases, [40] * len(read_bases), traces=traces, samples_per_base=4)
    result = analyze([("f.ab1", blob)], ref, [])
    drops = result["reads"][0]["post_poly_dropouts"]
    assert drops and drops[0]["base"] == "A"
    assert drops[0]["end_truncation"] is False and drops[0]["recovered"] is True
    assert "信号骤降" in result["conclusion"] and "区别于未覆盖" in result["conclusion"]

    # read 末端塌陷：end_truncation，结论不告警
    traces2 = _shaped_traces(read_bases, (40, 70), 30)
    for i in range(100, len(read_bases)):      # 距 read 末端 < 2×END_MARGIN
        for ch_i in range(4):
            row = traces2[ch_i]
            row[i * 4:i * 4 + 4] = [max(0, v - 90) for v in row[i * 4:i * 4 + 4]]
    blob2 = make_ab1(read_bases, [40] * len(read_bases), traces=traces2, samples_per_base=4)
    result2 = analyze([("f.ab1", blob2)], ref, [])
    drops2 = result2["reads"][0]["post_poly_dropouts"]
    assert drops2 and drops2[0]["end_truncation"] is True
    assert "信号骤降" not in result2["conclusion"]


def test_read_crl_in_output():
    """B5：CRL 进 read QC 输出"""
    ref = "ACGT" * 30
    blob = make_ab1(ref[10:130], [40] * 110)
    result = analyze([("f.ab1", blob)], ref, [])
    assert result["reads"][0]["crl"] == 110
    q = [40] * 60 + [3] * 5 + [40] * 45
    blob2 = make_ab1(ref[10:130], q)
    result2 = analyze([("f.ab1", blob2)], ref, [])
    assert result2["reads"][0]["crl"] == 60

