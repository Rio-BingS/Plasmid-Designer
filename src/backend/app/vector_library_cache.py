"""载体库进程级缓存（从 app.design_service 迁出）

analysis_routes / vector_routes / sequencing 等模块只需要「取载体库 /
失效缓存」，此前为此 import 整个 design_service（1200 行重模块）；
独立成模块以解开这层耦合。design_service 保留同名 re-export。
"""

from app.config import settings

_vector_library_cache = None


def get_vector_library():
    """进程级载体库缓存。"""
    global _vector_library_cache
    if _vector_library_cache is None:
        from core.vector_library import VectorLibrary

        lib = VectorLibrary()
        lib.load_from_directory(settings.VECTORS_DIR)
        _vector_library_cache = lib
    return _vector_library_cache


def invalidate_vector_library_cache() -> None:
    global _vector_library_cache
    _vector_library_cache = None
