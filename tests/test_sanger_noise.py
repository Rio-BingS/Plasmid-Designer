"""T13 局部噪声 SNR（goal_sanger_full_fix_v2 T13；A 1.2）

旧 _peak_snr 噪声本底 = 变体通道全轨迹中位数：通道大面积静默（真实文件
T 通道过半采样为 0）时中位 = 0 → SNR None → 置信度分级误当「响亮」。
新口径（core/sanger/noise.py）：本底 = ±10 峰内非调用通道峰窗面积的
max(1, 1.4826×MAD)，全零通道也给出确定 SNR；snr None 视为不确定。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/backend"))

from core.sanger.noise import local_noise, peak_snr  # noqa: E402

BENCH = Path(__file__).parent / "data" / "sanger_bench"


def _flat_trace(n_bases=20, spacing=10, main="A", main_amp=100,
                baseline=0):
    """合成 trace：调用通道主峰、其余通道 baseline（默认全零）"""
    n = n_bases * spacing
    trace = {ch: [baseline] * n for ch in "ACGT"}
    for i in range(n_bases):
        trace[main][i * spacing] = main_amp
    peaks = [i * spacing for i in range(n_bases)]
    bases = "".join(main if i % 4 == 0 else "CGTA"[i % 4] for i in range(n_bases))
    # 保证 bases[i] 与 trace 主通道一致：主通道 = main 时调用都是 main
    bases = "".join(main for _ in range(n_bases))
    return trace, peaks, bases


def test_local_noise_floor_all_zero():
    """非调用通道全零（真实文件 T 通道的常态）：MAD=0 → 本底兜底 1.0"""
    trace, peaks, bases = _flat_trace()
    noise = local_noise(trace, peaks, 5, bases)
    assert noise == 1.0


def test_local_noise_mad_with_crosstalk():
    """非调用通道带两档串扰（C=15、T=5、G=0）：本底 = 1.4826×MAD > 1；
    调用通道（幅 100）被逐峰排除，不抬高本底"""
    trace, peaks, bases = _flat_trace()
    for x in range(len(trace["C"])):
        trace["C"][x] = 15
        trace["T"][x] = 5
    noise = local_noise(trace, peaks, 5, bases)
    # 样本混合分布 {C 窗面积, T 窗面积, 0} → MAD ≈ 档间距离量级，远大于 1
    assert 1.0 < noise < 500
    # 调用通道幅 100 若未排除会显著抬高 MAD：把主通道抬到 1000 复算不变
    for x in range(len(trace["A"])):
        trace["A"][x] = 1000
    assert local_noise(trace, peaks, 5, bases) == noise


def test_peak_snr_real_peak_loud_full_zero_channels():
    """真实替换峰（全零邻通道 + max(1) 本底）：SNR 有值且 ≥10（响亮）"""
    trace, peaks, bases = _flat_trace()
    i = 9
    bases = bases[:i] + "T" + bases[i + 1:]      # 位点 10 调用 T
    trace["G"][i * 10] = 80                       # 替换峰 G
    snr = peak_snr(trace, peaks, 10, "G", bases)
    assert snr is not None and snr >= 10


def test_peak_snr_weak_peak_near_noise():
    """伪影峰（面积与串扰本底同量级）：SNR 落在个位数（<5 阈）"""
    trace, peaks, bases = _flat_trace()
    for x in range(len(trace["C"])):
        trace["C"][x] = 15
        trace["T"][x] = 5
    i = 9
    bases = bases[:i] + "T" + bases[i + 1:]
    for x in range(i * 10 - 2, i * 10 + 3):       # 弱 G 峰，面积 ≈ 串扰档位
        trace["G"][x] = 10
    snr = peak_snr(trace, peaks, 10, "G", bases)
    assert snr is not None and snr < 5


def test_peak_snr_none_cases():
    """read_pos 越界 / 峰缺失 / 通道缺失 → None（不确定，不当响亮）"""
    trace, peaks, bases = _flat_trace()
    assert peak_snr(trace, peaks, 0, "A", bases) is None       # 越界（1-based）
    assert peak_snr(trace, peaks, 21, "A", bases) is None
    assert peak_snr(trace, peaks, 5, "Z", bases) is None       # 无此通道
    peaks2 = list(peaks)
    peaks2[4] = None
    assert peak_snr(trace, peaks2, 5, "A", bases) is None      # 峰缺失
    assert peak_snr({}, [], 1, "A", "") is None


def test_3730_t_channel_snr_not_none():
    """goal 期望：3730.ab1 的 T 通道次峰位点 SNR 非 None（旧口径 T 通道
    全轨迹中位 = 0 → 恒 None）。对所有次级峰在 T 通道的位点断言。"""
    from core.sanger.abif_reader import extract_read
    from core.sanger.mixed_model import _pair_table
    info = extract_read((BENCH / "3730.ab1").read_bytes())
    tr, pks, bases = info["trace"], info["peak_indices"], str(info["bases"])
    table = _pair_table(bases, tr, pks)
    t_sites = [(i + 1, t) for i, t in enumerate(table)
               if t and t[1].get("T", 0) >= 0.25]
    assert t_sites, "基准文件应含 T 通道次峰位点"
    for pos, _t in t_sites:
        snr = peak_snr(tr, pks, pos, "T", bases)
        assert snr is not None, f"T 通道位点 {pos} SNR 不可估"
        assert snr > 0


def test_confidence_snr_none_is_uncertain_not_loud():
    """置信度分级：snr=None 不再当响亮——mp 干净 + Q45 也不给 high"""
    from core.sanger.pipeline import _variant_confidence
    v = {"read_pos": 100, "read_q": 45, "support_reads": 1,
         "type": "substitution", "alt_base": "A"}
    ev_none = {"mutant_pct": 98.0, "snr": None}
    ev_loud = {"mutant_pct": 98.0, "snr": 50.0}
    assert _variant_confidence(v, set(), ev_none) == "medium"   # 不确定 → 压到 medium
    assert _variant_confidence(v, set(), ev_loud) == "high"     # 有值且响亮 → high


def test_reason_code_snr_unavailable():
    """替换证据 snr=None → snr_unavailable；插入证据无 snr 键 → 不误报"""
    from core.sanger.evidence import _variant_reason_codes
    v = {"read_pos": 100, "type": "substitution"}
    codes = _variant_reason_codes(v, {"mutant_pct": 98.0, "snr": None}, None, set())
    assert "snr_unavailable" in codes
    v_ins = {"read_pos": 100, "type": "insertion"}
    codes2 = _variant_reason_codes(v_ins, {"insertion_peak_ratio": 0.8}, None, set())
    assert "snr_unavailable" not in codes2
