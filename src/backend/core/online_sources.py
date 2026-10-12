"""在线序列来源客户端：SnapGene 在线库 / Ensembl / UniProt / Addgene

与 `core/plasmid_map/ncbi.py` 同一套约定：本模块**只负责取字节/取文本**，
解析一律交给 `core.plasmid_map.parsers`（GenBank / FASTA / SnapGene .dna 都能读），
错误统一抛 `OnlineSourceError`（`MapParseError` 子类，带 `.status` 供路由映射 HTTP），
外呼做进程级最小间隔节流 + 体积上限 + 响应嗅探；`opener` 参数供测试注入打桩。

数据源与许可（务必随界面一起提示用户，来源：参考取证报告 + 各站条款）：

* SnapGene 在线质粒库（`https://www.snapgene.com/local`）
  地图/注释版权属 SnapGene（GraphPad Software, LLC）：学术、非营利、政府可无条件使用，
  但**必须引用 `www.snapgene.com/resources`**；**商业实体须先取得 GraphPad 书面许可**。
  `snapgene.com/robots.txt` 明确 `Disallow: /local` → 本站只做低频、单次、人工触发的
  读取，不做批量镜像、不做定时同步、不对外再分发 zip。
* Ensembl REST：数据本身无使用限制（代码 Apache-2.0），注明来源即可。
* UniProt：数据以 CC BY 4.0 提供（须署名）。**UniProt 是蛋白库**、不含质粒序列：
  本站只用它按基因/蛋白名反查 Entrez/EMBL/RefSeq 交叉引用，再把用户引到核酸来源。
* Addgene：非商业研究可下载；**2026-08 起全长序列文件需登录**。
  `robots.txt` 只禁 `/emta/`、`/emta-addgene-public/`、`/users/login/`；禁止商业抓取与
  访问非公开部分的是其网站条款（Terms of Use, Permitted Use）→ 本模块**绝不绕过登录**：
  匿名只取元数据与图谱图；配置了 `ADDGENE_SESSION_COOKIE`（用户自己的浏览器会话）时
  才带着该凭据取全长序列——仅以 https 发往 addgene.org（重定向逐跳校验、跨主机即丢弃），
  不写入响应/日志、不代用户登录。该模式只适合单用户自托管（凭据本身存于 .env/进程环境）。

环境变量：ONLINE_IMPORT_ENABLED（默认 true，设 false 整体关闭外呼）、
ONLINE_TIMEOUT（秒，默认 25）、ONLINE_MAX_BYTES（单次下载上限，默认 8 MB）、
SNAPGENE_BASE / ENSEMBL_BASE / UNIPROT_BASE（自建代理时可改）、
SNAPGENE_APPDATA（测试用）、ADDGENE_SESSION_COOKIE（可选，用户自备）。
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Optional

from core.plasmid_map.parsers import MapParseError, ParsedRecord, parse_file, parse_text

SNAPGENE_BASE = os.environ.get("SNAPGENE_BASE", "https://www.snapgene.com/local").rstrip("/")
ENSEMBL_BASE = os.environ.get("ENSEMBL_BASE", "https://rest.ensembl.org").rstrip("/")
UNIPROT_BASE = os.environ.get("UNIPROT_BASE", "https://rest.uniprot.org").rstrip("/")
ADDGENE_FILE_API = "https://www.addgene.org/api/get-sequence-file-collection/{sid}/"

DEFAULT_MAX_BYTES = int(os.environ.get("ONLINE_MAX_BYTES", str(8 * 1024 * 1024)))
ZIP_MAX_BYTES = int(os.environ.get("ONLINE_ZIP_MAX_BYTES", str(64 * 1024 * 1024)))
# SnapGene .dna 前若干字节含魔法串（文件偏移 4 起 8 字节）
SNAPGENE_MAGIC = b"SnapGene"

# 各站点最小请求间隔（秒）：机器人条款要求的"低频"落到代码里
_MIN_INTERVAL = {
    "www.snapgene.com": 1.0,
    "rest.ensembl.org": 0.2,
    "rest.uniprot.org": 0.2,
    "www.addgene.org": 1.0,
}
# 每个站点的「下一个可用时间槽」（monotonic 秒）；锁只保护这张表的读改写，
# 不在锁内 sleep（审查 M3：此前全局锁内 sleep → 各站点串行、线程池可被占满）
_next_slot: Dict[str, float] = {}
_throttle_lock = threading.Lock()

def public_base(url: str) -> str:
    """对外展示的基地址：去掉 userinfo（`https://user:pass@proxy/...` 不能回显给匿名用户）"""
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return ""
    if parts.username is None and parts.password is None:
        return str(url or "")
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


# ---------------------------------------------------------------- 许可文案

SNAPGENE_LICENSE = (
    "SnapGene 在线质粒库：学术/非营利/政府可无条件使用，须引用 www.snapgene.com/resources；"
    "商业用途须先获得 GraphPad Software, LLC 许可。本站 robots.txt 明确 Disallow: /local，"
    "请勿批量镜像或对外再分发。"
)
ENSEMBL_LICENSE = "Ensembl：数据无使用限制（代码 Apache-2.0），建议注明来源。"
UNIPROT_LICENSE = (
    "UniProt：数据以 CC BY 4.0 提供（须署名）。UniProt 是蛋白库、不含质粒序列——"
    "本站只用它按基因/蛋白名反查 Entrez/EMBL/RefSeq 交叉引用，再据此去核酸来源取 DNA。"
)
ADDGENE_LICENSE = (
    "Addgene：非商业研究可按网站条款下载（须保留版权声明）；2026-08 起全长序列需登录，"
    "商业抓取被 ToS 明文禁止——本工具不代为登录、不绕登录墙；如已自行登录，"
    "可用自己的会话凭据（ADDGENE_SESSION_COOKIE）取自己有权访问的序列。"
)
DISCLAIMER = (
    "导入的序列与注释版权归原数据源所有，请按各自条款使用并保留出处标注；"
    "自动化导入仅限低频、人工触发。"
)


class OnlineSourceError(MapParseError):
    """在线来源失败（含参数校验、体积上限、上游错误）。status 供路由映射 HTTP 状态。"""


class AddgeneLoginRequired(OnlineSourceError):
    """Addgene 全长序列需要登录——本模块刻意不绕过（见模块 docstring）。"""

    def __init__(self, message: str):
        super().__init__(message, 403)


# ---------------------------------------------------------------- 通用外呼

def import_enabled() -> bool:
    """ONLINE_IMPORT_ENABLED 开关：部署方不想让后端外呼时一键关闭"""
    raw = os.environ.get("ONLINE_IMPORT_ENABLED", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _ensure_enabled() -> None:
    if not import_enabled():
        raise OnlineSourceError("在线导入已被部署方关闭（ONLINE_IMPORT_ENABLED=false）", 503)


SNAPGENE_DISABLED_MSG = (
    "SnapGene 在线库已被部署方关闭（SNAPGENE_ONLINE_ENABLED=false）"
    "（其网站条款禁止存入检索系统、robots.txt 禁止 /local，仅限学术/非营利自用实例）")


def snapgene_online_enabled() -> bool:
    """SNAPGENE_ONLINE_ENABLED 开关（默认 **开启**，与 ONLINE_IMPORT_ENABLED 相互独立）。

    v2.12 审查 H2：SnapGene `/local` 在线库属合规高风险来源（网站条款 (a) 禁止
    "reproduce or store … in any … electronic retrieval system"、robots.txt
    `Disallow: /local`、端点来自客户端逆向），用户决定默认关闭、仅登录用户可用，
    v2.14.1 起用户改为默认开启、仍仅登录用户可用；部署方可设 false 关闭。Ensembl/UniProt/Addgene/NCBI 不受影响。
    """
    raw = os.environ.get("SNAPGENE_ONLINE_ENABLED", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _ensure_snapgene_enabled() -> None:
    if not snapgene_online_enabled():
        raise OnlineSourceError(SNAPGENE_DISABLED_MSG, 404)


def _timeout() -> float:
    try:
        return float(os.environ.get("ONLINE_TIMEOUT", "25"))
    except ValueError:
        return 25.0


def _throttle_max_wait() -> float:
    """单次外呼最多为节流排队等待多久（秒，ONLINE_THROTTLE_MAX_WAIT，默认 10）"""
    try:
        return max(0.0, float(os.environ.get("ONLINE_THROTTLE_MAX_WAIT", "10")))
    except ValueError:
        return 10.0


def _throttle(host: str) -> None:
    """同一站点两次外呼之间至少间隔 _MIN_INTERVAL[host]（进程级，按站点独立）。

    锁内只「预占」下一个时间槽，锁外 sleep：不同站点互不阻塞，同站点按槽排队。
    排队超过 `_throttle_max_wait()` 直接拒绝（429），不让请求无限占着线程池。
    """
    interval = _MIN_INTERVAL.get(host, 0.5)
    with _throttle_lock:
        now = time.monotonic()
        slot = max(now, _next_slot.get(host, 0.0))
        wait = slot - now
        if wait > _throttle_max_wait():
            raise OnlineSourceError(
                f"上游 {host} 的请求排队过长（需等待约 {wait:.0f} 秒），请稍后再试", 429)
        _next_slot[host] = slot + interval
    if wait > 0:
        time.sleep(wait)


class UnsafeUrlError(OnlineSourceError):
    """外呼目标（含重定向的每一跳）不是 https 或主机不在白名单：一律拒绝，不外呼。"""

    def __init__(self, message: str):
        super().__init__(message, 502)


# 重定向最多跟几跳（每一跳都重新校验 scheme + 主机白名单）
_MAX_REDIRECTS = 3
_REDIRECT_CODES = (301, 302, 303, 307, 308)


def _is_addgene_host(host: str) -> bool:
    host = (host or "").lower()
    return host == "addgene.org" or host.endswith(".addgene.org")


def _allowed_hosts() -> set:
    """外呼主机白名单：各来源基地址（含部署方改过的 *_BASE）的主机名。

    Addgene 的 addgene.org 及其子域另由 `_is_addgene_host` 放行。按调用时的
    模块常量计算，测试/部署改了 *_BASE 也能同步生效。
    """
    hosts = set()
    for base in (SNAPGENE_BASE, ENSEMBL_BASE, UNIPROT_BASE):
        h = urllib.parse.urlsplit(base).hostname
        if h:
            hosts.add(h.lower())
    return hosts


def _checked_host(url: str) -> str:
    """校验外呼 URL：只允许 https + 白名单主机（按裸主机名比对，不含端口/userinfo）。

    拒绝 file:// / ftp:// / data: / http:// 等一切非 https 链接——此前上游 JSON 给出的
    直链 scheme 未受限，`urlopen` 会照读本地文件或把凭据明文发出。
    """
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
        host = (parts.hostname or "").lower()
    except ValueError as e:
        raise UnsafeUrlError("外呼地址无法解析，已拒绝") from e
    if parts.scheme.lower() != "https":
        raise UnsafeUrlError(
            f"只允许 https 外呼，已拒绝 {parts.scheme or '(无 scheme)'}:// 链接")
    if not host or not (host in _allowed_hosts() or _is_addgene_host(host)):
        raise UnsafeUrlError(f"外呼主机 {host or '(空)'} 不在白名单内，已拒绝")
    return host


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """禁止 urllib 自动跟随重定向。

    CPython 的 HTTPRedirectHandler 会把原请求的普通头（含 Cookie）原样复制到
    新请求，不区分跨主机、也不阻止 https→http 降级。这里返回 None，3xx 以
    HTTPError 抛给 `_http_get`，由它逐跳校验后决定是否跟随、是否带凭据。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


