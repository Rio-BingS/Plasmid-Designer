"""二期 10d：poly 大量缺失报警（用户 2026-11-17 拍板）。

两条阈值「或」：确证缺失 ≥count 个，或占该 poly 参考长度 ≥pct%；各自可
停用；默认 5 个 / 15%；优先级 默认 < 环境变量 < 请求参数；所用阈值随
verdict 下发；重新判定时正文的大量缺失行随阈值刷新。
"""
import pytest

from core.sanger.judge import rejudge
from core.sanger.params import poly_large_deletion, resolve_poly_thresholds
from core.sanger.render_poly import LARGE_DEL_PREFIX
from core.sanger.verdict import build_verdict

DEF = {"count": 5, "pct": 15.0, "count_enabled": True, "pct_enabled": True}


def _pc(ref_len, measured, state="COUNT_CONFIRMED", start=101, reasons=None):
    return {"poly_id": f"A@{start}-{start + ref_len - 1}", "state": state,
            "tier": "poly", "base": "A", "unit": "A", "period": 1,
            "ref_start": start, "ref_end": start + ref_len - 1,
            "ref_len": ref_len, "measured": measured,
            "delta": measured - ref_len, "reasons": reasons or []}


def _result(pcs):
    return {"variants": [], "reads": [{"filename": "r.ab1"}],
            "coverage": {"verified_ranges": [[1, 400]]},
            "consensus": {"covered_ranges": [[1, 400]]},
            "poly_calls": pcs, "features": []}


@pytest.mark.parametrize("ref_len,measured,hit", [
    (20, 17, True),    # 3 个 = 15% → 百分比触发
    (100, 95, True),   # 5 个 = 5% → 个数触发
    (100, 97, False),  # 3 个 = 3% → 不报
    (30, 30, False),   # 完整
])
def test_or_rule_defaults(ref_len, measured, hit):
    assert poly_large_deletion(ref_len - measured, ref_len, DEF) is hit


def test_disable_one_condition():
    only_pct = {**DEF, "count_enabled": False}
    assert poly_large_deletion(5, 100, only_pct) is False   # 5%<15%
    only_cnt = {**DEF, "pct_enabled": False}
    assert poly_large_deletion(3, 20, only_cnt) is False    # 3<5
    none_ = {**DEF, "count_enabled": False, "pct_enabled": False}
    assert poly_large_deletion(50, 60, none_) is False


def test_priority_default_env_request(monkeypatch):
    assert resolve_poly_thresholds() == DEF
    monkeypatch.setenv("SANGER_POLY_LARGE_DEL_COUNT", "8")
    assert resolve_poly_thresholds()["count"] == 8
    assert resolve_poly_thresholds({"count": 3})["count"] == 3
    assert resolve_poly_thresholds({"pct_enabled": False})["pct_enabled"] is False
    assert resolve_poly_thresholds({"count": "bad"})["count"] == 8


def test_verdict_blocks_and_records_thresholds():
    v = build_verdict(_result([_pc(107, 68)]), 400)
    assert v["status"] == "fail"
    b = [x for x in v["blocking"] if x["kind"] == "poly_large_deletion"]
    assert len(b) == 1 and b[0]["missing"] == 39
    assert v["poly_thresholds"] == DEF


def test_not_confirmed_or_partial_does_not_block():
    for pc in (_pc(107, 68, state="DEFICIT_SUSPECTED"),
               _pc(107, 68, reasons=["PARTIAL_COVERAGE"]),
               _pc(100, 97)):
        v = build_verdict(_result([pc]), 400)
        assert not any(x["kind"] == "poly_large_deletion" for x in v["blocking"])


def test_request_override_disables_alarm():
    v = build_verdict(_result([_pc(100, 94)]), 400,
                      poly_thresholds={"count_enabled": False})
    assert not any(x["kind"] == "poly_large_deletion" for x in v["blocking"])


def test_rejudge_refreshes_body_line():
    rec = _result([_pc(107, 68)])
    v, conc = rejudge({**rec, "conclusion": "旧头行\n正文"}, 400)
    assert LARGE_DEL_PREFIX in conc and conc.split("\n")[2] == "正文"
    rec2 = {**rec, "conclusion": conc}
    v2, conc2 = rejudge(rec2, 400, poly_thresholds={"count_enabled": False,
                                                    "pct_enabled": False})
    assert LARGE_DEL_PREFIX not in conc2 and v2["status"] != "fail"
    assert conc2.split("\n")[1:] == ["正文"]
