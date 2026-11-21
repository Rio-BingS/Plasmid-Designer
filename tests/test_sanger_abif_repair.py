"""ab1 结构损坏识别与修复（2026-11-11；损坏形态发现于真实样本，
真实样本用例随 2026-11-21 政策迁往本机专用测试 tests/data/real_ab1/，
不入仓库——本文件以合成数据复现同一损坏形态）

损坏形态：改名工具把 MCHN 与 RUNN 元数据串改短（各少 2 字节）而不更新
偏移——目录失配、PBAS/PLOC/PCON 错位 2 字节。旧行为：Biopython 读出
0 碱基（碱基数过少）或按乱码峰位报大量双峰（△疑似混合）。期望：能修则
修复后与原文件读数逐一致并告警；修不了则明确报「文件结构损坏」，绝不按
乱码分析。
"""

import struct
from pathlib import Path

import pytest

from core.sanger.abif_reader import AbiParseError, extract_read
from core.sanger.abif_repair import base_peak_agreement, repair_abif, structure_problem
from core.sanger.pipeline import analyze
from tests.abif_utils import make_trace_ab1

DATA = Path(__file__).parent / "data"


def _relayout_dir_at_end(blob: bytes, insert_after_tag=b"DATA", meta=b"\x0fDEVICE-0000-002"):
    """把合成 ab1（目录在 128）改成真实布局：数据块在前、目录在末尾，
    并在 DATA 块之后插入一个 MCHN pString 元数据块"""
    nel = struct.unpack(">I", blob[18:22])[0]
    d0 = struct.unpack(">I", blob[26:30])[0]
    ents = []
    for i in range(nel):
        e = blob[d0 + i * 28: d0 + i * 28 + 28]
        tag, num, et, es, cnt, ds, do = struct.unpack(">4sIHHIII", e[:24])
        payload = e[20:24][:ds] if ds <= 4 else blob[do:do + ds]
        ents.append([tag, num, et, es, cnt, ds, payload])
    data_ents = [x for x in ents if x[0] == b"DATA"]
    other = [x for x in ents if x[0] != b"DATA"]
    order = data_ents + [[b"MCHN", 1, 18, 1, len(meta), len(meta), meta]] + other
    body = b""
    dirs = b""
    cur = 128
    for tag, num, et, es, cnt, ds, payload in order:
        if ds <= 4:
            dirs += struct.pack(">4sIHHII4sI", tag, num, et, es, cnt, ds, payload.ljust(4, b"\0"), 0)
            continue
        dirs += struct.pack(">4sIHHIIII", tag, num, et, es, cnt, ds, cur, 0)
        body += payload
        cur += ds
    hdr = bytearray(blob[:128])
    hdr[18:22] = struct.pack(">I", len(order))
    hdr[22:26] = struct.pack(">I", len(dirs))
    hdr[26:30] = struct.pack(">I", cur)
    return bytes(hdr) + body + dirs


def test_合成件_元数据改短导致整体平移可修复():
    good = _relayout_dir_at_end(make_trace_ab1("ACGT" * 60 + "GATTACA" * 20, q=40))
    ref = extract_read(good)
    assert structure_problem(ref) is None
    bad = good.replace(b"DEVICE-0000", b"DEMO-0000")
    assert len(bad) == len(good) - 2
    got = extract_read(bad)
    assert got.get("abif_repair")
    for k in ("bases", "quality", "peak_indices", "trace"):
        assert got[k] == ref[k], k


def test_合成件_峰位错位且不可修复时明确报损坏而非带病分析():
    import random
    rng = random.Random(5)
    seq = "".join(rng.choice("ACGT") for _ in range(320))
    blob = bytearray(make_trace_ab1(seq, q=40))
    nel = struct.unpack(">I", blob[18:22])[0]
    d0 = struct.unpack(">I", blob[26:30])[0]
    for i in range(nel):
        o = d0 + i * 28
        if blob[o:o + 4] == b"PLOC":
            off = struct.unpack(">I", blob[o + 20:o + 24])[0]
            blob[o + 20:o + 24] = struct.pack(">I", off + 4)  # 峰位整体错 1 个元素（32 位 PLOC）
    with pytest.raises(AbiParseError, match="结构损坏"):
        extract_read(bytes(blob))
    res = analyze([("bad.ab1", bytes(blob))], seq, [], circular=False)
    assert res["errors"] and "结构损坏" in res["errors"][0]["error"]
    assert not any(b["kind"] in ("mixed_sample", "mixed_variant")
                   for b in res["verdict"]["blocking"])


def test_正常文件不触发修复():
    files = sorted(DATA.glob("**/*.ab1"))
    assert files
    for f in files:
        r = extract_read(f.read_bytes())
        assert "abif_repair" not in r, f.name
        ag = base_peak_agreement(r)
        assert ag is None or ag >= 0.8, (f.name, ag)


def test_非平移型损坏不乱修():
    blob = make_trace_ab1("ACGT" * 50, q=40)
    assert repair_abif(blob) == (None, None)
    assert repair_abif(b"not an ab1") == (None, None)
