"""兼容壳：DTO 已上提为 app/schemas.py（服务层不该 import 路由层的模型）。

新代码请直接 `from app import schemas`；此处 re-export 维持旧导入路径。
"""

from app.schemas import *  # noqa: F401,F403
