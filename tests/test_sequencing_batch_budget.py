"""批量测序计算预算与墙钟软时限回归锁（审查 02 M5/M6 余项）

read 数上限管不住「大参考 × 多 read」：每组 read 数合规、但参考 200 kb 时
单批仍可占住并发槽数十分钟到数小时。比对前按 Σ(参考长度 × read 数) 估算，
超预算 413；运行中超过软时限的剩余分组不再比对。单请求入口与分块上传
finalize 共用同一管线，两条路径都要生效。图谱只解析一次。
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app import sequencing_batch
from app.main import app
from app.routes import sequencing_routes as sr
from tests.abif_utils import make_ab1

REF_A = "ATGAAACGT" * 12 + "TAA"   # 111 bp
REF_B = "ATGCCCGGT" * 12 + "TAA"
BASE = "/api/sequencing/batch-sessions"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    from app import site_settings
    from app.features import ALL_FEATURES as _ALL

    state = {"registration_open": True, "email_verification_required": False,
             "anonymous_features": list(_ALL), "user_features": list(_ALL)}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(state)))
    monkeypatch.setenv("SEQUENCING_BATCH_SESSION_DIR", str(tmp_path / "sessions"))
    calls = {"analyze": 0, "parse": 0}
    real_analyze = sequencing_batch.analyze
    real_parse = sequencing_batch.parse_reference_topology

    def analyze(*a, **kw):
        calls["analyze"] += 1
        return real_analyze(*a, **kw)

    def parse(*a, **kw):
        calls["parse"] += 1
        return real_parse(*a, **kw)

    monkeypatch.setattr(sequencing_batch, "analyze", analyze)
    monkeypatch.setattr(sequencing_batch, "parse_reference_topology", parse)
    return calls


def _files():
    return [("PA.fasta", f">PA\n{REF_A}\n".encode()),
            ("PB.fasta", f">PB\n{REF_B}\n".encode()),
            ("PA-1.ab1", make_ab1(REF_A, [40] * len(REF_A))),
            ("PA-2.ab1", make_ab1(REF_A, [40] * len(REF_A))),
            ("PB-1.ab1", make_ab1(REF_B, [40] * len(REF_B)))]


def _post(blobs):
    return TestClient(app).post(
        "/api/sequencing/analyze-batch", data={"min_q": "20"},
        files=[("files", (n, b, "application/octet-stream")) for n, b in blobs])


# 本批次计算量：PA 111 bp × 2 + PB 111 bp × 1 = 333 bp·read
COST = 333


def test_estimate_uses_parsed_reference_length():
    groups = [
        {"plasmid": "A", "reference": {"file": {}}, "reads": [1, 2]},
        {"plasmid": "B", "reference": {"file": {}}, "reads": [1]},
        {"plasmid": "C", "reference": None, "reads": [1, 2, 3]},      # 无图谱不比对
        {"plasmid": "D", "reference": {"file": {}}, "reads": [1]},    # 解析失败
    ]
    cache = {"A": ("A" * 100, [], True), "B": ("B" * 1000, [], True),
             "D": ValueError("bad")}
    assert sequencing_batch.estimate_batch_cost(groups, cache) == 100 * 2 + 1000


def test_over_budget_413_before_any_alignment(env, monkeypatch):
    monkeypatch.setattr(sr, "BATCH_COMPUTE_BUDGET_MBP", (COST - 1) / 1e6)
    r = _post(_files())
    assert r.status_code == 413, r.text
    detail = r.json()["detail"]
    assert "Mbp·read" in detail and "拆分为多个批次" in detail
    assert env["analyze"] == 0


def test_within_budget_parses_each_reference_once(env, monkeypatch):
    monkeypatch.setattr(sr, "BATCH_COMPUTE_BUDGET_MBP", COST / 1e6)
    r = _post(_files())
    assert r.status_code == 200, r.text
    assert [it["status"] for it in r.json()["items"]] == ["analyzed", "analyzed"]
    assert env["parse"] == 2          # 预算估算与比对共用一次解析
    assert env["analyze"] == 2


def test_zero_budget_disables(env, monkeypatch):
    monkeypatch.setattr(sr, "BATCH_COMPUTE_BUDGET_MBP", 0)
    assert _post(_files()).status_code == 200


def test_budget_enforced_at_session_finalize(env, monkeypatch):
    monkeypatch.setattr(sr, "BATCH_COMPUTE_BUDGET_MBP", (COST - 1) / 1e6)
    c = TestClient(app)
    body = c.post(BASE).json()
    sid, hdr = body["session_id"], {"X-Access-Token": body["access_token"]}
    up = c.post(f"{BASE}/{sid}/files", headers=hdr, data={"chunk_index": "0"},
                files=[("files", (n, b, "application/octet-stream")) for n, b in _files()])
    assert up.status_code == 200, up.text
    r = c.post(f"{BASE}/{sid}/finalize", data={"min_q": "20"}, headers=hdr)
    assert r.status_code == 413, r.text
    assert "Mbp·read" in r.json()["detail"]
    assert env["analyze"] == 0
    st = c.get(f"{BASE}/{sid}", headers=hdr).json()
    assert st["state"] == "failed" and st["error"]["status_code"] == 413


def test_soft_timeout_skips_remaining_groups(env, monkeypatch):
    real = sequencing_batch.analyze

    def slow(*a, **kw):
        time.sleep(0.2)
        return real(*a, **kw)

    monkeypatch.setattr(sequencing_batch, "analyze", slow)
    monkeypatch.setattr(sr, "BATCH_SOFT_TIMEOUT", 0.1)
    r = _post(_files())
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items[0]["status"] == "analyzed" and items[0]["analysis_id"]
    assert items[1]["status"] == "failed"
    assert "超过" in items[1]["conclusion"] and "时限" in items[1]["conclusion"]
    assert items[1]["conclusion"].startswith("分析失败：")
    assert env["analyze"] == 1        # 计数器在被替换前的包装里：只跑了第一组


def test_budget_env_documented_and_passed_to_docker():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    compose = (root / "deploy/docker/docker-compose.yml").read_text(encoding="utf-8")
    for var, default in (("SEQUENCING_BATCH_COMPUTE_BUDGET_MBP", "50"),
                         ("SEQUENCING_BATCH_SOFT_TIMEOUT", "1800")):
        assert f"- {var}=${{{var}:-{default}}}" in compose
        for env in ("deploy/docker/.env.example", "deploy/bare/.env.example"):
            assert f"\n{var}={default}\n" in (root / env).read_text(encoding="utf-8")