# 测试可往这里塞自定义 handler（如本地伪 HTTPS 服务），走的仍是真实 OpenerDirector
_EXTRA_HANDLERS: list = []


def _default_open(req, timeout=None):
    """真实外呼：自建 opener（禁自动重定向），不用全局 urlopen。"""
    opener = urllib.request.build_opener(_NoRedirectHandler(), *_EXTRA_HANDLERS)
    return opener.open(req, timeout=timeout)


def _translate_http_error(e: "urllib.error.HTTPError") -> OnlineSourceError:
    if e.code in (400, 403, 404):
        return OnlineSourceError(f"上游未找到或拒绝访问（HTTP {e.code}）", 404)
    if e.code == 429:
        return OnlineSourceError("上游请求过于频繁，请稍后再试", 503)
    return OnlineSourceError(f"上游服务返回错误（HTTP {e.code}）", 502)


def _http_get(url: str, *, max_bytes: int, accept: str = "*/*",
              timeout: Optional[float] = None,
              cookie: Optional[str] = None,
              opener: Optional[Callable] = None) -> bytes:
    """带体积上限的外呼；上游错误统一翻译成 OnlineSourceError。

    安全约束（v2.12 审查 H1）：
    * 起始 URL 与**每一跳**重定向目标都必须是 https 且主机在白名单内；
      最多跟 `_MAX_REDIRECTS` 跳，自动重定向被禁用（见 `_NoRedirectHandler`）。
    * `cookie`（Addgene 用户自备会话）只用 `add_unredirected_header` 附带，
      且只发给与起始 URL 相同的 addgene.org 主机；一旦跨主机跳转即永久丢弃。
    """
    _ensure_enabled()
    origin_host = _checked_host(url)
    send_cookie = bool(cookie) and _is_addgene_host(origin_host)
    open_fn = opener or _default_open
    to = timeout if timeout is not None else _timeout()
    current = url
    for _hop in range(_MAX_REDIRECTS + 1):
        host = _checked_host(current)
        send_cookie = send_cookie and host == origin_host
        req = urllib.request.Request(current, headers={
            "User-Agent": "PlasmidDesigner/online-import (+academic use)",
            "Accept": accept,
        })
        if send_cookie:
            req.add_unredirected_header("Cookie", cookie)
        _throttle(host)
        buf = bytearray()
        try:
            with open_fn(req, timeout=to) as resp:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise OnlineSourceError(
                            f"上游文件过大（超过 {max_bytes // (1024 * 1024)} MB），已中止下载", 413)
        except OnlineSourceError:
            raise
        except urllib.error.HTTPError as e:
            if e.code in _REDIRECT_CODES:
                location = e.headers.get("Location") if e.headers is not None else None
                e.close()
                if not location:
                    raise OnlineSourceError("上游重定向缺少目标地址", 502) from e
                current = urllib.parse.urljoin(current, location)
                continue
            raise _translate_http_error(e) from e
        except (socket.timeout, TimeoutError) as e:
            raise OnlineSourceError("连接上游超时，请稍后再试", 504) from e
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
                raise OnlineSourceError("连接上游超时，请稍后再试", 504) from e
            raise OnlineSourceError("无法连接上游数据源，请稍后再试", 502) from e
        return bytes(buf)
    raise OnlineSourceError(f"上游重定向超过 {_MAX_REDIRECTS} 跳，已中止", 502)


