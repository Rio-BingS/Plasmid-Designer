"""审计 #8：tracy basecall -f json 次级调用作为混合峰独立印证（tracy 以桩模拟）

JSON 结构按 tracy v0.9.1 实际输出（gear-genomics/tracy src/json.h：
pos/peakA..T/basecallPos/basecallQual/basecalls/primarySeq/secondarySeq）。
"""

import json
import os
import random
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core.sanger.pipeline as P  # noqa: E402
from abif_utils import make_ab1  # noqa: E402
from core.sanger.abif_reader import extract_read  # noqa: E402


def _tracy_json(primary, secondary, quals, positions):
    """按 tracy v0.9.1 的字段与形态拼 JSON（basecallPos 为 1-based 采样坐标）"""
    calls = {}
    for i, (p, s, pos) in enumerate(zip(primary, secondary, positions)):
        calls[str(pos)] = f"{i + 1}:{p}" + ("" if p == s else "|" + s)
    n = max(positions) + 3
    return json.dumps({
        "pos": list(range(1, n + 1)), "peakA": [4] * n, "peakC": [4] * n,
        "peakG": [4] * n, "peakT": [4] * n,
        "basecallPos": list(positions), "basecallQual": list(quals),
        "basecalls": calls, "primarySeq": primary, "secondarySeq": secondary,
    })


def test_parse_tracy_json_documented_shape():
    text = _tracy_json("ACGTA", "ACRTA", [40, 41, 30, 20, 0], [7, 19, 31, 43, 55])
    bases, qual, calls = P._parse_tracy_json(text)
    assert bases == "ACGTA" and qual == [40, 41, 30, 20, 0]
    assert calls["secondary"] == "ACRTA"
    assert calls["positions"] == [6, 18, 30, 42, 54]   # → 0-based，与 PLOC 同系


@pytest.mark.parametrize("bad", [
    "not json", "[]", json.dumps({"primarySeq": "ACGT"}),                 # 缺字段
    json.dumps({"primarySeq": "ACGT", "secondarySeq": "AC",              # 长度不符
                "basecallQual": [1, 2, 3, 4], "basecallPos": [1, 2, 3, 4]}),
    json.dumps({"primarySeq": "AC", "secondarySeq": "AC",
                "basecallQual": ["x", 2], "basecallPos": [1, 2]}),
])
def test_parse_tracy_json_rejects_unknown_shape(bad):
    """结构漂移（未来/过旧版本）一律返回 None，由调用方退回 primary-only"""
    assert P._parse_tracy_json(bad) is None


