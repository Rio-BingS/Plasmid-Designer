"""S7 第 3 片：双峰结论行——判定（crosscheck.mixed_summary）与文案（render_mixed）分离"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "backend"))

from core.sanger.crosscheck import mixed_summary  # noqa: E402
from core.sanger import render_mixed  # noqa: E402


def _r(fn, cls, count, mf=0.2, stretch=0, span=None, pos=(10, 11)):
    return {"filename": fn,
            "mixed_profile": {"class": cls, "count": count, "minor_fraction": mf,
                              "longest_stretch": stretch, "span": span,
                              "positions": list(pos)},
            "reliable_zone": {"start": 30, "end": 700, "raw_start": 25, "raw_end": 700}}


def test_single_read_widespread_true_mixed():
    rr = [_r("a.ab1", "widespread", 12, mf=0.3, stretch=9, span=[120, 140])]
    ms = mixed_summary(rr, {"by_read": {}, "total": 0, "multi_sites": []})
    assert ms["crossable"] is False and ms["wide"][0]["stretch"]
    lines = render_mixed.read_lines(ms)
    assert lines[0] == ("⚠ 疑似混合样品：a.ab1 检出 12 处双峰位点（估计次要克隆占比约 30%），"
                        "主峰序列按多数碱基判读——建议重新挑单克隆划线培养后复测"
                        "；自位置 120 起存在连续双峰段（两个克隆相差插入/缺失时的典型形态）")
    assert lines[1].startswith("  ↳ a.ab1：双峰 12 处，位于 read ")
    assert lines[1].endswith("（read 坐标；可信区 30–700（原始 25–700） 以外未计入）")
    assert render_mixed.signal_lines(ms) == []


def test_noise_like_and_multi_sites():
    rr = [_r("a.ab1", "widespread", 6, mf=0.2), _r("b.ab1", "none", 0)]
    corr = {"by_read": {"a.ab1": {"multi": 0, "clean": 4, "uncovered": 0, "uncovered_ref": []}},
            "total": 6, "multi_sites": [(101, ["a", "b"]), (205, ["a", "b"])]}
    ms = mixed_summary(rr, corr)
    assert ms["wide"][0]["noise_like"] is True
    assert ms["pending"] == []          # 可互检且无待核位点 → 不单列子行
    line = render_mixed.read_lines(ms)[0]
    assert line.startswith("⚠ a.ab1 检出 6 处双峰位点（估计次要克隆占比约 20%）——跨引物互检")
    assert render_mixed.signal_lines(ms) == [
        "⚠ 2 处参考位点被多条引物同报双峰（参考位置 101、205，倾向真实混合）——建议重新挑单克隆复测"]


def test_multi_wide_aggregate():
    rr = [_r("a.ab1", "widespread", 8, mf=0.2, stretch=10, span=[5, 20]),
          _r("b.ab1", "widespread", 15, mf=0.35)]
    ms = mixed_summary(rr, {"by_read": {}, "total": 0, "multi_sites": []})
    assert render_mixed.read_lines(ms)[0] == (
        "⚠ 2 条 read 均疑似混合样品（每条双峰位点 8–15 处，估计次要克隆占比约 20–35%）"
        "——样品为两种质粒的混合，主峰序列按多数碱基判读，建议重新挑单克隆划线培养后复测"
        "；a.ab1 存在连续双峰段（两个克隆相差插入/缺失时的典型形态）")


def test_readcall_switch_default_off(monkeypatch):
    """二期开关 a：⚠ 汇总行改由 ReadCall（全程/某点后套峰）判定，默认关"""
    from core.sanger.readcall import ReadCall, ReadCalls, MIXED_TEMPLATE
    rr = [_r("a.ab1", "widespread", 12), _r("b.ab1", "noise", 40)]
    rc = ReadCalls([ReadCall(filename="a.ab1"),
                    ReadCall(filename="b.ab1", state=MIXED_TEMPLATE)])
    corr = {"by_read": {}, "total": 0, "multi_sites": []}
    monkeypatch.setenv("SANGER_MIXED_LINES_READCALL", "0")   # 2026-11-17 起默认开
    ms = mixed_summary(rr, corr, rc)
    assert [w["filename"] for w in ms["wide"]] == ["a.ab1"]
    monkeypatch.setenv("SANGER_MIXED_LINES_READCALL", "1")
    ms = mixed_summary(rr, corr, rc)
    assert [w["filename"] for w in ms["wide"]] == ["b.ab1"]
    assert ms["pending"] == []           # 可互检且无待核位点
    # 未传 read_calls（旧存档重算）时保持旧口径
    assert [w["filename"] for w in mixed_summary(rr, corr)["wide"]] == ["a.ab1"]
