"""在线序列来源导入路由：SnapGene 在线库 / Ensembl / Addgene

设计要点（与既有 /api/vectors/import/* 保持一致）：

* 外呼一律在 `run_in_threadpool` 里执行（KNOWN_ISSUES 2.1：阻塞事件循环会让
  导入期间全站无响应）；真正落库的写端点需管理员，且路径落在
  `/api/vectors/import` 前缀下，沿用中间件的 upload 限流档。
* 只读端点（集合/索引/预览/来源清单）进进程缓存，避免前端切页签反复外呼；
  缓存键统一 `online:*` 前缀，落库后按需失效。
* 一切失败经 `MapParseError.status` 映射 HTTP 状态（422/403/404/413/502/503/504）。
* 导入落库走 `app/vector_import.save_record`（与文件上传同一条路径，形状与
  data/vectors/*.yaml 一致），并在描述里写入出处，满足来源站的引用要求。
"""

import hashlib
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app import config
from app.cache import cache
from app.auth.jwt_auth import get_admin_user
from app.design_service import invalidate_vector_library_cache
from app.routes.models import (
    SnapGeneImportRequest,
    EnsemblImportRequest,
    OnlinePreviewResponse,
)
from app.vector_import import save_record
from core import online_sources as src
from core.plasmid_map.parsers import MapParseError
from core.seq_utils import gc_percent

router = APIRouter(prefix="/api/vectors", tags=["online-sources"])

# 只读端点缓存 TTL：集合列表/索引变化很慢，预览按内容哈希缓存
CACHE_TTL = 3600


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


def _cache_get(key: str):
    try:
        return cache.backend.get(key)
    except Exception:  # noqa: BLE001 - 缓存不可用不影响主流程
        return None


