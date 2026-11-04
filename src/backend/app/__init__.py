"""Backend App 包。

避免在 import app 时强制加载 FastAPI 应用，便于单元测试与核心服务复用。
"""

__all__ = ["app"]


def __getattr__(name: str):
    if name == "app":
        from .main import app as _app
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