def _http_get_json(url: str, **kw) -> dict:
    raw = _http_get(url, accept="application/json", **kw)
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise OnlineSourceError("上游返回的不是 JSON", 502) from e
    if not isinstance(data, dict):
        raise OnlineSourceError("上游返回的 JSON 结构异常", 502)
    return data


# ================================================================ SnapGene

_SET_ID_RE = re.compile(r"^[A-Za-z0-9_()\-]{1,64}$")
# 序列 ID（索引里的 url 字段）：通常是下划线形式，但兼容空格；
# 一律再经 urllib.parse.quote 编码，且落库 ID 会被 safe_slug 清洗
_PLASMID_ID_RE = re.compile(r"^[A-Za-z0-9_.\-][A-Za-z0-9_.\- ]{0,118}[A-Za-z0-9_.\-]$|^[A-Za-z0-9_.\-]$")


def validate_set_id(raw: str) -> str:
    v = str(raw or "").strip()
    if not _SET_ID_RE.match(v):
        raise OnlineSourceError(
            f"集合 ID 无效：{str(raw or '')[:40]}（示例：yeast_plasmids、crispr_plasmids）", 422)
    return v


def validate_plasmid_id(raw: str) -> str:
    v = str(raw or "").strip()
    if not _PLASMID_ID_RE.match(v):
        raise OnlineSourceError(
            f"序列 ID 无效：{str(raw or '')[:40]}（示例：SARS_Genome、pYES2）", 422)
    return v


