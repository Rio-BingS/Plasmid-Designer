"""部署 .env 权限回归锁（审查 03 M1）

.env 含 SECRET_KEY、DB/Redis 口令与邮件凭据。此前 docker 路径的 deploy.sh
直接 cp 出 0644 的 .env，本机任意用户可读；裸机 install.sh 虽事后 chmod 600，
但 cat > 在 umask 022 下先建成 0644。这里固定 umask 022 跑 deploy.sh 黑盒，
并静态核对 install.sh / 手动部署文档。
"""

import os
import re
import stat
from pathlib import Path

import pytest

from tests.test_deploy_db_password import GOOD, _run, pytestmark  # noqa: F401

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def umask_022():
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


def _mode(tmp_path):
    env = tmp_path / "repo" / "deploy" / "docker" / ".env"
    return stat.S_IMODE(env.stat().st_mode)


def test_fresh_env_is_0600(tmp_path, umask_022):
    proc, env, _ = _run(tmp_path, env_text=None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _mode(tmp_path) == 0o600


def test_existing_0644_env_is_tightened(tmp_path, umask_022):
    # 旧版脚本/手动 cp 留下的 0644 .env：重跑应收紧，且已有值不变
    proc, env, _ = _run(tmp_path, env_text=GOOD + "DB_PASSWORD=keepme123\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _mode(tmp_path) == 0o600
    assert "DB_PASSWORD=keepme123" in env


def test_env_tightened_even_when_db_refused(tmp_path, umask_022):
    # 拒绝替换 DB 口令时脚本提前退出，密钥文件仍应先被收紧
    proc, _, _ = _run(
        tmp_path, env_text=GOOD + "DB_PASSWORD=\n", volumes="docker_postgres-data"
    )
    assert proc.returncode != 0
    assert _mode(tmp_path) == 0o600


def test_bare_install_creates_env_with_private_umask():
    txt = (REPO / "deploy" / "bare" / "install.sh").read_text(encoding="utf-8")
    # 所有写 .env 的重定向都必须在 umask 077 子 shell 内
    writes = re.findall(r"^.*>\s*\"\$INSTALL_DIR/\.env\".*$", txt, re.M)
    assert writes, "install.sh 未找到生成 .env 的语句"
    for line in writes:
        assert "umask 077" in line, line
    assert 'chmod 600 "$INSTALL_DIR/.env"' in txt


def test_manual_docs_create_env_private():
    docker = (REPO / "deploy" / "docker" / "README.md").read_text(encoding="utf-8")
    assert "\ncp .env.example .env\n" not in docker
    assert "chmod 600 .env" in docker
    bare = (REPO / "deploy" / "bare" / "README.md").read_text(encoding="utf-8")
    assert "sudo cp /opt/plasmid-designer/deploy/bare/.env.example" not in bare
    assert "install -m 600" in bare
