"""按阶段缓存（步骤 6）：S1 read 阶段结果的进程内 LRU。

默认关闭（SANGER_STAGE_CACHE=1 开启），开启后同一 read 在同参考、同参数下
重复分析（换范围重判、批量第二遍、重复上传）直接复用 S1 结果。
键 = read 文件名 + 内容 sha256 + 参考 sha256 + 本阶段全部参数；
命中返回深拷贝，下游原地标注不会污染缓存。
"""
import copy
import hashlib
import os
from collections import OrderedDict
from dataclasses import asdict, is_dataclass
from typing import Any, Optional

MAX_ENTRIES = int(os.environ.get("SANGER_STAGE_CACHE_MAX", "64"))
_CACHE: "OrderedDict[str, Any]" = OrderedDict()


def enabled() -> bool:
    return os.environ.get("SANGER_STAGE_CACHE", "0") == "1"


def _h(b) -> str:
    if isinstance(b, str):
        b = b.encode()
    return hashlib.sha256(b or b"").hexdigest()


def read_key(filename: str, blob: bytes, ref: str, **params) -> Optional[str]:
    if not enabled():
        return None
    norm = {k: (asdict(v) if is_dataclass(v) else v) for k, v in params.items()}
    if norm.get("full_ref"):
        norm["full_ref"] = _h(norm["full_ref"])
    from core.sanger.pipeline import SANGER_ALGO_VERSION
    # 口径开关（SANGER_* 环境变量）也进键：换开关不复用旧结果
    flags = sorted((k, v) for k, v in os.environ.items()
                   if k.startswith("SANGER_") and not k.startswith("SANGER_STAGE_CACHE"))
    return _h(repr((SANGER_ALGO_VERSION, filename, _h(blob), _h(ref),
                    sorted(norm.items()), flags)))


def get(key: Optional[str]):
    if key is None or key not in _CACHE:
        return None
    _CACHE.move_to_end(key)
    return copy.deepcopy(_CACHE[key])


def put(key: Optional[str], value) -> None:
    if key is None:
        return
    _CACHE[key] = copy.deepcopy(value)
    _CACHE.move_to_end(key)
    while len(_CACHE) > MAX_ENTRIES:
        _CACHE.popitem(last=False)


def clear() -> None:
    _CACHE.clear()
