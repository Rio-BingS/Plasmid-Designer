"""版本号单一来源：core.__version__、APP_VERSION 与前端 package.json 必须一致

审查 09-deps-ci-release L-6：core.__version__ 长期停在 "2.0.0"，与 APP_VERSION 脱节。
"""

import json
from pathlib import Path

import core
from app.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_core_version_matches_app_version_default():
    # 取字段默认值而非 settings 实例，避免环境变量 APP_VERSION 覆盖干扰
    assert core.__version__ == Settings.model_fields["APP_VERSION"].default


def test_frontend_package_version_matches():
    pkg = json.loads((ROOT / "src/frontend/package.json").read_text(encoding="utf-8-sig"))
    assert pkg["version"] == core.__version__
