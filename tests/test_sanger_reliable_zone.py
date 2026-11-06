"""read 可信区：信号驱动（goal T12；A 1.1）——20bp 滑窗 mean Q/峰距 CV/
半高宽比/次级占比，满足即可信；700bp 硬顶取消，tail_hard 仅无峰图兜底。
front_hard 硬区保留；期望值为 T12 新口径（窗口粒度 ±20bp）"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "backend"))

from core.sanger.reliable_zone import (  # noqa: E402
    ZoneParams, compute_reliable_zone, in_zone, zone_reason, zone_text,
)


def _trace(n: int, width_of=lambda i: 4, spacing=12):
    """合成单通道 trace：第 i 个峰半高宽约 width_of(i)"""
    L = n * spacing + 40
    sig = [0] * L
    peaks = []
    for i in range(n):
        c = 20 + i * spacing
        peaks.append(c)
        hw = max(1, width_of(i) // 2)
        for d in range(-hw * 2, hw * 2 + 1):
            if 0 <= c + d < L:
                h = 1000 if abs(d) <= hw else 300
                sig[c + d] = max(sig[c + d], h)
    return {"A": sig, "C": [0] * L, "G": [0] * L, "T": [0] * L}, peaks


def test_good_read_front_starts_right_after_hard_zone():
    # T12：无峰图（Q-only 窗）时 tail_hard 兜底——800bp 好读全可信，
    # 700bp 硬顶取消（旧口径 raw_end=700）
    z = compute_reliable_zone(800, 0, [40] * 800)
    assert (z["raw_start"], z["raw_end"]) == (31, 800)
    assert in_zone(z, 31) and not in_zone(z, 30)
    z2 = compute_reliable_zone(2000, 0, [40] * 2000)
    assert z2["raw_end"] == 900 and "tail_hard" in z2["tail_reason"]


def test_front_gray_low_q_pushes_start_inward():
    q = [40] * 800
    for i in range(30, 45):            # raw 31-45 Q15
        q[i] = 15
    z = compute_reliable_zone(800, 0, q)
    assert z["raw_start"] == 46
    assert "信号未稳定" in z["front_reason"]


def test_front_gray_never_stable_falls_back_to_gray_end():
    # T12：起点碱基本身须 Q≥20——raw 31–70 全 Q12 → 首个达标起点 71
    # （旧口径回退灰区末端 61）
    q = [40] * 800
    for i in range(30, 70):
        q[i] = 12
    z = compute_reliable_zone(800, 0, q)
    assert z["raw_start"] == 71
    assert "信号未稳定" in z["front_reason"]


def test_front_gray_broad_peaks_rejected_even_with_high_q():
    n = 800
    trace, peaks = _trace(n, width_of=lambda i: 12 if i < 50 else 4)
    z = compute_reliable_zone(n, 0, [40] * n, trace, peaks)
    assert z["raw_start"] > 50
    assert "峰宽" in z["front_reason"]


def test_mid_signal_decay_truncates_early():
    # T12：raw 401 起 Q12 → 最后一个可信窗覆盖到 410（20bp 窗粒度，
    # 窗均值滞后于衰减点与旧口径一致）；文案改「信号未达可信标准」
    q = [40] * 800
    for i in range(400, 800):
        q[i] = 12
    z = compute_reliable_zone(800, 0, q)
    assert z["raw_end"] == 410
    assert "信号未达可信标准" in z["tail_reason"]


def test_trim_offsets_and_short_read():
    # 质量修剪去掉前 40bp：可信区从修剪后第一个碱基起（原始 41），且不超过修剪终点
    z = compute_reliable_zone(500, 40, [40] * 400)
    assert (z["raw_start"], z["raw_end"]) == (41, 440)
    assert (z["start"], z["end"]) == (1, 400)
    assert "质量修剪" in z["tail_reason"]


def test_reasons_by_zone():
    z = compute_reliable_zone(1000, 0, [40] * 1000)
    assert "硬不可信区" in zone_reason(z, 10)
    # tail_gray/tail_hard 保留为 zone_reason 文案参照（T12：区界由信号定）
    assert "尾端灰区 701–900bp" in zone_reason(z, 750)
    assert "900bp 之后" in zone_reason(z, 950)
    assert zone_text(z).startswith("可信区 31–900bp")


def test_params_adjustable_and_validated():
    z = compute_reliable_zone(1000, 0, [40] * 1000, params=ZoneParams(20, 40, 800, 950))
    assert (z["raw_start"], z["raw_end"]) == (21, 950)  # 无峰图：tail_hard 兜底
    assert ZoneParams.parse("") == ZoneParams()
    assert ZoneParams.parse("10，20,600,800") == ZoneParams(10, 20, 600, 800)
    for bad in ("1,2,3", "60,30,700,900", "30,60,900,700", "a,b,c,d", "30,60,700,9000"):
        with pytest.raises(ValueError):
            ZoneParams.parse(bad)
