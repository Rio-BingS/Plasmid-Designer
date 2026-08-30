"""API 配置"""

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


class Settings(BaseSettings):
    """应用配置"""

    # 应用信息
    APP_NAME: str = "Plasmid Designer"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = True

    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # 数据库配置
    DATABASE_URL: str = "postgresql://user:password@localhost/plasmid_designer"

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

    # CORS
    CORS_ORIGINS: list = ["*"]

    class Config:
        env_file = ".env"


settings = Settings()
