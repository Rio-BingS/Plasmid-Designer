"""设计页一次性载体：本地上传 / 在线导入（**只取不存**）

载体库是共享库，写入口只对管理员开放（/api/vectors/import/*）；普通用户需要的
只是「用自己手里这份载体做一次设计」，不该为此改动共享库。所以这里的端点都
不落盘、不写 YAML：解析/下载后返回一次性载体载荷（DesignVectorPayload），
随 POST /api/design 的 vector 字段内联提交，由 app/adhoc_vector.vector_from_inline
现场构造 Vector 参与设计。

与既有在线导入的分工：

* 取字节/解析一律复用 core.online_sources 与 app.online_import（同一套限速、
  重定向校验、许可边界），不新写抓取逻辑
* Addgene 全长序列用部署方配置的会话（ADDGENE_SESSION_COOKIE），沿用
  /api/vectors/import/addgene 的口径：**只有管理员能触发**
* SnapGene 在线库与既有端点一致：开关打开 + 已登录
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from starlette.concurrency import run_in_threadpool

from app import online_import as oi
from app.adhoc_vector import payload_from_record
from app.auth.jwt_auth import get_current_user
from app.online_import import require_snapgene_user
from app.routes.models import (
    MAP_MAX_LENGTH, MAP_MAX_UPLOAD_BYTES, MAP_MAX_UPLOAD_MB,
    DesignVectorOnlineRequest, DesignVectorPayload,
)
from core import online_sources as src
from core.plasmid_map import MapParseError, parse_file, parse_text
from core.plasmid_map.ncbi import fetch_genbank_text, normalize_accession

logger = logging.getLogger("plasmid_designer.design_vector_routes")

router = APIRouter(prefix="/api/design/vector", tags=["design-vector"])


def _fail(exc: MapParseError) -> HTTPException:
    """来源/解析错误 → HTTPException（沿用 MapParseError.status）"""
    return HTTPException(status_code=getattr(exc, "status", 422), detail=str(exc))


async def _call(fn, *args, **kwargs):
    """阻塞调用（外呼/解析）交线程池，并把 MapParseError 映射成 HTTP 错误"""
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except MapParseError as exc:
        raise _fail(exc) from exc


def _check(fn, *args, **kwargs):
    """同步参数校验（失败同样是 MapParseError）"""
    try:
        return fn(*args, **kwargs)
    except MapParseError as exc:
        raise _fail(exc) from exc


async def _read_upload(file: UploadFile) -> bytes:
    """边读边计数：超过上限立刻 413，不把整份文件留在内存里"""
    buf = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > MAP_MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"文件过大（上限 {MAP_MAX_UPLOAD_MB:g} MB），请截取后再上传")
    return bytes(buf)


# ==================== 来源清单 ====================

@router.get("/sources")
async def design_vector_sources(user=Depends(get_current_user)) -> Dict[str, Any]:
    """在线来源清单与许可摘要（不出网；与载体库/图谱页同一份数据）"""
    return oi.sources_payload(is_admin=bool(user is not None and user.is_admin))


@router.get("/snapgene/search")
async def design_vector_snapgene_search(
    q: str = Query(..., min_length=1, max_length=80),
    limit: int = Query(default=20, ge=1, le=50),
    _: object = Depends(require_snapgene_user),
) -> Dict[str, Any]:
    """按质粒名跨集合搜索 SnapGene 在线库（只给候选，选中再走 /online 取全长）"""
    return await _call(oi.snapgene_search_payload, q, limit)


# ==================== 本地上传 ====================

@router.post("/upload", response_model=DesignVectorPayload)
async def upload_design_vector(file: UploadFile = File(...)) -> Dict[str, Any]:
    """解析上传的载体文件（GenBank / FASTA / SnapGene .dna）为一次性载体

    与载体库上传的区别只有一个：**不写 data/vectors**。因此也不需要管理员权限，
    且不会因为文件名撞车覆盖别人的载体。
    """
    data = await _read_upload(file)
    if not data:
        raise HTTPException(status_code=422, detail="上传的文件为空")
    name = (file.filename or "vector").replace("\\", "/").rsplit("/", 1)[-1]
    # 显示名 = 文件名去扩展名（与批量载体池同一口径）：文件内部 LOCUS 名
    # 常与文件名无关（如导出文件保留原质粒名），用户按文件名认自己的文件
    display_name = name.rsplit(".", 1)[0] if "." in name else name
    try:
        record = await _call(parse_file, name, data, MAP_MAX_LENGTH)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 解析器意外异常按坏文件处理，不回显内部信息
        logger.warning("设计页载体解析异常：%s", type(exc).__name__, exc_info=True)
        raise HTTPException(status_code=400, detail="文件解析失败，请确认格式正确") from exc

    source = "snapgene" if record.source_format == "snapgene" else "genbank"
    return await _call(payload_from_record, record, source=source,
                       max_length=MAP_MAX_LENGTH, display_name=display_name)


# ==================== 在线导入 ====================

@router.post("/online", response_model=DesignVectorPayload)
async def import_design_vector_online(req: DesignVectorOnlineRequest,
                                      user=Depends(get_current_user)) -> Dict[str, Any]:
    """按来源取一条序列作为一次性载体（SnapGene / Ensembl / NCBI / Addgene）"""
    source = req.source
    license_text = ""

    if source == "snapgene":
        # 与 /api/vectors/import/snapgene/preview 同一口径：开关 + 登录
        if not src.snapgene_online_enabled():
            raise HTTPException(status_code=404, detail=src.SNAPGENE_DISABLED_MSG)
        if user is None:
            raise HTTPException(status_code=401,
                                detail="SnapGene 在线库仅对已登录用户开放，请先登录",
                                headers={"WWW-Authenticate": "Bearer"})
        if not (req.set_id and req.plasmid_id):
            raise HTTPException(status_code=422, detail="SnapGene 导入需要集合与质粒 ID（可先搜索）")
        record, url, _name = await _call(oi.snapgene_plasmid_record, req.set_id,
                                         req.plasmid_id, MAP_MAX_LENGTH)
        license_text = src.SNAPGENE_LICENSE

    elif source == "ensembl":
        if not req.identifier:
            raise HTTPException(status_code=422, detail="Ensembl 导入需要序列 ID（如 ENSG00000139618）")
        identifier = _check(src.validate_ensembl_id, req.identifier)
        record = await _call(src.ensembl_record, identifier, req.seq_type,
                             max_length=MAP_MAX_LENGTH)
        url = (f"{src.public_base(src.ENSEMBL_BASE)}/sequence/id/{identifier}"
               f"?type={req.seq_type}")
        license_text = src.ENSEMBL_LICENSE
        source = f"ensembl_{req.seq_type}"

    elif source == "ncbi":
        if not req.accession:
            raise HTTPException(status_code=422, detail="NCBI 导入需要 accession（如 L09137）")
        acc = _check(normalize_accession, req.accession)

        def _fetch() -> str:
            return fetch_genbank_text(acc, max_bytes=MAP_MAX_UPLOAD_BYTES,
                                      max_length=MAP_MAX_LENGTH)

        text = await _call(_fetch)
        record = await _call(parse_text, text, None, None, MAP_MAX_LENGTH)
        url = f"https://www.ncbi.nlm.nih.gov/nuccore/{acc}"
        license_text = "NCBI 公共数据；个别提交者可能主张权利"
        source = "ncbi"

    else:  # addgene
        # 全长序列靠部署方配置的 Addgene 会话获取，等同「以该账号身份下载」：
        # 与 /api/vectors/import/addgene 一致，只允许管理员触发（审查 M1）
        if user is None:
            raise HTTPException(status_code=401, detail="Addgene 导入需管理员登录",
                                headers={"WWW-Authenticate": "Bearer"})
        if not getattr(user, "is_admin", False):
            raise HTTPException(status_code=403,
                                detail="Addgene 全长序列需管理员权限（会话由部署方配置）")
        if not req.addgene_id:
            raise HTTPException(status_code=422, detail="Addgene 导入需要质粒编号（如 12259）")
        oid = _check(src.validate_addgene_id, req.addgene_id)
        record = await _call(src.addgene_record, oid, fmt=req.fmt,
                             max_length=MAP_MAX_LENGTH)
        url = f"https://www.addgene.org/{oid}/"
        license_text = src.ADDGENE_LICENSE
        source = "addgene"

    return await _call(payload_from_record, record, source=source, url=url,
                       license_text=license_text, max_length=MAP_MAX_LENGTH)
