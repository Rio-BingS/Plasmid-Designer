"""docker 发布端口绑定回归锁（审查 03 M4）

此前前端容器端口写成 "${FRONTEND_PORT:-80}:80"，发布在 0.0.0.0。Docker 在
自己的 iptables 链里 DNAT，ufw 拦不住，宿主机 TLS 前置可被明文端口绕过。
现在所有发布端口都显式绑定地址，前端默认 127.0.0.1，可由 .env 的
FRONTEND_BIND 改为 0.0.0.0。沙箱无 docker：静态解析 compose，deploy.sh 用
test_deploy_db_password 的假 docker 黑盒跑。
"""

import re
from pathlib import Path

from tests.test_deploy_db_password import GOOD, _run, pytestmark  # noqa: F401

DOCKER = Path(__file__).resolve().parents[1] / "deploy" / "docker"
COMPOSE = (DOCKER / "docker-compose.yml").read_text(encoding="utf-8")


def _published_ports():
    out = []
    for m in re.finditer(r"^(\s*)ports:\n((?:\1\s+- .*\n)+)", COMPOSE, re.M):
        out += re.findall(r'-\s*"([^"]+)"', m.group(2))
    return out


def test_every_published_port_binds_explicit_host():
    ports = _published_ports()
    assert len(ports) >= 4, ports
    for spec in ports:
        # host_ip:host_port:container_port，host_ip 不得缺省（缺省即 0.0.0.0）
        parts = re.split(r":(?![^{]*\})", spec)
        assert len(parts) == 3, spec
        assert parts[0] not in ("", "0.0.0.0"), spec


def test_frontend_binds_loopback_by_default():
    assert '"${FRONTEND_BIND:-127.0.0.1}:${FRONTEND_PORT:-80}:80"' in COMPOSE
    env = (DOCKER / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^FRONTEND_BIND=127\.0\.0\.1$", env, re.M)


def test_docs_explain_ufw_bypass():
    readme = (DOCKER / "README.md").read_text(encoding="utf-8")
    assert "FRONTEND_BIND=0.0.0.0" in readme
    assert "ufw 管不住 Docker 发布的端口" in readme


def test_deploy_warns_when_frontend_is_loopback_only(tmp_path):
    proc, _, _ = _run(tmp_path, env_text=GOOD + "DB_PASSWORD=keepme123\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "发布于 127.0.0.1" in proc.stdout
    assert "FRONTEND_BIND=0.0.0.0" in proc.stdout


def test_deploy_no_warning_when_public_bind_chosen(tmp_path):
    proc, _, _ = _run(
        tmp_path, env_text=GOOD + "DB_PASSWORD=keepme123\nFRONTEND_BIND=0.0.0.0\n"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "发布于 0.0.0.0" in proc.stdout
    assert "公网无法直连" not in proc.stdout
