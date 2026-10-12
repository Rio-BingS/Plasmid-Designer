"""图谱页的在线序列来源导入（SnapGene 在线库 / Ensembl）

与既有 `POST /api/map/import/ncbi` 同一套路：

* 外呼一律 `run_in_threadpool`（KNOWN_ISSUES 2.1）；MapDocument 按「输入摘要 +
  选项 + 元件库/算法版本」缓存（复用 map_routes.cached_document）；
* 集合/索引与载体库侧（`/api/vectors/import/snapgene/*`）**共用同一批缓存键**
  （app/online_import）：用户在载体库看过一次，图谱页不再重复外呼；
* 失败经 `MapParseError.status` 映射 HTTP（422/403/404/413/502/503/504）。

许可边界与载体库侧同源：SnapGene 质粒库学术/非营利/政府可无条件使用（须引用
`www.snapgene.com/resources`），**商业用途须先获 GraphPad Software, LLC 许可**，
其 robots.txt 明确 `Disallow: /local` → 只做低频单次、人工触发的读取；
`ONLINE_IMPORT_ENABLED=false` 时全部端点返回 503（零外呼）。
SnapGene 端点另受 `SNAPGENE_ONLINE_ENABLED`（默认 true；false → 404）约束，开启后也只对
已登录用户开放（匿名 401），见 `app/online_import.require_snapgene_user`。
"""

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import online_import as oi
from app.auth.jwt_auth import get_current_user
from app.online_import import require_snapgene_user
from app.routes.map_routes import cached_document
from app.routes.models import MAP_MAX_LENGTH, MapDocument
from core import online_sources as src
from core.plasmid_map import document_from_record
from core.plasmid_map.parsers import MapParseError

router = APIRouter(prefix="/api/map", tags=["map"])

ORF_MIN_AA_LO, ORF_MIN_AA_HI = 10, 5000
EnzymeSetId = Field(default="common", max_length=40)


class MapSnapgeneRequest(BaseModel):
    """SnapGene 在线库：集合 + 序列 ID"""
    set_id: str = Field(..., min_length=1, max_length=64,
                        description="集合 ID，如 coronavirus_resources")
    plasmid_id: str = Field(..., min_length=1, max_length=120,
                            description="序列 ID（索引里的 url 字段），如 SARS_Genome")
    annotate: Optional[bool] = None
    enzyme_set: str = EnzymeSetId
    orf_min_aa: int = Field(default=100, ge=ORF_MIN_AA_LO, le=ORF_MIN_AA_HI)


class MapEnsemblRequest(BaseModel):
    """Ensembl：ID + 序列类型（只收 DNA，蛋白序列不作为图谱序列）"""
    identifier: str = Field(..., min_length=1, max_length=64)
    seq_type: Literal["genomic", "cds", "cdna"] = "cds"
    annotate: Optional[bool] = None
    enzyme_set: str = EnzymeSetId
    orf_min_aa: int = Field(default=100, ge=ORF_MIN_AA_LO, le=ORF_MIN_AA_HI)


def _http_error(exc: MapParseError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def _opts(req) -> Dict[str, Any]:
    return {"annotate": req.annotate, "enzyme_set": req.enzyme_set,
            "orf_min_aa": req.orf_min_aa}


async def _online_call(fn, *args, **kwargs):
    """阻塞外呼交线程池；来源层错误 → HTTP"""
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except MapParseError as exc:
        raise _http_error(exc) from exc


@router.get("/import/sources")
async def map_online_sources(user=Depends(get_current_user)) -> Dict[str, Any]:
    """在线来源清单与许可摘要（与载体库侧同一份数据）"""
    return oi.sources_payload(is_admin=bool(user is not None and user.is_admin))


@router.get("/import/snapgene/sets")
async def map_snapgene_sets(_: object = Depends(require_snapgene_user)) -> Dict[str, Any]:
    """SnapGene 在线库的集合列表（与载体库侧共用 1 小时缓存；开关 + 需登录）"""
    return await _online_call(oi.snapgene_sets_payload)


@router.get("/import/snapgene/sets/{set_id}")
async def map_snapgene_index(set_id: str,
                             _: object = Depends(require_snapgene_user)) -> Dict[str, Any]:
    """集合索引：id / 显示名 / 文件名（与载体库侧共用缓存）"""
    return await _online_call(oi.snapgene_index_payload, set_id)


def _snapgene_build(set_id: str, plasmid_id: str, opts: Dict[str, Any]) -> Dict[str, Any]:
    """下载 + 解析 + 组装（在缓存未命中时于线程池里跑）"""
    record, _url, _name = oi.snapgene_plasmid_record(set_id, plasmid_id, MAP_MAX_LENGTH)
    return document_from_record(record, **opts)


def _ensembl_build(identifier: str, seq_type: str, opts: Dict[str, Any]) -> Dict[str, Any]:
    record = src.ensembl_record(identifier, seq_type, max_length=MAP_MAX_LENGTH)
    return document_from_record(record, **opts)


@router.post("/import/snapgene", responses={200: {"model": MapDocument}})
async def import_map_snapgene(req: MapSnapgeneRequest,
                              _: object = Depends(require_snapgene_user)):
    """从 SnapGene 在线库下载 .dna（含完整图谱注释）并解析成 MapDocument。

    下载 + 解析都在线程池执行，且**只在 MapDocument 缓存未命中时**发生
    （键含输入摘要 + 选项 + 元件库/算法版本）：同参数重复打开不会重复下载。
    序列超 MAP_MAX_LENGTH 413，未找到 404，上游异常 502/503/504。
    """
    opts = _opts(req)
    key_payload = {"src": "snapgene", "set": req.set_id, "pid": req.plasmid_id, "opts": opts}
    return await cached_document("online", key_payload, _snapgene_build,
                                 req.set_id, req.plasmid_id, opts)


@router.post("/import/ensembl", responses={200: {"model": MapDocument}})
async def import_map_ensembl(req: MapEnsemblRequest):
    """按 Ensembl ID 取 DNA 序列（genomic/cds/cdna）并解析成 MapDocument。

    导入的是线性片段（非质粒）：`seq_type=genomic` 返回整个基因区，过大时 413。
    """
    opts = _opts(req)
    key_payload = {"src": "ensembl", "id": req.identifier, "type": req.seq_type, "opts": opts}
    return await cached_document("online", key_payload, _ensembl_build,
                                 req.identifier, req.seq_type, opts)
