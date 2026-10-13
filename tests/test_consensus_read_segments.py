"""拼接结果应由测序 read 拼出，而不是参考序列：
未测位置不进实测片段；read 的 N 调用不被参考碱基悄悄填上；
有插入时导出按共识下标切片。"""
import random

from core.sanger.aligner import align_read
from core.sanger.pipeline import _build_consensus

random.seed(7)
REF = "".join(random.choice("ACGT") for _ in range(400))


def _rr(read: str):
    return {"mean_q": 40, "alignment": align_read(read, REF, [40] * len(read))}


def test_segments_only_cover_read_regions():
    read = REF[50:250]
    cons = _build_consensus(REF, [_rr(read)])
    assert len(cons["sequence"]) == len(REF)          # 坐标对齐的全长共识仍保留
    segs = cons["segments"]
    assert len(segs) == 1
    g = segs[0]
    assert (g["ref_start"], g["ref_end"]) == (51, 250)
    assert g["sequence"] == read                       # 实测片段就是 read 拼出的序列
    assert cons["assembled_length"] == 200


def test_two_reads_give_two_segments():
    cons = _build_consensus(REF, [_rr(REF[0:120]), _rr(REF[250:380])])
    assert [(g["ref_start"], g["ref_end"]) for g in cons["segments"]] == [(1, 120), (251, 380)]
    assert cons["assembled_length"] == 250


def test_n_call_not_filled_with_reference():
    read = list(REF[100:300])
    read[50] = "N"
    cons = _build_consensus(REF, [_rr("".join(read))])
    seg = cons["segments"][0]["sequence"]
    assert seg[50] == "N"                             # 单 read 的 N 不再被参考碱基替代


def test_insertion_segment_slices_by_consensus_index():
    read = REF[100:200] + "GGGG" + REF[200:300]
    cons = _build_consensus(REF, [_rr(read)])
    g = cons["segments"][0]
    assert g["sequence"] == read
    assert cons["sequence"][g["cons_start"] - 1:g["cons_end"]] == read
    assert (g["ref_start"], g["ref_end"]) == (101, 300)
