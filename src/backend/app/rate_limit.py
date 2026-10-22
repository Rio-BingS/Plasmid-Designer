"""
速率限制中间件
防止 API 滥用，支持多种限制策略
"""
import ipaddress
import json
import re
import time
from functools import lru_cache
from typing import Dict, List, Optional, Tuple, Callable
from fastapi import Request, HTTPException, Depends
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import logging
from collections import OrderedDict
from threading import Lock

from app.config import settings

logger = logging.getLogger(__name__)


class RateLimitExceeded(HTTPException):
    """速率限制超出异常"""
    def __init__(self, retry_after: int):
        super().__init__(
            status_code=429,
            detail=f"Rate limit exceeded. Retry after {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)}
        )


class _KeyTable:
    """限流计数表：普通 key 与受保护 key 分两张 OrderedDict 存放。

    两张表都按「最近一次活跃」排序（命中即 move_to_end），表头永远是
    最久未活跃的 key，所以淘汰只需从表头 popitem，均摊 O(1)，不再全表
    排序（审查 02 M1：超限后每请求 ~5 ms，单进程上限 ~192 req/s）。

    受保护 key（如按账号的登录失败计数 rate:login_fail:*）单独成表：
    攻击者批量制造普通 key 只会挤掉普通表里的旧 key，无法借此冲掉
    某个账号尚未过期的失败计数、重置口令猜测配额。
    """

    __slots__ = ("general", "protected")

    def __init__(self):
        # key -> [window_seconds, timestamps]
        self.general: "OrderedDict[str, list]" = OrderedDict()
        self.protected: "OrderedDict[str, list]" = OrderedDict()

    def table_for(self, key: str) -> "OrderedDict[str, list]":
        return self.protected if key.startswith(PROTECTED_KEY_PREFIXES) else self.general

    def get(self, key: str):
        return self.table_for(key).get(key)

    def pop(self, key: str, default=None):
        return self.table_for(key).pop(key, default)

    def clear(self) -> None:
        self.general.clear()
        self.protected.clear()

    def __contains__(self, key) -> bool:
        return key in self.general or key in self.protected

    def __len__(self) -> int:
        return len(self.general) + len(self.protected)


# 受保护 key 前缀：普通 key 再多也不会把它们挤出去（见 _KeyTable）
PROTECTED_KEY_PREFIXES: Tuple[str, ...] = ("rate:login_fail:",)


class InMemoryRateLimiter:
    """内存速率限制器（滑动窗口；key 淘汰均摊 O(1)）"""

    # 普通 key 总量上限：防止伪造 IP / 邮箱无限制造新 key 撑爆内存
    # （KNOWN_ISSUES 1.2/2.2）。淘汰已是 O(1)，上限可以放宽，减少洪泛时
    # 合法用户的计数桶被挤掉（审查 02 M1）。
    MAX_TRACKED_KEYS = 50_000
    # 受保护 key：只淘汰窗口已过期的；超过硬上限（内存兜底）才按最久
    # 未活跃强制淘汰并告警。
    MAX_PROTECTED_KEYS = 50_000
    MAX_PROTECTED_KEYS_HARD = 200_000

    def __init__(self):
        self._requests = _KeyTable()
        self._lock = Lock()

    @staticmethod
    def _expired(entry: list, now: float) -> bool:
        window, ts = entry
        return not ts or ts[-1] <= now - window

    def _evict_locked(self, now: float) -> None:
        """从表头（最久未活跃）淘汰超额 key。必须在持有 self._lock 时调用。"""
        general = self._requests.general
        while len(general) > self.MAX_TRACKED_KEYS:
            general.popitem(last=False)

        protected = self._requests.protected
        while len(protected) > self.MAX_PROTECTED_KEYS:
            head = next(iter(protected.values()))
            if self._expired(head, now):
                protected.popitem(last=False)
            elif len(protected) > self.MAX_PROTECTED_KEYS_HARD:
                key, _ = protected.popitem(last=False)
                logger.warning("受保护限流 key 超过硬上限，强制淘汰: %s", key)
            else:
                break

    def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        """
        检查是否允许请求

        Args:
            key: 限制键（如 IP 地址或用户 ID）
            max_requests: 时间窗口内最大请求数
            window_seconds: 时间窗口（秒）

        Returns:
            (是否允许, 剩余秒数)
        """
        now = time.time()
        window_start = now - window_seconds

        with self._lock:
            table = self._requests.table_for(key)
            entry = table.get(key)
            if entry is None:
                entry = [window_seconds, []]
                table[key] = entry
            else:
                table.move_to_end(key)
                entry[0] = max(entry[0], window_seconds)
            # 清理过期记录（单 key 至多 max_requests 条）
            ts = [t for t in entry[1] if t > window_start]
            entry[1] = ts

            # 检查是否超过限制
            if len(ts) >= max_requests:
                retry_after = int(min(ts) + window_seconds - now) + 1
                return False, retry_after

            # 记录请求
            ts.append(now)
            self._evict_locked(now)
            return True, 0

    def reset(self, key: str) -> None:
        """清空某个 key 的计数（如登录成功后清除该账号的失败记录）"""
        with self._lock:
            self._requests.pop(key, None)

    def get_remaining(self, key: str, max_requests: int, window_seconds: int) -> int:
        """获取剩余请求次数（只读：不为未知 key 建表项）"""
        window_start = time.time() - window_seconds

        with self._lock:
            entry = self._requests.get(key)
            if entry is None:
                return max_requests
            used = sum(1 for t in entry[1] if t > window_start)
            return max(0, max_requests - used)


