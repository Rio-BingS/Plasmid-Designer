"""批量测序分块上传的部署配置回归锁

- 两份 nginx 为 /api/sequencing/batch-sessions 单独放行 ≥90 MB 分块
  （外加 multipart 开销）且读超时覆盖 finalize 的请求内等待；
- Docker 暂存目录走具名卷（镜像内预建、属 plasmid）：宿主机 bind mount 的
  data/ 常属 root，非 root 容器写不进去；
- 新环境变量在 compose 透传、在两份 .env.example 有说明。
"""

import re
from pathlib import Path

import pytest

from app import sequencing_upload_sessions as store
from app.routes import sequencing_session_routes as ssr

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
VARS = ("SEQUENCING_BATCH_SESSION_MAX_MB", "SEQUENCING_BATCH_SESSIONS_PER_OWNER",
        "SEQUENCING_BATCH_SESSIONS_TOTAL", "SEQUENCING_BATCH_FINALIZE_WAIT")


def _size_mb(v: str) -> int:
    m = re.fullmatch(r"(\d+)([mMkKgG]?)", v)
    n, unit = int(m.group(1)), m.group(2).lower()
    return n * 1024 if unit == "g" else (n // 1024 if unit == "k" else n)


def _secs(v: str) -> int:
    m = re.fullmatch(r"(\d+)(s|m)?", v)
    return int(m.group(1)) * (60 if m.group(2) == "m" else 1)


@pytest.mark.parametrize("conf", ["docker/nginx.conf", "bare/nginx.conf"])
def test_nginx_session_location_fits_chunks(conf):
    text = (DEPLOY / conf).read_text(encoding="utf-8")
    m = re.search(r"location \^~ /api/sequencing/batch-sessions \{(.*?)\n    \}", text, re.S)
    assert m, conf
    body = m.group(1)
    size = re.search(r"client_max_body_size (\S+);", body).group(1)
    assert _size_mb(size) >= store.CHUNK_MAX_BYTES // (1024 * 1024) + 5
    read_timeout = _secs(re.search(r"proxy_read_timeout (\S+);", body).group(1))
    assert read_timeout > ssr.FINALIZE_WAIT
    assert "X-Forwarded-Proto" in body and "X-Real-IP" in body


def test_compose_uses_named_volume_for_session_dir():
    compose = (DEPLOY / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "- batch-uploads:/app/batch_uploads" in compose
    assert "- SEQUENCING_BATCH_SESSION_DIR=/app/batch_uploads" in compose
    assert re.search(r"^  batch-uploads:\n    driver: local", compose, re.M)
    dockerfile = (DEPLOY / "docker" / "Dockerfile.backend").read_text(encoding="utf-8")
    mk = dockerfile.index("mkdir -p /app/batch_uploads")
    assert mk < dockerfile.index("chown -R plasmid:plasmid /app")   # 预建后统一改属主


@pytest.mark.parametrize("var", VARS)
def test_compose_passes_session_env(var):
    compose = (DEPLOY / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"- {var}=${{{var}:-" in compose


@pytest.mark.parametrize("env_file", ["docker/.env.example", "bare/.env.example"])
def test_env_examples_document_session_vars(env_file):
    text = (DEPLOY / env_file).read_text(encoding="utf-8")
    for var in VARS:
        assert f"\n{var}=" in text, (env_file, var)


def test_finalize_wait_below_cloudflare_timeout():
    # Cloudflare 免费版源站响应超时固定 100 秒（524）
    assert ssr.FINALIZE_WAIT < 100
