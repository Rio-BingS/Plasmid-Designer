"""带容量上限与 TTL 的内存镜像字典

designs_db / batch_jobs 这类「存储层之上的读缓存」此前无界增长：匿名
持续提交合法请求即可把进程内存线性撑大（第二轮审计 P2）。BoundedMirror
用与 sequencing _ANALYSES 相同的治理模式（容量上限 + TTL + 访问续期），
但只清内存镜像——存储层仍是权威，淘汰后的下次读取自动回源重载。

刻意继承 dict：既有调用方与测试直接使用 len()/clear()/in/get/pop 等
原生语义，子类全部保持兼容（get/contains 等入口逐一覆写以同步时间戳）。
"""

import threading
import time
from typing import Any, Dict


class BoundedMirror(dict):
    def __init__(self, max_items: int = 500, ttl_seconds: float = 24 * 3600):
        super().__init__()
        self._max_items = max_items
        self._ttl = ttl_seconds
        self._ts: Dict[str, float] = {}
        self._lock = threading.Lock()

    # ---- 写入：登记时间戳并执行淘汰（超 TTL 或超容量删最旧）----
    def __setitem__(self, key: str, value: Any) -> None:
        now = time.monotonic()
        with self._lock:
            super().__setitem__(key, value)
            self._ts[key] = now
            self._sweep_locked(now)

    def setdefault(self, key: str, default=None) -> Any:
        if key not in self:
            self[key] = default
        return self[key]

    # ---- 读取：访问续期（热数据不被 TTL 误杀）----
    def __getitem__(self, key: str) -> Any:
        with self._lock:
            self._ts[key] = time.monotonic()
            return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    # ---- 删除：同步时间戳 ----
    def __delitem__(self, key: str) -> None:
        with self._lock:
            super().__delitem__(key)
            self._ts.pop(key, None)

    def pop(self, key: str, *args) -> Any:
        with self._lock:
            self._ts.pop(key, None)
            return super().pop(key, *args)

    def clear(self) -> None:
        with self._lock:
            super().clear()
            self._ts.clear()

    # ---- 淘汰：仅清内存镜像，不触碰存储层 ----
    def _sweep_locked(self, now: float) -> None:
        for k in [k for k, ts in self._ts.items() if now - ts > self._ttl]:
            if k in self:
                super().__delitem__(k)
            self._ts.pop(k, None)
        while len(self) > self._max_items:
            oldest = min(self._ts, key=self._ts.get)  # type: ignore[arg-type]
            super().__delitem__(oldest)
            self._ts.pop(oldest, None)