class _FakeTracy:
    """模拟 tracy 可执行文件：--version 与 basecall -f json|fastq"""

    def __init__(self, version="0.9.1", json_text=None, fail=False):
        self.version, self.json_text, self.fail = version, json_text, fail
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd[1:3] if cmd[1] == "basecall" else cmd[1:2])
        if cmd[1] == "--version":
            out = f"Tracy version: v{self.version}\n" if self.version else "garbage\n"
            return subprocess.CompletedProcess(cmd, 0, out, "")
        if self.fail:
            return subprocess.CompletedProcess(cmd, 255, "", "File lacks basecalls!")
        fmt, out = cmd[cmd.index("-f") + 1], cmd[cmd.index("-o") + 1]
        with open(out, "w") as fh:
            if fmt == "json":
                fh.write(self.json_text or _tracy_json("ACGTAC", "ACGYAC", [40] * 6,
                                                       [5, 15, 25, 35, 45, 55]))
            else:
                fh.write("@primary\nACGTAC\n+\n" + chr(33 + 40) * 6 + "\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def fake_tracy(monkeypatch):
    def install(**kw):
        fake = _FakeTracy(**kw)
        monkeypatch.setattr(P.shutil, "which", lambda name: "/opt/fake/tracy")
        monkeypatch.setattr(P.subprocess, "run", fake)
        monkeypatch.setattr(P, "_TRACY_VERSION_CACHE", {})
        return fake
    return install


def test_tracy_basecall_json_returns_secondary(fake_tracy):
    fake = fake_tracy()
    bases, qual, calls = P._try_tracy_basecall(b"ab1")
    assert bases == "ACGTAC" and qual == [40] * 6
    assert calls["secondary"] == "ACGYAC" and calls["version"] == "0.9.1"
    assert ["basecall", "-f"] in fake.calls and fake.calls[-1] == ["basecall", "-f"]


def test_tracy_basecall_old_version_uses_fastq(fake_tracy):
    """版本守卫：低于 JSON 最低版本只跑 FASTQ，返回旧形态 (bases, quality)"""
    fake_tracy(version="0.3.2")
    out = P._try_tracy_basecall(b"ab1")
    assert out == ("ACGTAC", [40] * 6)


def test_tracy_basecall_unparseable_json_falls_back(fake_tracy):
    """JSON 结构识别不了（含版本号无法判定时）→ 退回 FASTQ，不报错"""
    fake_tracy(version=None, json_text='{"primarySeq": "ACGTAC"}')
    assert P._try_tracy_basecall(b"ab1") == ("ACGTAC", [40] * 6)


def test_tracy_basecall_failure_and_missing_degrade_silently(fake_tracy, monkeypatch):
    fake_tracy(fail=True)
    assert P._try_tracy_basecall(b"ab1") is None
    monkeypatch.setattr(P.shutil, "which", lambda name: None)
    monkeypatch.setattr(P, "TRACY_BIN", "/nonexistent/tracy")
    assert P._try_tracy_basecall(b"ab1") is None


def test_tracy_basecall_timeout_degrades(fake_tracy, monkeypatch):
    fake_tracy()

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 60)
    monkeypatch.setattr(P.subprocess, "run", boom)
    monkeypatch.setattr(P, "_TRACY_VERSION_CACHE", {})
    assert P._try_tracy_basecall(b"ab1") is None


def test_classify_mixed_tracy_corroboration_counts():
    """agree / disagree / unmatched / tracy_only 计数；院内分级不受影响"""
    peaks = [10 * i + 5 for i in range(100)]              # 院内峰坐标
    entries = [
        {"pos": 31, "ratio": 0.6, "secondary_base": "G", "called_base": "A", "pullup": False},
        {"pos": 41, "ratio": 0.5, "secondary_base": "T", "called_base": "C", "pullup": False},
        {"pos": 51, "ratio": 0.5, "secondary_base": "A", "called_base": "G", "pullup": False},
        {"pos": 61, "ratio": 0.5, "secondary_base": "C", "called_base": "T", "pullup": False},
        {"pos": 71, "ratio": 0.4, "secondary_base": "C", "called_base": "A", "pullup": True},
    ]
    base = P._classify_mixed([dict(e) for e in entries], 100)
    # tracy 的峰坐标整体偏 2 个采样点（不同 caller 的峰定位略有出入）
    primary = list("C" * 100)
    secondary = list(primary)
    primary[30], secondary[30] = "A", "R"      # pos 31：A/G 一致 → agree
    primary[40], secondary[40] = "C", "C"      # pos 41：tracy 单峰 → disagree
    primary[50], secondary[50] = "G", "T"      # pos 51：次级碱基不同 → disagree
    secondary[75] = "T"                        # 院内未报 → tracy_only
    secondary[5] = "T"                         # 首尾 END_MARGIN 内：不计
    tpos = [p + 2 for p in peaks]
    del primary[60], secondary[60], tpos[60]   # pos 61 附近无 tracy 调用 → unmatched
    calls = {"primary": "".join(primary), "secondary": "".join(secondary),
             "positions": tpos, "version": "0.9.1"}
    prof = P._classify_mixed([dict(e) for e in entries], 100, tracy_calls=calls, peaks=peaks)
    tc = prof["tracy_corroboration"]
    assert (tc["agree"], tc["disagree"], tc["unmatched"], tc["tracy_only"]) == (1, 2, 1, 1)
    assert tc["agreement"] == pytest.approx(1 / 3, abs=1e-3) and tc["version"] == "0.9.1"
    assert {k: v for k, v in prof.items() if k != "tracy_corroboration"} == \
        {k: v for k, v in base.items() if k != "tracy_corroboration"}
    assert base["tracy_corroboration"] is None


