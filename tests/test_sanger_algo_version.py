"""二期开关 f：SANGER_ALGO_VERSION 组合化（默认关）"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "backend"))

from core.sanger import algo_version as av  # noqa: E402
from core.sanger.pipeline import SANGER_ALGO_VERSION  # noqa: E402


def test_default_off_returns_base(monkeypatch):
    # 用户 2026-11-18：组合版本号默认关，显式关同样回基础版本号
    for k in av.SWITCHES:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SANGER_ALGO_VERSION_COMPOSITE", "0")
    assert av.compose(SANGER_ALGO_VERSION) == SANGER_ALGO_VERSION
    assert av.extra_fields(SANGER_ALGO_VERSION) == {}


def test_composite_lists_stage_versions_and_non_default_switches(monkeypatch):
    for k in av.SWITCHES:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SANGER_ALGO_VERSION_COMPOSITE", "1")
    s = av.compose("v17")
    assert s.startswith("v17+") and "+" not in s[4:].split(".")[-1]
    # 开关取非默认值时才列出（pmin 默认已为 1，取 0 才出现）
    monkeypatch.setenv("SANGER_POLY_MISSING_MIN", "0")
    monkeypatch.setenv("SANGER_READ_MIXED_CALL", "0")
    assert av.compose("v17").endswith("+pmin=0,rmix=0")
    assert av.extra_fields("v17")["algo_versions"]["switches"] == {"pmin": "0", "rmix": "0"}
