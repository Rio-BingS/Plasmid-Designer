"""pytest 共享配置：将 src/backend 注入 sys.path（相对定位，任何机器可用）。

历史上多个测试文件把别人机器的绝对路径（/root/.openclaw/...）写死在文件里，
导致换机器后整个测试套件无法导入。此 conftest 由 pytest 自动加载，
统一完成路径注入，旧文件中的无效路径插入退化为无害空操作。
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "src" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
