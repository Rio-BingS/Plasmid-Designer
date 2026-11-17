"""二期 h/i/j/k + 统一酶库（用户 2026-11-18 00:03 拍板）"""
import os

os.environ.setdefault("SECRET_KEY", "t")


def _variant(reliable=True):
    return {"type": "deletion", "ref_pos": 105, "length": 1,
            "homopolymer": {"base": "A", "unit": "A", "start": 100, "end": 111,
                            "ref_repeat_count": 12, "observed_repeat_count": 11,
                            "count_reliable": reliable, "peak_count": None}}


def _pc(state="COUNT_CONFIRMED", observed=11):
    return {"base": "A", "unit": "A", "ref_start": 100, "ref_end": 111,
            "state": state, "observed": observed, "per_read": [], "tier": "run"}


def test_h_repeat_line_reads_polycall(monkeypatch):
    from core.sanger.render_poly import repeat_variant_lines
    monkeypatch.delenv("SANGER_REPEAT_FROM_POLYCALL", raising=False)
    # 变体级说可靠，PolyCall 判未决 → 以 PolyCall 为准
    line = repeat_variant_lines([_variant(True)], [_pc("CONFLICTING", 11)])
    if line:  # UNRESOLVED 若已出峰图判读行则去重，不出变体行
        assert "可能不准" in line[0]
    line = repeat_variant_lines([_variant(False)], [_pc("COUNT_CONFIRMED", 10)])
    assert "重复数计数可靠" in line[0] and "测得 10 个" in line[0]


def test_h_switch_off_legacy(monkeypatch):
    from core.sanger.render_poly import repeat_variant_lines
    monkeypatch.setenv("SANGER_REPEAT_FROM_POLYCALL", "0")
    line = repeat_variant_lines([_variant(False)], [_pc("COUNT_CONFIRMED", 10)])
    assert "可能不准" in line[0] and "测得 11 个" in line[0]


def test_i_vstatus_unified_with_site_status():
    from core.sanger.evidence import _vstatus_of
    from core.sanger.sitecall import site_status
    for v in ({}, {"confidence": None}, {"confidence": "low"},
              {"confidence": "high"}, {"cross_read_resolved": True},
              {"status": "unresolved", "confidence": "high"}):
        assert _vstatus_of(v) == site_status(v)


def test_k_confidence_pass_is_pure(monkeypatch):
    import core.sanger.pipeline as p
    seen = {}

    def fake(vs, *a, **k):
        for v in vs:
            v["confidence"] = "low"
        seen["n"] = len(vs)
    monkeypatch.setattr(p, "_annotate_confidence", fake)
    orig = [{"type": "substitution", "ref_pos": 3}]
    out = p._variant_confidence_pass(orig, [], False)
    assert seen["n"] == 1 and out[0]["confidence"] == "low"
    assert "confidence" not in orig[0]


def test_j_excel_mixed_count_cells():
    from app.sequencing_report import _SCOPE_COL_TITLES, _scope_cells
    assert _SCOPE_COL_TITLES[-2:] == ["套峰 read 数", "零散双峰数"]
    rec = {"verdict": {"scope": {}},
           "read_calls": [{"filename": "a.ab1", "state": "MIXED_TEMPLATE"},
                          {"filename": "b.ab1", "state": "CLEAN"}],
           "mixed_profiles": {"a.ab1": {"count": 40}, "b.ab1": {"count": 3}}}
    cells = _scope_cells(rec)
    assert len(cells) == len(_SCOPE_COL_TITLES)
    assert cells[-2:] == ("1", "3")
    assert _scope_cells({})[-2:] == ("", "")


def test_unified_enzyme_single_cut_matches_map():
    from core.plasmid_map.enzymes import scan_enzyme
    from core.sanger.design_enzymes import build_design_enzymes
    ref = ("AT" * 50) + "GAATTC" + ("CG" * 60) + "GGATCC" + ("TA" * 50) + "GGATCC" + "AT" * 20
    d = build_design_enzymes(ref, circular=True)
    names = {e["name"] for e in d["enzymes"]}
    assert "EcoRI" in names and "BamHI" not in names
    assert scan_enzyme(ref.upper(), "EcoRI", True)["cut_count"] == 1
