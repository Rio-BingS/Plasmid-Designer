"""API 文档默认不对 Docker 部署公网开放（审查 10-docs-compat L7）

此前 deploy/docker/nginx.conf 无条件反代 /docs 与 /openapi.json，容器内无法按
来源限制（宿主机经发布端口进入时来源都是 compose 网关），文档还引导用户从
公网访问 /docs。现由后端 ENABLE_API_DOCS 控制，Docker compose 默认关闭。
"""

import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings

ROOT = Path(__file__).resolve().parents[1]
DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


def test_disabled_docs_return_404():
    from app.main import api_docs_kwargs

    client = TestClient(FastAPI(**api_docs_kwargs(False)))
    for path in DOC_PATHS:
        assert client.get(path).status_code == 404, path


def test_enabled_docs_served():
    from app.main import api_docs_kwargs

    client = TestClient(FastAPI(**api_docs_kwargs(True)))
    for path in DOC_PATHS:
        assert client.get(path).status_code == 200, path


def test_app_follows_setting():
    from app.config import settings
    from app.main import app

    assert (app.openapi_url is not None) is settings.ENABLE_API_DOCS
    assert (app.docs_url is not None) is settings.ENABLE_API_DOCS
    assert (app.redoc_url is not None) is settings.ENABLE_API_DOCS


def test_local_dev_default_keeps_docs():
    # 本地开发（未设变量）保持 /docs 可用；生产由部署配置显式关闭
    assert Settings.model_fields["ENABLE_API_DOCS"].default is True


def test_docker_compose_disables_docs_by_default():
    compose = (ROOT / "deploy/docker/docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"-\s*ENABLE_API_DOCS=\$\{ENABLE_API_DOCS:-false\}", compose)
    env = (ROOT / "deploy/docker/.env.example").read_text(encoding="utf-8")
    assert re.search(r"^ENABLE_API_DOCS=false$", env, re.M)
