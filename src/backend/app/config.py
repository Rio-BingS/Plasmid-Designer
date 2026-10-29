"""API 配置"""

import json
import os
import sys
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# 确保 core 包可被导入
_BACKEND_DIR = Path(__file__).resolve().parents[1]  # config.py -> app -> backend
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from core.paths import PROJECT_ROOT  # noqa: E402  路径单一来源（core 不得反向 import app）
from core._version import __version__ as _VERSION  # noqa: E402  版本号单一来源

DATA_DIR_DEFAULT = str(PROJECT_ROOT / "data")
# SQLite 默认库文件路径（跟随 DATA_DIR，未设 DATA_DIR 时位于项目 data/ 下）
_DATABASE_URL_DEFAULT = "sqlite:///" + (Path(os.environ.get("DATA_DIR", DATA_DIR_DEFAULT)) / "plasmid_designer.db").as_posix()


class Settings(BaseSettings):
    """应用配置"""

    # 应用信息
    APP_NAME: str = "Plasmid Designer"
    APP_VERSION: str = _VERSION  # 发版改 core/_version.py
    # 生产环境保持 False；开发调试时通过 .env / 环境变量显式打开
    DEBUG: bool = False
    # /docs、/redoc、/openapi.json 开关。本地开发默认开启；Docker compose 默认 false
    # （容器 nginx 无法按来源限制，开启即对公网暴露全部接口结构，审查 10 L7）
    ENABLE_API_DOCS: bool = True

    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # 数据库配置 — 单一来源（database/models.py 引用本值）。
    # 默认 SQLite，容器/生产通过环境变量或 .env 覆盖为 PostgreSQL；
    # 此前 models.py 用 os.getenv 直读环境变量、不读 .env，导致 .env 配置静默失效
    DATABASE_URL: str = _DATABASE_URL_DEFAULT

    # JWT 密钥 — 必须通过环境变量 / .env 设置强随机值（openssl rand -hex 32）；
    # 不再提供默认值，开发环境同样需要配置（校验见 validate_secret_key）
    SECRET_KEY: str = ""

    # Redis 配置
    REDIS_URL: str = "redis://localhost:6379/0"

    # 文件存储 — 优先使用环境变量，Docker 部署时通过 env 注入
    DATA_DIR: str = os.environ.get("DATA_DIR", DATA_DIR_DEFAULT)
    VECTORS_DIR: str = os.environ.get("VECTORS_DIR", str(Path(DATA_DIR_DEFAULT) / "vectors"))
    CODON_TABLES_DIR: str = os.environ.get("CODON_TABLES_DIR", str(Path(DATA_DIR_DEFAULT) / "codon_tables"))

    # CORS — 支持 "*"、逗号分隔（"https://a.com,https://b.com"）或 JSON 数组（'["*"]'）三种写法，
    # 解析见 cors_origins_list。此处必须声明为 str：pydantic-settings 会对 list/dict 等
    # "复杂类型" 字段先做 JSON 解码再进 field_validator，环境变量传 "*" 会直接 SettingsError，
    # validator 根本没有机会执行（2026-09 VPS 部署踩坑）
    CORS_ORIGINS: str = "*"

    # 可信反向代理（逗号分隔 IP 或 CIDR）。只有 socket 对端落在此列表内时才采信
    # X-Real-IP / X-Forwarded-For 作为限流维度；默认只信本机（裸机 nginx 同机）。
    # docker compose 下前端 nginx 位于容器网络，需在 compose 中显式配置其网段
    TRUSTED_PROXIES: str = "127.0.0.1,::1"

    # 登录会话 Cookie（httpOnly + SameSite=Lax）的 Secure 标记：
    # auto  — 请求为 https 时置位（可信代理 TRUSTED_PROXIES 转发的
    #         X-Forwarded-Proto 同样采信）；
    # true  — 总是置位（TLS 在更外层代理终止、内层只见 http 时用它）；
    # false — 从不置位（仅限纯 http 的本地开发）
    AUTH_COOKIE_SECURE: str = "auto"

    # ==================== 管理员引导 ====================
    # 启动时若数据库中不存在该邮箱的用户，则自动创建为管理员（并视为已验证邮箱）；
    # 该邮箱已被非管理员账号注册时不会提升（防抢注接管），仅记录错误日志需人工处理。
    # 不设置则不创建（默认开发环境不变）
    ADMIN_USERNAME: str = "admin"
    ADMIN_EMAIL: str = ""
    ADMIN_PASSWORD: str = ""

    # ==================== 邮件发送（注册邮箱验证码） ====================
    # console: 验证码打到后端日志（开发默认，无需任何外部服务）
    # smtp:    任意邮箱服务商的 SMTP（QQ/163/Gmail 授权码等，零额外注册）
    # resend:  https://resend.com 免费 3000 封/月（100/天），邮箱注册无需信用卡
    # brevo:   https://www.brevo.com 免费 300 封/天，邮箱注册无需信用卡
    MAIL_PROVIDER: str = "console"
    MAIL_FROM: str = "Plasmid Designer <no-reply@localhost>"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_SSL: bool = True  # 465 端口用 SSL；587 STARTTLS 时设 false
    RESEND_API_KEY: str = ""
    BREVO_API_KEY: str = ""

    @property
    def cors_origins_list(self) -> list:
        """解析 CORS_ORIGINS 为来源列表，main.py 据此接线 CORSMiddleware"""
        v = (self.CORS_ORIGINS or "").strip()
        if not v:
            return ["*"]
        if v.startswith("["):
            return json.loads(v)
        return [item.strip() for item in v.split(",") if item.strip()]

    model_config = SettingsConfigDict(env_file=".env")


