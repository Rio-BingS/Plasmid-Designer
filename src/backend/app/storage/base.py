"""存储层抽象基类"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from datetime import datetime


class DesignStoreBase(ABC):
    """设计任务存储抽象基类"""

    @abstractmethod
    def save(self, design_id: str, data: Dict[str, Any]) -> None:
        """保存设计记录"""
        pass

    @abstractmethod
    def get(self, design_id: str) -> Optional[Dict[str, Any]]:
        """获取设计记录，返回 None 如果不存在"""
        pass

    @abstractmethod
    def update(self, design_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        """更新设计记录字段"""
        pass

    @abstractmethod
    def exists(self, design_id: str) -> bool:
        """检查设计记录是否存在"""
        pass

    @abstractmethod
    def add_primer(self, design_id: str, primer_data: Dict[str, Any]) -> None:
        """添加引物到设计记录"""
        pass

    @abstractmethod
    def add_warning(self, design_id: str, message: str) -> None:
        """添加警告到设计记录"""
        pass

    @abstractmethod
    def add_error(self, design_id: str, message: str) -> None:
        """添加错误到设计记录"""
        pass

    @abstractmethod
    def list_designs(self, skip: int = 0, limit: int = 50) -> List[Dict[str, Any]]:
        """列出设计记录"""
        pass


class BatchStoreBase(ABC):
    """批量任务存储抽象基类"""

    @abstractmethod
    def save(self, batch_id: str, data: Dict[str, Any]) -> None:
        """保存批量任务记录"""
        pass

    @abstractmethod
    def get(self, batch_id: str) -> Optional[Dict[str, Any]]:
        """获取批量任务记录"""
        pass

    @abstractmethod
    def update(self, batch_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        """更新批量任务字段"""
        pass

    @abstractmethod
    def exists(self, batch_id: str) -> bool:
        """检查批量任务是否存在"""
        pass

    @abstractmethod
    def add_result(self, batch_id: str, design_id: str) -> None:
        """添加已完成的设计 ID"""
        pass

    @abstractmethod
    def add_error(self, batch_id: str, error_data: Dict[str, Any]) -> None:
        """添加错误记录"""
        pass

    @abstractmethod
    def increment_completed(self, batch_id: str) -> None:
        """增加完成计数"""
        pass

    @abstractmethod
    def increment_failed(self, batch_id: str) -> None:
        """增加失败计数"""
        pass
