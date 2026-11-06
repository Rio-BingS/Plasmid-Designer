"""T20 通用伪迹检测（goal_sanger_full_fix_v2 T20；A 3.5）

压缩（峰距 < 0.6 × 局部中位连续 ≥2）、dye blob（read 前 150bp 四通道同时
宽峰无主通道）、短同聚物（3-7bp）滑移 echo。只作 T15 特征与 verdict info，
不进任何判定分支。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/backend"))

from core.sanger.artifacts import (  # noqa: E402
    artifact_at, detect_artifacts, detect_compression, detect_dye_blob,
    detect_short_slippage,
)


def test_compression_zone():
    """峰距 20 的均匀峰列中 3 个连续 6bp 间距 → 压缩区（read 坐标 10-13）"""
    peaks = [20 * i for i in range(30)]
    peaks[10], peaks[11], peaks[12] = 186, 192, 198   # 与 base9/相互间距 6
    peaks[13] = 218                                   # 恢复 20 间距
    out = detect_compression({}, peaks)
    assert len(out) == 1
    z = out[0]
    assert z["kind"] == "compression"
    assert z["start"] == 10 and z["end"] == 13
    assert z["positions"] == [10, 11, 12, 13]
    # 均匀峰列无压缩
    assert detect_compression({}, [20 * i for i in range(30)]) == []


def test_dye_blob_four_channel_broad():
    """前 4 碱基四通道等宽平台峰（无主通道）→ blob；正常单通道峰不触发"""
    peaks = [20 * i for i in range(40)]
    n = 800
    trace = {c: [0] * n for c in "ACGT"}
    for i in range(4):                     # blob：四通道平台高 100（宽 13 采样点）
        pk = peaks[i]
        for c in "ACGT":
            for x in range(max(0, pk - 6), pk + 7):
                trace[c][x] = 100
    for i in range(4, 40):                 # 正常峰：单通道 1000、宽 5
        pk = peaks[i]
        c = "ACGT"[i % 4]
        for x in range(pk - 2, pk + 3):
            trace[c][x] = 1000
    bases = "ACGT" * 10
    out = detect_dye_blob(trace, peaks, bases)
    assert len(out) == 1
    z = out[0]
    assert z["kind"] == "dye_blob"
    assert z["start"] == 1 and z["end"] == 4
    # 全正常峰 → 无 blob
    trace2 = {c: [0] * n for c in "ACGT"}
    for i in range(40):
        pk = peaks[i]
        c = "ACGT"[i % 4]
        for x in range(pk - 2, pk + 3):
            trace2[c][x] = 1000
    assert detect_dye_blob(trace2, peaks, bases) == []


def test_short_slippage_echo():
    """5×T run 末端几何衰减 echo（40%、30%）→ short_slippage，echo 位 6-7"""
    peaks = [10 * i for i in range(20)]
    n = 220
    trace = {c: [0] * n for c in "ACGT"}
    bases = "TTTTT" + "GCACGTACGTACGTAC"
    for i in range(5):                     # run：T 通道高 100
        trace["T"][peaks[i]] = 100
    trace["T"][peaks[5]] = 40              # 首 echo：40%（∈[5%,60%]，紧贴 run 末端）
    trace["T"][peaks[6]] = 30              # 次 echo：≤40×0.9
    trace["G"][peaks[5]] = 1000            # 真实调用峰（他通道）
    trace["C"][peaks[6]] = 1000
    trace["A"][peaks[7]] = 1000
    out = detect_short_slippage(trace, peaks, bases)
    assert len(out) == 1
    z = out[0]
    assert z["kind"] == "short_slippage"
    assert z["start"] == 1 and z["end"] == 5
    assert z["positions"] == [6, 7]


def test_artifact_at_and_detect_artifacts():
    """artifact_at 按位查 kind；detect_artifacts 三检测汇总按坐标排序"""
    zones = [{"kind": "compression", "start": 10, "end": 13},
             {"kind": "dye_blob", "start": 1, "end": 4}]
    assert artifact_at(zones, 11) == "compression"
    assert artifact_at(zones, 2) == "dye_blob"
    assert artifact_at(zones, 5) is None
    assert artifact_at(zones, None) is None
    # 汇总：短 run + 压缩并存，区段按 start 升序
    peaks = [20 * i for i in range(30)]
    peaks[10], peaks[11], peaks[12] = 186, 192, 198
    peaks[13] = 218
    n = 700
    trace = {c: [0] * n for c in "ACGT"}
    bases = "AAAAA" + "CGTACGTACGTACGTACGTACGTACGT"
    for i in range(5):
        trace["A"][peaks[i]] = 100
    arts = detect_artifacts(trace, peaks, bases)
    starts = [a["start"] for a in arts]
    assert starts == sorted(starts)
    assert "compression" in [a["kind"] for a in arts]


def test_e2e_artifacts_wiring():
    """端到端：read 结果带 artifacts 键、evidence 项带 artifact 字段（干净
    合成 trace 为空），verdict 不因伪迹改变状态（T20：只特征与 info）"""
    import random
    from core.sanger.pipeline import analyze
    from sanger_scenarios import _read_from_ref
    rng = random.Random("t20e2e")
    ref = "".join(rng.choice("ACGT") for _ in range(1200))
    blob = _read_from_ref(ref, 100, 1100, q=50,
                          subs=[(300, "G" if ref[299] != "G" else "T")])
    res = analyze([("r.ab1", blob)], ref, [])
    r0 = res["reads"][0]
    assert isinstance(r0["artifacts"], list)
    sub = next(v for v in res["variants"] if v["type"] == "substitution")
    ev = sub["evidence_by_read"][0]
    assert "artifact" in ev
    assert ev["artifact"] is None
    # 干净 trace 不产生 read_artifacts info
    assert not any(i["kind"] == "read_artifacts"
                   for i in res["verdict"]["info"])
