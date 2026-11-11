"""v3.5：单通道染料团检测"""
import math

from core.sanger.artifacts import (
    BLOB1_MAX_REGIONS, detect_dye_blob_single, dye_blob_at,
)

SP = 12  # 每碱基 12 个采样点（真实 3730 量级）


def _trace(bases, humps=()):
    """逐碱基高斯峰（sigma=2.5，FWHM≈0.5 间距）；humps=(通道, 中心碱基序号, 碱基宽, 相对高)"""
    n = len(bases) * SP + SP
    tr = {c: [0] * n for c in "ACGT"}
    peaks = []
    for i, b in enumerate(bases):
        c0 = SP // 2 + i * SP
        peaks.append(c0)
        for x in range(max(0, c0 - 10), min(n, c0 + 11)):
            tr[b][x] += int(600 * math.exp(-((x - c0) ** 2) / (2 * 2.5 ** 2)))
    for ch, ci, wb, rel in humps:
        c0 = SP // 2 + ci * SP
        sig = wb * SP / 2.355
        for x in range(n):
            tr[ch][x] += int(600 * rel * math.exp(-((x - c0) ** 2) / (2 * sig ** 2)))
    return tr, peaks


BASES = ("ACGGATCCAGTACGATGCAAGCTAGCATGCCAGGTACGATCGAGCAAGCCGATAGCAGC" * 5)[:240]


def test_single_channel_broad_hump_detected():
    # 宽 T 鼓包压在 G/A 碱基峰下（shasgg T7 read 第 ~74 位形态）
    tr, pk = _trace(BASES, humps=[("T", 72, 2.5, 0.4)])
    out = detect_dye_blob_single(tr, pk, BASES)
    hit = dye_blob_at(out, 73)
    assert hit and hit["channel"] == "T"
    assert hit["width_spacings"] >= 1.4


def test_clean_trace_no_blob():
    tr, pk = _trace(BASES)
    assert detect_dye_blob_single(tr, pk, BASES) == []


def test_called_homopolymer_not_blob():
    b = BASES[:70] + "TTTTTT" + BASES[76:]
    tr, pk = _trace(b)
    assert dye_blob_at(detect_dye_blob_single(tr, pk, b), 73) is None


def test_widespread_background_not_blob():
    b = ("ACGGACGCAGCAGCGAGCAAGCAGCAGG" * 9)[:240]   # 无 T，鼓包都不与调用碱基重合
    humps = [("T", 20 + 35 * k, 2.5, 0.3) for k in range(BLOB1_MAX_REGIONS + 2)]
    tr, pk = _trace(b, humps=humps)
    assert len(detect_dye_blob_single(tr, pk, b[:0] + b)) == 0
    one, pk1 = _trace(b, humps=humps[:1])
    assert len(detect_dye_blob_single(one, pk1, b)) == 1


def test_synthetic_one_sample_per_base_skipped():
    tr = {c: [0] * len(BASES) for c in "ACGT"}
    for i, b in enumerate(BASES):
        tr[b][i] = 500
    assert detect_dye_blob_single(tr, list(range(len(BASES))), BASES) == []

