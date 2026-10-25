"""路径与数据目录解析 — app.config 与 core 共用的叶子模块

分层约束：core 不得反向 import app（此前的 codon_optimizer /
external_vector_importer 各自从 app.config 取目录配置）。
需要目录配置的 core 模块统一从这里取；本模块只依赖标准库。

解析顺序与 app.config 一致：环境变量 → 项目根 .env（pydantic-settings 读
的是同一份文件、同样的键名）→ 项目内 data/ 默认值。
"""

import os
from pathlib import Path

# paths.py 位于 src/backend/core/ 下
BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _env_file_values() -> dict:
    """解析项目根 .env 的 KEY=VALUE 行（去引号、# 注释；不支持多行/插值）。

    只为 core 兜住 .env 配置；app 侧仍由 pydantic-settings 完整解析。
    """
    values: dict = {}
    try:
        text = (PROJECT_ROOT / ".env").read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


_ENV_FILE = _env_file_values()


def _lookup(key: str):
    """环境变量优先，其次项目根 .env（与 app.config 的取值顺序一致）"""
    return os.environ.get(key) or _ENV_FILE.get(key) or None


def resolve_dir(env_key: str, default: Path) -> Path:
    """按配置键解析目录：环境变量 / .env 优先，否则给默认值"""
    return Path(_lookup(env_key) or default)


# 与 app.config 的默认口径保持一致：VECTORS_DIR / CODON_TABLES_DIR 的默认值
# 锚定项目内 data/（不跟随 DATA_DIR 环境变量，历史行为如此）
#
# 注意：目录一律在「调用时」用 resolve_dir() 解析，不要 import 模块级常量——
# 常量在首次导入时求值，测试里 monkeypatch.setenv 之后不会生效，测试会静默
# 回落到真实 data/ 目录（历史上 Ecoli_K12.yaml 被测试标记污染即此原因）
DATA_DIR = Path(_lookup("DATA_DIR") or PROJECT_ROOT / "data")


def vectors_dir() -> Path:
    return resolve_dir("VECTORS_DIR", PROJECT_ROOT / "data" / "vectors")


def codon_tables_dir() -> Path:
    return resolve_dir("CODON_TABLES_DIR", PROJECT_ROOT / "data" / "codon_tables")
