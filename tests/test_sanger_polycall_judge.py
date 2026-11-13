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
from core.sanger.homopolymer import peak_verdict_phrase, _poly_anchor_notes


def test_render_phrase_matches_legacy():
    e = _entry([_rd("f", "full", [101, 130], 28, 28, "resolvable"),
                _rd("r", "full", [101, 130], 30, 30, "resolvable", "-")])
    call = pc.poly_call(e)
    assert rp.phrase(call) == peak_verdict_phrase(e)
    assert rp.warning_lines([call])[0].startswith("注意：poly(A) 同聚物 101-130 峰图判读：")
    assert len(rp.detail_lines([call])) == 1
    assert rp.detail_keys([call]) == {("A", "A", 101)}


def test_render_anchor_notes_match_legacy():
    reads = [_rd("f", "full", [101, 130], 30, 30, "resolvable")]
    reads[0]["anchor_grade"] = "unreliable"
    e = _entry(reads)
    call = pc.poly_call(e)
    assert rp.anchor_notes([call]) == _poly_anchor_notes([e])
    assert rp.confirmed_keys([call]) == {("A", 101)}
