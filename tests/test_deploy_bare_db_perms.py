"""裸机 SQLite 库权限回归锁（审查 03 M2）

install.sh 为让 nginx 穿过安装目录而设 751，SQLite 库却在安装目录根下、
以 umask 022 建成 0644，本机任意用户可按固定路径读出用户表。这里把
install.sh 中的 sqlite_db_path / secure_sqlite_files 原样抽出，在临时目录
里用假 chown 运行（沙箱非 root），并静态核对 service 与模板。
"""

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BARE = REPO / "deploy" / "bare"
BASH = shutil.which("bash")

needs_bash = pytest.mark.skipif(
    BASH is None or sys.platform.startswith("win"), reason="需要 bash"
)


def _functions():
    txt = (BARE / "install.sh").read_text(encoding="utf-8")
    parts = []
    m = re.search(r'^SQLITE_DIR_NAME=.*$', txt, re.M)
    assert m, "install.sh 缺少 SQLITE_DIR_NAME"
    parts.append(m.group(0))
    for name in ("sqlite_db_path", "secure_sqlite_files"):
        m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", txt, re.M | re.S)
        assert m, f"install.sh 缺少 {name}()"
        parts.append(m.group(0))
    return "\n".join(parts)


def _run(tmp_path, env_line, files=()):
    inst = tmp_path / "opt pd"
    inst.mkdir()
    if env_line is not None:
        (inst / ".env").write_text(f"SECRET_KEY=x\n{env_line}\n", encoding="utf-8")
    for rel in files:
        p = inst / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("db", encoding="utf-8")
        p.chmod(0o644)
    fake = tmp_path / "bin"
    fake.mkdir()
    chown = fake / "chown"
    chown.write_text('#!/bin/sh\necho "chown $*" >> "$FAKE_LOG"\n', encoding="utf-8")
    chown.chmod(0o755)
    log = tmp_path / "chown.log"
    log.write_text("", encoding="utf-8")
    script = "set -euo pipefail\n" + _functions() + '\nsecure_sqlite_files "$1"\n'
    proc = subprocess.run(
        [BASH, "-c", script, "bash", str(inst)],
        env={"PATH": f"{fake}{os.pathsep}{os.environ.get('PATH', '')}", "FAKE_LOG": str(log)},
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return inst, log.read_text(encoding="utf-8")


def _mode(p):
    return stat.S_IMODE(p.stat().st_mode)


@needs_bash
def test_new_layout_dir_0700_and_db_0600(tmp_path):
    inst = tmp_path / "opt pd"
    inst, chowns = _run(
        tmp_path, f"DATABASE_URL=sqlite:///{inst}/db/plasmid_designer.db",
        files=("db/plasmid_designer.db", "db/plasmid_designer.db-wal"),
    )
    assert _mode(inst / "db") == 0o700
    assert _mode(inst / "db" / "plasmid_designer.db") == 0o600
    assert _mode(inst / "db" / "plasmid_designer.db-wal") == 0o600
    assert f"chown plasmid:plasmid {inst}/db/plasmid_designer.db" in chowns


@needs_bash
def test_legacy_root_db_with_extra_slash_is_tightened(tmp_path):
    # 旧版 install.sh 写的是 sqlite:////$INSTALL_DIR/...（多一个斜杠），库在安装目录根下
    inst = tmp_path / "opt pd"
    inst, _ = _run(
        tmp_path, f"DATABASE_URL=sqlite:////{inst}/plasmid_designer.db",
        files=("plasmid_designer.db", "plasmid_designer.db-journal"),
    )
    assert _mode(inst / "plasmid_designer.db") == 0o600
    assert _mode(inst / "plasmid_designer.db-journal") == 0o600
    assert _mode(inst / "db") == 0o700


@needs_bash
def test_default_data_dir_db_when_url_unset(tmp_path):
    inst, _ = _run(tmp_path, "REDIS_ENABLED=false", files=("data/plasmid_designer.db",))
    assert _mode(inst / "data" / "plasmid_designer.db") == 0o600


@needs_bash
def test_postgres_url_touches_no_db_file(tmp_path):
    inst, chowns = _run(
        tmp_path, "DATABASE_URL=postgresql://plasmid:pw@localhost/plasmid_designer",
        files=("data/plasmid_designer.db",),
    )
    assert _mode(inst / "data" / "plasmid_designer.db") == 0o644
    assert "plasmid_designer.db" not in chowns


def test_new_sqlite_installs_use_private_db_dir():
    txt = (BARE / "install.sh").read_text(encoding="utf-8")
    m = re.search(r'DATABASE_URL="(sqlite:[^"]*)"', txt)
    assert m and "/$SQLITE_DIR_NAME/" in m.group(1), m and m.group(1)
    assert 'secure_sqlite_files "$INSTALL_DIR"' in txt
    # nginx 仍需穿过安装目录读取前端 dist
    assert 'chmod 751 "$INSTALL_DIR"' in txt
    env = (BARE / ".env.example").read_text(encoding="utf-8")
    m = re.search(r"^DATABASE_URL=(sqlite:.*)$", env, re.M)
    assert m and "/plasmid-designer/db/" in m.group(1)


def test_service_sets_private_umask():
    txt = (BARE / "plasmid-backend.service").read_text(encoding="utf-8")
    assert re.search(r"^UMask=0077$", txt, re.M)
