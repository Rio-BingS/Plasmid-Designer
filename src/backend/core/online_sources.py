"""在线序列来源客户端：SnapGene 在线库 / Ensembl / Addgene

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
* Addgene：非商业研究可下载；**2026-08 起全长序列文件需登录**，
  `robots.txt`/ToS 禁止商业抓取与非公开路径访问 → 本模块**绝不绕过登录**，
  只取匿名可达的元数据与图谱图，序列请用户自行登录下载或改用 NCBI。

环境变量：ONLINE_IMPORT_ENABLED（默认 true，设 false 整体关闭外呼）、
ONLINE_TIMEOUT（秒，默认 25）、ONLINE_MAX_BYTES（单次下载上限，默认 8 MB）、
SNAPGENE_BASE / ENSEMBL_BASE（自建代理时可改）、SNAPGENE_APPDATA（测试用）。
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
ADDGENE_FILE_API = "https://www.addgene.org/api/get-sequence-file-collection/{sid}/"

DEFAULT_MAX_BYTES = int(os.environ.get("ONLINE_MAX_BYTES", str(8 * 1024 * 1024)))
ZIP_MAX_BYTES = int(os.environ.get("ONLINE_ZIP_MAX_BYTES", str(64 * 1024 * 1024)))
# SnapGene .dna 前若干字节含魔法串（文件偏移 4 起 8 字节）
SNAPGENE_MAGIC = b"SnapGene"

# 各站点最小请求间隔（秒）：机器人条款要求的"低频"落到代码里
_MIN_INTERVAL = {
    "www.snapgene.com": 1.0,
    "rest.ensembl.org": 0.2,
    "www.addgene.org": 1.0,
}
_last_request: Dict[str, float] = {}
_throttle_lock = threading.Lock()

# ---------------------------------------------------------------- 许可文案

SNAPGENE_LICENSE = (
    "SnapGene 在线质粒库：学术/非营利/政府可无条件使用，须引用 www.snapgene.com/resources；"
    "商业用途须先获得 GraphPad Software, LLC 许可。本站 robots.txt 明确 Disallow: /local，"
    "请勿批量镜像或对外再分发。"
)
ENSEMBL_LICENSE = "Ensembl：数据无使用限制（代码 Apache-2.0），建议注明来源。"
ADDGENE_LICENSE = (
    "Addgene：非商业研究可按网站条款下载（须保留版权声明）；2026-08 起全长序列需登录，"
    "商业抓取被 ToS 明文禁止——本工具不代为登录、不绕登录墙。"
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


def _timeout() -> float:
    try:
        return float(os.environ.get("ONLINE_TIMEOUT", "25"))
    except ValueError:
        return 25.0


def _throttle(host: str) -> None:
    """同一站点两次外呼之间至少间隔 _MIN_INTERVAL[host]（进程级）。"""
    interval = _MIN_INTERVAL.get(host, 0.5)
    with _throttle_lock:
        wait = _last_request.get(host, 0.0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[host] = time.monotonic()


def _http_get(url: str, *, max_bytes: int, accept: str = "*/*",
              timeout: Optional[float] = None,
              opener: Optional[Callable] = None) -> bytes:
    """带体积上限的外呼；上游错误统一翻译成 OnlineSourceError。"""
    _ensure_enabled()
    host = urllib.parse.urlsplit(url).netloc
    req = urllib.request.Request(url, headers={
        "User-Agent": "PlasmidDesigner/online-import (+academic use)",
        "Accept": accept,
    })
    open_fn = opener or urllib.request.urlopen
    _throttle(host)
    buf = bytearray()
    try:
        with open_fn(req, timeout=timeout if timeout is not None else _timeout()) as resp:
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
        if e.code in (400, 403, 404):
            raise OnlineSourceError(f"上游未找到或拒绝访问（HTTP {e.code}）", 404) from e
        if e.code == 429:
            raise OnlineSourceError("上游请求过于频繁，请稍后再试", 503) from e
        raise OnlineSourceError(f"上游服务返回错误（HTTP {e.code}）", 502) from e
    except (socket.timeout, TimeoutError) as e:
        raise OnlineSourceError("连接上游超时，请稍后再试", 504) from e
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
            raise OnlineSourceError("连接上游超时，请稍后再试", 504) from e
        raise OnlineSourceError("无法连接上游数据源，请稍后再试", 502) from e
    return bytes(buf)


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
    sid = validate_set_id(set_id)
    url = f"{SNAPGENE_BASE}/fetch.php?set={urllib.parse.quote(sid, safe='')}"
    raw = _http_get(url, max_bytes=16 * 1024 * 1024, accept="application/xml",
                    timeout=timeout, opener=opener)
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
    sid = validate_set_id(set_id)
    url = f"{SNAPGENE_BASE}/fetch.php?set={urllib.parse.quote(sid, safe='')}&zip"
    data = _http_get(url, max_bytes=max_bytes or ZIP_MAX_BYTES,
                     accept="application/zip", timeout=timeout, opener=opener)
    if data[:2] != b"PK":
        raise OnlineSourceError("上游返回的不是 zip（接口可能已变化）", 502)
    return data


def assert_snapgene_dna(data: bytes, what: str = "序列") -> None:
    """校验是 SnapGene .dna 二进制（前 64 字节内含魔法串），否则 415"""
    if SNAPGENE_MAGIC not in data[:64]:
        head = data[:80].decode("utf-8", errors="replace").replace("\n", " ")
        raise OnlineSourceError(
            f"{what} 返回的不是 SnapGene .dna 文件（接口可能已变化）：{head!r}", 415)


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


def addgene_sequence_bytes(object_id: str, *, fmt: str = "genbank",
                           timeout: Optional[float] = None,
                           opener: Optional[Callable] = None) -> bytes:
    """尝试取全长序列 —— 匿名会 404；一律抛 AddgeneLoginRequired（不绕登录）。"""
    meta = addgene_metadata(object_id, timeout=timeout, opener=opener)
    key = "snapgeneUrl" if fmt in ("dna", "snapgene") else "genbankUrl"
    direct = str(meta.get(key) or "")
    if not direct:
        raise AddgeneLoginRequired(
            f"Addgene 元数据里没有 {key}（该质粒可能未公开序列文件）")
    try:
        return _http_get(direct, max_bytes=DEFAULT_MAX_BYTES, accept="*/*",
                         timeout=timeout, opener=opener)
    except OnlineSourceError as e:
        raise AddgeneLoginRequired(
            f"Addgene 全长序列需要登录（{key} 匿名取用失败，HTTP {e.status}）。"
            "本工具不绕过登录：请用自己的 Addgene 账号在浏览器下载后走「上传文件」导入，"
            "或改用 NCBI 上的对应 GenBank 记录（很多 Addgene 质粒有 accession）。") from e


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
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    if term.isdigit():
        return [e for e in data if str(e.get("id")) == term][:limit]
    if not term:
        return []
    low = term.lower()
    hits = [e for e in data if low in str(e.get("name", "")).lower()]
    hits.sort(key=lambda e: (len(str(e.get("name", ""))), str(e.get("id", ""))))
    return hits[:limit]
