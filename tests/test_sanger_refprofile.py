"""S0a/S0b：参考画像纯函数与 resolve_scope 唯一入口"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "backend"))

from core.sanger.refprofile import build_reference_profile  # noqa: E402
from core.sanger.homopolymer import find_repeat_runs  # noqa: E402
from core.sanger.scope import Scope, coerce_scope, parse_scope_cell, resolve_scope  # noqa: E402

FEATS = [{"name": "GFP", "type": "CDS", "start": 11, "end": 40, "strand": "+"}]


def test_profile_is_pure_and_normalizes():
    seq = "acgu" + "A" * 12 + "CGTACGTACG" * 5
    prof = build_reference_profile(seq, FEATS, circular=True, scope="GFP")
    assert prof["ref"] == seq.upper().replace("U", "T")
    assert prof["length"] == len(seq) and prof["circular"] is True
    assert prof["poly_runs"] == find_repeat_runs(prof["ref"])
    assert isinstance(prof["scope"], Scope)
    assert prof["scope"] == coerce_scope("GFP", FEATS, len(seq))
    assert build_reference_profile(seq, FEATS)["scope"] is None


def test_resolve_scope_matches_legacy_entries():
    for text in ("GFP", "11-40", "foo-bar-unknown"):
        assert resolve_scope(text, FEATS, 100, normalize=False) == parse_scope_cell(text, FEATS, 100)
        assert resolve_scope(text, FEATS, 100)[0] == coerce_scope(text, FEATS, 100)
    assert resolve_scope(None, FEATS, 100) == (None, None)
