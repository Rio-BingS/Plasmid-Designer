"""批量整理包落盘回归锁（审查 02 I2：多 worker 下整理包下载随机 404）

整理包此前放在各 worker 进程自己的内存（_BATCHES）里，BACKEND_WORKERS>1
时下载请求落到另一个 worker 就 404。现在写入私有目录（0700/0600），任一
进程都能按 batch_id 读到；TTL 与条数/总字节上限对整个服务生效，属主校验
不变。
"""

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import batch_report_store as brs
from app.main import app
from app.routes import sequencing_routes as sr
from app.routes import sequencing_session_routes as ssr
from tests.abif_utils import make_ab1

REF = "ATGAAACGT" * 12 + "TAA"
BACKEND = Path(__file__).resolve().parents[1] / "src" / "backend"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    from app import site_settings
    from app.features import ALL_FEATURES as _ALL

    state = {"registration_open": True, "email_verification_required": False,
             "anonymous_features": list(_ALL), "user_features": list(_ALL)}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(state)))
    root = tmp_path / "reports"
    monkeypatch.setenv("SEQUENCING_BATCH_REPORT_DIR", str(root))
    return root


def _post():
    files = [("files", ("MX.fasta", f">MX\n{REF}\n".encode(), "application/octet-stream")),
             ("files", ("MX-1.ab1", make_ab1(REF, [40] * len(REF)), "application/octet-stream"))]
    r = TestClient(app).post("/api/sequencing/analyze-batch", files=files, data={"min_q": "20"})
    assert r.status_code == 200, r.text
    return r.json()


def _get(bid, token=None):
    h = {"X-Access-Token": token} if token else {}
    return TestClient(app).get(f"/api/sequencing/batches/{bid}/report", headers=h)


def _assert_private(path, mode: int) -> None:
    """断言私有权限位。

    只在 POSIX 上逐位校验：Windows 的文件模式不表达 rwx（目录恒报 0o777、
    chmod 只切只读位），同样的代码在 Linux 上给出 0o700/0o600。
    其余（目录/文件确实落在私有数据目录、内容正确）两个平台都照常断言。
    """
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(path).st_mode) == mode, f"{path} 权限位不是 {oct(mode)}"


def test_report_written_to_private_dir_and_not_kept_in_memory(env):
    body = _post()
    bid = body["batch_id"]
    assert body["report_ready"] is True
    assert not hasattr(sr, "_BATCHES")
    zip_path, meta_path = env / f"{bid}.zip", env / f"{bid}.json"
    assert zip_path.is_file() and meta_path.is_file()
    _assert_private(env, 0o700)
    _assert_private(zip_path, 0o600)
    _assert_private(meta_path, 0o600)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["access_token_hash"] and body["access_token"] not in meta_path.read_text()
    r = _get(bid, body["access_token"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.content == zip_path.read_bytes()
    assert r.headers["content-length"] == str(len(r.content))
    assert "filename*=UTF-8''" in r.headers["content-disposition"]
    assert _get(bid).status_code == 404                     # 无令牌仍是 404


def test_report_written_by_another_process_is_served(env):
    """模拟另一个 worker：独立进程写入整理包，本进程照样提供下载"""
    token = "tok-from-other-worker"
    bid = "seqbatch_0123456789ab"
    code = (
        "import sys, time; sys.path.insert(0, sys.argv[1]);"
        "from app import batch_report_store as b, sequencing_store as s;"
        "assert b.save(sys.argv[2], b'PK-other-worker', {'created_ts': time.time(),"
        " 'zip_name': 'z', 'owner_id': None,"
        " 'access_token_hash': s.hash_access_token(sys.argv[3])})"
    )
    subprocess.run([sys.executable, "-c", code, str(BACKEND), bid, token],
                   check=True, env={**os.environ, "SEQUENCING_BATCH_REPORT_DIR": str(env)},
                   timeout=60)
    r = _get(bid, token)
    assert r.status_code == 200 and r.content == b"PK-other-worker"
    assert _get(bid, "wrong").status_code == 404


def test_expired_report_404_even_before_sweep(env, monkeypatch):
    body = _post()
    bid = body["batch_id"]
    monkeypatch.setattr(sr, "_sweep_reports", lambda: 0)    # 不靠清理也要判过期
    meta = brs.load_meta(bid)
    brs.save(bid, b"old", {**meta, "created_ts": time.time() - sr.BATCH_TTL - 5})
    r = _get(bid, body["access_token"])
    assert r.status_code == 404 and "过期" in r.json()["detail"]


def test_periodic_sweep_removes_expired_reports(env):
    brs.save("seqbatch_aaaaaaaaaaaa", b"x", {"created_ts": time.time() - sr.BATCH_TTL - 5})
    brs.save("seqbatch_bbbbbbbbbbbb", b"y", {"created_ts": time.time()})
    (env / "seqbatch_cccccccccccc.zip.1234abcd.tmp").write_bytes(b"partial")
    os.utime(env / "seqbatch_cccccccccccc.zip.1234abcd.tmp",
             (time.time() - sr.BATCH_TTL - 5,) * 2)
    ssr.sweep_now()
    assert sorted(p.name for p in env.iterdir()) == [
        "seqbatch_bbbbbbbbbbbb.json", "seqbatch_bbbbbbbbbbbb.zip"]


@pytest.mark.parametrize("bad", ["..", "seqbatch_../../etc", "seqbatch_ABCDEF012345", "x" * 40])
def test_malformed_batch_id_404(bad):
    assert brs.load_meta(bad) is None and brs.open_zip(bad) is None
    with pytest.raises(ValueError):
        brs.save(bad, b"x", {})


def test_unwritable_dir_disables_download_but_keeps_result(env, monkeypatch):
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_bytes(b"not a dir")                           # 目录位置被普通文件占用
    body = _post()
    assert body["report_ready"] is False
    assert body["items"][0]["status"] == "analyzed"


def test_session_finalize_report_downloadable(env, monkeypatch, tmp_path):
    monkeypatch.setenv("SEQUENCING_BATCH_SESSION_DIR", str(tmp_path / "sessions"))
    c = TestClient(app)
    created = c.post("/api/sequencing/batch-sessions").json()
    sid, hdr = created["session_id"], {"X-Access-Token": created["access_token"]}
    files = [("files", ("MX.fasta", f">MX\n{REF}\n".encode(), "application/octet-stream")),
             ("files", ("MX-1.ab1", make_ab1(REF, [40] * len(REF)), "application/octet-stream"))]
    assert c.post(f"/api/sequencing/batch-sessions/{sid}/files", files=files,
                  data={"chunk_index": "0"}, headers=hdr).status_code == 200
    r = c.post(f"/api/sequencing/batch-sessions/{sid}/finalize", data={"min_q": "20"},
               headers=hdr)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report_ready"] is True
    got = _get(body["batch_id"], body["access_token"])
    assert got.status_code == 200 and got.content[:2] == b"PK"