class RedisRateLimiter:
    """Redis 速率限制器（分布式）"""
    
    def __init__(self, redis_client):
        self.redis = redis_client
    
    async def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        """使用 Redis 滑动窗口算法"""
        try:
            now = time.time()
            window_start = now - window_seconds
            
            pipe = self.redis.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            pipe.zadd(key, {str(now): now})
            pipe.expire(key, window_seconds)
            results = pipe.execute()
            
            count = results[1]
            if count >= max_requests:
                # 获取最早请求时间
                oldest = self.redis.zrange(key, 0, 0, withscores=True)
                if oldest:
                    retry_after = int(oldest[0][1] + window_seconds - now) + 1
                    return False, retry_after
                return False, window_seconds
            
            return True, 0
        except Exception as e:
            logger.error(f"Redis rate limit error: {e}")
            # Redis 失败时允许请求
            return True, 0


# 限制策略配置
RATE_LIMITS = {
    # API 端点限制
    "default": {"requests": 100, "window": 60},  # 100 请求/分钟
    "design": {"requests": 10, "window": 60},    # 10 设计任务/分钟
    "batch": {"requests": 3, "window": 60},      # 3 批量任务/分钟
    "upload": {"requests": 20, "window": 3600},  # 20 上传/小时
    # 批量测序分块上传的单个分块：一个批次只在建会话时计入 upload 档，
    # 分块另计（一批可拆成数十个分块，计入 upload 档会被 20 次/小时卡死）
    "upload_chunk": {"requests": 300, "window": 3600},
    "auth": {"requests": 5, "window": 60},       # 5 次/分钟（同 IP + 同目标邮箱）
    "auth_ip": {"requests": 20, "window": 60},   # 20 次/分钟（同 IP 全部认证写操作）
    # 按账号（规范化邮箱）的登录失败闸，不含 IP 维度：换 IP 也绕不过
    # 单账号的口令猜测（审查 02 H1）。登录成功清零；由 login 路由调用。
    "login_account": {"requests": 10, "window": 900},  # 10 次失败/15 分钟
    # 重型只读：打包/导出下载与 NCBI 外呼代理（终审 C-04 后曾退化到 default 档）
    "export": {"requests": 10, "window": 60},
    "ncbi": {"requests": 10, "window": 60},
    
    # 用户级别限制（已登录用户更高配额）
    # 生效条件：request.state.user 由 AuthStateMiddleware（app/auth/middleware.py）
    # 从 Bearer Token 解析写入；匿名请求仍按 IP 维度限流。
    "user_default": {"requests": 200, "window": 60},
    "user_design": {"requests": 30, "window": 60},
    "user_batch": {"requests": 10, "window": 60},
    "user_export": {"requests": 30, "window": 60},
    "user_ncbi": {"requests": 30, "window": 60},
}

# 全局限制器实例
limiter = InMemoryRateLimiter()


@lru_cache(maxsize=16)
def _parse_trusted_proxies(raw: str) -> Tuple:
    """解析 TRUSTED_PROXIES（逗号分隔 IP/CIDR）；非法项忽略并告警"""
    nets = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning("TRUSTED_PROXIES 含非法项，已忽略: %r", item)
    return tuple(nets)