_ALT_OF = {"A": "G", "C": "T", "G": "A", "T": "C"}


def _mixed_ab1(seq, sites):
    traces = {ch: [100 if ch == b else 4 for b in seq] for ch in "ATGC"}
    for pos, alt in sites.items():
        traces[alt][pos] = 60
    return make_ab1(seq, [40] * len(seq), traces=[traces[c] for c in "ATGC"])


def _stub_tracy_from(blob, het_sites):
    """以 ab1 自身碱基与峰坐标构造 tracy 结果，het_sites {0-based: IUPAC 次级}"""
    rd = extract_read(blob)
    sec = list(rd["bases"])
    for i, code in het_sites.items():
        sec[i] = code
    return (rd["bases"], list(rd["quality"]),
            {"primary": rd["bases"], "secondary": "".join(sec),
             "positions": list(rd["peak_indices"]), "version": "0.9.1"})


def test_analyze_mixed_sample_tracy_agrees_and_clean_stays_clean(monkeypatch):
    """MX 样品仍判 widespread 并获 tracy 全部印证；干净样品不新增双峰"""
    random.seed(7)
    ref = "".join(random.choice("ACGT") for _ in range(600))
    sites = {p: _ALT_OF[ref[p]] for p in (80, 130, 200, 260, 330, 400, 470, 540)}
    mx = _mixed_ab1(ref, sites)
    clean = _mixed_ab1(ref, {})
    iupac = {frozenset("AG"): "R", frozenset("CT"): "Y"}
    stubs = {mx: _stub_tracy_from(mx, {p: iupac[frozenset((ref[p], a))]
                                       for p, a in sites.items()}),
             clean: _stub_tracy_from(clean, {})}
    monkeypatch.setattr(P, "_try_tracy_basecall", lambda blob: stubs[blob])

    res = P.analyze([("mx.ab1", mx)], ref, [])
    prof = res["mixed_profiles"]["mx.ab1"]
    assert prof["class"] == "widespread" and prof["count"] == 8
    tc = prof["tracy_corroboration"]
    assert tc["agree"] == 8 and tc["disagree"] == 0 and tc["agreement"] == 1.0
    assert all(d["tracy"] == "agree" for d in res["reads"][0]["mixed_detail"])
    assert "tracy_calls" not in res["reads"][0]          # 内部数据不随结果下发
    assert res["engine"] == "internal+biopython+tracy-basecall"

    res2 = P.analyze([("clean.ab1", clean)], ref, [])
    assert res2["reads"][0]["mixed_positions"] == []
    assert res2["reads"][0]["mixed_profile"]["class"] == "none"
    assert res2["reads"][0]["mixed_profile"]["tracy_corroboration"]["tracy_only"] == 0


def test_analyze_tracy_fastq_fallback_has_no_corroboration(monkeypatch):
    """退化路径（旧版 tracy，仅 primary）：照常交叉 basecall，不出印证字段值"""
    random.seed(7)
    ref = "".join(random.choice("ACGT") for _ in range(600))
    sites = {p: _ALT_OF[ref[p]] for p in (80, 130, 200, 260, 330)}
    mx = _mixed_ab1(ref, sites)
    rd = extract_read(mx)
    monkeypatch.setattr(P, "_try_tracy_basecall",
                        lambda blob: (rd["bases"], list(rd["quality"])))
    res = P.analyze([("mx.ab1", mx)], ref, [])
    prof = res["mixed_profiles"]["mx.ab1"]
    assert prof["class"] == "widespread" and prof["tracy_corroboration"] is None
    assert res["engine"] == "internal+biopython+tracy-basecall"
