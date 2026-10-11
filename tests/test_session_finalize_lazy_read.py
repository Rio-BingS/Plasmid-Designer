"""分块会话 finalize 按需读取暂存文件回归锁（审查 02 M5 余项）

finalize 此前把整批（单批上限 2 GB）一次读进内存再分析。现在暂存文件以
DiskBlob 进入管线，条目内容按需读取：比对第一组时，后续分组的文件尚未
读入；整理包与单请求入口逐字节一致。
"""

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import sequencing_batch
from app import sequencing_upload_sessions as store
from app.main import app
from tests.abif_utils import make_ab1

REF_A = "ATGAAACGT" * 12 + "TAA"
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
    monkeypatch.setenv("SEQUENCING_BATCH_REPORT_DIR", str(tmp_path / "reports"))


def _blobs():
    return [("PA.fasta", f">PA\n{REF_A}\n".encode()),
            ("PB.fasta", f">PB\n{REF_B}\n".encode()),
            ("PA-1.ab1", make_ab1(REF_A, [40] * len(REF_A))),
            ("PB-1.ab1", make_ab1(REF_B, [40] * len(REF_B))),
            ("orphan.ab1", make_ab1(REF_A[:60], [40] * 60)),
            ("empty.ab1", b""),
            ("说明.txt", b"notes")]


def _multipart(blobs):
    return [("files", (n, b, "application/octet-stream")) for n, b in blobs]


def _session(c, blobs):
    body = c.post(BASE).json()
    sid, hdr = body["session_id"], {"X-Access-Token": body["access_token"]}
    up = c.post(f"{BASE}/{sid}/files", files=_multipart(blobs),
                data={"chunk_index": "0"}, headers=hdr)
    assert up.status_code == 200, up.text
    return sid, hdr


def test_read_all_returns_disk_blobs_not_bytes():
    c = TestClient(app)
    sid, _hdr = _session(c, _blobs())
    blobs, excel = store.read_all(store.load_meta(sid))
    assert excel is None
    assert all(isinstance(b, store.DiskBlob) for _n, b in blobs)
    assert [b.size for _n, b in blobs] == [len(b) for _n, b in _blobs()]


def test_later_group_files_not_read_while_first_group_aligns(monkeypatch):
    c = TestClient(app)
    sid, hdr = _session(c, _blobs())
    reads = []
    real_read = store.DiskBlob.read

    def spy(self):
        reads.append(self.path)
        return real_read(self)

    monkeypatch.setattr(store.DiskBlob, "read", spy)
    names = {}
    meta = store.load_meta(sid)
    for e in meta["files"]:
        names[e["disk"]] = e["name"]
    seen_at_first_analyze = []
    real_analyze = sequencing_batch.analyze

    def analyze(*a, **kw):
        if not seen_at_first_analyze:
            seen_at_first_analyze.append({names[p.rsplit("/", 1)[-1]] for p in reads})
        return real_analyze(*a, **kw)

    monkeypatch.setattr(sequencing_batch, "analyze", analyze)
    r = c.post(f"{BASE}/{sid}/finalize", data={"min_q": "20"}, headers=hdr)
    assert r.status_code == 200, r.text
    first = seen_at_first_analyze[0]
    assert "PA-1.ab1" in first
    assert "PB-1.ab1" not in first and "orphan.ab1" not in first
    assert "empty.ab1" not in {names[p.rsplit("/", 1)[-1]] for p in reads}


def _zip_files(content):
    zf = zipfile.ZipFile(io.BytesIO(content))
    out = {}
    for n in zf.namelist():
        if n.endswith("/") or n.endswith(".md") or n.endswith(".json") or n.endswith(".txt") \
                or n.endswith(".csv"):
            continue  # 报告/清单/总览含批次 id 与时间，逐字节比较只看原始文件副本
        out[n] = zf.read(n)
    return out


def test_chunked_report_zip_matches_single_request():
    c = TestClient(app)
    single = c.post("/api/sequencing/analyze-batch", files=_multipart(_blobs()),
                    data={"min_q": "20"})
    assert single.status_code == 200, single.text
    sb = single.json()
    z1 = c.get(f"/api/sequencing/batches/{sb['batch_id']}/report",
               headers={"X-Access-Token": sb["access_token"]})
    sid, hdr = _session(c, _blobs())
    fin = c.post(f"{BASE}/{sid}/finalize", data={"min_q": "20"}, headers=hdr)
    assert fin.status_code == 200, fin.text
    cb = fin.json()
    z2 = c.get(f"/api/sequencing/batches/{cb['batch_id']}/report",
               headers={"X-Access-Token": cb["access_token"]})
    assert z1.status_code == z2.status_code == 200
    f1, f2 = _zip_files(z1.content), _zip_files(z2.content)
    assert f1 and f1 == f2
    assert sorted(zipfile.ZipFile(io.BytesIO(z1.content)).namelist()) == \
        sorted(zipfile.ZipFile(io.BytesIO(z2.content)).namelist())
