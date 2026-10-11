"""审计 #6：种子-延伸窗口比对与整条参考局部比对逐字段一致的差分测试"""

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.sanger.aligner as A  # noqa: E402
from core.sanger.aligner import align_read, align_read_circular, revcomp  # noqa: E402


def _rnd(rng, n):
    return "".join(rng.choice("ACGT") for _ in range(n))


def _mutate(rng, s, sub, indel, nfrac=0.0):
    out = []
    for c in s:
        r = rng.random()
        if r < sub:
            out.append(rng.choice([b for b in "ACGT" if b != c]))
        elif r < sub + indel / 2:
            continue                                   # 缺失
        elif r < sub + indel:
            out.append(c + _rnd(rng, rng.randint(1, 4)))   # 插入
        elif rng.random() < nfrac:
            out.append("N")
        else:
            out.append(c)
    return "".join(out)


@pytest.fixture
def seed_stats(monkeypatch):
    """统计窗口路径命中 / 回退次数，并提供关闭种子的整条比对作对照"""
    stats = {"seeded": 0, "fallback": 0}
    orig = A._align_seeded

    def wrapped(*a, **kw):
        r = orig(*a, **kw)
        stats["seeded" if r is not None else "fallback"] += 1
        return r
    monkeypatch.setattr(A, "_align_seeded", wrapped)
    return stats


def _full(fn, read, ref, q, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(A, "SEED_MIN_REF_BP", 10 ** 12)
        return fn(read, ref, q)


def test_seeded_matches_full_alignment_synthetic(monkeypatch, seed_stats):
    """线性/环状跨原点、正反链、indel、重复区、N、两端垃圾序列：结果完全相同"""
    monkeypatch.setattr(A, "SEED_MIN_REF_BP", 1500)   # 小参考也走窗口路径，测试才快
    rng = random.Random(20261011)
    kinds = ["plain", "indel", "repeat", "tandem", "lowq", "origin"] * 7
    for t, kind in enumerate(kinds):
        L = rng.choice([2500, 4000, 6000])
        ref = _rnd(rng, L)
        if kind == "repeat":          # 600 bp 完全重复：同分时须取与整条比对相同的拷贝
            p = rng.randint(800, L - 700)
            ref = ref[:p] + ref[100:700] + ref[p + 600:]
        if kind == "tandem":
            unit = _rnd(rng, rng.randint(2, 30))
            p = rng.randint(0, L - 400)
            ref = ref[:p] + unit * (300 // len(unit)) + ref[p + 300:]
        n = rng.randint(150, 1000)
        circular = kind == "origin" or rng.random() < 0.3
        st = rng.randint(L - n + 50, L - 50) if kind == "origin" else rng.randint(0, L - n)
        if kind == "repeat":
            st = rng.choice([100, p])
            n = min(n, 600)
        src = (ref + ref)[st:st + n]
        sub, indel = {"plain": (0.01, 0.0), "lowq": (rng.choice([0.05, 0.12]), 0.03)}.get(
            kind, (0.01, 0.01))
        read = _mutate(rng, src, sub, indel, nfrac=0.01 if t % 3 == 0 else 0.0)
        if t % 4 == 1:
            read = _rnd(rng, rng.randint(5, 40)) + read + _rnd(rng, rng.randint(5, 40))
        if t % 2:
            read = revcomp(read)
        q = [rng.randint(5, 60) for _ in read]
        fn = align_read_circular if circular else align_read
        new = fn(read, ref, q)
        old = _full(fn, read, ref, q, monkeypatch)
        assert new == old, (t, kind, circular)
        if kind == "origin":
            assert new["wraps_origin"] is True
    # 大部分用例确实走了窗口路径（低质量 read 回退属预期）
    assert seed_stats["seeded"] >= 25


def test_seeded_default_threshold_large_reference(monkeypatch, seed_stats):
    """默认阈值下 25 kb 参考：窗口路径生效且与整条比对一致（含反向跨原点）"""
    rng = random.Random(7)
    ref = _rnd(rng, 25000)
    read_lin = _mutate(rng, ref[12000:12900], 0.01, 0.005)
    read_circ = revcomp(_mutate(rng, (ref + ref)[24600:25400], 0.01, 0.005))
    for fn, read in ((align_read, read_lin), (align_read_circular, read_circ)):
        new = fn(read, ref, None)
        assert new == _full(fn, read, ref, None, monkeypatch)
    assert seed_stats == {"seeded": 2, "fallback": 0}


def test_seed_index_built_once_per_reference(monkeypatch):
    """同一参考的一批 read 只建一次索引；小参考不建索引"""
    monkeypatch.setattr(A, "SEED_MIN_REF_BP", 1500)
    monkeypatch.setattr(A, "_INDEX_CACHE", type(A._INDEX_CACHE)())
    built = []
    orig = A._KmerIndex.__init__

    def counting(self, seq, k=A.SEED_K):
        built.append(len(seq))
        orig(self, seq, k)
    monkeypatch.setattr(A._KmerIndex, "__init__", counting)
    rng = random.Random(3)
    ref = _rnd(rng, 3000)
    for st in (0, 700, 1500, 2100):
        align_read(ref[st:st + 600], ref)
        align_read_circular(ref[st:st + 600], ref)
    assert built == [3000, 6000]          # 线性一份、环状（参考+参考）一份
    align_read("ACGT" * 50, ref[:1000])   # 低于阈值：不建索引
    assert len(built) == 2


def test_seed_weak_vote_falls_back(monkeypatch, seed_stats):
    """无可靠种子（无关 read / 高错误率）回退整条比对，结果不变"""
    monkeypatch.setattr(A, "SEED_MIN_REF_BP", 1500)
    rng = random.Random(11)
    ref = _rnd(rng, 4000)
    junk = _rnd(rng, 500)
    noisy = _mutate(rng, ref[1000:1600], 0.2, 0.05)
    for read in (junk, noisy):
        assert align_read(read, ref) == _full(align_read, read, ref, None, monkeypatch)
    assert seed_stats["seeded"] == 0 and seed_stats["fallback"] == 2


def test_min_seed_hits_bound():
    """计数下界：完美比对 n - k + 1；得分越低下界越小直至无法保证"""
    assert A._min_seed_hits(900, 1800) == 900 - A.SEED_K + 1
    assert A._min_seed_hits(900, 1700) > A._min_seed_hits(900, 1600)
    assert A._min_seed_hits(900, 1000) < 1


def test_seeded_exact_repeat_tie_picks_same_copy(monkeypatch, seed_stats):
    """两份完全相同的拷贝同分：与整条比对一样取参考上靠前的那份"""
    monkeypatch.setattr(A, "SEED_MIN_REF_BP", 1500)
    rng = random.Random(5)
    seg = _rnd(rng, 500)
    ref = _rnd(rng, 800) + seg + _rnd(rng, 2000) + seg + _rnd(rng, 900)
    for read in (seg[60:], revcomp(seg)):
        new = align_read(read, ref)
        assert new == _full(align_read, read, ref, None, monkeypatch)
        assert new["ref_start"] <= 861
    assert seed_stats == {"seeded": 2, "fallback": 0}
