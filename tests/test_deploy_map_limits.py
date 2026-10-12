"""图谱上限类环境变量须能在两种部署方式下生效

v2.12.0 审查修复新增的图谱 / NCBI 变量此前只写在 docs/MAP.md：Docker 后端
容器只拿到 compose environment 列出的变量，.env 里调了也不生效。
"""

from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
VARS = ("MAP_AUTO_ANNOTATE_MAX_BP", "MAP_ANNOTATE_MAX_CELLS", "MAP_MAX_SITES_PER_ENZYME",
        "MAP_MAX_TOTAL_SITES", "MAP_CACHE_MAX_DOC_BYTES", "MAP_CACHE_MAX_TOTAL_BYTES",
        "NCBI_MAX_CONCURRENCY", "NCBI_TIMEOUT", "MAP_MAX_JSON_MB")


@pytest.mark.parametrize("var", VARS)
def test_compose_passes_map_limit_env(var):
    compose = (DEPLOY / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"- {var}=${{{var}:-" in compose


@pytest.mark.parametrize("env_file", ["docker/.env.example", "bare/.env.example"])
def test_env_examples_document_map_limits(env_file):
    text = (DEPLOY / env_file).read_text(encoding="utf-8")
    for var in VARS:
        assert f"\n{var}=" in text, (env_file, var)