def _cache_set(key: str, value) -> None:
    try:
        cache.backend.set(key, value, CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass


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
async def list_online_sources() -> Dict[str, Any]:
    """可用在线来源与许可摘要（前端据此渲染页签与提示）"""
    enabled = src.import_enabled()
    return {
        "enabled": enabled,
        "disclaimer": src.DISCLAIMER,
        "sources": [
            {
                "id": "snapgene",
                "name": "SnapGene 在线质粒库",
                "kind": "online_library",
                "url": src.SNAPGENE_BASE,
                "license": src.SNAPGENE_LICENSE,
                "importable": True,
                "note": "19 个集合（基础克隆载体/CRISPR/荧光蛋白/酵母质粒…），含完整图谱与注释",
            },
            {
                "id": "ensembl",
                "name": "Ensembl REST",
                "kind": "online_sequence",
                "url": src.ENSEMBL_BASE,
                "license": src.ENSEMBL_LICENSE,
                "importable": True,
                "note": "按 Ensembl ID 取 genomic / cds / cdna 序列（蛋白序列不作为质粒序列导入）",
            },
            {
                "id": "addgene",
                "name": "Addgene",
                "kind": "online_library",
                "url": "https://www.addgene.org",
                "license": src.ADDGENE_LICENSE,
                "importable": False,
                "note": "只提供本地索引搜索与匿名元数据；全长序列需登录，本工具不代为登录",
            },
            {
                "id": "ncbi",
                "name": "NCBI GenBank（已有通道）",
                "kind": "online_sequence",
                "url": "https://www.ncbi.nlm.nih.gov/nuccore/",
                "license": "NCBI 公共数据；个别提交者可能主张权利",
                "importable": True,
                "note": "见 /api/vectors/import/ncbi、/api/vectors/import/ncbi-id；图谱页亦可用 accession 打开",
            },
        ],
    }


# ==================== SnapGene 在线库 ====================

@router.get("/import/snapgene/sets")
async def snapgene_sets() -> Dict[str, Any]:
    """列出 SnapGene 在线序列集合（低频：结果缓存 1 小时）"""
    key = "online:snapgene:sets"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    sets = await _call(src.snapgene_sets)
    payload = {"count": len(sets), "sets": sets, "license": src.SNAPGENE_LICENSE}
    _cache_set(key, payload)
    return payload


@router.get("/import/snapgene/sets/{set_id}")
async def snapgene_set_index(set_id: str) -> Dict[str, Any]:
    """列出集合内全部序列（索引，等价客户端 snapgeneSequences/<set>.xml）"""
    key = f"online:snapgene:index:{set_id}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    rows = await _call(src.snapgene_set_index, set_id)
    payload = {"set_id": _check(src.validate_set_id, set_id), "count": len(rows), "plasmids": rows}
    _cache_set(key, payload)
    return payload


def _snapgene_fetch(set_id: str, plasmid_id: str):
    """下载 .dna 并解析（阻塞），返回 (record, url, 文件名)"""
    data = src.snapgene_plasmid_bytes(set_id, plasmid_id)
    sid = src.validate_set_id(set_id)
    pid = src.validate_plasmid_id(plasmid_id)
    url = (f"{src.SNAPGENE_BASE}/fetch.php?set={sid}&plasmid={pid}")
    # 索引里的 filename 更友好（如 "SARS Genome.dna"），拿不到就用 plasmid_id
    name = pid
    try:
        name = next((p["filename"] for p in src.snapgene_set_index(sid)
                     if p["plasmid_id"] == pid), "") or pid
        name = name[:-4] if name.lower().endswith(".dna") else name
    except MapParseError:
        pass
    return src.snapgene_record(data, name=name), url


@router.get("/import/snapgene/preview", response_model=OnlinePreviewResponse)
async def snapgene_preview(set_id: str = Query(..., max_length=64),
                           plasmid_id: str = Query(..., max_length=120)):
    """下载并解析单个 .dna 作预览（不落库）"""
    key = "online:snapgene:preview:" + hashlib.sha256(
        f"{set_id}|{plasmid_id}".encode("utf-8")).hexdigest()[:32]
    cached = _cache_get(key)
    if cached is not None:
        return cached
    record, url = await _call(_snapgene_fetch, set_id, plasmid_id)
    resp = _preview(record, source="snapgene", url=url, license_text=src.SNAPGENE_LICENSE)
    _cache_set(key, resp.model_dump(mode="json"))
    return resp


@router.post("/import/snapgene")
async def import_snapgene(req: SnapGeneImportRequest,
                          _: object = Depends(get_admin_user)) -> Dict[str, Any]:
    """从 SnapGene 在线库导入一个序列到载体库（需管理员）"""
    record, url = await _call(_snapgene_fetch, req.set_id, req.plasmid_id)
    result = await run_in_threadpool(
        save_record, record,
        source="snapgene",
        vectors_dir=config.settings.VECTORS_DIR,
        url=url,
        vector_id=f"snapgene_{_check(src.validate_plasmid_id, req.plasmid_id)}",
        attribution=("来源：SnapGene 在线质粒库（引用 www.snapgene.com/resources）；"
                     "学术/非营利/政府可无条件使用，商业用途须获 GraphPad Software, LLC 许可"),
    )
    invalidate_vector_library_cache()
    _invalidate_vector_cache(result["id"])
    return {"success": True, "vector": result, "license": src.SNAPGENE_LICENSE}


# ==================== Ensembl ====================

@router.get("/import/ensembl/lookup/{identifier}")
async def ensembl_lookup(identifier: str, expand: bool = False) -> Dict[str, Any]:
    """Ensembl 元数据（名称/类型/物种/位置）"""
    key = f"online:ensembl:lookup:{identifier}:{int(bool(expand))}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    data = await _call(src.ensembl_lookup, identifier, expand=expand)
    payload = {"identifier": _check(src.validate_ensembl_id, identifier), "entry": data,
               "license": src.ENSEMBL_LICENSE}
    _cache_set(key, payload)
    return payload


@router.get("/import/ensembl/preview", response_model=OnlinePreviewResponse)
async def ensembl_preview(identifier: str = Query(..., max_length=64),
                          seq_type: str = Query(default="cds")):
    """取 Ensembl 序列并解析作预览（不落库）"""
    key = f"online:ensembl:preview:{identifier}:{seq_type}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    record = await _call(src.ensembl_record, identifier, seq_type)
    url = f"{src.ENSEMBL_BASE}/sequence/id/{_check(src.validate_ensembl_id, identifier)}?type={seq_type}"
    resp = _preview(record, source=f"ensembl:{seq_type}", url=url,
                    license_text=src.ENSEMBL_LICENSE)
    _cache_set(key, resp.model_dump(mode="json"))
    return resp


@router.post("/import/ensembl")
async def import_ensembl(req: EnsemblImportRequest,
                         _: object = Depends(get_admin_user)) -> Dict[str, Any]:
    """从 Ensembl 导入 DNA 序列到载体库（需管理员）"""
    record = await _call(src.ensembl_record, req.identifier, req.seq_type, name=req.name)
    url = (f"{src.ENSEMBL_BASE}/sequence/id/{src.validate_ensembl_id(req.identifier)}"
           f"?type={req.seq_type}")
    result = await run_in_threadpool(
        save_record, record,
        source=f"ensembl_{req.seq_type}",
        vectors_dir=config.settings.VECTORS_DIR,
        url=url,
        vector_id=f"ensembl_{_check(src.validate_ensembl_id, req.identifier)}_{req.seq_type}",
        attribution="来源：Ensembl REST（数据无使用限制，建议注明来源）",
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
                           try_sequence: bool = Query(default=False)) -> Dict[str, Any]:
    """Addgene 匿名元数据（各文件 URL）；try_sequence=true 时尝试取全长序列

    实测（2026-08 起）：匿名只能拿到 map 图，`.gbk`/`.dna` 直链 404 → 本端点一律
    返回 403 + 合法路径指引，**不代为登录、不绕登录墙**（Addgene ToS 明文禁止）。
    """
    oid = _check(src.validate_addgene_id, plasmid_id)
    meta = await _call(src.addgene_metadata, oid)
    out: Dict[str, Any] = {
        "plasmid_id": oid,
        "metadata": meta,
        "url": f"https://www.addgene.org/{oid}/",
        "sequence_available": False,
        "license": src.ADDGENE_LICENSE,
        "note": ("Addgene 全长序列需登录：请用自己账号在浏览器下载后走"
                 "「上传文件」导入，或改用 NCBI 上的对应 GenBank 记录"),
    }
    if try_sequence:
        # 预期失败（403 + 指引）；成功则说明上游放开了匿名，也照实返回
        data = await _call(src.addgene_sequence_bytes, oid)
        out["sequence_available"] = True
        out["sequence_bytes"] = len(data)
        out["note"] = "上游允许匿名取序列（接口有变），请改用返回的直链自行核对后导入"
    return out
