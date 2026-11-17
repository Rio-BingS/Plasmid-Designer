"""二期开关 c：AF<0.15 双峰只在声明范围内进 verdict（SANGER_LOW_AF_SCOPE_ONLY，默认关）"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "backend"))

from core.sanger.verdict import build_verdict  # noqa: E402


def _res():
    mv = [{"ref_pos": 150, "af": 0.13, "called_base": "A", "secondary_base": "G",
           "ref_base": "A", "reads": ["a.ab1"]},
          {"ref_pos": 800, "af": 0.12, "called_base": "C", "secondary_base": "T",
           "ref_base": "C", "reads": ["a.ab1"]}]
    return {"variants": [], "mixed_variants": mv, "reads": [{"filename": "a.ab1"}],
            "features": [], "coverage": {"verified_ranges": [[1, 1000]]},
            "consensus": {"covered_ranges": [[1, 1000]]}}


def _mv_info(v):
    return sorted(i.get("detail", "")[:4] for i in v["info"] if i["kind"] == "mixed_variant")


def test_low_af_scope_only(monkeypatch):
    monkeypatch.setenv("SANGER_LOW_AF_SCOPE_ONLY", "0")
    off = build_verdict(_res(), 1000, scope="100-300")
    assert len(_mv_info(off)) == 2
    monkeypatch.setenv("SANGER_LOW_AF_SCOPE_ONLY", "1")
    on = build_verdict(_res(), 1000, scope="100-300")
    assert _mv_info(on) == ["150 "]
    assert on["status"] == off["status"]
    # 未声明范围：AF<0.15 一律不进 verdict
    assert _mv_info(build_verdict(_res(), 1000)) == []
