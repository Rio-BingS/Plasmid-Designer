"""批量测序 read 上限环境变量须能在两种部署方式下生效

Docker 后端容器只拿到 compose environment 列出的变量：此前
SEQUENCING_BATCH_MAX_* 未透传，按文档在 .env 调大对容器不生效。
"""

from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
VARS = ("SEQUENCING_BATCH_MAX_READS_PER_GROUP", "SEQUENCING_BATCH_MAX_TOTAL_READS",
        "SEQUENCING_BATCH_READS_PER_GROUP_HARD_MAX", "SEQUENCING_BATCH_READS_HARD_MAX")


@pytest.mark.parametrize("var", VARS)
def test_compose_passes_read_limit_env(var):
    compose = (DEPLOY / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"- {var}=${{{var}:-" in compose


@pytest.mark.parametrize("env_file", ["docker/.env.example", "bare/.env.example"])
def test_env_examples_document_read_limits(env_file):
    text = (DEPLOY / env_file).read_text(encoding="utf-8")
    for var in VARS:
        assert f"\n{var}=" in text, (env_file, var)
