"""
日志配置模块
支持结构化日志、文件轮转、错误追踪
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler
import json
from typing import Any, Dict

# 日志目录：跨平台默认（backend/logs/），可用环境变量 PLASMID_LOG_DIR 覆盖；
# 创建失败时降级为仅控制台日志，不阻断应用启动
LOG_DIR: Path | None = Path(
    os.getenv("PLASMID_LOG_DIR", str(Path(__file__).resolve().parent.parent / "logs"))
)
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    LOG_DIR = None


class StructuredFormatter(logging.Formatter):
    """结构化日志格式化器"""
    
    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录为 JSON"""
        log_data: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        
        # 添加异常信息
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # 添加额外字段
        if hasattr(record, "user_id"):
            log_data["user_id"] = record.user_id
        if hasattr(record, "design_id"):
            log_data["design_id"] = record.design_id
        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id
        
        return json.dumps(log_data)


class RequestFormatter(logging.Formatter):
    """请求日志格式化器"""
    
    def format(self, record: logging.LogRecord) -> str:
        """格式化请求日志"""
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        return f"[{timestamp}] {record.levelname:8} | {record.name:20} | {record.getMessage()}"


def setup_logging(
    level: str = "INFO",
    log_file: str = "app.log",
    enable_json: bool = False
) -> logging.Logger:
    """
    配置日志系统
    
    Args:
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: 日志文件名
        enable_json: 是否启用 JSON 格式日志
    
    Returns:
        配置好的 Logger 实例
    """
    # 根日志器
    logger = logging.getLogger("plasmid_designer")
    logger.setLevel(getattr(logging, level.upper()))
    
    # 清除现有处理器
    logger.handlers.clear()
    
    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(RequestFormatter())
    logger.addHandler(console_handler)
    
    # 文件处理器（带轮转）—— 目录不可用时跳过，仅保留控制台输出
    if LOG_DIR is not None:
        file_handler = RotatingFileHandler(
            LOG_DIR / log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)

        if enable_json:
            file_handler.setFormatter(StructuredFormatter())
        else:
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            ))

        logger.addHandler(file_handler)

        # 错误日志单独文件
        try:
            error_handler = RotatingFileHandler(
                LOG_DIR / "errors.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8"
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(StructuredFormatter())
            logger.addHandler(error_handler)
        except OSError:
            pass

    return logger


def get_logger(name: str) -> logging.Logger:
    """获取指定名称的日志器"""
    return logging.getLogger(f"plasmid_designer.{name}")


# 便捷函数
def log_request(logger: logging.Logger, method: str, path: str, user_id: str = None):
    """记录请求日志"""
    logger.info(
        f"Request: {method} {path}",
        extra={"user_id": user_id} if user_id else {}
    )


def log_response(logger: logging.Logger, status_code: int, duration_ms: float):
    """记录响应日志"""
    logger.info(f"Response: {status_code} ({duration_ms:.2f}ms)")


def log_error(logger: logging.Logger, error: Exception, context: Dict = None):
    """记录错误日志"""
    logger.error(
        f"Error: {type(error).__name__}: {str(error)}",
        exc_info=True,
        extra=context or {}
    )


def log_design_event(logger: logging.Logger, event: str, design_id: str, details: Dict = None):
    """记录设计事件日志"""
    logger.info(
        f"Design Event: {event}",
        extra={"design_id": design_id, **(details or {})}
    )


# 初始化日志
logger = setup_logging()
