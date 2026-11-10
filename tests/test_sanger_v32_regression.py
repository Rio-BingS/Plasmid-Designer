"""v3.2 本地回归修复（algo v16，2026-11-10）的场景与真实数据锁。

本轮不是「退回 main」：保留 T1（证据不足≠一致）、T12（好 read 的 700bp
之后不白丢）、W4（低丰度双峰不静默）、v3.1 复核队列的原意，只把与真实
数据冲突的那部分改成能区分的规则。每条规则在这里各有一正一反两把锁：

- XR1 互检否决伪影对：两条 read 各报一个单 read indel（位置相邻），彼此在
  对方位置干净读出设计碱基 → 两者都不得确证（mutual_support 曾把这类
  互相否决的伪影「互证」复活为 confirmed——P0-3 回归的最小复现）。
- 尾端扩展区：干净替换（Q≥40、峰宽正常）按证据确证（S1 原意，另由 S1/
  M13 锁）；同区单 read 插入只进待核，不确证也不静默。
- 低丰度双峰：单 read AF 0.15-0.23 列出不阻断（真实单克隆普遍存在）；
  两条 read 同位点印证 → minor_af_site 待核（W4 原意）。
- 真实数据：MUTPK 5 条 read 逐条结论与 main(v2.36.0) 一致，且锁住本版
  verified 覆盖（T12 的覆盖收益保留）。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.sanger.pipeline import analyze  # noqa: E402
from tests.abif_utils import make_trace_ab1  # noqa: E402
from tests.sanger_scenarios import _rand_seq, _rng, _read_from_ref  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data" / "real_ab1"


def _status(res):
    return res["verdict"]["status"]


def _blocking_kinds(res):
    return {b["kind"] for b in res["verdict"]["blocking"]}


# ---------- XR1：互检否决伪影对不得互证复活 ----------

def test_xr1_mutually_vetoed_indel_pair_never_confirmed():
    """P0-3 回归最小复现（work/probe_mutual.py 同构造）：A/B 两条正向 read
    各有一处单 read 1bp 缺失（1260 / 2117），C/D 两条反向 read 在对应位置
    干净读出设计序列 → 两处都被互检否决，不得被「互证」复活为确证。
    b39b46a：rejected；cf77c3a（mutual_support）：confirmed/fail；本版：rejected"""
    import random
    from core.seq_utils import revcomp
    r = random.Random(7)
    ref = "".join(r.choice("ACGT") for _ in range(4000))

    def dele(s, e, pos):
        return ref[s - 1:pos - 1] + ref[pos:e]
    reads = [("A_F.ab1", make_trace_ab1(dele(1000, 1700, 1260), q=45, seed=1)),
             ("B_F.ab1", make_trace_ab1(dele(1700, 2400, 2117), q=45, seed=2)),
             ("C_R.ab1", make_trace_ab1(revcomp(ref[999:1800]), q=45, seed=3)),
             ("D_R.ab1", make_trace_ab1(revcomp(ref[1699:2400]), q=45, seed=4))]
    feats = [dict(name="GeneX", type="CDS", start=1001, end=2400, strand="+")]
    res = analyze(reads, ref, feats, circular=True, scope="GeneX")
    by = {v["ref_pos"]: v for v in res["variants"] if v["type"] == "deletion"}
    for pos in (1260, 2117):
        v = by.get(pos)
        assert v is not None, f"{pos} 必须留痕"
        assert v.get("status") == "rejected", (pos, v.get("status"))
        assert not v.get("cross_read_mutual_support")
    assert _status(res) != "fail", res["verdict"]["headline"]


@pytest.mark.xfail(strict=True, reason=(
    "新发现（2026-11-10，未修）：两条 read 在相距 4bp 处各报不同的单 read "
    "插入，各自在对方位点 ±5bp 内有差异 → 互检不作反证，两处都按单 read "
    "高 Q 确证 → ✗。应判 overlap_discordance 待核。修法待定，先锁现状"))
def test_xr1b_adjacent_discordant_indels_not_both_confirmed():
    rng = _rng("xr1")
    ref = _rand_seq(rng, 1400)
    a = ref[100:400] + "G" + ref[400:800]
    b = ref[200:404] + "T" + ref[404:900]
    res = analyze([("a.ab1", make_trace_ab1(a, q=50)),
                   ("b.ab1", make_trace_ab1(b, q=50, seed=11))],
                  ref, [], circular=False)
    conf = [v for v in res["variants"] if v["type"] == "insertion"
            and 395 <= v["ref_pos"] <= 410 and v.get("status") == "confirmed"]
    assert len(conf) < 2, [(v["ref_pos"], v.get("status")) for v in conf]


# ---------- 尾端扩展区：单 read 插入进待核 ----------

def test_tail_extension_single_read_insertion_pending_not_confirmed():
    rng = _rng("tailins")
    ref = _rand_seq(rng, 1500)
    mol = ref[0:760] + "A" + ref[760:1000]      # read 761bp 处插入 1bp
    res = analyze([("t.ab1", make_trace_ab1(mol, q=50))], ref, [], circular=False)
    v = next(x for x in res["variants"]
             if x["type"] == "insertion" and 755 <= x["ref_pos"] <= 765)
    assert v["zone_class"] == "tail_ext"
    assert v["status"] == "unresolved"
    assert "尾端扩展区" in (v.get("low_reason") or "")
    assert "unresolved_variant" in _blocking_kinds(res)   # 不静默
    assert _status(res) == "pending"


def test_tail_extension_clean_substitution_confirmed():
    """与 S1 同口径的独立构造：尾端扩展区 Q50、峰宽正常的替换 → 确证 ✗"""
    rng = _rng("tailsub")
    ref = _rand_seq(rng, 1500)
    alt = "A" if ref[819] != "A" else "C"
    res = analyze([("t.ab1", _read_from_ref(ref, 1, 1000, subs=[(820, alt)]))],
                  ref, [], circular=False)
    v = next(x for x in res["variants"] if x["ref_pos"] == 820)
    assert v.get("tail_clean") is True
    assert v["status"] == "confirmed"
    assert _status(res) == "fail"


# ---------- 低丰度双峰：两 read 印证才待核 ----------

def test_low_af_corroborated_by_two_reads_blocks():
    rng = _rng("lowaf2")
    ref = _rand_seq(rng, 1400)
    alt = next(c for c in "ACGT" if c != ref[449])
    feats = [dict(name="GeneX", type="CDS", start=301, end=600, strand="+")]
    r1 = _read_from_ref(ref, 1, 900, secondary={450: (alt, 0.22)}, q=50)
    r2 = _read_from_ref(ref, 101, 1000, secondary={350: (alt, 0.22)}, q=50, seed=13)
    res = analyze([("a.ab1", r1), ("b.ab1", r2)], ref, feats, circular=False)
    mv = [m for m in res.get("mixed_variants") or [] if m["ref_pos"] == 450]
    assert mv and len(mv[0]["reads"]) == 2, mv
    assert "minor_af_site" in _blocking_kinds(res)
    assert _status(res) == "pending"


# ---------- 真实数据锁（MUTPK / S99676，tests/data/real_ab1） ----------

_real = pytest.mark.skipif(
    not DATA_DIR.is_dir() or not list(DATA_DIR.glob("*.ab1")),
    reason="真实 AB1 子集不在场（tests/data/real_ab1）")

# 结论 = main(v2.36.0, bd5dff4) 逐条结论（work/cmp_variants.py 实测）；
# 覆盖下限 = 本版实测 verified 碱基数减 20bp——锁住 T12 尾端扩展区带来的
# 覆盖收益（main 对 M13R 只主张 2169–2837）
_MAIN = {
    "S99676-11131-1CseqR1.ab1": ("pass", 755),    # 本版 3190–3964 = 775bp
    "S99676-17647-seqF1.ab1": ("pass", 910),      # 本版 2862–3791 = 930bp
    "S99676-M13F-75.ab1": ("pass", 100),          # 本版 4123–4232 = 110bp
    "S99676-M13R-88.ab1": ("pass", 1019),         # 本版 2169–3207 = 1039bp；main 669bp
}


def _real_one(fn):
    from core.sanger.reference_parser import parse_reference_topology
    ref, feats, topo = parse_reference_topology(
        "MUTPK.dna", (DATA_DIR / "MUTPK.dna").read_bytes())
    p = DATA_DIR / fn
    return analyze([(p.name, p.read_bytes())], ref, feats, circular=topo is not False)


@_real
@pytest.mark.parametrize("fn", sorted(_MAIN))
def test_real_clean_reads_pass_like_main(fn):
    res = _real_one(fn)
    want, min_cov = _MAIN[fn]
    assert _status(res) == want, res["verdict"]["headline"]
    assert not res["verdict"]["blocking"], res["verdict"]["blocking"][:3]
    assert not [v for v in res["variants"] if v.get("status") == "confirmed"]
    vr = (res.get("coverage") or {}).get("verified_ranges") or []
    assert sum(e - s + 1 for s, e in vr) >= min_cov, vr


@_real
def test_real_10855_only_4081_confirmed():
    res = _real_one("S99676-10855-1seqF1.ab1")
    assert _status(res) == "fail"
    conf = sorted(v["ref_pos"] for v in res["variants"] if v.get("status") == "confirmed")
    assert conf == [4081], conf
