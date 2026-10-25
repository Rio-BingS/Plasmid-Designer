"""质粒图谱 API（契约 plasmid-map/1，docs/MAP.md）

POST /api/map/parse            上传文件（multipart file）或 JSON 文本 → MapDocument
POST /api/map/analyze          编辑后的序列/特征 → 重算酶切/ORF/自动注释
GET  /api/map/enzyme-sets      酶集合
GET  /api/map/enzymes/{name}   单把酶详情（厂商、同切酶、Type IIS、甲基化）
GET  /api/map/suppliers        REBASE 厂商及各自可供的酶（前端按厂商过滤）
POST /api/map/export/genbank   MapDocument → .gb 下载
POST /api/map/import/ncbi      NCBI accession → efetch GenBank → MapDocument

整个路由挂 `map` 功能开关（main.py）。解析/比对/扫描是 CPU 计算，一律移交
线程池；结果按「输入摘要 + 选项 + 元件库版本 + 算法版本」缓存（只缓存
不超过 MAP_CACHE_MAX_BP、序列化不超过 MAP_CACHE_MAX_DOC_BYTES 的文档，本进程
写入总量不超过 MAP_CACHE_MAX_TOTAL_BYTES，避免内存缓存被大文档撑满）。
限流沿用中间件 default 档（与 /api/analysis 相同）；NCBI 导入走 ncbi 档
（外呼代理，rate_limit._WRITE_EXACT_RULES）。
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Literal, Optional, Tuple

import anyio
import anyio.to_thread
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.cache import cache
from app.routes.models import (
    MAP_MAX_ENZYMES, MAP_MAX_FEATURES, MAP_MAX_LENGTH,
    MAP_MAX_UPLOAD_BYTES, MAP_MAX_UPLOAD_MB,
    MapDocument, MapFeature, MapPrimer,
)
from core.plasmid_map import (
    MapParseError, document_from_record, parse_file, parse_text,
)
from core.plasmid_map.annotate import library_version
from core.plasmid_map.document import build_document
from core.plasmid_map.enzymes import enzyme_detail, enzyme_sets, suppliers
from core.plasmid_map.genbank import document_to_genbank
from core.plasmid_map.ncbi import fetch_genbank_text, normalize_accession
from core.plasmid_map.parsers import clean_sequence

router = APIRouter(prefix="/api/map", tags=["map"])

logger = logging.getLogger(__name__)

# MAP_MAX_UPLOAD_MB / MAP_MAX_UPLOAD_BYTES 定义在 app/routes/models.py
# （设计页的载体上传与图谱页共用同一上限）
# 其余 /api/map/* 写请求的请求体上限（导出请求体是整份 MapDocument）；
# 由 app/map_body_limit.py 在读取时流式执行
MAP_MAX_JSON_MB = float(os.environ.get("MAP_MAX_JSON_MB", "16"))
MAP_MAX_JSON_BYTES = int(MAP_MAX_JSON_MB * 1024 * 1024)
MAP_CACHE_MAX_BP = int(os.environ.get("MAP_CACHE_MAX_BP", "50000"))
# 体积门槛：序列化后超过此字节数的文档不缓存（commercial 酶组的高频切酶位点
# 会让 50 kb 文档膨胀到数 MB，内存缓存里驻留约 4 倍）；同时限制本进程写入
# 缓存的图谱文档总字节数，超出按写入先后淘汰（MemoryCache 只按条数封顶）
MAP_CACHE_MAX_DOC_BYTES = int(os.environ.get("MAP_CACHE_MAX_DOC_BYTES", str(1024 * 1024)))
MAP_CACHE_MAX_TOTAL_BYTES = int(os.environ.get("MAP_CACHE_MAX_TOTAL_BYTES", str(64 * 1024 * 1024)))
MAP_CACHE_TTL = 3600
# 产出口径版本：改变 MapDocument 内容的代码改动须递增（元件库变化已由
# library_version 自动进键）
MAP_ALGO_VERSION = "4"

# NCBI 原始 GenBank 文本缓存：与产出口径无关（不进 MAP_ALGO_VERSION），
# 只缓存不超过上限的文本；不带版本号的 accession 可能被 NCBI 更新，TTL 取 1 天
NCBI_TEXT_CACHE_TTL = 86400
NCBI_TEXT_CACHE_MAX_BYTES = int(os.environ.get("NCBI_TEXT_CACHE_MAX_BYTES", str(2 * 1024 * 1024)))

ORF_MIN_AA_LO, ORF_MIN_AA_HI = 10, 5000
EnzymeSetId = Field(default="common", max_length=40)


def _cache_key(kind: str, payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"map:{MAP_ALGO_VERSION}:{library_version()}:{kind}:{digest}"


def _cache_get(key: str):
    try:
        return cache.backend.get(key)
    except Exception:
        return None


_cache_sizes: "OrderedDict[str, Tuple[int, float]]" = OrderedDict()  # key → (字节, 到期)
_cache_sizes_lock = threading.Lock()


def _doc_bytes(doc: Dict) -> int:
    return len(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _cache_put(key: str, doc: Dict, size: Optional[int] = None) -> None:
    """按长度与序列化体积决定是否缓存；size 为调用方已算好的体积（线程池里）"""
    if doc.get("length", 0) > MAP_CACHE_MAX_BP:
        return
    if size is None:
        size = _doc_bytes(doc)
    if size > MAP_CACHE_MAX_DOC_BYTES:
        return
    evict = []
    now = time.monotonic()
    with _cache_sizes_lock:
        for k in [k for k, (_, exp) in _cache_sizes.items() if exp <= now]:
            del _cache_sizes[k]
        _cache_sizes.pop(key, None)
        _cache_sizes[key] = (size, now + MAP_CACHE_TTL)
        total = sum(v[0] for v in _cache_sizes.values())
        while total > MAP_CACHE_MAX_TOTAL_BYTES and len(_cache_sizes) > 1:
            k, (sz, _) = _cache_sizes.popitem(last=False)
            total -= sz
            evict.append(k)
    for k in evict:
        try:
            cache.backend.delete(k)
        except Exception:
            logger.warning("图谱缓存失效失败 key=%s", k, exc_info=True)
    try:
        cache.backend.set(key, doc, MAP_CACHE_TTL)
    except Exception:
        logger.warning("图谱缓存写入失败 key=%s", key, exc_info=True)


def _http_error(e: MapParseError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=str(e))


async def cached_document(kind: str, key_payload: Any, fn, *args, **kwargs) -> Dict:
    """线程池里算 MapDocument，带缓存；MapParseError → HTTP 错误"""
    return await _cached_document(kind, key_payload, None, fn, args, kwargs)


async def _cached_document(kind: str, key_payload: Any, limiter, fn, args, kwargs) -> Dict:
    """limiter 为 None 时用全局线程池；给定 anyio CapacityLimiter 时在其独立
    的令牌里跑（外呼类任务不占全局 40 个令牌）"""
    key = _cache_key(kind, key_payload)
    hit = _cache_get(key)
    if hit is not None:
        return hit

    def run():
        doc = fn(*args, **kwargs)
        size = _doc_bytes(doc) if doc.get("length", 0) <= MAP_CACHE_MAX_BP else None
        return doc, size

    try:
        if limiter is None:
            doc, size = await run_in_threadpool(run)
        else:
            doc, size = await anyio.to_thread.run_sync(run, limiter=limiter)
    except MapParseError as e:
        raise _http_error(e)
    if size is not None:
        _cache_put(key, doc, size)
    return doc


# ==================== 请求模型 ====================

class MapTextRequest(BaseModel):
    text: str = Field(..., min_length=1)
    name: Optional[str] = Field(default=None, max_length=120)
    topology: Optional[Literal["circular", "linear"]] = None
    annotate: Optional[bool] = None
    enzyme_set: Optional[str] = Field(default=None, max_length=40)
    orf_min_aa: Optional[int] = Field(default=None, ge=ORF_MIN_AA_LO, le=ORF_MIN_AA_HI)


class MapNcbiRequest(BaseModel):
    accession: str = Field(..., min_length=1, max_length=40)
    annotate: Optional[bool] = None
    enzyme_set: str = EnzymeSetId
    orf_min_aa: int = Field(default=100, ge=ORF_MIN_AA_LO, le=ORF_MIN_AA_HI)


class MapAnalyzeRequest(BaseModel):
    sequence: str = Field(..., min_length=1)
    topology: Literal["circular", "linear"]
    name: Optional[str] = Field(default=None, max_length=120)
    features: Optional[List[MapFeature]] = Field(default=None, max_length=MAP_MAX_FEATURES)
    primers: Optional[List[MapPrimer]] = Field(default=None, max_length=MAP_MAX_FEATURES)
    annotate: Optional[bool] = None
    enzyme_set: str = EnzymeSetId
    enzymes: Optional[List[str]] = Field(default=None, max_length=MAP_MAX_ENZYMES)
    orf_min_aa: int = Field(default=100, ge=ORF_MIN_AA_LO, le=ORF_MIN_AA_HI)


def _opt_bool(v) -> Optional[bool]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    raise HTTPException(status_code=422, detail=f"annotate 取值无效：{v}")


def _opt_int(v, name: str, lo: int, hi: int) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{name} 必须是整数")
    if not lo <= n <= hi:
        raise HTTPException(status_code=422, detail=f"{name} 须在 {lo}-{hi} 之间")
    return n


def _too_large() -> HTTPException:
    return HTTPException(
        status_code=413,
        detail=f"文件过大（上限 {MAP_MAX_UPLOAD_MB:g} MB），请截取后再打开")


async def _read_upload(f: UploadFile) -> bytes:
    buf = bytearray()
    while True:
        chunk = await f.read(1024 * 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > MAP_MAX_UPLOAD_BYTES:
            raise _too_large()
    return bytes(buf)


def _parse_and_build(source: Dict, opts: Dict) -> Dict:
    if source["kind"] == "file":
        rec = parse_file(source["filename"], source["data"], max_length=MAP_MAX_LENGTH,
                         topology=source.get("topology"))
    else:
        rec = parse_text(source["text"], name=source.get("name"),
                         topology=source.get("topology"), max_length=MAP_MAX_LENGTH)
    return document_from_record(rec, **opts)


# ==================== 端点 ====================

@router.post("/parse", responses={200: {"model": MapDocument}})
async def parse_map(
    request: Request,
    annotate: Optional[str] = Query(default=None, description="是否自动注释；缺省时文件无特征才注释"),
    enzyme_set: Optional[str] = Query(default=None, max_length=40),
    orf_min_aa: Optional[int] = Query(default=None, ge=ORF_MIN_AA_LO, le=ORF_MIN_AA_HI),
):
    """解析 GenBank / FASTA / SnapGene .dna 文件（multipart `file`），
    或 JSON `{text, name?, topology?}`（原始序列 / FASTA / GenBank 文本）。"""
    ctype = (request.headers.get("content-type") or "").lower()
    clen = request.headers.get("content-length")
    # multipart 包装开销预留 64 KB；超限直接拒绝，不读请求体
    if clen and clen.isdigit() and int(clen) > MAP_MAX_UPLOAD_BYTES + 65536:
        raise _too_large()

    fields: Dict[str, Any] = {}
    if ctype.startswith("multipart/form-data"):
        # async with：请求结束即关闭 SpooledTemporaryFile（否则 ResourceWarning/句柄泄漏）
        async with request.form(max_files=1, max_fields=20) as form:
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                raise HTTPException(status_code=422, detail="缺少上传文件字段 file")
            data = await _read_upload(upload)
            filename = upload.filename or "sequence"
            for k in ("annotate", "enzyme_set", "orf_min_aa", "topology"):
                v = form.get(k)
                if isinstance(v, str):
                    fields[k] = v
        if not data:
            raise HTTPException(status_code=422, detail="上传的文件为空")
        topo = fields.get("topology") or None
        if topo not in (None, "circular", "linear"):
            raise HTTPException(status_code=422, detail="topology 只能是 circular 或 linear")
        source = {"kind": "file", "filename": filename, "data": data, "topology": topo}
        key_src = {"file": hashlib.sha256(data).hexdigest(), "ext": filename.rsplit(".", 1)[-1].lower(),
                   "name": filename, "topology": topo}
    elif ctype.startswith("application/json"):
        body = await request.body()
        if len(body) > MAP_MAX_UPLOAD_BYTES:
            raise _too_large()
        try:
            req = MapTextRequest.model_validate_json(body)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors(include_url=False,
                                                                 include_context=False))
        fields = {k: v for k, v in (("annotate", req.annotate), ("enzyme_set", req.enzyme_set),
                                    ("orf_min_aa", req.orf_min_aa)) if v is not None}
        source = {"kind": "text", "text": req.text, "name": req.name, "topology": req.topology}
        key_src = {"text": hashlib.sha256(req.text.encode("utf-8")).hexdigest(),
                   "name": req.name, "topology": req.topology}
    else:
        raise HTTPException(status_code=415,
                            detail="请以 multipart/form-data 上传文件，或以 JSON 提交 text")

    opts = {
        "annotate": _opt_bool(fields.get("annotate", annotate)),
        "enzyme_set": (fields.get("enzyme_set") or enzyme_set or "common")[:40],
        "orf_min_aa": _opt_int(fields.get("orf_min_aa", orf_min_aa), "orf_min_aa",
                               ORF_MIN_AA_LO, ORF_MIN_AA_HI) or 100,
    }
    return await cached_document("parse", {"src": key_src, "opts": opts},
                                 _parse_and_build, source, opts)


def _analyze(req: MapAnalyzeRequest) -> Dict:
    seq = clean_sequence(req.sequence, MAP_MAX_LENGTH)
    feats = [f.model_dump(exclude_none=True) for f in (req.features or [])]
    prims = [p.model_dump(exclude_none=True) for p in (req.primers or [])]
    return build_document(
        name=req.name or "sequence", sequence=seq, topology=req.topology,
        source_format="raw", features=feats, primers=prims, annotate=req.annotate,
        enzyme_set=req.enzyme_set, enzymes=req.enzymes, orf_min_aa=req.orf_min_aa,
    )


@router.post("/analyze", responses={200: {"model": MapDocument}})
async def analyze_map(req: MapAnalyzeRequest):
    """编辑后的序列 / 特征 → 重算酶切位点、ORF 与（可选）自动注释"""
    # 粗过滤：带空白的粘贴序列在 clean_sequence 里再精确校验长度
    if len(req.sequence) > MAP_MAX_LENGTH * 2:
        raise HTTPException(status_code=413,
                            detail=f"序列过长（上限 {MAP_MAX_LENGTH} bp）")
    key = req.model_dump(mode="json")
    key["sequence"] = hashlib.sha256(req.sequence.encode("utf-8")).hexdigest()
    return await cached_document("analyze", key, _analyze, req)


@router.get("/enzyme-sets")
async def get_enzyme_sets() -> List[Dict]:
    """酶集合：common / unique6 / commercial / golden_gate / type_iis"""
    sets = await run_in_threadpool(enzyme_sets)
    return [dict(s) for s in sets]


_ENZYME_NAME = re.compile(r"^[A-Za-z0-9.\-]{2,20}$")


@router.get("/enzymes/{name}")
async def get_enzyme_detail(name: str) -> Dict:
    """单把酶的详情（REBASE / Bio.Restriction）：识别序列、末端、厂商、
    可商购同切酶/新切酶、Type IIS 与 Golden Gate 适用性、甲基化敏感性"""
    if not _ENZYME_NAME.match(name or ""):
        raise HTTPException(status_code=422, detail="酶名格式无效")
    detail = await run_in_threadpool(enzyme_detail, name)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"未知或不支持的酶：{name}")
    return detail


@router.get("/suppliers")
async def get_suppliers() -> List[Dict]:
    """REBASE 厂商代码、名称及各自可供的酶（按酶数从多到少）"""
    rows = await run_in_threadpool(suppliers)
    return [dict(r) for r in rows]


NCBI_MAX_CONCURRENCY = max(1, int(os.environ.get("NCBI_MAX_CONCURRENCY", "4")))
_ncbi_limiter_obj: Optional[anyio.CapacityLimiter] = None


def _ncbi_limiter() -> anyio.CapacityLimiter:
    """NCBI 导入专用的线程令牌（惰性创建：CapacityLimiter 须在事件循环里构造）"""
    global _ncbi_limiter_obj
    if _ncbi_limiter_obj is None:
        _ncbi_limiter_obj = anyio.CapacityLimiter(NCBI_MAX_CONCURRENCY)
    return _ncbi_limiter_obj


def _ncbi_text(acc: str) -> str:
    """NCBI GenBank 文本：先查缓存，未命中再 efetch（阻塞，线程池里调用）"""
    key = f"map:ncbi-text:{acc}"
    hit = _cache_get(key)
    if isinstance(hit, str):
        return hit
    text = fetch_genbank_text(acc, max_bytes=MAP_MAX_UPLOAD_BYTES, max_length=MAP_MAX_LENGTH)
    if len(text) <= NCBI_TEXT_CACHE_MAX_BYTES:
        try:
            cache.backend.set(key, text, NCBI_TEXT_CACHE_TTL)
        except Exception:
            logger.warning("NCBI 文本缓存写入失败 acc=%s", acc, exc_info=True)
    return text


def _ncbi_build(acc: str, opts: Dict) -> Dict:
    text = _ncbi_text(acc)
    rec = parse_text(text, max_length=MAP_MAX_LENGTH)
    return document_from_record(rec, **opts)


@router.post("/import/ncbi", responses={200: {"model": MapDocument}})
async def import_ncbi(req: MapNcbiRequest):
    """按 NCBI accession（如 L09137、M77789.1）取 GenBank 记录并解析成 MapDocument。

    efetch（rettype=gbwithparts）在线程池执行；GenBank 文本与 MapDocument
    分别缓存；超过体积/长度上限 413，未找到 404，NCBI 异常 502/503/504。"""
    try:
        acc = normalize_accession(req.accession)
    except MapParseError as e:
        raise _http_error(e)
    opts = {"annotate": req.annotate, "enzyme_set": req.enzyme_set, "orf_min_aa": req.orf_min_aa}
    return await _cached_document("ncbi", {"acc": acc, "opts": opts}, _ncbi_limiter(),
                                  _ncbi_build, (acc, opts), {})


_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


@router.post("/export/genbank", response_class=Response,
             responses={200: {"content": {"chemical/x-genbank": {}}}})
async def export_genbank(doc: MapDocument):
    """MapDocument → GenBank 文件下载（含自动注释特征与颜色）"""
    try:
        seq = clean_sequence(doc.sequence, MAP_MAX_LENGTH)
    except MapParseError as e:
        raise _http_error(e)
    data = doc.model_dump(by_alias=True, exclude_none=True)
    data["sequence"] = seq
    linear = data.get("topology") != "circular"
    for f in (data.get("features") or []) + (data.get("primers") or []):
        for s in [f] + list(f.get("segments") or []):
            if s["start"] > len(seq) or s["end"] > len(seq):
                raise HTTPException(status_code=422,
                                    detail=f"「{f.get('name')}」坐标超出序列长度")
            if linear and s["start"] > s["end"]:
                # 线性分子没有原点可跨：join(s..L,1..e) 在线性记录里无意义
                raise HTTPException(status_code=422,
                                    detail=f"「{f.get('name')}」跨原点（start > end），"
                                           "线性分子不能导出跨原点特征")
    text = await run_in_threadpool(document_to_genbank, data)
    fname = (_FILENAME_SAFE.sub("_", doc.name).strip("_") or "sequence")[:80] + ".gb"
    return Response(
        content=text,
        media_type="chemical/x-genbank",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
