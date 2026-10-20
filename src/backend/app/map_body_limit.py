"""/api/map/* 请求体流式体积上限（纯 ASGI 中间件）

FastAPI 在进入处理函数之前就会把 JSON 请求体整体读入并交给 pydantic 解析，
multipart 也会先整体 spool 到临时文件；只看 Content-Length 的预检挡不住
chunked 传输。这里在最外层（CORS 之内）边收边计数：超过上限立即回 413，
不调用下游应用；未超限的请求体原样回放给下游。

上限：
- POST /api/map/parse：MAP_MAX_UPLOAD_MB + 64 KB（multipart 包装开销）；
- 其余 /api/map/* 写请求（analyze、export/genbank、import/*）：MAP_MAX_JSON_MB
  （默认 16 MB：导出请求体是整份 MapDocument，含位点明细与 ORF）。
- 设计页一次性载体（/api/design/vector/*）：上传与 /api/map/parse 同一上限，
  其余按 JSON 上限（内联载体回传可能带整条载体序列）。
nginx 的 `location ^~ /api/map/` client_max_body_size 须不小于两者中的较大值。
"""

import json
from typing import Callable, Optional

MAP_PREFIX = "/api/map/"
# 设计页一次性载体：上传文件与回传内联载体走同一套体积上限
DESIGN_VECTOR_PREFIX = "/api/design/vector/"
MULTIPART_OVERHEAD = 64 * 1024
_WRITE_METHODS = {"POST", "PUT", "PATCH"}


def map_body_limit(path: str) -> int:
    """上限取 map_routes 的常量（环境变量只在那里读一次），惰性导入避免循环"""
    from app.routes import map_routes as M

    if path.rstrip("/") == "/api/map/parse":
        return M.MAP_MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD
    return M.MAP_MAX_JSON_BYTES


def design_vector_body_limit(path: str) -> int:
    """设计页一次性载体：上传按文件上限，其余（在线导入/内联载体）按 JSON 上限"""
    from app.routes import map_routes as M  # 复用同一份体积上限常量
    from app.routes import models as _models

    if path.rstrip("/").endswith("/upload"):
        return _models.MAP_MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD
    return M.MAP_MAX_JSON_BYTES


class MapBodyLimitMiddleware:
    def __init__(self, app, limit_for: Callable[[str], int] = map_body_limit,
                 prefix: str = MAP_PREFIX):
        self.app = app
        self.limit_for = limit_for
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if (scope.get("type") != "http" or scope.get("method") not in _WRITE_METHODS
                or not scope.get("path", "").startswith(self.prefix)):
            await self.app(scope, receive, send)
            return
        limit = self.limit_for(scope["path"])
        clen = _content_length(scope)
        if clen is not None and clen > limit:
            await _send_413(send, limit)
            return
        chunks = []
        size = 0
        more = True
        while more:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            if msg["type"] != "http.request":
                continue
            body = msg.get("body", b"")
            size += len(body)
            if size > limit:
                await _send_413(send, limit)
                return
            chunks.append(body)
            more = msg.get("more_body", False)

        replayed = False
        data = b"".join(chunks)
        chunks.clear()

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": data, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def _content_length(scope) -> Optional[int]:
    for k, v in scope.get("headers") or ():
        if k == b"content-length":
            try:
                return int(v)
            except ValueError:
                return None
    return None


async def _send_413(send, limit: int) -> None:
    mb = limit / (1024 * 1024)
    body = json.dumps({"detail": f"请求体过大（上限约 {mb:.1f} MB）"},
                      ensure_ascii=False).encode("utf-8")
    await send({"type": "http.response.start", "status": 413, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        (b"connection", b"close"),
    ]})
    await send({"type": "http.response.body", "body": body})
