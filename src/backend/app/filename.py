"""下载响应的文件名安全处理（全仓库唯一实现，见 KNOWN_ISSUES 防回潮约束）

两个职责：
- sanitize_filename：把用户可控的名称收敛成可安全放进响应头的字符串
  （白名单过滤引号/CR/LF 等，防止 Content-Disposition 头注入）
- content_disposition：组装符合 RFC 6266 的头值——HTTP 头仅限 latin-1，
  非 ASCII（如中文样品名）必须走 RFC 5987 的 filename* 扩展，否则
  Starlette 会以 UnicodeEncodeError 拒绝整个响应（500）
"""

import re
from typing import Optional
from urllib.parse import quote

# 白名单：ASCII 安全字符 + 常用中文区间（一-鿿 = U+4E00..U+9FFF）。
# 其余（引号、分号、CR/LF、控制字符、emoji 等）一律折叠为 "_"。
# 文件系统 slug（YAML 文件名、载体 ID）不允许 CJK，走 allow_cjk=False
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._\-一-鿿]+")
_FILENAME_SAFE_ASCII = re.compile(r"[^A-Za-z0-9._\-]+")
_BAD_EDGES = "._"


def sanitize_filename(name: str, *, max_len: int = 80,
                      fallback: str = "export",
                      allow_cjk: bool = True) -> str:
    """把任意用户输入收敛成安全文件名：白名单替换 → 去首尾点下划线 → 截断。

    结果保证非空且只含白名单字符；全非法输入回退 fallback。"""
    pattern = _FILENAME_SAFE if allow_cjk else _FILENAME_SAFE_ASCII
    safe = pattern.sub("_", str(name or "")).strip(_BAD_EDGES)
    safe = safe[:max_len].strip(_BAD_EDGES)
    return safe or fallback


def content_disposition(safe_name: str, *,
                        ascii_fallback: Optional[str] = None) -> str:
    """组装 `attachment` 的 Content-Disposition 值。

    safe_name 应先经 sanitize_filename；纯 ASCII 直接 filename=，非 ASCII
    走 filename*=UTF-8''...（RFC 5987），并给不支持 filename* 的旧客户端
    一个 ASCII 兜底名。"""
    if safe_name.isascii():
        return f"attachment; filename={safe_name}"
    ascii_name = ascii_fallback or "download"
    return (f"attachment; filename={ascii_name}; "
            f"filename*=UTF-8''{quote(safe_name)}")