# 公开仓库里出现过的占位值：任何人都可用它们自签令牌，永远拒绝
SECRET_KEY_PLACEHOLDERS = frozenset({
    "dev-insecure-secret-key-change-me",
    "change_this_in_production",
    "change_this_in_production_use_strong_random_string",
    "your_jwt_secret_key",
})
SECRET_KEY_MIN_LENGTH = 32
# 两条阈值只拦人为弱密钥（单一/少数字符、低频率熵），不能误杀真随机密钥。
# 32 位 hex（token_hex(16)，真实 128 bit）按字符频率估出的熵有长尾：
# 旧阈值 96 bit / 8 种字符时约万分之一被误判。现阈值下按多项分布精确
# 计算误判率约 7e-15，'a'*32、周期串等弱密钥仍全部拒绝。
_SECRET_KEY_MIN_DISTINCT = 5
_SECRET_KEY_MIN_BITS = 64.0


def _shannon_bits(value: str) -> float:
    """按字符频率估算的总熵（bit），用于拦截 'a'*32 这类明显弱密钥"""
    import math
    from collections import Counter

    n = len(value)
    return -sum(c / n * math.log2(c / n) for c in Counter(value).values()) * n


def validate_secret_key(key: str) -> None:
    """JWT 密钥校验：占位值、过短或明显低熵（单一/重复模式）一律抛 RuntimeError。

    与 DEBUG 无关、在 jwt_auth 导入时执行——此前只在 lifespan 里检查，
    DEBUG=true 或 `--lifespan off` 即可绕过，公开默认值可自签管理员令牌。
    """
    hint = "请在环境变量或 .env 设置强随机 SECRET_KEY（openssl rand -hex 32）"
    key = key or ""
    if not key.strip():
        raise RuntimeError(f"SECRET_KEY 未设置，拒绝启动：{hint}")
    if key in SECRET_KEY_PLACEHOLDERS:
        raise RuntimeError(f"SECRET_KEY 为公开占位值，拒绝启动：{hint}")
    if len(key) < SECRET_KEY_MIN_LENGTH:
        raise RuntimeError(
            f"SECRET_KEY 长度 <{SECRET_KEY_MIN_LENGTH}，拒绝启动：{hint}")
    periodic = (key + key).find(key, 1) < len(key)
    if (periodic or len(set(key)) < _SECRET_KEY_MIN_DISTINCT
            or _shannon_bits(key) < _SECRET_KEY_MIN_BITS):
        raise RuntimeError(f"SECRET_KEY 熵过低（重复/单一字符模式），拒绝启动：{hint}")


settings = Settings()
