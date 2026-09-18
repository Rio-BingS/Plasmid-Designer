"""pytest 共享配置：将 src/backend 注入 sys.path（相对定位，任何机器可用）。

历史上多个测试文件把别人机器的绝对路径（/root/.openclaw/...）写死在文件里，
导致换机器后整个测试套件无法导入。此 conftest 由 pytest 自动加载，
统一完成路径注入，旧文件中的无效路径插入退化为无害空操作。
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "src" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每个测试前清空内存限流计数。

    限流器是进程级单例（upload 档 20 次/小时），全套件运行时 /sequencing
    的历史请求会把配额占满，让排在后面的测试收到与被测逻辑无关的 429。
    """
    from app.rate_limit import limiter

    limiter._requests.clear()
    yield