def snapgene_sets(*, timeout: Optional[float] = None,
                  opener: Optional[Callable] = None) -> List[Dict[str, str]]:
    """列出在线序列集合（GET /local/sets.php?xml）"""
    _ensure_snapgene_enabled()
    url = f"{SNAPGENE_BASE}/sets.php?xml"
    raw = _http_get(url, max_bytes=2 * 1024 * 1024, accept="application/xml",
                    timeout=timeout, opener=opener)
    try:
        root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    except ET.ParseError as e:
        raise OnlineSourceError("SnapGene 集合列表解析失败（接口可能已变化）", 502) from e
    return [{"set_id": s.get("path", ""), "name": s.get("name", "")}
            for s in root.findall("Set") if s.get("path")]


def snapgene_set_index(set_id: str, *, timeout: Optional[float] = None,
                       opener: Optional[Callable] = None) -> List[Dict[str, object]]:
    """取集合内全部序列的索引（GET /local/fetch.php?set=<id>）"""
    _ensure_snapgene_enabled()
    sid = validate_set_id(set_id)
    url = f"{SNAPGENE_BASE}/fetch.php?set={urllib.parse.quote(sid, safe='')}"
    raw = _http_get(url, max_bytes=16 * 1024 * 1024, accept="application/xml",
                    timeout=timeout, opener=opener)
    if not raw.strip():
        # 上游对不存在的集合回 200 + 空包体；常见是把质粒名（如 pET-30a）当集合输入
        raise OnlineSourceError(
            f"SnapGene 没有集合「{sid}」。要找某个质粒（如 pET-30a），"
            "直接在集合框输入质粒名回车即可跨集合搜索", 404)
    return parse_set_index(raw, sid)


def parse_set_index(raw: bytes, set_id: str = "") -> List[Dict[str, object]]:
    """解析集合索引 XML（远端接口与客户端 snapgeneSequences/<set>.xml 同构）"""
    try:
        root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    except ET.ParseError as e:
        raise OnlineSourceError(
            f"集合 {set_id or '(未知)'} 的索引解析失败（接口可能已变化）", 502) from e
    out: List[Dict[str, object]] = []
    for p in root.findall("Plasmid"):
        try:
            index = int(p.get("index", "-1") or -1)
        except ValueError:
            index = -1
        out.append({
            "name": p.get("name", ""),
            "plasmid_id": p.get("url", ""),
            "filename": p.get("filename", ""),
            "index": index,
        })
    return out


def snapgene_plasmid_bytes(set_id: str, plasmid_id: str, *, max_bytes: Optional[int] = None,
                           timeout: Optional[float] = None,
                           opener: Optional[Callable] = None) -> bytes:
    """下载单个 .dna（GET /local/fetch.php?set=&plasmid=），并校验 SnapGene 魔法串"""
    _ensure_snapgene_enabled()
    sid = validate_set_id(set_id)
    pid = validate_plasmid_id(plasmid_id)
    url = (f"{SNAPGENE_BASE}/fetch.php?set={urllib.parse.quote(sid, safe='')}"
           f"&plasmid={urllib.parse.quote(pid, safe='')}")
    data = _http_get(url, max_bytes=max_bytes or DEFAULT_MAX_BYTES,
                     accept="application/octet-stream", timeout=timeout, opener=opener)
    assert_snapgene_dna(data, f"{sid}/{pid}")
    return data


