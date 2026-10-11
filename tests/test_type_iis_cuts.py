"""Type IIS（BsaI / BsmBI / BbsI）正反链切点对照 Bio.Restriction（审查 04 M1）

v2.7.1 反链位点取的是下链切点，比上链（Bio.Restriction.search 的口径）
偏 4 bp（突出端长度）。v2.8.0 酶表统一到 Bio.Restriction 后扫描器与消化
已改用上链切点，但 /analysis/restriction-sites 的 cut_position 仍沿用
「酶自身结合链」口径，反链位点照旧偏 4 bp。这里逐层锁定：扫描器、
位点分析、消化接口、克隆方案回退扫描。
"""

import random

import pytest
from Bio import Restriction as R
from Bio.Seq import Seq
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.analysis_routes import router
from core.enzyme_sites import find_enzyme_sites
from core.seq_utils import revcomp
from core.sequence_analysis import RestrictionSiteAnalyzer

ENZYMES = ("BsaI", "BsmBI", "BbsI")


def _filler(rng, n):
    # 只用 A/T 填充，避免意外生成额外的 Type IIS 位点
    return "".join(rng.choice("AT") for _ in range(n))


def _case(name, strand, seed=0):
    rng = random.Random(f"{name}{strand}{seed}")
    site = getattr(R, name).site
    site = site if strand == "+" else revcomp(site)
    return _filler(rng, 30 + seed) + site + _filler(rng, 30)


def _bio_cut_before(name, seq, linear=True):
    """Bio.Restriction 切点（切口后第一位，1-based）→ 切口前一位"""
    return sorted(p - 1 for p in getattr(R, name).search(Seq(seq), linear=linear))


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("name", ENZYMES)
@pytest.mark.parametrize("strand", ["+", "-"])
def test_scanner_cut_fwd_matches_biopython(name, strand):
    for seed in range(3):
        seq = _case(name, strand, seed)
        hits = find_enzyme_sites(seq, [name])
        assert [h["strand"] for h in hits] == [strand]
        assert [h["cut_fwd"] for h in hits] == _bio_cut_before(name, seq)


@pytest.mark.parametrize("name", ENZYMES)
@pytest.mark.parametrize("strand", ["+", "-"])
def test_restriction_sites_cut_position_matches_biopython(name, strand, client):
    seq = _case(name, strand)
    sites = RestrictionSiteAnalyzer().find_sites(seq, [name])
    assert [(s.strand, s.cut_position) for s in sites] == \
        [(strand, c) for c in _bio_cut_before(name, seq)]
    r = client.post("/api/analysis/restriction-sites", json={"sequence": seq, "enzymes": [name]})
    assert r.status_code == 200, r.text
    assert [s["cut_position"] for s in r.json()["sites"]] == _bio_cut_before(name, seq)


@pytest.mark.parametrize("name", ENZYMES)
@pytest.mark.parametrize("strand", ["+", "-"])
def test_digest_boundary_matches_biopython(name, strand, client):
    seq = _case(name, strand)
    r = client.post("/api/analysis/digest", json={"sequence": seq, "enzymes": [name]})
    assert r.status_code == 200, r.text
    frags = r.json()["fragments"]
    assert [f["end"] for f in frags[:-1]] == _bio_cut_before(name, seq)


@pytest.mark.parametrize("name", ENZYMES)
def test_both_strands_in_one_sequence_circular(name):
    rng = random.Random(name)
    site = getattr(R, name).site
    seq = _filler(rng, 40) + site + _filler(rng, 60) + revcomp(site) + _filler(rng, 40)
    for circular in (False, True):
        got = sorted(h["cut_fwd"] for h in find_enzyme_sites(seq, [name], circular=circular))
        assert got == _bio_cut_before(name, seq, linear=not circular), circular


@pytest.mark.parametrize("name", ENZYMES)
def test_clone_strategy_fallback_scan_matches_biopython(name):
    from core.clone_strategy import _scan_enzyme_sites
    seq = _case(name, "-")
    cuts = sorted(h["cut_fwd"] for h in _scan_enzyme_sites(seq, name))
    # 回退扫描按环状载体扫描
    assert cuts == _bio_cut_before(name, seq, linear=False)
