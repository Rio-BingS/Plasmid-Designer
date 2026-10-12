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
* Addgene：非商业研究可下载；**2026-08 起全长序列文件需登录**，
  `robots.txt`/ToS 禁止商业抓取与非公开路径访问 → 本模块**绝不绕过登录**：
  匿名只取元数据与图谱图；配置了 `ADDGENE_SESSION_COOKIE`（用户自己的浏览器会话）时
  才带着该凭据取全长序列——凭据只发给 addgene.org，不落盘、不回显、不代用户登录。

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
_last_request: Dict[str, float] = {}
_throttle_lock = threading.Lock()

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
              headers: Optional[Dict[str, str]] = None,
              opener: Optional[Callable] = None) -> bytes:
    """带体积上限的外呼；上游错误统一翻译成 OnlineSourceError。

    `headers` 只用于必需场景（如 Addgene 用户自备会话 Cookie），调用方必须保证
    不把凭据发给不相关的主机（见 `_addgene_session_headers`）。
    """
    _ensure_enabled()
    host = urllib.parse.urlsplit(url).netloc
    req = urllib.request.Request(url, headers={
        "User-Agent": "PlasmidDesigner/online-import (+academic use)",
        "Accept": accept,
        **(headers or {}),
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

    **只从环境变量读**，不落盘、不进日志、不回显；调用方不得把它写进响应。
    """
    return (os.environ.get("ADDGENE_SESSION_COOKIE") or "").strip()


def addgene_has_session() -> bool:
    """是否配置了用户自备凭据（前端据此显示「可用自己的登录态取序列」）"""
    return bool(addgene_session_cookie())


def _addgene_session_headers(url: str) -> Dict[str, str]:
    """仅对 addgene.org 主机附带会话 Cookie（防止上游给的直链指向别处时泄凭据）"""
    cookie = addgene_session_cookie()
    host = urllib.parse.urlsplit(url).netloc.lower()
    if not cookie:
        return {}
    if not (host == "addgene.org" or host.endswith(".addgene.org")):
        return {}
    return {"Cookie": cookie}


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
    logged_in = addgene_has_session()
    try:
        return _http_get(direct, max_bytes=DEFAULT_MAX_BYTES, accept="*/*",
                         timeout=timeout, headers=_addgene_session_headers(direct),
                         opener=opener)
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
