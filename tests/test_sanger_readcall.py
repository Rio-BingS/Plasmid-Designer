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


# ---- S4 第 2 步：ReadCall 阶段与下游接口（解耦） ----

def _calls(*items):
    return rc.ReadCalls(rc.ReadCall.from_dict(fn, d) for fn, d in items)


def test_readcalls_blocking_hit_needs_every_supporting_read():
    mixed = rc.classify_read_mixing(_dense(30, 350), {"start": 16, "end": 355}, 355)
    calls = _calls(("a.ab1", mixed), ("b.ab1", {"state": rc.OK}))
    hit = calls.blocking_hit([{"read": "a.ab1", "read_pos": 100}])
    assert hit == {"read": "a.ab1", "state": rc.MIXED_TEMPLATE, "breakpoint": None}
    # 另一条 read 在干净区支持 → 不阻断
    assert calls.blocking_hit([{"read": "a.ab1", "read_pos": 100},
                               {"read": "b.ab1", "read_pos": 100}]) is None
    assert calls.blocking_hit([]) is None
    assert not calls.covers("unknown.ab1", 100)


def test_build_read_calls_is_pure_and_mirrors_legacy():
    r = {"filename": "a.ab1", "mixed_positions": list(_dense(30, 350)),
         "reliable_zone": {"start": 16, "end": 355}, "trimmed_bases": "A" * 355}
    before = dict(r)
    calls = rc.build_read_calls([r])
    assert r == before  # 不改输入
    c = calls.get("a.ab1")
    assert c.state == rc.MIXED_TEMPLATE and c.ref_spans == []  # 无比对 → 无参考区段
    assert c.legacy()["ref_spans"] == []
    assert not c.corroborated


def test_readcalls_from_result_fallback_recomputes_corroboration():
    res = {"reads": [_r("a.ab1", rc.MIXED_TEMPLATE, [[100, 400]]),
                     _r("b.ab1", rc.SHIFT_AFTER_POINT, [[350, 600]]),
                     {"filename": "c.ab1"}]}
    calls = rc.ReadCalls.from_result(res)
    assert [c.filename for c in calls.template_reads()] == ["a.ab1", "b.ab1"]
    assert all(c.corroborated for c in calls.template_reads())
    again = rc.ReadCalls.from_result({"read_calls": calls.to_list()})
    assert [c.to_dict() for c in again] == calls.to_list()


def test_decide_variant_blocks_in_mixed_template():
    from core.sanger.sitecall import decide_variant
    mixed = rc.classify_read_mixing(_dense(30, 350), {"start": 16, "end": 355}, 355)
    calls = _calls(("a.ab1", mixed))
    v = {"type": "substitution", "ref_pos": 10, "alt_base": "T", "read": "a.ab1",
         "confidence": "high", "support_reads": 1,
         "evidence_by_read": [{"read": "a.ab1", "read_pos": 100}]}
    sc = decide_variant(v, None, calls)
    assert v["in_mixed_template"]["state"] == rc.MIXED_TEMPLATE
    assert v["status"] != "confirmed" and v["confidence"] == "low"
    assert sc["state"] == "UNRESOLVED"
    assert "UNR_IN_MIXED_TEMPLATE" in sc["reasons"]


def test_collect_mixed_variants_skip_is_injected():
    import inspect
    from core.sanger import mixed_call
    assert "skip" in inspect.signature(mixed_call.collect_mixed_variants).parameters
    assert "readcall" not in inspect.getsource(mixed_call)