def _is_trusted_proxy(host: str) -> bool:
    """socket 对端是否为配置的可信前置代理（settings.TRUSTED_PROXIES）。

    此前凡回环/私网对端一律信任——同一 docker 网络或内网里的任何主机
    都能伪造 X-Real-IP 换取全新限流 key（KNOWN_ISSUES 1.2）。现在只信
    显式允许清单，默认仅本机。
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _parse_trusted_proxies(settings.TRUSTED_PROXIES))


def _valid_ip(value: str) -> Optional[str]:
    value = (value or "").strip()
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        return None


def get_client_ip(request: Request) -> str:
    """获取客户端 IP（限流维度）。

    仅当对端是可信代理时才采信转发头：优先 X-Real-IP（nginx 用
    `proxy_set_header X-Real-IP $remote_addr` 强制覆写）；否则从右向左
    遍历 X-Forwarded-For，跳过可信代理，取第一个不可信地址。
    对端不可信时直接使用 socket 对端地址。
    """
    client_host = request.client.host if request.client else "unknown"
    if not _is_trusted_proxy(client_host):
        return client_host

    real_ip = _valid_ip(request.headers.get("X-Real-IP", ""))
    if real_ip:
        return real_ip

    forwarded = request.headers.get("X-Forwarded-For", "")
    for hop in reversed([h.strip() for h in forwarded.split(",") if h.strip()]):
        ip = _valid_ip(hop)
        if ip is None:
            # 跳过无法解析的跳，而不是把整条链判为不可信：一个畸形条目
            # （客户端自己塞的垃圾、或中间代理写坏）不该让所有请求退化到
            # 「按代理 IP 计数」的共享桶。仍取最右侧的有效非可信地址，
            # 也就是自家代理追加的那一跳，伪造左侧内容不影响结果。
            continue
        if not _is_trusted_proxy(ip):
            return ip
    return client_host


# IPv6 按前缀聚合：家宽/云主机通常整段分到一个 /64，按完整地址计数时
# 同一 /64 内每换一个地址就是新桶，所有按 IP 的配额都能绕过（审查 02 H1）
IPV6_PREFIX_LEN = 64


def normalize_ip_for_key(ip: str) -> str:
    """把客户端 IP 规范成限流维度。

    - IPv4 原样返回
    - IPv4 映射的 IPv6（::ffff:a.b.c.d）按其 IPv4 计
    - 其余 IPv6 取 /IPV6_PREFIX_LEN 前缀（如 `2001:db8::/64`），丢弃 zone id
    - 无法解析的值（如 "unknown"、测试客户端主机名）原样返回
    """
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return ip
    if addr.version == 4:
        return str(addr)
    if addr.ipv4_mapped is not None:
        return str(addr.ipv4_mapped)
    shift = 128 - IPV6_PREFIX_LEN
    net = ipaddress.IPv6Network(((int(addr) >> shift) << shift, IPV6_PREFIX_LEN))
    return str(net)


def get_client_key_ip(request: Request) -> str:
    """限流用的客户端维度：get_client_ip 之后再做 IPv6 前缀聚合"""
    return normalize_ip_for_key(get_client_ip(request))


def get_user_id(request: Request) -> Optional[str]:
    """获取用户 ID（如果已登录）"""
    user = getattr(request.state, "user", None)
    if user:
        return str(user.get("id", "anonymous"))
    return None


# 认证类端点不按令牌 sub 计数（登录/注册本就是未登录行为，携带任意令牌
# 即可换桶），而是按「客户端 IP + 请求体里的目标邮箱」计数
_AUTH_BUCKETS = ("auth",)


def normalize_email(value) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()[:254]


def get_rate_limit_key(request: Request, endpoint: str, identity: Optional[str] = None) -> str:
    """生成速率限制键。

    - auth 档：`rate:auth:{ip}:{email}`，忽略令牌（identity 为规范化邮箱）
    - 其余：已登录按用户，匿名按 IP
    - {ip} 均经 normalize_ip_for_key：IPv6 按 /64 聚合
    """
    ip = get_client_key_ip(request)
    if endpoint in _AUTH_BUCKETS:
        return f"rate:{endpoint}:{ip}:{identity or '-'}"

    user_id = get_user_id(request)
    if user_id:
        return f"rate:user:{user_id}:{endpoint}"
    return f"rate:ip:{ip}:{endpoint}"


async def _auth_identity(request: Request) -> str:
    """从登录/注册请求体取规范化邮箱；体积过大或非 JSON 时返回空串"""
    try:
        if int(request.headers.get("content-length") or 0) > 8192:
            return ""
        if "json" not in request.headers.get("content-type", ""):
            return ""
        body = await request.body()
        data = json.loads(body or b"{}")
    except (ValueError, TypeError):
        return ""
    return normalize_email(data.get("email")) if isinstance(data, dict) else ""


# ==================== 按账号的登录失败闸（审查 02 H1） ====================
# 不论账号是否存在都同样计数、同样拦截，429 响应与邮箱是否注册无关，
# 不会成为账号存在性探针。计数在校验口令之前原子占位（并发请求也
# 无法越过上限），口令正确即清零，所以实际只累计失败次数。

def _login_account_key(email) -> str:
    return f"rate:login_fail:{normalize_email(email) or '-'}"


def acquire_login_attempt(email) -> int:
    """登录前调用：返回 0 表示放行（已占一次失败名额），否则为 Retry-After 秒数"""
    cfg = RATE_LIMITS.get("login_account")
    if not cfg:
        return 0
    ok, retry_after = limiter.is_allowed(
        _login_account_key(email), cfg["requests"], cfg["window"])
    return 0 if ok else retry_after


def reset_login_failures(email) -> None:
    """口令校验通过后调用：清除该账号的失败计数"""
    limiter.reset(_login_account_key(email))


def too_many_response(key: str, limit_config: Dict, retry_after: int) -> JSONResponse:
    """统一的 429 响应（与中间件同形：detail + retry_after，带 Retry-After 头）"""
    logger.warning(f"Rate limit exceeded: {key}")
    return JSONResponse(
        status_code=429,
        content={
            "detail": f"Rate limit exceeded. Retry after {retry_after} seconds.",
            "retry_after": retry_after
        },
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(limit_config["requests"]),
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(int(time.time()) + retry_after)
        }
    )


def login_account_too_many(email, retry_after: int) -> JSONResponse:
    cfg = RATE_LIMITS.get("login_account") or {"requests": 0}
    return too_many_response(_login_account_key(email), cfg, retry_after)


# ==================== 路由级限流分类（终审 C-04） ====================
# 规则表：(HTTP 方法, 路径模板) → 限流档位。模板里 {param} 之后用前缀
# 匹配（/api/design/ 覆盖 /api/design/{id}）。
# 整段模板精确匹配的写操作规则（{param} 匹配单个路径段），先于下方前缀规则：
# 分块上传会话的子路径共享前缀，前缀匹配分不开
_WRITE_EXACT_RULES: List[Tuple[str, str, str]] = [
    ("POST", "/api/sequencing/batch-sessions", "upload"),
    ("POST", "/api/sequencing/batch-sessions/{id}/files", "upload_chunk"),
    ("POST", "/api/sequencing/batch-sessions/{id}/finalize", "batch"),
    ("DELETE", "/api/sequencing/batch-sessions/{id}", "default"),
    # 图谱页按 accession 从 NCBI 导入：外呼代理，与 /api/vectors/search/ncbi 同档
    ("POST", "/api/map/import/ncbi", "ncbi"),
    # 图谱页从在线序列来源导入（SnapGene 在线库 / Ensembl，在线来源导入批次）：
    # 同属外呼代理，与 NCBI 导入同档
    ("POST", "/api/map/import/snapgene", "ncbi"),
    ("POST", "/api/map/import/ensembl", "ncbi"),
    # 设计页一次性载体：上传走 upload 档、在线来源外呼走 ncbi 档——
    # 与载体库侧同口径（前缀规则 "POST /api/design" 会先命中 design 档，
    # 所以必须在这里精确匹配）
    ("POST", "/api/design/vector/upload", "upload"),
    ("POST", "/api/design/vector/online", "ncbi"),
    # 双酶切实时预览：每次改酶名都会调一次，只是读载体算布局不落库，
    # 吃 design 写配额会让用户连选几次酶就被 429
    ("POST", "/api/design/restriction-preview", "default"),
]


_ENDPOINT_RULES: List[Tuple[str, str, str]] = [
    # 测序上传（含批量）
    ("POST", "/api/sequencing/analyze-batch", "upload"),
    ("POST", "/api/sequencing/analyze", "upload"),
    ("POST", "/api/designs/{id}/sequencing/analyze", "upload"),
    ("POST", "/api/vectors/{id}/sequencing/analyze", "upload"),
    # 批量设计
    ("POST", "/api/design/batch", "batch"),
    # 单条设计
    ("POST", "/api/design", "design"),
    # 认证写操作（登录爆破面）
    ("POST", "/api/auth/login", "auth"),
    ("POST", "/api/auth/register", "auth"),
    ("POST", "/api/auth/verify", "auth"),
    ("POST", "/api/auth/resend-code", "auth"),
    # 登出不是爆破面：回落到 auth 档会让同一出口 IP 的他人把配额刷光，
    # 受害者登出 429、Cookie 不删、令牌不吊销（审查 01 M-1）
    ("POST", "/api/auth/logout", "default"),
    # 其余上传/导入类写操作
    ("POST", "/api/vectors/import", "upload"),
]


# 只读但重型的端点：整段模板匹配（{param} 匹配单个路径段）
_READ_RULES: List[Tuple[str, str]] = [
    ("/api/design/batch/{id}/download", "export"),
    ("/api/design/batch/{id}/report", "export"),
    ("/api/design/{id}/download/{fmt}", "export"),
    ("/api/analysis/design/{id}/export", "export"),
    ("/api/analysis/vector/{id}/export", "export"),
    ("/api/sequencing/batches/{id}/report", "export"),
    ("/api/sequencing/analyses/{id}/consensus/export", "export"),
    ("/api/vectors/search/ncbi", "ncbi"),
    ("/api/vectors/preview/ncbi/{id}", "ncbi"),
    # 在线来源的外呼代理（元数据/条目查询）：与 NCBI 代理同档。
    # 更具体的路径必须排在 {id} 模板之前：Addgene 本地索引搜索不联网，
    # 保持 default 档（读 17 万条本地 JSON，但不出网）
    ("/api/vectors/import/addgene/search", "default"),
    ("/api/vectors/import/addgene/{id}", "ncbi"),
    ("/api/vectors/import/uniprot/search", "ncbi"),
    ("/api/vectors/import/uniprot/{id}", "ncbi"),
    # 在线来源的集合/索引列举同样是外呼代理（与载体库侧共用缓存）
    ("/api/map/import/snapgene/search", "ncbi"),
    ("/api/vectors/import/snapgene/search", "ncbi"),
    ("/api/map/import/snapgene/sets", "ncbi"),
    ("/api/map/import/snapgene/sets/{id}", "ncbi"),
    # 载体库侧同一批外呼代理（审查 M4：此前落在 default 档 100/min，与图谱侧不一致）
    ("/api/vectors/import/snapgene/sets", "ncbi"),
    ("/api/vectors/import/snapgene/sets/{id}", "ncbi"),
    ("/api/vectors/import/snapgene/preview", "ncbi"),
    ("/api/vectors/import/ensembl/lookup/{id}", "ncbi"),
    ("/api/vectors/import/ensembl/preview", "ncbi"),
    # 设计页 SnapGene 跨集合搜索（外呼代理，与载体库/图谱页共用缓存）
    ("/api/design/vector/snapgene/search", "ncbi"),
]


def _template_regex(template: str) -> "re.Pattern":
    parts = re.split(r"\{[^}]+\}", template)
    return re.compile("^" + "[^/]+".join(re.escape(p) for p in parts) + "/?$")


_READ_RULES_RE = [(_template_regex(t), b) for t, b in _READ_RULES]
_WRITE_EXACT_RULES_RE = [(m, _template_regex(t), b) for m, t, b in _WRITE_EXACT_RULES]


def classify_endpoint(method: str, path: str) -> str:
    """按（方法 + 路径模板）判定限流档位。

    只读请求（GET/HEAD/OPTIONS）一律走宽松的 default 档——轮询设计进度、
    拉取峰图、checkAuth 都是高频只读，计入业务写配额会让用户在提交一次
    后就被 429 卡死（终审 C-04 实测：匿名 POST /api/design 后第 10 次轮询
    429；GET /api/auth/verify 第 6 次 429 → 前端 catch 直接 clearAuth
    把用户强制登出）。
    """
    method = (method or "GET").upper()
    for rule_method, rx, bucket in _WRITE_EXACT_RULES_RE:
        if rule_method == method and rx.match(path):
            return bucket
    for rule_method, template, bucket in _ENDPOINT_RULES:
        if rule_method != method:
            continue
        prefix = template.split("{")[0]
        if path == template or path.startswith(prefix):
            return bucket
    # 未命中规则表：只读除重型下载/外呼外一律 default，写操作按路径回退业务档
    if method in ("GET", "HEAD", "OPTIONS"):
        if method != "OPTIONS":
            for rx, bucket in _READ_RULES_RE:
                if rx.match(path):
                    return bucket
        return "default"
    if "/sequencing" in path:
        return "upload"
    if "/design" in path:
        return "design"
    if "/auth" in path:
        return "auth"
    return "default"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """速率限制中间件"""
    
    def __init__(self, app, limits: Dict = None):
        super().__init__(app)
        self.limits = limits or RATE_LIMITS
    
    async def dispatch(self, request: Request, call_next):
        # 跳过健康检查和静态文件
        if request.url.path in ["/health", "/"] or request.url.path.startswith(("/static", "/assets")):
            return await call_next(request)

        # 终审 C-05：预检 OPTIONS 不计入限流配额（浏览器对跨域预检无法重试，
        # 计入配额会让正常前端被 429 卡死；429 响应本身已由最外层 CORS 补头）
        if request.method == "OPTIONS":
            return await call_next(request)

        # 确定端点类型
        endpoint = self._get_endpoint_type(request)
        limit_config = self.limits.get(endpoint, self.limits["default"])

        identity = None
        if endpoint in _AUTH_BUCKETS:
            # 认证写操作：先过同 IP 总量闸，再按 IP+目标邮箱计数；不看令牌
            ip_cfg = self.limits.get("auth_ip")
            if ip_cfg:
                ok, retry = limiter.is_allowed(
                    f"rate:auth_ip:{get_client_key_ip(request)}",
                    ip_cfg["requests"], ip_cfg["window"])
                if not ok:
                    return self._too_many("auth_ip", ip_cfg, retry)
            identity = await _auth_identity(request)
        else:
            # 检查用户级别限制
            user_id = get_user_id(request)
            if user_id:
                user_endpoint = f"user_{endpoint}"
                if user_endpoint in self.limits:
                    limit_config = self.limits[user_endpoint]

        # 生成限制键
        key = get_rate_limit_key(request, endpoint, identity)

        # 检查限制
        allowed, retry_after = limiter.is_allowed(
            key,
            limit_config["requests"],
            limit_config["window"]
        )

        if not allowed:
            return self._too_many(key, limit_config, retry_after)
        
        # 添加限制头
        response = await call_next(request)
        
        remaining = limiter.get_remaining(
            key,
            limit_config["requests"],
            limit_config["window"]
        )
        
        response.headers["X-RateLimit-Limit"] = str(limit_config["requests"])
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(time.time()) + limit_config["window"])
        
        return response
    
    @staticmethod
    def _too_many(key: str, limit_config: Dict, retry_after: int) -> JSONResponse:
        return too_many_response(key, limit_config, retry_after)

    # 终审 C-04：此前按路径子串分类——轮询 GET /api/design/{id} 被 "/design"
    # 命中写档（实测提交后第 10 次轮询即 429），GET /api/auth/verify、/auth/me
    # 也计入 auth 档（第 6 次 429），前端 checkAuth 收到 429 就清会话 →
    # 用户被强制登出。改为按（HTTP 方法 + 路由模板）匹配：只读端点统一走
    # 宽松的 default 档，只有真正的写操作才计入业务配额。
    def _get_endpoint_type(self, request: Request) -> str:
        """按（方法 + 路由模板）确定限流档位

        匹配顺序：先查精确的 (method, path) 特殊规则，再按路由模板前缀
        匹配，最后回退 method 判定（写操作按业务档、只读一律 default）。
        """
        return classify_endpoint(request.method, request.url.path)


# 依赖注入：用于单个路由的速率限制
def rate_limit(endpoint: str = "default"):
    """速率限制依赖"""
    async def dependency(request: Request):
        limit_config = RATE_LIMITS.get(endpoint, RATE_LIMITS["default"])
        key = get_rate_limit_key(request, endpoint)
        
        allowed, retry_after = limiter.is_allowed(
            key,
            limit_config["requests"],
            limit_config["window"]
        )
        
        if not allowed:
            raise RateLimitExceeded(retry_after)
        
        return True
    
    return dependency


# 装饰器版本
def rate_limited(endpoint: str = "default"):
    """速率限制装饰器"""
    def decorator(func):
        async def wrapper(*args, request: Request = None, **kwargs):
            limit_config = RATE_LIMITS.get(endpoint, RATE_LIMITS["default"])
            key = get_rate_limit_key(request, endpoint)
            
            allowed, retry_after = limiter.is_allowed(
                key,
                limit_config["requests"],
                limit_config["window"]
            )
            
            if not allowed:
                raise RateLimitExceeded(retry_after)
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator
