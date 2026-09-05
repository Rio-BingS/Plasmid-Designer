"""API 配置"""

import json
import os
import sys
from pathlib import Path
from pydantic_settings import BaseSettings

# 项目根目录：src/backend 的父级，即 plasmid-designer-v2/
PROJECT_ROOT = Path(__file__).resolve().parents[3]  # config.py -> app -> backend -> src -> project_root
BACKEND_DIR = Path(__file__).resolve().parents[1]  # config.py -> app -> backend

# 确保 core 包可被导入
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DATA_DIR_DEFAULT = str(PROJECT_ROOT / "data")
# SQLite 默认库文件路径（跟随 DATA_DIR，未设 DATA_DIR 时位于项目 data/ 下）
_DATABASE_URL_DEFAULT = "sqlite:///" + (Path(os.environ.get("DATA_DIR", DATA_DIR_DEFAULT)) / "plasmid_designer.db").as_posix()


class Settings(BaseSettings):
    """应用配置"""

    # 应用信息
    APP_NAME: str = "Plasmid Designer"
    APP_VERSION: str = "2.0.0"
    # 生产环境保持 False；开发调试时通过 .env / 环境变量显式打开
    DEBUG: bool = False

    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # 数据库配置 — 单一来源（database/models.py 引用本值）。
    # 默认 SQLite，容器/生产通过环境变量或 .env 覆盖为 PostgreSQL；
    # 此前 models.py 用 os.getenv 直读环境变量、不读 .env，导致 .env 配置静默失效
    DATABASE_URL: str = _DATABASE_URL_DEFAULT

    # JWT 密钥 — 生产环境必须通过环境变量 SECRET_KEY 设置，否则使用开发默认值
    SECRET_KEY: str = "dev-insecure-secret-key-change-me"

    # Redis 配置
    REDIS_URL: str = "redis://localhost:6379/0"

    # 文件存储 — 优先使用环境变量，Docker 部署时通过 env 注入
    DATA_DIR: str = os.environ.get("DATA_DIR", DATA_DIR_DEFAULT)
    VECTORS_DIR: str = os.environ.get("VECTORS_DIR", str(Path(DATA_DIR_DEFAULT) / "vectors"))
    CODON_TABLES_DIR: str = os.environ.get("CODON_TABLES_DIR", str(Path(DATA_DIR_DEFAULT) / "codon_tables"))
    UPLOAD_DIR: str = "/tmp/plasmid_designer/uploads"
    OUTPUT_DIR: str = "/tmp/plasmid_designer/output"

    # CORS — 支持 "*"、逗号分隔（"https://a.com,https://b.com"）或 JSON 数组（'["*"]'）三种写法，
    # 解析见 cors_origins_list。此处必须声明为 str：pydantic-settings 会对 list/dict 等
    # "复杂类型" 字段先做 JSON 解码再进 field_validator，环境变量传 "*" 会直接 SettingsError，
    # validator 根本没有机会执行（2026-09 VPS 部署踩坑）
    CORS_ORIGINS: str = "*"

    @property
    def cors_origins_list(self) -> list:
        """解析 CORS_ORIGINS 为来源列表，main.py 据此接线 CORSMiddleware"""
        v = (self.CORS_ORIGINS or "").strip()
        if not v:
            return ["*"]
        if v.startswith("["):
            return json.loads(v)
        return [item.strip() for item in v.split(",") if item.strip()]

    class Config:
        env_file = ".env"


settings = Settings()
