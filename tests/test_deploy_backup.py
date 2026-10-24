"""backup.sh 备份脚本回归锁（审查清单「无数据库备份机制」）

黑盒验证 deploy/scripts/backup.sh：
- PostgreSQL 模式（compose 运行中）→ 调 docker compose exec 跑 pg_dump，产出 .sql.gz
- SQLite 模式 → 用 sqlite3 .backup 产出 .db.gz；无 sqlite3 时退回文件拷贝并告警
- 保留策略 --keep N 只留最近 N 份
沙箱/CI 无 docker / postgres：PATH 前面放假的 docker，真实 bash 跑完整脚本。
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKUP = REPO / "deploy" / "scripts" / "backup.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    BASH is None or sys.platform.startswith("win"), reason="需要 bash"
)


@pytest.fixture
def sandbox(tmp_path):
    """隔离环境：假项目根（data/）、假 docker、空 PATH 前缀"""
    project = tmp_path / "proj"
    project.mkdir()
    scripts = project / "deploy" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(BACKUP, scripts / "backup.sh")

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    (fake_bin / "docker").write_text(
        "#!/bin/bash\n"
        'echo "docker $*" >> "$FAKE_LOG"\n'
        'if [ "$1" = "compose" ] && [ "$2" = "ps" ]; then echo "container_id"; exit 0; fi\n'
        'if [ "$1" = "compose" ] && [ "$2" = "exec" ]; then cat; exit 0; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").chmod(0o755)
    for tool in ("gzip", "date", "du", "cut", "mkdir", "grep", "ls", "rm",
                 "tar", "cat", "tr", "tail"):
        src = shutil.which(tool)
        if src:
            (fake_bin / tool).symlink_to(src)
    return project, fake_bin, tmp_path


def run_backup(project, fake_bin, tmp_path, *args, env_extra=None):
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["FAKE_LOG"] = str(tmp_path / "docker.log")
    env.pop("DATABASE_URL", None)
    env.pop("DATA_DIR", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(BASH), *args], cwd=project, env=env,
        capture_output=True, text=True, timeout=60,
    )


def test_postgres_mode_dumps_via_compose(sandbox):
    project, fake_bin, tmp_path = sandbox
    compose_env = project / "deploy" / "docker" / ".env"
    compose_env.parent.mkdir()
    compose_env.write_text("DB_PASSWORD=secret\nDB_USER=bob\n", encoding="utf-8")
    r = run_backup(project, fake_bin, tmp_path)
    assert r.returncode == 0, r.stderr
    log = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert "exec -T db pg_dump -U bob -d plasmid_designer" in log
    out = project / "data" / "backups"
    files = list(out.glob("plasmid_designer_*.sql.gz"))
    assert len(files) == 1
    # exec 的 stdin（cat）即 pg_dump 输出通道：这里 cat 回显了空输入，gzip 仍产出文件
    assert files[0].stat().st_size >= 0


def test_sqlite_mode_uses_sqlite3_backup(sandbox):
    project, fake_bin, tmp_path = sandbox
    data = project / "data"
    data.mkdir()
    (data / "plasmid_designer.db").write_bytes(b"sqlite-main")
    fake_sqlite = fake_bin / "sqlite3"
    fake_sqlite.write_text(
        "#!/bin/bash\n"
        'echo "sqlite3 $*" >> "$FAKE_LOG"\n'
        'echo "snapshot" > "$(echo $2 | sed "s/.backup //;s/\\x27//g")"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_sqlite.chmod(0o755)
    r = run_backup(project, fake_bin, tmp_path)
    assert r.returncode == 0, r.stderr
    files = list((data / "backups").glob("plasmid_designer_*.db.gz"))
    assert len(files) == 1
    log = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert "sqlite3" in log


def test_sqlite_fallback_without_sqlite3_warns(sandbox):
    project, fake_bin, tmp_path = sandbox
    data = project / "data"
    data.mkdir()
    (data / "plasmid_designer.db").write_bytes(b"sqlite-main")
    (fake_bin / "sqlite3").unlink(missing_ok=True)
    r = run_backup(project, fake_bin, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "sqlite3" in r.stderr  # 退回拷贝必须有告警
    assert len(list((data / "backups").glob("plasmid_designer_*.db.gz"))) == 1


def test_keep_prunes_old_backups(sandbox):
    import time
    project, fake_bin, tmp_path = sandbox
    data = project / "data"
    backups = data / "backups"
    data.mkdir()
    (data / "plasmid_designer.db").write_bytes(b"x")
    (fake_bin / "sqlite3").unlink(missing_ok=True)  # 走拷贝路径，少一个假工具
    backups.mkdir(parents=True)
    for i, name in enumerate(["a", "b", "c"]):
        f = backups / f"plasmid_designer_old{i}.db.gz"
        f.write_bytes(b"old")
        os.utime(f, (time.time() - (3 - i) * 1000,) * 2)
    r = run_backup(project, fake_bin, tmp_path, "--keep", "2")
    assert r.returncode == 0, r.stderr
    remain = sorted(p.name for p in backups.glob("plasmid_designer_*.db.gz"))
    # 新备份 + 最近的 1 份旧备份（old2），共 2 份
    assert len(remain) == 2
    assert "plasmid_designer_old2.db.gz" in remain