def snapgene_set_zip_bytes(set_id: str, *, max_bytes: Optional[int] = None,
                           timeout: Optional[float] = None,
                           opener: Optional[Callable] = None) -> bytes:
    """下载整个集合的 zip（GET /local/fetch.php?set=&zip）。

    体积上限单独放宽到 ONLINE_ZIP_MAX_BYTES；调用方应提醒用户整包可达数 MB。
    """
    _ensure_snapgene_enabled()
    sid = validate_set_id(set_id)
    url = f"{SNAPGENE_BASE}/fetch.php?set={urllib.parse.quote(sid, safe='')}&zip"
    data = _http_get(url, max_bytes=max_bytes or ZIP_MAX_BYTES,
                     accept="application/zip", timeout=timeout, opener=opener)
    if data[:2] != b"PK":
        raise OnlineSourceError("上游返回的不是 zip（接口可能已变化）", 502)
    return data


def assert_snapgene_dna(data: bytes, what: str = "序列") -> None:
    """校验是 SnapGene .dna 二进制（前 64 字节内含魔法串），否则 415

    错误信息不回显上游响应内容（审查 L6：可能是登录页 HTML 等），只给长度。
    """
    if SNAPGENE_MAGIC not in data[:64]:
        raise OnlineSourceError(
            f"{what} 返回的不是 SnapGene .dna 文件（接口可能已变化；收到 {len(data)} 字节）", 415)


def snapgene_record(data: bytes, *, name: Optional[str] = None,
                    topology: Optional[str] = None,
                    max_length: Optional[int] = None) -> ParsedRecord:
    """.dna 字节 → ParsedRecord（交给项目统一解析器，含特征/颜色/拓扑）

    `name` 来自集合索引（filename/name），比文件内部的名字可靠：实测 SnapGene
    .dna 里的 `record.name` 是状态标记（如 "REVIEWED"），并非质粒名。
    `max_length` 由调用方按用途给上限（图谱页传 MAP_MAX_LENGTH，超限 413）。
    """
    filename = f"{name or 'snapgene'}.dna"
    try:
        rec = parse_file(filename, data, max_length=max_length, topology=topology)
    except MapParseError as e:
        raise OnlineSourceError(f"SnapGene 文件解析失败：{e}", e.status) from e
    if name:
        rec.name = str(name)[:80]
    return rec


# ================================================================ Ensembl

_ENSEMBL_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
# 只接受能作为 DNA 的来源：蛋白序列不是质粒/图谱序列，明确拒绝
ENSEMBL_SEQ_TYPES = ("genomic", "cds", "cdna")


def validate_ensembl_id(raw: str) -> str:
    v = str(raw or "").strip()
    if not _ENSEMBL_ID_RE.match(v):
        raise OnlineSourceError(
            f"Ensembl ID 无效：{str(raw or '')[:40]}（示例：ENSG00000139618、ENST00000288602）", 422)
    return v


def ensembl_lookup(identifier: str, *, expand: bool = False,
                   timeout: Optional[float] = None,
                   opener: Optional[Callable] = None) -> dict:
    """GET /lookup/id/<id>（元数据：名称、类型、物种、位置等）"""
    ident = validate_ensembl_id(identifier)
    params = {"content-type": "application/json"}
    if expand:
        params["expand"] = "1"
    url = f"{ENSEMBL_BASE}/lookup/id/{urllib.parse.quote(ident, safe='')}?{urllib.parse.urlencode(params)}"
    return _http_get_json(url, max_bytes=2 * 1024 * 1024, timeout=timeout, opener=opener)


def ensembl_sequence(identifier: str, seq_type: str = "cds", *, timeout: Optional[float] = None,
                     opener: Optional[Callable] = None) -> str:
    """GET /sequence/id/<id>?type=...，返回 FASTA 文本（DNA 序列）"""
    ident = validate_ensembl_id(identifier)
    if seq_type not in ENSEMBL_SEQ_TYPES:
        raise OnlineSourceError(
            f"不支持的序列类型 {seq_type!r}（可选：{'、'.join(ENSEMBL_SEQ_TYPES)}；"
            "蛋白序列不能作为质粒/图谱序列导入）", 422)
    qs = urllib.parse.urlencode({"type": seq_type, "content-type": "text/x-fasta"})
    url = f"{ENSEMBL_BASE}/sequence/id/{urllib.parse.quote(ident, safe='')}?{qs}"
    raw = _http_get(url, max_bytes=DEFAULT_MAX_BYTES, accept="text/x-fasta",
                    timeout=timeout, opener=opener)
    text = raw.decode("utf-8", errors="replace")
    if not text.lstrip().startswith(">"):
        raise OnlineSourceError("Ensembl 未返回 FASTA 序列（ID 或类型可能有误）", 502)
    return text


def ensembl_record(identifier: str, seq_type: str = "cds", *, name: Optional[str] = None,
                   max_length: Optional[int] = None,
                   timeout: Optional[float] = None,
                   opener: Optional[Callable] = None) -> ParsedRecord:
    """Ensembl 序列 → ParsedRecord（默认线性：导入的是片段而非质粒）

    `max_length` 按用途给上限：`seq_type=genomic` 时返回的是整个基因区，
    给染色体 ID 会很大——图谱页传 MAP_MAX_LENGTH，超限 413。
    """
    text = ensembl_sequence(identifier, seq_type, timeout=timeout, opener=opener)
    try:
        rec = parse_text(text, name=name or validate_ensembl_id(identifier),
                         topology="linear", max_length=max_length)
    except MapParseError as e:
        raise OnlineSourceError(f"Ensembl 序列解析失败：{e}", e.status) from e
    rec.source_format = f"ensembl:{seq_type}"
    rec.warnings.append(f"Ensembl {seq_type} 序列按线性片段导入")
    return rec


