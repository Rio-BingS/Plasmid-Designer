"""匿名测序记录访问加固回归锁（令牌比较 / 遗留记录 / 整理包 / 令牌存储）"""

from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app
from app.routes import sequencing_routes as sr


def _fake_result():
    return {"engine": "internal", "conclusion": "ok", "reads": [], "variants": [],
            "consensus": {"sequence": "ACGT" * 20, "coverage_percent": 100.0},
            "coverage_ranges": [], "errors": [], "traces": []}


def _anon_record():
    return sr._register_analysis("x", "ACGT" * 20, [], _fake_result(), owner_id=None)


def test_non_ascii_token_is_403_not_500():
    aid, _tok, _ = _anon_record()
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get(f"/api/sequencing/analyses/{aid}",
              headers={"X-Access-Token": "\u00e9t\u00e9".encode("utf-8")})
    assert r.status_code == 403
    assert not sr._can_access({"owner_id": None, "access_token": "abc"}, None, "\u00e9t\u00e9")
