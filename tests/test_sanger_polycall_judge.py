"""S4 PolyCall 与 S6 唯一判定入口（judge.py）单元测试"""

from core.sanger import polycall as pc
from core.sanger.judge import compose_conclusion, conclusion_body, rejudge


def _rd(fn, cov, span, called, peaks, res, d="+"):
    return {"filename": fn, "direction": d, "coverage": cov, "covered_span": span,
            "called_count": called, "peak_count": peaks, "resolution": res,
            "length_estimate": None, "length_method": None, "length_ci": None,
            "anchor_grade": "reliable"}


RUN = {"base": "A", "unit": "A", "period": 1, "tier": "poly", "lus": 30,
       "start": 101, "end": 130, "length": 30, "repeat_count": 30}


def _entry(reads):
    e = {**{k: RUN[k] for k in ("base", "unit", "period", "tier", "lus",
                                "start", "end", "length")},
         "ref_repeat_count": 30, "observed_repeat_count": 30,
         "count_reliable": True, "variant": None, "read_counts": reads}
    e.update(pc.aggregate_run(RUN, reads))
    return e


def test_aggregate_is_pure_and_accepts_consistent_reads():
    reads = [_rd("f", "full", [101, 130], 28, 28, "resolvable"),
             _rd("r", "full", [101, 130], 28, 28, "resolvable", "-")]
    snap = [dict(x) for x in reads]
    e = _entry(reads)
    assert reads == snap
    assert e["run_verdict"] == "accepted" and e["observed_repeat_count"] == 28
    call = pc.poly_call(e)
    assert call["state"] == pc.COUNT_CONFIRMED
    assert call["measured"] == 28 and call["delta"] == -2
    assert call["covered_ranges"] == [[101, 130]] and call["reasons"] == []


def test_conflicting_and_unresolved_states():
    e = _entry([_rd("f", "full", [101, 130], 28, 28, "resolvable"),
                _rd("r", "full", [101, 130], 30, 30, "resolvable", "-")])
    call = pc.poly_call(e)
    assert call["state"] == pc.CONFLICTING
    assert pc.unresolved_polys([call]) == [call]
    assert pc.LEGACY_VERDICT[call["state"]] == "contradictory"


def test_partial_coverage_reason_and_not_covered():
    e = _entry([_rd("f", "partial", [101, 115], 15, 15, "resolvable")])
    e["run_covered"] = 15
    assert "PARTIAL_COVERAGE" in pc.poly_call(e)["reasons"]
    other = {**RUN, "start": 900, "end": 925, "length": 26, "repeat_count": 26}
    calls = pc.build_poly_calls([RUN, other], [e])
    assert [c["state"] for c in calls][1] == pc.NOT_COVERED
    assert calls[1]["per_read"] == [] and calls[1]["ref_start"] == 900


def test_compose_and_body():
    assert compose_conclusion("H", ["a", "b"]) == "H\na\nb"
    assert conclusion_body({"conclusion": "H\na", "reads": [{}]}) == ["a"]
    # 没有 read 的记录本无头行：不吞正文首行
    assert conclusion_body({"conclusion": "a\nb", "reads": []}) == ["a", "b"]


def test_rejudge_keeps_body_and_replaces_headline(monkeypatch):
    import core.sanger.judge as j
    monkeypatch.setattr(j, "build_verdict",
                        lambda *a, **k: {"headline": "NEW", "status": "pass"})
    rec = {"conclusion": "OLD\nline1\nline2", "reads": [{"filename": "x"}]}
    v, c = rejudge(rec, 1000)
    assert v["headline"] == "NEW" and c == "NEW\nline1\nline2"
    assert rec["conclusion"] == "OLD\nline1\nline2"  # 不改 record
    v, c = rejudge({"conclusion": "only", "reads": []}, 1000)
    assert c == "NEW\nonly"


# ---- S7 呈现：render_poly 只读 PolyCall，措辞与旧口径一致 ----
from core.sanger import render_poly as rp


def test_render_phrase_matches_legacy():
    e = _entry([_rd("f", "full", [101, 130], 28, 28, "resolvable"),
                _rd("r", "full", [101, 130], 30, 30, "resolvable", "-")])
    call = pc.poly_call(e)
    assert rp.phrase(call) == "缺失 2 个 A：实测 28 个，参考 30 个"
    assert rp.warning_lines([call])[0].startswith("注意：poly(A) 同聚物 101-130 峰图判读：")
    assert len(rp.detail_lines([call])) == 1
    assert rp.detail_keys([call]) == {("A", "A", 101)}


def test_render_anchor_notes_match_legacy():
    reads = [_rd("f", "full", [101, 130], 30, 30, "resolvable")]
    reads[0]["anchor_grade"] = "unreliable"
    e = _entry(reads)
    call = pc.poly_call(e)
    assert rp.anchor_notes([call]) == [
        "注意：poly(A) 同聚物 101-130 重复数已由峰图计数确证（30 个），但该 run的锚定路标不可靠"
        "（路标落在信号异常区）——计数确证但定位存疑，run 起止可能整体偏移数 bp，建议对照图谱核对边界"]
    assert rp.confirmed_keys([call]) == {("A", 101)}


def test_missing_min_switch_default_off(monkeypatch):
    """二期开关 b：完整覆盖 read 之间缺失取 min；默认关为旧口径 max"""
    reads = [_rd("f", "full", [101, 130], 25, 25, "partial_merge"),
             _rd("r", "full", [101, 130], 10, 10, "partial_merge", "-"),
             _rd("p", "partial", [101, 115], 5, 5, "partial_merge")]
    monkeypatch.delenv("SANGER_POLY_MISSING_MIN", raising=False)
    e = _entry([dict(x) for x in reads])
    assert e["peak_missing"] == 20 and e["peak_measured"] == 10
    monkeypatch.setenv("SANGER_POLY_MISSING_MIN", "1")
    e = _entry([dict(x) for x in reads])
    assert e["peak_missing"] == 5 and e["peak_measured"] == 25
    # 无完整覆盖 read：部分覆盖分段之间仍取 max
    parts = [_rd("a", "partial", [101, 115], 12, 12, "partial_merge"),
             _rd("b", "partial", [116, 130], 8, 8, "partial_merge", "-")]
    e = _entry(parts)
    assert e["peak_missing"] == 7
    # noisy read（峰数>调用数）峰数多算，不参与 min（DEMO1 A107 形态）
    e = _entry([_rd("n", "full", [101, 130], 20, 29, "noisy"),
                _rd("g", "full", [101, 130], 18, 18, "resolvable", "-")])
    assert e["peak_missing"] == 12 and e["peak_measured"] == 18