def ensembl_ping(*, timeout: Optional[float] = None, opener: Optional[Callable] = None) -> bool:
    try:
        return "ok" in json.dumps(_http_get_json(
            f"{ENSEMBL_BASE}/info/ping?content-type=application/json",
            max_bytes=64 * 1024, timeout=timeout, opener=opener)).lower()
    except OnlineSourceError:
        return False


# ================================================================ UniProt
# 定位：**不给质粒序列**（蛋白库），只做「按基因/蛋白名查条目 → 反查核酸交叉引用」，
# 把用户引到 NCBI/Ensembl 去取 DNA；蛋白 FASTA 仅供未来蛋白级注释使用。

_UNIPROT_ACC_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,19}$")
# 这些交叉引用库里的条目是核酸（或其转录本），可作为取 DNA 的入口
UNIPROT_NUCLEOTIDE_DBS = ("EMBL", "RefSeq", "Ensembl", "CCDS")


def validate_uniprot_accession(raw: str) -> str:
    v = str(raw or "").strip()
    if not _UNIPROT_ACC_RE.match(v):
        raise OnlineSourceError(
            f"UniProt 登录号无效：{str(raw or '')[:40]}"
            "（示例：P04637、P53_HUMAN、ENSG00000141510 的蛋白产物 P04637）", 422)
    return v


def _uniprot_entry_view(data: Dict[str, object]) -> Dict[str, object]:
    """UniProt 条目 JSON → 本项目用的精简视图（含核酸交叉引用）"""
    desc = data.get("proteinDescription") or {}
    rec = (desc.get("recommendedName") or {}) if isinstance(desc, dict) else {}
    name = ""
    if isinstance(rec, dict):
        name = str((rec.get("fullName") or {}).get("value") or "")
    if not name and isinstance(desc, dict):
        subs = desc.get("submissionNames") or []
        if subs:
            name = str((subs[0].get("fullName") or {}).get("value") or "")
    gene = ""
    for g in (data.get("genes") or []):
        if isinstance(g, dict) and isinstance(g.get("geneName"), dict):
            gene = str(g["geneName"].get("value") or "")
            if gene:
                break
    seq = data.get("sequence") or {}
    refs: List[Dict[str, str]] = []
    for x in (data.get("uniProtKBCrossReferences") or []):
        if not isinstance(x, dict):
            continue
        db = str(x.get("database") or "")
        if db not in UNIPROT_NUCLEOTIDE_DBS:
            continue
        props = {str(p.get("key")): str(p.get("value"))
                 for p in (x.get("properties") or []) if isinstance(p, dict)}
        item = {"database": db, "id": str(x.get("id") or "")}
        if props.get("ProteinId"):
            item["protein_id"] = props["ProteinId"]
        if props.get("MoleculeType"):
            item["molecule_type"] = props["MoleculeType"]
        refs.append(item)
    return {
        "accession": str(data.get("primaryAccession") or ""),
        "entry_id": str(data.get("uniProtkbId") or ""),
        "name": name,
        "gene": gene,
        "organism": str((data.get("organism") or {}).get("scientificName") or ""),
        "length": int(seq.get("length") or 0) if isinstance(seq, dict) else 0,
        "reviewed": "reviewed" in str(data.get("entryType") or "").lower(),
        "cross_references": refs,
    }


def nucleotide_candidates(entry: Dict[str, object]) -> List[Dict[str, str]]:
    """从 UniProt 条目里挑出可直接去取 DNA 的核酸入口（前端渲染成跳转链接）"""
    out: List[Dict[str, str]] = []
    for ref in (entry.get("cross_references") or []):
        if not isinstance(ref, dict):
            continue
        db, rid = str(ref.get("database") or ""), str(ref.get("id") or "")
        if not rid:
            continue
        if db in ("EMBL", "RefSeq"):
            out.append({"source": "ncbi", "accession": rid,
                        "label": f"{db} {rid}",
                        "url": f"https://www.ncbi.nlm.nih.gov/nuccore/{rid}"})
        elif db == "Ensembl":
            ident = rid.split(".")[0]
            out.append({"source": "ensembl", "accession": ident, "label": f"Ensembl {rid}",
                        "url": f"https://www.ensembl.org/id/{ident}"})
        elif db == "CCDS":
            out.append({"source": "ccds", "accession": rid, "label": f"CCDS {rid}",
                        "url": f"https://www.ncbi.nlm.nih.gov/CCDS/CcdsBrowse.cgi?REQUEST=CCDS&DATA={rid}"})
    return out


