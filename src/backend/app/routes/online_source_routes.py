"""在线序列来源导入路由：SnapGene 在线库 / Ensembl / UniProt / Addgene

设计要点（与既有 /api/vectors/import/* 保持一致）：

* 外呼一律在 `run_in_threadpool` 里执行（KNOWN_ISSUES 2.1：阻塞事件循环会让
  导入期间全站无响应）；真正落库的写端点需管理员，且路径落在
  `/api/vectors/import` 前缀下，沿用中间件的 upload 限流档。
* 只读端点（集合/索引/预览/来源清单）进进程缓存，避免前端切页签反复外呼；
  缓存键统一 `online:*` 前缀，落库后按需失效。会外呼的只读代理（SnapGene
  集合/索引/预览、Ensembl lookup/预览、UniProt 检索/条目、Addgene 元数据）都走
  ncbi 档（见 app/rate_limit.py 的 _READ_RULES）；来源清单与 Addgene 本地索引
  搜索不出网，走 default 档。
* 一切失败经 `MapParseError.status` 映射 HTTP 状态（422/403/404/413/502/503/504）。
* 导入落库走 `app/vector_import.save_record`（与文件上传同一条路径，形状与
  data/vectors/*.yaml 一致），出处写进结构化 `data_provenance` **并**在描述里
  保留一句可读标注，满足来源站的引用要求。
* Addgene：匿名只读元数据；只有部署方配置了 `ADDGENE_SESSION_COOKIE`（用户自己的
  会话，仅适合单用户自托管）时才由管理员带凭据取全长序列——仅以 https 发往
  addgene.org（重定向逐跳校验、跨主机即丢弃），不回显、不代登录。
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app import config
from app import online_import as oi
from app.online_import import require_snapgene_enabled, require_snapgene_user
from app.cache import cache
from app.auth.jwt_auth import get_admin_user, get_current_user
from app.vector_library_cache import invalidate_vector_library_cache
from app.routes.models import (
    MAP_MAX_LENGTH,
    SnapGeneImportRequest,
    EnsemblImportRequest,
    OnlinePreviewResponse,
)
from app.vector_import import provenance_block, save_record
from core import online_sources as src
from core.plasmid_map.parsers import MapParseError
from core.seq_utils import gc_percent

router = APIRouter(prefix="/api/vectors", tags=["online-sources"])


def _fail(exc: MapParseError) -> HTTPException:
    """MapParseError/OnlineSourceError → HTTPException（沿用其 .status）"""
    return HTTPException(status_code=getattr(exc, "status", 502), detail=str(exc))


async def _call(fn, *args, **kwargs):
    """阻塞外呼交给线程池，并把来源层错误转成 HTTP 错误"""
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except MapParseError as exc:
        raise _fail(exc) from exc


def _check(fn, *args, **kwargs):
    """同步校验（参数合法性等），失败同样映射成 HTTP 错误"""
    try:
        return fn(*args, **kwargs)
    except MapParseError as exc:
        raise _fail(exc) from exc


def _invalidate_vector_cache(vector_id: Optional[str] = None) -> None:
    """与 vector_routes 相同的失效策略（详情键 + 全部列表键）"""
    try:
        if vector_id:
            cache.backend.delete(f"vector:{vector_id}")
        cache.backend.clear_pattern("vector_list:*")
    except Exception:  # noqa: BLE001
        pass


def _preview(record, *, source: str, url: str, license_text: str) -> OnlinePreviewResponse:
    return OnlinePreviewResponse(
        source=source,
        id=record.name,
        name=record.name,
        url=url,
        length=len(record.sequence),
        topology=record.topology,
        gc_content=round(gc_percent(record.sequence), 2),
        features=[{"name": f.get("name"), "type": f.get("type"),
                   "start": f.get("start"), "end": f.get("end"),
                   "strand": f.get("strand")} for f in record.features[:200]],
        warnings=list(record.warnings),
        license=license_text,
    )


# ==================== 来源清单 ====================

@router.get("/import/sources")
async def list_online_sources(user=Depends(get_current_user)) -> Dict[str, Any]:
    """可用在线来源与许可摘要（前端据此渲染页签与提示；与图谱页同一份数据）"""
    return oi.sources_payload(is_admin=bool(user is not None and user.is_admin))


# ==================== SnapGene 在线库 ====================

# 开关（SNAPGENE_ONLINE_ENABLED，默认开）+ 需登录；落库另需管理员（见 import_snapgene）

@router.get("/import/snapgene/sets")
async def snapgene_sets(_: object = Depends(require_snapgene_user)) -> Dict[str, Any]:
    """列出 SnapGene 在线序列集合（低频：与图谱页共用同一份 1 小时缓存）"""
    return await _call(oi.snapgene_sets_payload)


@router.get("/import/snapgene/search")
async def snapgene_search(q: str = Query(..., min_length=1, max_length=80),
                          limit: int = Query(50, ge=1, le=200),
                          _: object = Depends(require_snapgene_user)) -> Dict[str, Any]:
    """按质粒名跨集合搜索（如 pET-30a）；冷缓存约 20 秒，之后走 1 小时缓存"""
    return await _call(oi.snapgene_search_payload, q, limit)


@router.get("/import/snapgene/sets/{set_id}")
async def snapgene_set_index(set_id: str,
                             _: object = Depends(require_snapgene_user)) -> Dict[str, Any]:
    """列出集合内全部序列（索引，等价客户端 snapgeneSequences/<set>.xml）"""
    return await _call(oi.snapgene_index_payload, set_id)


async def _snapgene_fetch(set_id: str, plasmid_id: str):
    """下载并解析单个 .dna（阻塞调用交线程池），返回 (record, url)"""
    record, url, _name = await _call(oi.snapgene_plasmid_record, set_id, plasmid_id,
                                     MAP_MAX_LENGTH)
    return record, url


@router.get("/import/snapgene/preview", response_model=OnlinePreviewResponse)
async def snapgene_preview(set_id: str = Query(..., max_length=64),
                           plasmid_id: str = Query(..., max_length=120),
                           _: object = Depends(require_snapgene_user)):
    """下载并解析单个 .dna 作预览（不落库）"""
    key = oi.preview_cache_key("snapgene", set_id, plasmid_id)
    cached = oi.cache_get(key)
    if cached is not None:
        return cached
    record, url = await _snapgene_fetch(set_id, plasmid_id)
    resp = _preview(record, source="snapgene", url=url, license_text=src.SNAPGENE_LICENSE)
    oi.cache_set(key, resp.model_dump(mode="json"))
    return resp


@router.post("/import/snapgene")
async def import_snapgene(req: SnapGeneImportRequest,
                          _enabled: None = Depends(require_snapgene_enabled),
                          _: object = Depends(get_admin_user)) -> Dict[str, Any]:
    """从 SnapGene 在线库导入一个序列到载体库（需管理员）"""
    record, url = await _snapgene_fetch(req.set_id, req.plasmid_id)
    result = await run_in_threadpool(
        save_record, record,
        source="snapgene",
        vectors_dir=config.settings.VECTORS_DIR,
        url=url,
        vector_id=f"snapgene_{_check(src.validate_plasmid_id, req.plasmid_id)}",
        attribution=("来源：SnapGene 在线质粒库（引用 www.snapgene.com/resources）；"
                     "学术/非营利/政府可无条件使用，商业用途须获 GraphPad Software, LLC 许可"),
        provenance=provenance_block(
            "SnapGene 在线质粒库", url=url, license=src.SNAPGENE_LICENSE,
            set=req.set_id, plasmid=req.plasmid_id,
            citation="www.snapgene.com/resources"),
    )
    invalidate_vector_library_cache()
    _invalidate_vector_cache(result["id"])
    return {"success": True, "vector": result, "license": src.SNAPGENE_LICENSE}


# ==================== Ensembl ====================

@router.get("/import/ensembl/lookup/{identifier}")
async def ensembl_lookup(identifier: str, expand: bool = False) -> Dict[str, Any]:
    """Ensembl 元数据（名称/类型/物种/位置）"""
    key = oi.preview_cache_key("ensembl-lookup", identifier, str(int(bool(expand))))
    cached = oi.cache_get(key)
    if cached is not None:
        return cached
    data = await _call(src.ensembl_lookup, identifier, expand=expand)
    payload = {"identifier": _check(src.validate_ensembl_id, identifier), "entry": data,
               "license": src.ENSEMBL_LICENSE}
    oi.cache_set(key, payload)
    return payload


@router.get("/import/ensembl/preview", response_model=OnlinePreviewResponse)
async def ensembl_preview(identifier: str = Query(..., max_length=64),
                          seq_type: str = Query(default="cds")):
    """取 Ensembl 序列并解析作预览（不落库）"""
    key = oi.preview_cache_key("ensembl", identifier, seq_type)
    cached = oi.cache_get(key)
    if cached is not None:
        return cached
    record = await _call(src.ensembl_record, identifier, seq_type, max_length=MAP_MAX_LENGTH)
    url = f"{src.public_base(src.ENSEMBL_BASE)}/sequence/id/{_check(src.validate_ensembl_id, identifier)}?type={seq_type}"
    resp = _preview(record, source=f"ensembl:{seq_type}", url=url,
                    license_text=src.ENSEMBL_LICENSE)
    oi.cache_set(key, resp.model_dump(mode="json"))
    return resp


@router.post("/import/ensembl")
async def import_ensembl(req: EnsemblImportRequest,
                         _: object = Depends(get_admin_user)) -> Dict[str, Any]:
    """从 Ensembl 导入 DNA 序列到载体库（需管理员）"""
    record = await _call(src.ensembl_record, req.identifier, req.seq_type,
                         name=req.name, max_length=MAP_MAX_LENGTH)
    url = (f"{src.public_base(src.ENSEMBL_BASE)}/sequence/id/{src.validate_ensembl_id(req.identifier)}"
           f"?type={req.seq_type}")
    result = await run_in_threadpool(
        save_record, record,
        source=f"ensembl_{req.seq_type}",
        vectors_dir=config.settings.VECTORS_DIR,
        url=url,
        vector_id=f"ensembl_{_check(src.validate_ensembl_id, req.identifier)}_{req.seq_type}",
        attribution="来源：Ensembl REST（数据无使用限制，建议注明来源）",
        provenance=provenance_block(
            "Ensembl REST", url=url, license=src.ENSEMBL_LICENSE,
            identifier=req.identifier, seq_type=req.seq_type),
    )
    invalidate_vector_library_cache()
    _invalidate_vector_cache(result["id"])
    return {"success": True, "vector": result, "license": src.ENSEMBL_LICENSE}


# ==================== Addgene（只读，不绕登录） ====================

@router.get("/import/addgene/search")
async def addgene_search(term: str = Query(..., min_length=1, max_length=60),
                         limit: int = Query(default=20, ge=1, le=100)) -> Dict[str, Any]:
    """在 SnapGene 客户端自带的 Addgene 索引里离线搜索（本机未装客户端时返回空）

    索引文件：%APPDATA%\\SnapGene\\addgenePlasmids\\addgeneProcessed.json（约 17 万条）。
    """
    rows = await run_in_threadpool(src.addgene_local_index, term, limit)
    return {
        "term": term, "count": len(rows), "results": rows,
        "license": src.ADDGENE_LICENSE,
        "note": ("本地索引来自 SnapGene 客户端；未找到时可用质粒号直接查匿名元数据"
                 "（/api/vectors/import/addgene/{质粒号}）"),
    }


@router.get("/import/addgene/{plasmid_id}")
async def addgene_metadata(plasmid_id: str,
                           try_sequence: bool = Query(default=False),
                           user=Depends(get_current_user)) -> Dict[str, Any]:
    """Addgene 匿名元数据（各文件 URL）；try_sequence=true 时尝试取全长序列

    实测（2026-08 起）：匿名只能拿到 map 图，`.gbk`/`.dna` 直链 404 → 本端点一律
    返回 403 + 合法路径指引，**不代为登录、不绕登录墙**（Addgene ToS 明文禁止）。

    `try_sequence` 分支**仅管理员**（审查 M1）：配置了 `ADDGENE_SESSION_COOKIE` 时它会以
    部署方的 Addgene 账号身份下载，不能让匿名/普通用户驱动；`session_configured`
    同样只对管理员下发。
    """
    is_admin = bool(user is not None and user.is_admin)
    if try_sequence and not is_admin:
        if user is None:
            raise HTTPException(status_code=401, detail="取 Addgene 全长序列需管理员登录",
                                headers={"WWW-Authenticate": "Bearer"})
        raise HTTPException(status_code=403, detail="取 Addgene 全长序列需要管理员权限")
    oid = _check(src.validate_addgene_id, plasmid_id)
    meta = await _call(src.addgene_metadata, oid)
    out: Dict[str, Any] = {
        "plasmid_id": oid,
        "metadata": meta,
        "url": f"https://www.addgene.org/{oid}/",
        "sequence_available": False,
        "license": src.ADDGENE_LICENSE,
        "note": ("Addgene 全长序列需登录：请用自己账号在浏览器下载后走"
                 "「上传文件」导入，或改用 NCBI 上的对应 GenBank 记录；"
                 "若已自行登录，可把浏览器会话 Cookie 配到 ADDGENE_SESSION_COOKIE 后直接导入"),
    }
    if try_sequence:
        # 无凭据时预期失败（403 + 指引）；配了用户自有会话则照实返回
        data = await _call(src.addgene_sequence_bytes, oid)
        out["sequence_available"] = True
        out["sequence_bytes"] = len(data)
        out["note"] = ("已用你配置的 Addgene 会话取到序列文件；"
                       "可点「导入为本地载体」落库（序列与注释版权归 Addgene/原作者）"
                       if src.addgene_has_session() else
                       "上游允许匿名取序列（接口有变），请改用返回的直链自行核对后导入")
    out["session_configured"] = src.addgene_has_session() if is_admin else False
    return out


@router.post("/import/addgene/{plasmid_id}")
async def import_addgene(plasmid_id: str,
                         fmt: str = Query(default="genbank", pattern="^(genbank|dna)$"),
                         name: Optional[str] = Query(default=None, max_length=80),
                         _: object = Depends(get_admin_user)) -> Dict[str, Any]:
    """用配置好的 Addgene 会话把全长序列导入载体库（需管理员；无凭据 → 403）

    设计边界：**不代为登录、不绕登录墙、不保存凭据**。凭据来自部署方的
    `ADDGENE_SESSION_COOKIE`（用户自己在浏览器登录后的会话），仅以 https 发往 addgene.org。
    """
    oid = _check(src.validate_addgene_id, plasmid_id)
    record = await _call(src.addgene_record, oid, fmt=fmt, name=name,
                         max_length=MAP_MAX_LENGTH)
    url = f"https://www.addgene.org/{oid}/"
    result = await run_in_threadpool(
        save_record, record,
        source="addgene",
        vectors_dir=config.settings.VECTORS_DIR,
        url=url,
        vector_id=f"addgene_{oid}",
        attribution=("来源：Addgene（非商业研究可按其条款使用，须保留版权声明）；"
                     "序列由用户自备登录态获取"),
        provenance=provenance_block(
            "Addgene", url=url, license=src.ADDGENE_LICENSE,
            plasmid=oid, format=fmt, credential="用户自备会话"),
    )
    invalidate_vector_library_cache()
    _invalidate_vector_cache(result["id"])
    return {"success": True, "vector": result, "license": src.ADDGENE_LICENSE}


# ==================== UniProt（蛋白库：只做反查，不给质粒序列） ====================

@router.get("/import/uniprot/search")
async def uniprot_search(query: str = Query(..., min_length=1, max_length=120),
                         limit: int = Query(default=10, ge=1, le=25),
                         reviewed_only: bool = Query(default=False)) -> Dict[str, Any]:
    """按基因/蛋白名/登录号检索 UniProt（精简字段；核酸交叉引用见条目端点）"""
    rows = await _call(src.uniprot_search, query, limit=limit, reviewed_only=reviewed_only)
    return {
        "query": query, "count": len(rows),
        "results": rows,
        "license": src.UNIPROT_LICENSE,
        "note": ("UniProt 是蛋白库、不含质粒序列；检索只返回精简字段，"
                 "点条目（GET /api/vectors/import/uniprot/{登录号}）看 Entrez/EMBL/RefSeq 交叉引用，"
                 "再据此去核酸来源取 DNA"),
    }


@router.get("/import/uniprot/{accession}")
async def uniprot_entry(accession: str, with_fasta: bool = Query(default=False)) -> Dict[str, Any]:
    """UniProt 条目详情 + 核酸交叉引用（with_fasta=1 时附蛋白 FASTA 供人工核对）"""
    entry = await _call(src.uniprot_entry, accession)
    out: Dict[str, Any] = {
        "accession": entry.get("accession") or accession,
        "entry": entry,
        "nucleotide": src.nucleotide_candidates(entry),
        "url": f"https://www.uniprot.org/uniprotkb/{entry.get('accession') or accession}/entry",
        "license": src.UNIPROT_LICENSE,
        "note": "UniProt 不给核酸序列：到核酸来源（NCBI/Ensembl）取 DNA 后再导入",
    }
    if with_fasta:
        out["fasta"] = await _call(src.uniprot_fasta, accession)
    return out
