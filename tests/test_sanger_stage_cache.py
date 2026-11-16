"""步骤 6：S1 read 阶段缓存（默认关；开启后命中结果与重算一致且互不污染）"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "backend"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from core.sanger import stage_cache  # noqa: E402
from core.sanger.pipeline import analyze  # noqa: E402
from core.sanger import params  # noqa: E402


def _run(ref, blob):
    return analyze([("r1.ab1", blob)], ref, circular=False)


def test_cache_off_by_default(monkeypatch):
    monkeypatch.delenv("SANGER_STAGE_CACHE", raising=False)
    assert stage_cache.read_key("a", b"x", "ACGT", min_q=20) is None


def test_cache_hit_equals_recompute(monkeypatch):
    from test_sequencing_routes import make_ab1
    ref = "ACGTTGCA" * 30
    blob = make_ab1(ref[10:200], [40] * 190)
    monkeypatch.delenv("SANGER_STAGE_CACHE", raising=False)
    base = _run(ref, blob)
    monkeypatch.setenv("SANGER_STAGE_CACHE", "1")
    stage_cache.clear()
    first = _run(ref, blob)
    first["reads"][0]["filename"] = "mutated"   # 下游改动不得污染缓存
    second = _run(ref, blob)
    stage_cache.clear()
    assert second["reads"][0]["filename"] == "r1.ab1"
    assert second["variants"] == base["variants"]
    assert second["consensus"]["coverage_percent"] == base["consensus"]["coverage_percent"]


def test_params_grouped():
    for names in params.STAGE_PARAMS.values():
        for n in names:
            assert hasattr(params, n)


def test_hard_blocks_explicit():
    """步骤 7：合并压缩、互检矛盾、染料团为显式硬阻断——即使 p_real 高也不 CONFIRMED"""
    from core.sanger.sitecall import HARD_BLOCKS, call_variant, UNRESOLVED
    assert {"UNR_MERGED_ZONE", "UNR_CROSS_CONFLICT", "UNR_DYE_BLOB",
            "UNR_OVERLAP_DISCORDANCE", "UNR_IN_MIXED_TEMPLATE"} <= HARD_BLOCKS
    for flag, code in (("merged_zone_artifact", "UNR_MERGED_ZONE"),
                       ("cross_read_conflict", "UNR_CROSS_CONFLICT"),
                       ("dye_blob", "UNR_DYE_BLOB")):
        sc = call_variant({"ref_pos": 10, "type": "substitution", "alt": "A",
                           "p_real": 0.999, "support_reads": 2, flag: True})
        assert sc["state"] == UNRESOLVED and sc["primary_reason"] == code, sc
