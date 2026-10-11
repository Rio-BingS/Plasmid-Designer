"""环状拓扑开关确实改变结果（审查 07 M-1 的回归锁）

- 批量/离线共用的 run_batch_groups：按参考拓扑决定是否环状比对
  （GenBank 标 circular 或 FASTA 未标 → 环状；标 linear → 线性）。此前把
  reference_circular(topology) 改成 False、或 FASTA 默认改成线性，测试全绿。
- /analysis/digest 的 circular 字段：同一序列、同一酶，开关不同结果不同。
- 位点扫描 find_enzyme_sites(circular=...)：跨原点位点只在环状时报出。
"""

import os
import random
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from abif_utils import make_ab1  # noqa: E402
from app.analysis_routes import router  # noqa: E402
from app.sequencing_batch import reference_circular, run_batch_groups  # noqa: E402
from core.genbank_format import format_locus_line  # noqa: E402


def _origin_block(seq: str) -> str:
    lines = []
    for i in range(0, len(seq), 60):
        chunk = seq[i:i + 60]
        lines.append(f"{i + 1:>9} " + " ".join(chunk[j:j + 10] for j in range(0, len(chunk), 10)))
    return "\n".join(lines)


def _gb(seq: str, topology: str) -> bytes:
    return (format_locus_line("TopoRef", len(seq), topology=topology, date="01-JAN-2026") + "\n"
            "DEFINITION  topology test.\nACCESSION   toporef\n"
            "FEATURES             Location/Qualifiers\n"
            f"     source          1..{len(seq)}\n"
            "                     /organism=\"synthetic construct\"\n"
            f"ORIGIN\n{_origin_block(seq)}\n//\n").encode()


def _wrap_case():
    rng = random.Random(17)
    ref = "".join(rng.choice("ACGT") for _ in range(900))
    read = list(ref[750:] + ref[:200])             # 跨原点 350 bp
    read[100] = "A" if read[100] != "A" else "C"   # 参考 851
    read[250] = "A" if read[250] != "A" else "C"   # 参考 101
    return ref, "".join(read)


# ---------- 批量路径 ----------

def test_reference_circular_decision_table():
    assert reference_circular(True) is True
    assert reference_circular(None) is True    # FASTA 未标拓扑按环状（产品决策）
    assert reference_circular(False) is False


def _run_batch(ref_name: str, ref_bytes: bytes, read: str):
    captured = {}

    def register(label, ref_seq, features, res):
        captured["res"] = res
        return "aid-1", {"label": label}

    group = {
        "plasmid": "P1", "clone": "",
        "reference": {"file": {"name": ref_name, "bytes": ref_bytes}},
        "reads": [{"file": {"name": "P1_w.ab1", "bytes": make_ab1(read, [40] * len(read))}}],
    }
    items, records = run_batch_groups([group], min_q=20, register=register)
    assert items[0]["status"] == "analyzed", items[0]
    assert "aid-1" in records
    return captured["res"]


@pytest.mark.parametrize("name,blob_fn", [
    ("P1.gb", lambda ref: _gb(ref, "circular")),
    ("P1.fasta", lambda ref: f">P1\n{ref}\n".encode()),
])
def test_batch_circular_reference_wraps_origin(name, blob_fn):
    ref, read = _wrap_case()
    res = _run_batch(name, blob_fn(ref), read)
    assert res["circular"] is True
    assert [list(r) for r in res["coverage_ranges"]] == [[1, 200], [751, 900]]
    # 原点之后（参考 101）的差异也必须报出，线性比对会把这段截掉
    assert sorted(v["ref_pos"] for v in res["variants"]) == [101, 851]


def test_batch_linear_genbank_does_not_wrap():
    ref, read = _wrap_case()
    res = _run_batch("P1.gb", _gb(ref, "linear"), read)
    assert res["circular"] is False
    assert [list(r) for r in res["coverage_ranges"]] != [[1, 200], [751, 900]]
    assert len(res["variants"]) < 2


# ---------- 消化模拟 / 位点扫描 ----------

@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_digest_flag_changes_fragments(client):
    seq = "A" * 30 + "GAATTC" + "C" * 60 + "AAGCTT" + "T" * 30
    sizes = {}
    for circular in (False, True):
        r = client.post("/api/analysis/digest", json={
            "sequence": seq, "enzymes": ["EcoRI", "HindIII"], "circular": circular})
        assert r.status_code == 200, r.text
        assert r.json()["circular"] is circular
        sizes[circular] = sorted(f["size"] for f in r.json()["fragments"])
    assert len(sizes[False]) == 3 and len(sizes[True]) == 2
    assert sum(sizes[False]) == sum(sizes[True]) == len(seq)
    assert sizes[True] != sizes[False]


def test_site_scan_origin_site_only_when_circular():
    from core.enzyme_sites import find_enzyme_sites
    seq = "ATTC" + "A" * 40 + "C" * 40 + "GA"  # G|AATTC 跨原点
    assert find_enzyme_sites(seq, ["EcoRI"], circular=False) == []
    hits = [h for h in find_enzyme_sites(seq, ["EcoRI"], circular=True) if h["strand"] == "+"]
    assert [h["position"] for h in hits] == [len(seq) - 1]
