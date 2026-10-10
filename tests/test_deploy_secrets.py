"""部署模板回归锁：SECRET_KEY 不得带可用默认值"""

import re
from pathlib import Path

from app.config import SECRET_KEY_PLACEHOLDERS

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def test_compose_requires_secret_key():
    txt = (DEPLOY / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "SECRET_KEY=${SECRET_KEY:?" in txt
    assert "${SECRET_KEY:-" not in txt


def test_env_examples_have_no_usable_secret_key():
    for p in (DEPLOY / "bare" / ".env.example", DEPLOY / "docker" / ".env.example"):
        m = re.search(r"^SECRET_KEY=(.*)$", p.read_text(encoding="utf-8"), re.M)
        assert m, p
        val = m.group(1).strip()
        # 只允许留空或已知占位标记（后端 validate_secret_key 会拒绝）
        assert val == "" or val in SECRET_KEY_PLACEHOLDERS, f"{p}: {val!r}"


def test_compose_cors_default_not_wildcard():
    txt = (DEPLOY / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    m = re.search(r"CORS_ORIGINS=\$\{CORS_ORIGINS:-([^}]*)\}", txt)
    assert m and m.group(1).strip() and "*" not in m.group(1)
    env = (DEPLOY / "docker" / ".env.example").read_text(encoding="utf-8")
    m = re.search(r"^CORS_ORIGINS=(.*)$", env, re.M)
    assert m and "*" not in m.group(1)
