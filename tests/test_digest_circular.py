"""/analysis/digest 环状拓扑（审计 #5）。

此前消化模拟一律按线性完全消化：质粒上 N 个切点得 N+1 个片段，跨原点
的那段被拆成两条假条带，单切线性化也报 2 个片段。现在请求可带
circular（默认 False，拓扑未知时保持线性），环状时首尾片段相连。
片段大小对照 Bio.Restriction.search(linear=...) 的切点。
"""

import random
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "src" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import yaml  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.analysis_routes import router  # noqa: E402

PUC19 = "".join(yaml.safe_load(
    (BACKEND.parents[1] / "data" / "vectors" / "pUC19.yaml").read_text(encoding="utf-8")
)["sequence"].split()).upper()


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _digest(client, seq, enzymes, circular=None):
    body = {"sequence": seq, "enzymes": enzymes}
    if circular is not None:
        body["circular"] = circular
    resp = client.post("/api/analysis/digest", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _bio_sizes(seq, enzymes, circular):
    """Bio.Restriction 切点（切点后第一位，1-based）→ 片段大小多重集"""
    from Bio.Restriction import RestrictionBatch
    from Bio.Seq import Seq
    res = RestrictionBatch(enzymes).search(Seq(seq), linear=not circular)
    bounds = sorted({p - 1 for v in res.values() for p in v})
    n = len(seq)
    if circular:
        bounds = sorted({(b - 1) % n + 1 for b in bounds})
        if not bounds:
            return []
        return sorted(((bounds[(i + 1) % len(bounds)] - b) % n) or n
                      for i, b in enumerate(bounds))
    bounds = [b for b in bounds if 0 < b < n]
    if not bounds:
        return []
    edges = [0] + bounds + [n]
    return sorted(edges[i + 1] - edges[i] for i in range(len(edges) - 1))


def test_puc19_single_cut_circular_vs_linear(client):
    assert len(PUC19) == 2686
    circ = _digest(client, PUC19, ["EcoRI"], circular=True)
    assert circ["circular"] is True
    assert circ["total_fragments"] == 1
    frag = circ["fragments"][0]
    assert frag["size"] == 2686
    assert frag["wraps_origin"] is True and frag["start"] == frag["end"] + 1

    lin = _digest(client, PUC19, ["EcoRI"], circular=False)
    assert lin["circular"] is False
    assert lin["total_fragments"] == 2
    assert sum(f["size"] for f in lin["fragments"]) == 2686


def test_puc19_double_digest_circular_matches_biopython(client):
    data = _digest(client, PUC19, ["EcoRI", "HindIII"], circular=True)
    sizes = sorted(f["size"] for f in data["fragments"])
    assert data["total_fragments"] == 2
    assert sum(sizes) == 2686
    assert sizes == _bio_sizes(PUC19, ["EcoRI", "HindIII"], circular=True)
    # EcoRI 与 HindIII 在 pUC19 MCS 中相距 51 bp
    assert sizes[0] == 51
    wrap = [f for f in data["fragments"] if f.get("wraps_origin")]
    assert len(wrap) == 1 and wrap[0]["size"] == 2686 - 51


def test_circular_field_defaults_to_linear(client):
    """不带 circular（旧客户端 / FASTA 拓扑未知）保持线性行为"""
    data = _digest(client, PUC19, ["EcoRI", "HindIII"])
    assert data["circular"] is False
    assert data["total_fragments"] == 3
    assert data["fragments"][-1]["cut_by"] == []


def test_origin_spanning_site_only_when_circular(client):
    seq = "ATTC" + "A" * 40 + "AAGCTT" + "C" * 40 + "GA"  # EcoRI G|AATTC 跨原点
    lin = _digest(client, seq, ["EcoRI"], circular=False)
    assert lin["total_fragments"] == 0
    circ = _digest(client, seq, ["EcoRI", "HindIII"], circular=True)
    assert circ["total_fragments"] == 2
    assert set(circ["enzymes_with_sites"]) == {"EcoRI", "HindIII"}
    eco_end = [f for f in circ["fragments"] if f["cut_by"] == ["EcoRI"]][0]
    assert eco_end["end"] == len(seq) - 1  # 切在倒数第二位的 G 之后
    assert sum(f["size"] for f in circ["fragments"]) == len(seq)


def test_cut_at_origin_does_not_create_empty_fragment(client):
    seq = "AATTC" + "T" * 30 + "G"  # 环状上 G|AATTC 恰好切在原点
    circ = _digest(client, seq, ["EcoRI"], circular=True)
    assert circ["fragments"] == [
        {"start": 1, "end": len(seq), "size": len(seq), "cut_by": ["EcoRI"]}]


def test_linear_type_iis_cut_outside_sequence_ignored(client):
    # BsaI GGTCTC(N1) 切在识别序列后 1/5 位：贴近 3' 末端时切点落在序列外
    seq = "A" * 30 + "GGTCTC"
    lin = _digest(client, seq, ["BsaI"], circular=False)
    assert lin["total_fragments"] == 0
    circ = _digest(client, seq, ["BsaI"], circular=True)
    assert circ["total_fragments"] == 1


def test_reverse_type_iis_uses_top_strand_cut(client):
    """反链 BsaI 的边界取正向链切点，与 Bio.Restriction 一致"""
    seq = "A" * 20 + "GAGACC" + "A" * 20
    data = _digest(client, seq, ["BsaI"], circular=False)
    sizes = sorted(f["size"] for f in data["fragments"])
    assert sizes == _bio_sizes(seq, ["BsaI"], circular=False)
    assert data["fragments"][0]["end"] == 15


def test_random_plasmids_match_biopython(client):
    rng = random.Random(11)
    menu = ["EcoRI", "BamHI", "HindIII", "PstI", "BsaI", "BbsI", "AvaI", "XcmI", "PvuII"]
    for _ in range(15):
        seq = "".join(rng.choice("ACGT") for _ in range(rng.randint(1500, 4000)))
        enzymes = rng.sample(menu, 3)
        for circular in (False, True):
            resp = client.post("/api/analysis/digest", json={
                "sequence": seq, "enzymes": enzymes, "circular": circular})
            data = resp.json()
            got = sorted(f["size"] for f in data["fragments"])
            assert got == _bio_sizes(seq, enzymes, circular), (enzymes, circular)
            if got:
                assert sum(got) == len(seq)
