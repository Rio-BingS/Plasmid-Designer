"""deploy.sh 数据库口令/健康检查回归锁（审查 03 H1 / 10 H1）

此前 .env 中 DB_PASSWORD 为空或等于旧公开占位值时，deploy.sh 一律换成
新随机值；已有 postgres 数据卷时 PostgreSQL 仍用旧口令，后端连不上库，
脚本照样打印「部署完成」。沙箱/CI 无 docker：这里把脚本拷到临时目录，
在 PATH 前面放假的 docker/curl/sleep，按黑盒方式跑完整脚本。
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    BASH is None or sys.platform.startswith("win"), reason="需要 bash"
)

FAKE_DOCKER = r"""#!/bin/bash
echo "docker $*" >> "$FAKE_LOG"
if [ "$1" = "volume" ] && [ "$2" = "inspect" ]; then
    if [ -n "$FAKE_DOCKER_DOWN" ]; then
        echo "permission denied while trying to connect to the Docker daemon socket" >&2
        exit 1
    fi
    for v in $FAKE_VOLUMES; do
        [ "$v" = "$3" ] && { echo "[{\"Name\": \"$3\"}]"; exit 0; }
    done
    echo "Error: No such volume: $3" >&2
    exit 1
fi
exit 0
"""

FAKE_CURL = r"""#!/bin/bash
echo "curl $*" >> "$FAKE_LOG"
if [ "$FAKE_HEALTH" = "ok" ]; then
    echo '{"status":"healthy","timestamp":"t","storage_mode":"database","db":"ok"}'
    exit 0
fi
# 后端在、库不通：503，curl -f 以 22 退出
exit 22
"""


def _run(tmp_path, env_text=None, volumes="", health="ok", extra_env=None):
    root = tmp_path / "repo"
    (root / "deploy" / "scripts").mkdir(parents=True)
    (root / "deploy" / "docker").mkdir(parents=True)
    shutil.copy(REPO / "deploy" / "scripts" / "deploy.sh", root / "deploy" / "scripts")
    shutil.copy(REPO / "deploy" / "docker" / ".env.example", root / "deploy" / "docker")
    env_file = root / "deploy" / "docker" / ".env"
    if env_text is not None:
        env_file.write_text(env_text, encoding="utf-8")

    fake = tmp_path / "bin"
    fake.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL), ("sleep", "#!/bin/sh\nexit 0\n")):
        p = fake / name
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")

    env = {
        "PATH": f"{fake}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
        "FAKE_LOG": str(log),
        "FAKE_VOLUMES": volumes,
        "FAKE_HEALTH": health,
        "HEALTH_RETRIES": "3",
    }
    env.update(extra_env or {})
    proc = subprocess.run(
        [BASH, str(root / "deploy" / "scripts" / "deploy.sh")],
        env=env, capture_output=True, text=True, timeout=60,
    )
    env_after = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    return proc, env_after, log.read_text(encoding="utf-8")


def _val(env_text, key):
    for line in env_text.splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return None


GOOD = "SECRET_KEY=" + "ab" * 32 + "\nDB_USER=plasmid\nREDIS_PASSWORD=r" + "1" * 31 + "\n"


def test_fresh_install_generates_db_password(tmp_path):
    proc, env, calls = _run(tmp_path, env_text=None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    pw = _val(env, "DB_PASSWORD")
    assert pw and len(pw) == 32
    assert "docker volume inspect docker_postgres-data" in calls
    assert "部署完成" in proc.stdout


@pytest.mark.parametrize("old", ["", "plasmid_secure_2026"])
def test_existing_volume_refuses_to_replace_db_password(tmp_path, old):
    proc, env, calls = _run(
        tmp_path, env_text=GOOD + f"DB_PASSWORD={old}\n", volumes="docker_postgres-data"
    )
    assert proc.returncode != 0
    assert _val(env, "DB_PASSWORD") == old  # 未被改写
    assert "ALTER USER" in proc.stderr
    assert "docker_postgres-data" in proc.stderr
    # 拒绝发生在构建/启动之前
    assert "compose build" not in calls and "compose up" not in calls
    assert "部署完成" not in proc.stdout


def test_existing_volume_keeps_custom_password(tmp_path):
    proc, env, _ = _run(
        tmp_path, env_text=GOOD + "DB_PASSWORD=keepme123\n", volumes="docker_postgres-data"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _val(env, "DB_PASSWORD") == "keepme123"


def test_volume_name_follows_compose_project_name(tmp_path):
    env_text = GOOD + "COMPOSE_PROJECT_NAME=plasmid\nDB_PASSWORD=\n"
    proc, env, calls = _run(tmp_path, env_text=env_text, volumes="plasmid_postgres-data")
    assert "docker volume inspect plasmid_postgres-data" in calls
    assert proc.returncode != 0
    assert _val(env, "DB_PASSWORD") == ""


def test_redis_password_still_generated_when_db_refused(tmp_path):
    # Redis 口令每次启动随命令行生效，可安全生成；先写入后 compose 插值才不报错
    env_text = "SECRET_KEY=" + "ab" * 32 + "\nREDIS_PASSWORD=\nDB_PASSWORD=\n"
    proc, env, _ = _run(tmp_path, env_text=env_text, volumes="docker_postgres-data")
    assert proc.returncode != 0
    assert len(_val(env, "REDIS_PASSWORD")) == 32
    assert _val(env, "DB_PASSWORD") == ""


def test_unreachable_docker_does_not_generate_db_password(tmp_path):
    # daemon 不可达不能当作「无数据卷」，否则新口令先写进 .env，之后无从察觉
    proc, env, _ = _run(
        tmp_path, env_text=GOOD + "DB_PASSWORD=\n", extra_env={"FAKE_DOCKER_DOWN": "1"}
    )
    assert proc.returncode != 0
    assert _val(env, "DB_PASSWORD") == ""
    assert "部署完成" not in proc.stdout


def test_deploy_fails_when_health_db_check_fails(tmp_path):
    proc, _, calls = _run(
        tmp_path, env_text=GOOD + "DB_PASSWORD=keepme123\n", health="down"
    )
    assert proc.returncode != 0
    assert "部署完成" not in proc.stdout
    assert "/health" in calls


def test_install_sh_health_check_requires_db_and_fails():
    txt = (REPO / "deploy" / "bare" / "install.sh").read_text(encoding="utf-8")
    i = txt.index("# ==================== 健康检查")
    block = txt[i:txt.index("# ==================== 完成")]
    assert '"db" *: *"ok"' in block
    assert "exit 1" in block


def test_docker_readme_db_troubleshooting_does_not_suggest_down_v():
    txt = (REPO / "deploy" / "docker" / "README.md").read_text(encoding="utf-8")
    i = txt.index("问题 6: 数据库连接失败")
    section = txt[i:txt.index("### ", i + 1)]
    for line in section.splitlines():
        if "down -v" in line:
            assert "不要" in line, line
    assert "ALTER USER" in section
