"""pytest 共享配置：将 src/backend 注入 sys.path（相对定位，任何机器可用）。

历史上多个测试文件把别人机器的绝对路径（/root/.openclaw/...）写死在文件里，
导致换机器后整个测试套件无法导入。此 conftest 由 pytest 自动加载，
统一完成路径注入，旧文件中的无效路径插入退化为无害空操作。
"""

import os
import secrets
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "src" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# ==================== 测试数据隔离（终审 G-01 回归锁） ====================
# 此前测试直接读写真实开发库 data/plasmid_designer.db：
#   - test_db_retention_prunes_oldest 打桩 MAX_DB_RECORDS=2 触发全表淘汰，
#     跑一次全套件就把开发库测序记录删到只剩 2 条（2026-10-08 已实际发生）；
#   - _ensure_user 等向真实库写入固定测试账号。
# pytest 在收集任何测试模块之前加载本文件，这里在 import app 之前把全部
# 持久化环境变量强制指向临时目录——测试模块随后 `from app.main import app`
# 拿到的引擎/日志目录就是隔离后的实例，整套件零触碰仓库内真实数据。
# 注意：必须用直接赋值（而非 setdefault）——开发者会话若自带 DATA_DIR
# 指向真实数据，setdefault 会让隔离形同虚设。
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="plasmid-test-data-"))
os.environ["DATA_DIR"] = str(_TEST_DATA_DIR)
os.environ["DATABASE_URL"] = "sqlite:///" + (_TEST_DATA_DIR / "plasmid_designer.db").as_posix()
os.environ["PLASMID_LOG_DIR"] = str(_TEST_DATA_DIR / "logs")
# jwt_auth 导入即校验 SECRET_KEY（占位/过短/低熵直接拒绝），测试统一注入
# 每次运行随机生成的强密钥；直接赋值，避免开发者会话里的弱值混进来
os.environ["SECRET_KEY"] = secrets.token_hex(32)


def _init_isolated_db() -> None:
    """在隔离库上建表。

    干净临时库上任何用例都不该再见到「no such table: users」（终审 G-02
    的根因正是部分用例隐式依赖本机真实库已建过表）；TestClient 不进
    lifespan 上下文时 init_db 不会自动跑，这里统一兜底。"""
    from app.database import init_db

    init_db()


_init_isolated_db()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每个测试前清空内存限流计数。

    限流器是进程级单例（upload 档 20 次/小时），全套件运行时 /sequencing
    的历史请求会把配额占满，让排在后面的测试收到与被测逻辑无关的 429。
    """
    from app.rate_limit import limiter

    limiter._requests.clear()
    yield
