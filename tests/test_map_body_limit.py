"""/api/map/* 请求体流式体积上限（v2.12.0 审查 M3）

此前 JSON 请求体先 request.body() 整体读入、multipart 先 spool、/analyze 与
/export 由 FastAPI 在进入处理函数前全量解析，只有带 Content-Length 的 /parse
有预检：chunked 传输可绕过，实际上限成了 nginx 的 100 MB。"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import site_settings
from app.features import ALL_FEATURES

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def client(monkeypatch):
    from app.main import app

    s = {"registration_open": True, "email_verification_required": False,
         "anonymous_features": list(ALL_FEATURES), "user_features": list(ALL_FEATURES)}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(s)))
    return TestClient(app)


@pytest.fixture()
def small_limits(monkeypatch):
    from app.routes import map_routes

    monkeypatch.setattr(map_routes, "MAP_MAX_JSON_BYTES", 50_000)
    monkeypatch.setattr(map_routes, "MAP_MAX_UPLOAD_BYTES", 20_000)
    called = []

    def boom(*a, **k):
        called.append(1)
        raise AssertionError("超限请求不应进入处理逻辑")
    monkeypatch.setattr(map_routes, "_analyze", boom)
    monkeypatch.setattr(map_routes, "_parse_and_build", boom)
    return called


def _chunks(total: int, prefix: bytes = b'{"sequence":"', piece: int = 8192):
    yield prefix
    sent = 0
    while sent < total:
        yield b"A" * piece
        sent += piece
    yield b'","topology":"circular"}'


def test_chunked_json_over_limit_is_413_without_reading_all(client, small_limits):
    r = client.post("/api/map/analyze", content=_chunks(200_000),
                    headers={"content-type": "application/json"})
    assert r.status_code == 413 and "过大" in r.json()["detail"]
    assert not small_limits


def test_content_length_over_limit_is_413(client, small_limits):
    body = json.dumps({"sequence": "A" * 60_000, "topology": "circular"})
    r = client.post("/api/map/export/genbank", content=body,
                    headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_chunked_multipart_parse_over_upload_limit(client, small_limits):
    boundary = "xyzBOUNDARY"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"a.fa\"\r\nContent-Type: text/plain\r\n\r\n>x\n").encode()

    def gen():
        yield head
        for _ in range(20):
            yield b"ACGT" * 1024
        yield f"\r\n--{boundary}--\r\n".encode()
    r = client.post("/api/map/parse", content=gen(),
                    headers={"content-type": f"multipart/form-data; boundary={boundary}"})
    assert r.status_code == 413
    assert not small_limits


def test_under_limit_requests_pass_through(client, monkeypatch):
    from app.routes import map_routes

    monkeypatch.setattr(map_routes, "MAP_MAX_JSON_BYTES", 50_000)
    r = client.post("/api/map/analyze", content=_chunks(8192 * 2),
                    headers={"content-type": "application/json"})
    assert r.status_code == 200, r.text
    assert r.json()["length"] == 8192 * 2


def test_other_paths_unaffected(client, monkeypatch):
    """非 /api/map/ 前缀不走这个上限（各自有自己的限制）"""
    from app.map_body_limit import MapBodyLimitMiddleware

    seen = []

    async def app(scope, receive, send):
        seen.append(scope["path"])
    mw = MapBodyLimitMiddleware(app, limit_for=lambda p: 1)
    import anyio

    async def run():
        async def receive():
            return {"type": "http.request", "body": b"x" * 100, "more_body": False}
        await mw({"type": "http", "method": "POST", "path": "/api/design", "headers": []},
                 receive, None)
    anyio.run(run)
    assert seen == ["/api/design"]


@pytest.mark.parametrize("conf", ["deploy/bare/nginx.conf", "deploy/docker/nginx.conf"])
def test_nginx_map_location_body_size_matches_backend_limits(conf):
    from app.routes import map_routes

    text = (ROOT / conf).read_text(encoding="utf-8")
    m = re.search(r"location \^~ /api/map/ \{(.*?)\n    \}", text, re.S)
    assert m, conf
    size = re.search(r"client_max_body_size (\d+)m;", m.group(1))
    assert size
    mb = int(size.group(1))
    need = max(map_routes.MAP_MAX_UPLOAD_MB + 64 / 1024, map_routes.MAP_MAX_JSON_MB)
    assert need <= mb < 100  # 比通用 /api 的 100m 严，又不小于后端上限
    assert text.index("location ^~ /api/map/") < text.index("location /api {")
