"""存储层工厂 — 通过 STORAGE_MODE 环境变量切换内存/数据库"""

import os

# 默认 memory，避免未配置数据库时阻塞设计主路径；生产可设 STORAGE_MODE=database
STORAGE_MODE = os.environ.get("STORAGE_MODE", "memory")

_design_store = None
_batch_store = None


def get_design_store():
    """获取设计存储实例（单例）"""
    global _design_store
    if _design_store is None:
        if STORAGE_MODE == "memory":
            from .memory_store import MemoryDesignStore
            _design_store = MemoryDesignStore()
        else:
            from .db_store import DBDesignStore
            _design_store = DBDesignStore()
    return _design_store


def get_batch_store():
    """获取批量任务存储实例（单例）"""
    global _batch_store
    if _batch_store is None:
        if STORAGE_MODE == "memory":
            from .memory_store import MemoryBatchStore
            _batch_store = MemoryBatchStore()
        else:
            from .db_store import DBBatchStore
            _batch_store = DBBatchStore()
    return _batch_store
