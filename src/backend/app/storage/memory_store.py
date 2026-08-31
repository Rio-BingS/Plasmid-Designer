"""内存存储实现 — 用于 HuggingFace 等无需持久化的部署"""

from typing import Optional, List, Dict, Any
from datetime import datetime

from .base import DesignStoreBase, BatchStoreBase


class MemoryDesignStore(DesignStoreBase):
    """基于 dict 的内存设计存储"""

    # 条目上限：达到上限按写入顺序淘汰最旧条目，防止长期运行内存只涨不降（KNOWN_ISSUES 2.2）
    MAX_ENTRIES = 1_000

    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}

    def save(self, design_id: str, data: Dict[str, Any]) -> None:
        self._store[design_id] = data.copy()
        # 确保可变列表是独立的
        if "primers" not in self._store[design_id]:
            self._store[design_id]["primers"] = []
        if "warnings" not in self._store[design_id]:
            self._store[design_id]["warnings"] = []
        if "errors" not in self._store[design_id]:
            self._store[design_id]["errors"] = []
        while len(self._store) > self.MAX_ENTRIES:
            self._store.pop(next(iter(self._store)))

    def get(self, design_id: str) -> Optional[Dict[str, Any]]:
        return self._store.get(design_id)

    def update(self, design_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        if design_id not in self._store:
            return None
        for key, value in kwargs.items():
            self._store[design_id][key] = value
        return self._store[design_id]

    def exists(self, design_id: str) -> bool:
        return design_id in self._store

    def add_primer(self, design_id: str, primer_data: Dict[str, Any]) -> None:
        if design_id in self._store:
            self._store[design_id].setdefault("primers", []).append(primer_data)

    def add_warning(self, design_id: str, message: str) -> None:
        if design_id in self._store:
            self._store[design_id].setdefault("warnings", []).append(message)

    def add_error(self, design_id: str, message: str) -> None:
        if design_id in self._store:
            self._store[design_id].setdefault("errors", []).append(message)

    def list_designs(self, skip: int = 0, limit: int = 50) -> List[Dict[str, Any]]:
        items = list(self._store.values())
        return items[skip:skip + limit]


class MemoryBatchStore(BatchStoreBase):
    """基于 dict 的内存批量任务存储"""

    # 条目上限：达到上限按写入顺序淘汰最旧条目（KNOWN_ISSUES 2.2）
    MAX_ENTRIES = 500

    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}

    def save(self, batch_id: str, data: Dict[str, Any]) -> None:
        self._store[batch_id] = data.copy()
        if "results" not in self._store[batch_id]:
            self._store[batch_id]["results"] = []
        if "errors" not in self._store[batch_id]:
            self._store[batch_id]["errors"] = []
        while len(self._store) > self.MAX_ENTRIES:
            self._store.pop(next(iter(self._store)))

    def get(self, batch_id: str) -> Optional[Dict[str, Any]]:
        return self._store.get(batch_id)

    def update(self, batch_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        if batch_id not in self._store:
            return None
        for key, value in kwargs.items():
            self._store[batch_id][key] = value
        return self._store[batch_id]

    def exists(self, batch_id: str) -> bool:
        return batch_id in self._store

    def add_result(self, batch_id: str, design_id: str) -> None:
        if batch_id in self._store:
            self._store[batch_id].setdefault("results", []).append(design_id)

    def add_error(self, batch_id: str, error_data: Dict[str, Any]) -> None:
        if batch_id in self._store:
            self._store[batch_id].setdefault("errors", []).append(error_data)

    def increment_completed(self, batch_id: str) -> None:
        if batch_id in self._store:
            self._store[batch_id]["completed"] = self._store[batch_id].get("completed", 0) + 1

    def increment_failed(self, batch_id: str) -> None:
        if batch_id in self._store:
            self._store[batch_id]["failed"] = self._store[batch_id].get("failed", 0) + 1