def uniprot_search(query: str, *, limit: int = 10, reviewed_only: bool = False,
                   timeout: Optional[float] = None,
                   opener: Optional[Callable] = None) -> List[Dict[str, object]]:
    """GET /uniprotkb/search：按基因名/蛋白名/登录号检索（返回精简视图）"""
    term = str(query or "").strip()
    if not term or len(term) > 120:
        raise OnlineSourceError("UniProt 检索词无效（1–120 字符，如 TP53、p53 human）", 422)
    n = max(1, min(int(limit), 25))
    if reviewed_only:
        term = f"({term}) AND (reviewed:true)"
    qs = urllib.parse.urlencode({
        "query": term, "format": "json", "size": n,
        "fields": "accession,id,protein_name,gene_names,organism_name,length",
    })
    data = _http_get_json(f"{UNIPROT_BASE}/uniprotkb/search?{qs}",
                          max_bytes=4 * 1024 * 1024, timeout=timeout, opener=opener)
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise OnlineSourceError("UniProt 检索返回结构异常（接口可能已变化）", 502)
    return [_uniprot_entry_view(r) for r in results if isinstance(r, dict)]


def uniprot_entry(accession: str, *, timeout: Optional[float] = None,
                  opener: Optional[Callable] = None) -> Dict[str, object]:
    """GET /uniprotkb/{accession}.json：条目详情 + 核酸交叉引用"""
    acc = validate_uniprot_accession(accession)
    data = _http_get_json(f"{UNIPROT_BASE}/uniprotkb/{urllib.parse.quote(acc, safe='')}.json",
                          max_bytes=4 * 1024 * 1024, timeout=timeout, opener=opener)
    return _uniprot_entry_view(data)


def uniprot_fasta(accession: str, *, timeout: Optional[float] = None,
                  opener: Optional[Callable] = None) -> str:
    """GET /uniprotkb/{accession}.fasta：蛋白序列（供未来蛋白级注释；不作图谱序列）"""
    acc = validate_uniprot_accession(accession)
    raw = _http_get(f"{UNIPROT_BASE}/uniprotkb/{urllib.parse.quote(acc, safe='')}.fasta",
                    max_bytes=2 * 1024 * 1024, accept="text/plain",
                    timeout=timeout, opener=opener)
    text = raw.decode("utf-8", errors="replace")
    if not text.lstrip().startswith(">"):
        raise OnlineSourceError("UniProt 未返回 FASTA（登录号可能有误）", 502)
    return text


# ================================================================ Addgene

_ADDGENE_ID_RE = re.compile(r"^\d{1,10}$")


def validate_addgene_id(raw: str) -> str:
    v = str(raw or "").strip().lstrip("#")
    if not _ADDGENE_ID_RE.match(v):
        raise OnlineSourceError(
            f"Addgene 质粒号无效：{str(raw or '')[:40]}（示例：52961、48138）", 422)
    return v


def addgene_metadata(object_id: str, *, timeout: Optional[float] = None,
                     opener: Optional[Callable] = None) -> Dict[str, object]:
    """匿名元数据接口：GET /api/get-sequence-file-collection/<seqObjectId>/

    返回各文件 URL（genbankUrl / snapgeneUrl / mapPngUrl / mapSvgUrl）与 inProgress。
    注意：匿名只对 map 图返回 200，`.gbk`/`.dna` 直链是 404（登录墙）。
    """
    oid = validate_addgene_id(object_id)
    url = ADDGENE_FILE_API.format(sid=urllib.parse.quote(oid, safe=''))
    return _http_get_json(url, max_bytes=1 * 1024 * 1024, timeout=timeout, opener=opener)


def addgene_session_cookie() -> str:
    """用户自备的 Addgene 会话 Cookie（未配置则空串）。

    **只从环境变量读**，不进日志、不回显，调用方不得把它写进响应；注意它本身存于
    部署方的 `.env`/进程环境里，只适合单用户自托管（多用户实例启动时告警，见
    `app/online_import.addgene_shared_session_warning`）。
    """
    return (os.environ.get("ADDGENE_SESSION_COOKIE") or "").strip()


def addgene_has_session() -> bool:
    """是否配置了用户自备凭据（前端据此显示「可用自己的登录态取序列」）"""
    return bool(addgene_session_cookie())


def _addgene_cookie_for(url: str) -> Optional[str]:
    """仅当 URL 是 https 且主机（裸主机名，不含端口）属 addgene.org 时返回会话 Cookie。

    这只是起点校验；重定向时的逐跳校验与跨主机丢弃由 `_http_get` 负责。
    """
    cookie = addgene_session_cookie()
    if not cookie:
        return None
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
        host = parts.hostname or ""
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not _is_addgene_host(host):
        return None
    return cookie


