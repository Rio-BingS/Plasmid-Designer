"""二期 ReadCall 套峰判定单元测试（readcall.py）"""
import importlib

import pytest

from core.sanger import readcall as rc


def _dense(a, b, every=3):
    return list(range(a, b + 1, every))


def test_ok_when_sparse():
    res = rc.classify_read_mixing([50, 120, 300, 420, 560], {"start": 20, "end": 700}, 800)
    assert res["state"] == rc.OK and res["segments"] == []


def test_tail_decay_not_overlap():
    # 尾端 86bp 内 11 处（AcGFP 形态）达不到段密度/位点数门槛
    pos = [483, 490, 497, 505, 512, 520, 530, 540, 550, 560, 568]
    assert rc.classify_read_mixing(pos, {"start": 18, "end": 572}, 790)["state"] == rc.OK


def test_mixed_template():
    res = rc.classify_read_mixing(_dense(30, 350), {"start": 16, "end": 355}, 355)
    assert res["state"] == rc.MIXED_TEMPLATE
    assert rc.in_mixed_segment(res, 352)  # 全程套峰：段外也不予确证


def test_shift_after_point():
    pos = [40, 90] + _dense(209, 413, 4)
    res = rc.classify_read_mixing(pos, {"start": 6, "end": 420}, 649)
    assert res["state"] == rc.SHIFT_AFTER_POINT and res["breakpoint"] == 209
    assert rc.in_mixed_segment(res, 418) and not rc.in_mixed_segment(res, 150)


def test_local_mixed():
    res = rc.classify_read_mixing(_dense(60, 160, 2), {"start": 20, "end": 900}, 950)
    assert res["state"] == rc.LOCAL_MIXED
    assert rc.in_mixed_segment(res, 100) and not rc.in_mixed_segment(res, 500)


def test_switch_off(monkeypatch):
    monkeypatch.setenv("SANGER_READ_MIXED_CALL", "0")
    res = rc.classify_read_mixing(_dense(30, 350), {"start": 16, "end": 355}, 355)
    assert res["state"] == rc.OK


def _r(fn, state, spans):
    return {"filename": fn, "read_mixing": {"state": state, "ref_spans": spans}}


def test_corroborated_needs_two_reads_same_region():
    a = _r("a.ab1", rc.MIXED_TEMPLATE, [[100, 400]])
    b = _r("b.ab1", rc.MIXED_TEMPLATE, [[350, 600]])
    c = _r("c.ab1", rc.SHIFT_AFTER_POINT, [[2000, 2300]])
    assert set(rc.corroborated([a, b, c])) == {"a.ab1", "b.ab1"}
    assert rc.corroborated([c]) == []
