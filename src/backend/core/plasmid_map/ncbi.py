"""按 accession 从 NCBI 取 GenBank 记录（E-utilities efetch）

只做「取文本」这一步：校验 accession 格式 → efetch（db=nuccore,
rettype=gbwithparts, retmode=text，带 tool / email / api_key）→ 体积与长度
上限检查。解析交给现有 parsers.parse_text 流水线，缓存与限流在路由层。

NCBI 使用规范（https://www.ncbi.nlm.nih.gov/books/NBK25497/）：请求须带 tool
与 email；无 API key 每秒不超过 3 次、有 key 不超过 10 次——本模块对整个
进程做最小间隔节流（与按客户端的限流中间件互补）。

环境变量：NCBI_API_KEY、NCBI_EMAIL（均可选）、NCBI_TOOL（默认
PlasmidDesigner）、NCBI_TIMEOUT（秒，默认 30：单次导入从排队到读完的总时长上限）。
并发：路由层用独立的 anyio CapacityLimiter（NCBI_MAX_CONCURRENCY，默认 4）跑
NCBI 导入，不占全局线程池。
"""

import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

from core.plasmid_map.parsers import MapParseError

EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# GenBank/ENA/DDBJ 核酸 accession（1-2 字母 + 5-8 位数字；WGS/TSA 4-6 字母
# + 8-10 位数字）与 RefSeq（NC_/NZ_/NM_… 下划线，NZ_ 后可带 2-4 字母 WGS 前缀），
# 版本号可选。只收 accession，不收 GI 号与检索词
_ACCESSION_RE = re.compile(
    r"^(?:[A-Z]{1,2}\d{5,8}|[A-Z]{4,6}\d{8,10}|[A-Z]{2}_(?:[A-Z]{2,4})?\d{6,11})(?:\.\d{1,3})?$"
)
_LOCUS_LEN_RE = re.compile(r"^LOCUS\s+\S+\s+(\d+)\s+bp", re.MULTILINE)

_throttle_lock = threading.Lock()
_last_request = 0.0


def normalize_accession(raw: str) -> str:
    """去空白、转大写并校验格式；不合法抛 MapParseError(422)"""
    acc = re.sub(r"\s+", "", str(raw or "")).upper()
    if not acc or len(acc) > 24 or not _ACCESSION_RE.match(acc):
        raise MapParseError(
            f"NCBI accession 格式无效：{str(raw or '')[:30]}（示例：L09137、M77789.1、NC_005213.1）", 422)
    return acc


def _min_interval(api_key: Optional[str]) -> float:
    return 0.11 if api_key else 0.34


def _reserve_slot(api_key: Optional[str]) -> float:
    """锁内只预约下一个发请求的时间点（相邻请求间隔 ≥ _min_interval），返回
    还需等待的秒数；sleep 在锁外进行，排队的请求不再串行持锁睡眠"""
    global _last_request
    with _throttle_lock:
        now = time.monotonic()
        slot = max(now, _last_request + _min_interval(api_key))
        _last_request = slot
    return slot - now


def _throttle(api_key: Optional[str], deadline: Optional[float] = None) -> None:
    wait = _reserve_slot(api_key)
    if deadline is not None and time.monotonic() + wait > deadline:
        raise MapParseError("NCBI 请求排队过久，请稍后再试", 503)
    if wait > 0:
        time.sleep(wait)


def _set_read_timeout(resp, seconds: float) -> None:
    """把响应底层 socket 的单次读超时收紧到剩余时间（拿不到 socket 时忽略）"""
    try:
        sock = resp.fp.raw._sock  # http.client.HTTPResponse → SocketIO → socket
        sock.settimeout(max(0.05, seconds))
    except Exception:
        pass


def efetch_params(accession: str) -> dict:
    params = {
        "db": "nuccore",
        "id": accession,
        "rettype": "gbwithparts",
        "retmode": "text",
        "tool": os.environ.get("NCBI_TOOL", "PlasmidDesigner") or "PlasmidDesigner",
    }
    email = os.environ.get("NCBI_EMAIL", "").strip()
    if email:
        params["email"] = email
    api_key = os.environ.get("NCBI_API_KEY", "").strip()
    if api_key:
        params["api_key"] = api_key
    return params


def fetch_genbank_text(
    accession: str,
    *,
    max_bytes: int,
    max_length: int,
    timeout: Optional[float] = None,
    opener: Optional[Callable] = None,
) -> str:
    """取一条 accession 的 GenBank 文本（阻塞 IO，调用方须放线程池）。

    错误一律 MapParseError：404 未找到、413 超过体积/长度上限、
    502 NCBI 返回异常、503 NCBI 限流、504 超时。"""
    acc = normalize_accession(accession)
    params = efetch_params(acc)
    url = f"{EFETCH_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": f"{params['tool']} (+map NCBI import)",
        "Accept": "text/plain",
    })
    if timeout is None:
        timeout = float(os.environ.get("NCBI_TIMEOUT", "30"))
    # timeout 是整次取数（排队节流 + 连接 + 全部读取）的总时长上限；urlopen 的
    # timeout 只管单次 socket 操作，慢速吐数据的服务器能把线程占任意久
    deadline = time.monotonic() + timeout
    _throttle(params.get("api_key"), deadline)
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(req, timeout=max(0.05, deadline - time.monotonic())) as resp:
            buf = bytearray()
            locus_checked = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MapParseError("从 NCBI 下载超时，请稍后再试", 504)
                _set_read_timeout(resp, remaining)
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise MapParseError(
                        f"NCBI 记录 {acc} 过大（超过 {max_bytes // (1024 * 1024)} MB），无法在图谱中打开", 413)
                # 长度在 LOCUS 行里，读到首行就能提前拒绝超长记录，不必下载完
                if not locus_checked and b"\n" in buf:
                    _check_locus_length(bytes(buf[:400]), acc, max_length)
                    locus_checked = True
    except MapParseError:
        raise
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            raise MapParseError(f"NCBI 中未找到 accession {acc}", 404) from e
        if e.code == 429:
            raise MapParseError("NCBI 请求过于频繁，请稍后再试", 503) from e
        raise MapParseError(f"NCBI 服务返回错误（HTTP {e.code}），请稍后再试", 502) from e
    except (socket.timeout, TimeoutError) as e:
        raise MapParseError("连接 NCBI 超时，请稍后再试", 504) from e
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
            raise MapParseError("连接 NCBI 超时，请稍后再试", 504) from e
        raise MapParseError("无法连接 NCBI，请稍后再试", 502) from e

    text = bytes(buf).decode("utf-8", errors="replace")
    if not text.lstrip().startswith("LOCUS"):
        # efetch 对不存在的 id 有时返回 200 + 空体或 "Error ..." 文本
        raise MapParseError(f"NCBI 中未找到 accession {acc}", 404)
    _check_locus_length(text[:400].encode("utf-8"), acc, max_length)
    return text


def _check_locus_length(head: bytes, acc: str, max_length: int) -> None:
    m = _LOCUS_LEN_RE.search(head.decode("utf-8", errors="replace"))
    if m and int(m.group(1)) > max_length:
        raise MapParseError(
            f"NCBI 记录 {acc} 长 {int(m.group(1)):,} bp，超过图谱上限 {max_length:,} bp", 413)