def addgene_sequence_bytes(object_id: str, *, fmt: str = "genbank",
                           timeout: Optional[float] = None,
                           opener: Optional[Callable] = None) -> bytes:
    """取全长序列字节。

    * 未配置会话（`ADDGENE_SESSION_COOKIE`）时：匿名直链实测 404 →
      抛 `AddgeneLoginRequired`（403 + 合法路径指引），**不代登录、不绕墙**。
    * 配置了用户自己的登录态时：带该凭据取自己有权访问的直链；
      仍失败（凭据过期/无权限）同样抛 403，但提示先重新登录再更新凭据。
    """
    meta = addgene_metadata(object_id, timeout=timeout, opener=opener)
    key = "snapgeneUrl" if fmt in ("dna", "snapgene") else "genbankUrl"
    direct = str(meta.get(key) or "")
    if not direct:
        raise AddgeneLoginRequired(
            f"Addgene 元数据里没有 {key}（该质粒可能未公开序列文件）")
    # 直链来自上游 JSON：先校验（https + addgene.org），不合格直接拒绝且不带凭据
    host = _checked_host(direct)
    if not _is_addgene_host(host):
        raise UnsafeUrlError(f"Addgene 元数据给出的下载链接不在 addgene.org 下（{host}），已拒绝")
    logged_in = addgene_has_session()
    try:
        return _http_get(direct, max_bytes=DEFAULT_MAX_BYTES, accept="*/*",
                         timeout=timeout, cookie=_addgene_cookie_for(direct),
                         opener=opener)
    except UnsafeUrlError:
        raise
    except OnlineSourceError as e:
        if logged_in:
            raise AddgeneLoginRequired(
                f"用你配置的 Addgene 会话取 {key} 失败（HTTP {e.status}）：凭据可能已过期，"
                "请重新登录后更新 ADDGENE_SESSION_COOKIE；或改用 NCBI 上的对应 GenBank 记录。") from e
        raise AddgeneLoginRequired(
            f"Addgene 全长序列需要登录（{key} 匿名取用失败，HTTP {e.status}）。"
            "本工具不绕过登录：请用自己的 Addgene 账号在浏览器下载后走「上传文件」导入，"
            "或改用 NCBI 上的对应 GenBank 记录（很多 Addgene 质粒有 accession）；"
            "若已自行登录，可把浏览器会话 Cookie 配到 ADDGENE_SESSION_COOKIE。") from e


def addgene_record(object_id: str, *, fmt: str = "genbank", name: Optional[str] = None,
                   max_length: Optional[int] = None, timeout: Optional[float] = None,
                   opener: Optional[Callable] = None) -> ParsedRecord:
    """带（用户自备）凭据取到的 Addgene 全长序列 → ParsedRecord。

    解析交给项目统一解析器（.gbk / .dna 都能读）；无凭据时由
    `addgene_sequence_bytes` 抛 403 + 指引，本函数不做任何绕过。
    """
    oid = validate_addgene_id(object_id)
    data = addgene_sequence_bytes(oid, fmt=fmt, timeout=timeout, opener=opener)
    stem = name or f"addgene_{oid}"
    filename = f"{stem}.dna" if fmt in ("dna", "snapgene") else f"{stem}.gbk"
    try:
        rec = parse_file(filename, data, max_length=max_length)
    except MapParseError as e:
        raise OnlineSourceError(f"Addgene 序列文件解析失败：{e}", e.status) from e
    rec.name = str(stem)[:80]
    return rec


# Addgene 本地索引（约 7 MB / 17 万条）解析结果的进程内缓存：
# {路径: (mtime_ns, size, 列表)}；文件变了（mtime/大小）才重读（审查 L4：此前每次请求整读）
_addgene_index_cache: Dict[str, tuple] = {}
_addgene_index_lock = threading.Lock()


def _read_addgene_index(path: str) -> list:
    with open(path, encoding="utf-8", errors="replace") as fh:
        data = json.load(fh)
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def _addgene_index_rows(path: str) -> list:
    """按 (mtime, size) 校验的缓存读取；读失败返回空列表（不缓存失败结果）"""
    try:
        st = os.stat(path)
    except OSError:
        return []
    key = (st.st_mtime_ns, st.st_size)
    with _addgene_index_lock:
        hit = _addgene_index_cache.get(path)
        if hit is not None and hit[:2] == key:
            return hit[2]
        try:
            rows = _read_addgene_index(path)
        except (OSError, ValueError):
            _addgene_index_cache.pop(path, None)
            return []
        _addgene_index_cache[path] = (key[0], key[1], rows)
        return rows


def addgene_local_index(term: str, limit: int = 20,
                        index_path: Optional[str] = None) -> List[Dict[str, object]]:
    """在 SnapGene 客户端自带的 Addgene 全量索引里离线搜索（不联网）。

    索引位于 `%APPDATA%\\SnapGene\\addgenePlasmids\\addgeneProcessed.json`
    （约 17 万条 {"id":…, "name":…}）。缺失时返回空列表，由调用方提示用户。
    """
    path = index_path or os.path.join(
        os.environ.get("SNAPGENE_APPDATA") or os.path.join(
            os.environ.get("APPDATA", ""), "SnapGene"),
        "addgenePlasmids", "addgeneProcessed.json")
    if not os.path.isfile(path):
        return []
    term = str(term or "").strip()
    data = _addgene_index_rows(path)
    if not data:
        return []
    if term.isdigit():
        return [e for e in data if str(e.get("id")) == term][:limit]
    if not term:
        return []
    low = term.lower()
    hits = [e for e in data if low in str(e.get("name", "")).lower()]
    hits.sort(key=lambda e: (len(str(e.get("name", ""))), str(e.get("id", ""))))
    return hits[:limit]
