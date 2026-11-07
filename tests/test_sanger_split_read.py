"""T19 split-read 二次定位（goal_sanger_full_fix_v2 T19；A 2.1/2.3）

端部软剪切的高质量段在参考（含环状延长与反向互补）上二次局部比对：
命中 → structural_breakpoint（fail，段落点可查）；未命中 → 未知序列（fail）。
Q 加权打分：六组合成场景 180 read + MX 真实数据错位率 0.0，已评估无需改动
（work/measure_align_misplacement.py，测量记录见模块 docstring）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/backend"))

from core.sanger.split_read import classify_segment  # noqa: E402


def test_breakpoint_hit_same_reference():
    """段来自参考另一处（连接错误典型形态）→ breakpoint，落点即断点坐标"""
    ref = "ACGT" * 250 + "GTAAAACGACGGCCAGTACGT" + "ACGT" * 250
    seg = ref[1000:1064]                     # 参考他处取 64bp
    out = classify_segment(seg, ref)
    assert out["status"] == "breakpoint"
    assert out["ref_start"] == 1001 and out["ref_end"] == 1064
    assert out["direction"] == "+" and out["identity"] >= 0.99


def test_unknown_sequence():
    """随机 junk 段（S5/S6 形态）→ unknown"""
    import random
    rng = random.Random("t19-unknown")
    ref = "ACGT" * 500
    junk = "".join(rng.choice("ACGT") for _ in range(80))
    assert classify_segment(junk, ref) == {"status": "unknown"}


def test_minus_strand_hit():
    """− 链段（电泳顺序 = revcomp）→ direction='-'，落点折回正向坐标"""
    ref = "ACGT" * 250 + "TTTCGTCGCGGCCCGTGACGT" + "ACGT" * 250   # 1001-1020 为 M13R revcomp 区
    seg_electro = "CACGGCCGCGACGAAA"           # 参考该处的 revcomp（电泳顺序）
    out = classify_segment(seg_electro, ref)
    assert out["status"] == "breakpoint"
    assert out["direction"] == "-"
    assert 1001 <= out["ref_start"] and out["ref_end"] <= 1020


def test_circular_wrap_hit():
    """跨原点段（参考尾部 + 头部拼接）→ wraps_origin=True，坐标折回"""
    import random
    rng = random.Random("t19circ")
    ref = "".join(rng.choice("ACGT") for _ in range(1000))
    seg = ref[960:1000] + ref[0:24]             # 尾 40bp + 头 24bp（跨原点）
    # 线性比对只覆盖 40/64=0.625 < 0.8 → 环状延长才能整段命中
    out = classify_segment(seg, ref, circular=True)
    assert out["status"] == "breakpoint"
    assert out["wraps_origin"] is True
    assert out["ref_start"] == 961


def test_verdict_structural_breakpoint_e2e():
    """端到端：3′ 端连上参考远处片段（桥接代价 > 尾段得分，比对必然剪切）
    → fail + structural_breakpoint；junk 尾段 → fail + unaligned_hq_segment"""
    import random
    from abif_utils import make_trace_ab1
    from core.sanger.pipeline import analyze
    rng = random.Random("t19e2e")
    ref = "".join(rng.choice("ACGT") for _ in range(1500))
    # 主比对 175-1075，3′ 端接参考 1430-1499（距离 355bp：以缺失桥接需
    # 付 ~714 分 gap 代价，远超尾段 140 分 → 局部比对剪掉尾段）
    blob = make_trace_ab1(ref[174:1075] + ref[1430:1500], q=50)
    res = analyze([("r.ab1", blob)], ref, [], circular=False)
    kinds = {b["kind"] for b in res["verdict"]["blocking"]}
    assert "structural_breakpoint" in kinds
    bp = next(b for b in res["verdict"]["blocking"]
              if b["kind"] == "structural_breakpoint")
    assert bp["ref_start"] and bp["ref_end"]       # 断点坐标下发
    assert 1430 <= bp["ref_start"] and bp["ref_end"] <= 1500
    assert "连接/结构异常" in bp["detail"]
    # 对照：junk 尾段（S6 形态）仍走 unaligned_hq_segment，detail 含「未知序列」
    junk = "".join(rng.choice("ACGT") for _ in range(160))
    blob2 = make_trace_ab1(ref[174:1075] + junk, q=50)
    res2 = analyze([("r.ab1", blob2)], ref, [], circular=False)
    kinds2 = {b["kind"] for b in res2["verdict"]["blocking"]}
    assert "unaligned_hq_segment" in kinds2
    unk = next(b for b in res2["verdict"]["blocking"]
               if b["kind"] == "unaligned_hq_segment")
    assert "未知序列" in unk["detail"]
