"""载体库路由 — 从 main.py 提取

读取走进程级 VectorLibrary 缓存（与 design_service 共享），
写操作（删除/更新/上传/导入）后统一失效缓存。
"""

import logging
import os
import re
import yaml
import tempfile
import shutil
from typing import List, Optional, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from app.cache import cache
from app import config
from app.design_service import get_vector_library, invalidate_vector_library_cache
from app.map_documents import vector_map_preview
from app.auth.jwt_auth import get_admin_user
from core.genbank_format import format_locus_line
from core.seq_utils import gc_percent
from app.routes.models import (
    VectorInfo, VectorUpdateRequest, VectorPreviewResponse,
    BatchImportRequest, MapDocument, SaveMapAsVectorRequest, MAP_MAX_LENGTH
)

logger = logging.getLogger("plasmid_designer.vector_routes")

router = APIRouter(prefix="/api/vectors", tags=["vectors"])

# 列表响应形状版本，进缓存键：形状变化（如新增 map_preview / data_provenance / topology）后不读旧缓存
VECTOR_LIST_SHAPE = 4


def _invalidate_vector_cache(vector_id: Optional[str] = None) -> None:
    """载体写操作后失效响应缓存（详情键 + 全部列表键）。"""
    try:
        if vector_id:
            cache.backend.delete(f"vector:{vector_id}")
        cache.backend.clear_pattern("vector_list:*")
    except Exception:
        # 缓存失效失败不影响写操作结果（TTL 兜底）
        pass


def _safe_filename(name: str) -> str:
    """输出文件名白名单过滤：仅保留字母/数字/点/下划线/连字符。

    此前上传载体用 vector.name.replace('/', '_') 拼 YAML 文件名，
    未过滤反斜杠等字符，Windows 下可构成路径分隔（KNOWN_ISSUES 3.3）。
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned or "vector"


# ==================== 路由 ====================

@router.get("", response_model=List[VectorInfo])
async def list_vectors(
    vector_type: Optional[str] = None,
    host: Optional[str] = None
):
    """列出所有可用载体（每项附 map_preview 供列表缩略图）"""
    filters = {"vector_type": vector_type, "host": host, "shape": VECTOR_LIST_SHAPE}

    cached_list = cache.get_vector_list(filters)
    if cached_list is not None:
        return cached_list

    library = get_vector_library()

    vectors = library.list_vectors(filter_type=vector_type)

    result = []
    for v in vectors:
        # 过滤宿主
        if host and host not in v.host:
            continue

        mcs_enzymes = v.mcs.get_unique_enzymes() if v.mcs else []

        result.append(VectorInfo(
            id=v.id,
            name=v.name,
            source=v.source,
            vector_type=v.vector_type,
            host=v.host,
            antibiotic_resistance=v.antibiotic_resistance,
            copy_number=v.copy_number,
            description=v.description,
            features=[{"name": e.name, "type": e.element_type.value} for e in v.elements],
            mcs_enzymes=mcs_enzymes,
            topology="linear" if v.topology == "linear" else "circular",
            map_preview=vector_map_preview(v),
            data_provenance=dict(v.provenance),
        ))

    try:
        cache.cache_vector_list(filters, [v.model_dump(mode="json") for v in result])
    except Exception:
        pass

    return result


@router.get("/{vector_id}", response_model=VectorInfo)
async def get_vector(vector_id: str):
    """获取载体详情"""
    cached_vector = cache.get_vector(vector_id)
    if cached_vector is not None:
        return cached_vector

    library = get_vector_library()
    vector = library.get_vector(vector_id)
    if not vector:
        raise HTTPException(status_code=404, detail="Vector not found")

    mcs_enzymes = vector.mcs.get_unique_enzymes() if vector.mcs else []

    info = VectorInfo(
        id=vector.id,
        name=vector.name,
        source=vector.source,
        vector_type=vector.vector_type,
        host=vector.host,
        antibiotic_resistance=vector.antibiotic_resistance,
        copy_number=vector.copy_number,
        description=vector.description,
        features=[{"name": e.name, "type": e.element_type.value} for e in vector.elements],
        mcs_enzymes=mcs_enzymes,
        topology="linear" if vector.topology == "linear" else "circular",
        data_provenance=dict(vector.provenance),
    )

    try:
        cache.cache_vector(vector_id, info.model_dump(mode="json"))
    except Exception:
        pass

    return info


@router.delete("/{vector_id}")
async def delete_vector(vector_id: str, _: object = Depends(get_admin_user)):
    """删除本地载体"""
    vectors_dir = config.settings.VECTORS_DIR

    for file in os.listdir(vectors_dir):
        if file.endswith('.yaml'):
            file_path = os.path.join(vectors_dir, file)
            with open(file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            if data and data.get('id') == vector_id:
                os.remove(file_path)
                invalidate_vector_library_cache()
                _invalidate_vector_cache(vector_id)
                return {"deleted": True, "vector_id": vector_id}

    raise HTTPException(status_code=404, detail="Vector not found")


@router.put("/{vector_id}")
async def update_vector(vector_id: str, request: VectorUpdateRequest, _: object = Depends(get_admin_user)):
    """更新载体信息"""
    vectors_dir = config.settings.VECTORS_DIR
    vector_file = None

    # 查找载体文件
    for file in os.listdir(vectors_dir):
        if file.endswith('.yaml'):
            file_path = os.path.join(vectors_dir, file)
            with open(file_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            if data and data.get('id') == vector_id:
                vector_file = file_path
                data_orig = data
                break

    if not vector_file:
        raise HTTPException(status_code=404, detail="Vector not found")

    # 更新字段
    if request.name is not None:
        data_orig['name'] = request.name
    if request.description is not None:
        data_orig['description'] = request.description
    if request.vector_type is not None:
        data_orig['type'] = request.vector_type
    if request.host is not None:
        data_orig['host'] = request.host
    if request.antibiotic_resistance is not None:
        data_orig['antibiotic'] = request.antibiotic_resistance
    if request.copy_number is not None:
        data_orig['copy_number'] = request.copy_number

    # 保存
    with open(vector_file, 'w', encoding='utf-8') as f:
        yaml.dump(data_orig, f, allow_unicode=True, default_flow_style=False)

    invalidate_vector_library_cache()
    _invalidate_vector_cache(vector_id)
    return {"updated": True, "vector_id": vector_id}


@router.get("/{vector_id}/sequence")
async def get_vector_sequence(vector_id: str, format: str = "fasta"):
    """获取载体序列（FASTA 或 GenBank 格式）"""
    library = get_vector_library()
    vector = library.get_vector(vector_id)

    if not vector:
        raise HTTPException(status_code=404, detail="Vector not found")

    if format.lower() == "genbank":
        # 生成 Biopython 可解析的标准 GenBank（测序页深链会把该文件作为参考序列传回）
        seq_upper = vector.sequence.upper()
        lines = [
            format_locus_line(vector.name, len(seq_upper), topology="circular"),
            f"DEFINITION  {vector.name}.",
            f"ACCESSION   {vector.id}",
            "SOURCE      .",
            "FEATURES             Location/Qualifiers",
            f"     source          1..{len(seq_upper)}",
            '                     /organism="synthetic construct"',
        ]
        for elem in vector.elements:
            ftype = (elem.element_type.value or "misc_feature")[:15]
            loc = f"complement({elem.start}..{elem.end})" if elem.strand == "-" else f"{elem.start}..{elem.end}"
            lines.append(f"     {ftype:<15} {loc}")
            lines.append(f'                     /label="{elem.name}"')
            if elem.description:
                lines.append(f'                     /note="{elem.description[:80]}"')
        lines.append("ORIGIN")
        for i in range(0, len(seq_upper), 60):
            chunk = seq_upper[i:i + 60]
            groups = ' '.join([chunk[j:j + 10] for j in range(0, len(chunk), 10)])
            lines.append(f"{i + 1:>9} {groups}")
        lines.append("//")
        content = '\n'.join(lines)
    else:
        # FASTA 格式
        content = f">{vector.id} {vector.name}\n{vector.sequence.upper()}"

    return PlainTextResponse(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={vector_id}.{format.lower()}"}
    )


@router.get("/{vector_id}/map", responses={200: {"model": MapDocument}})
async def get_vector_map_data(
    vector_id: str,
    enzyme_set: str = Query(default="common", max_length=40),
    orf_min_aa: int = Query(default=100, ge=10, le=5000),
):
    """载体图谱（契约 plasmid-map/1，特征 source=vector）"""
    import hashlib

    from app.map_documents import _vector_topology, vector_map_document
    from app.routes.map_routes import cached_document

    library = get_vector_library()
    vector = library.get_vector(vector_id)
    if not vector:
        raise HTTPException(status_code=404, detail="Vector not found")
    key = {
        "vector": vector_id,
        "seq": hashlib.sha256(vector.sequence.encode("utf-8")).hexdigest(),
        "features": [(e.name, e.element_type.value, e.start, e.end, e.strand, e.description)
                     for e in vector.elements],
        "mcs": [vector.mcs.start, vector.mcs.end] if vector.mcs else None,
        # 名称 / 拓扑 / MCS 名也进键：管理员只改这些时不再返回 1 小时内的旧文档
        "name": vector.name, "topology": _vector_topology(vector),
        "mcs_name": getattr(vector.mcs, "name", None) if vector.mcs else None,
        "enzyme_set": enzyme_set, "orf_min_aa": orf_min_aa,
    }
    return await cached_document("vector", key, vector_map_document, vector,
                                 enzyme_set=enzyme_set, orf_min_aa=orf_min_aa)


# ==================== NCBI 导入路由 ====================

@router.post("/import/ncbi")
async def import_vector_from_ncbi(query: str, limit: int = 5, _: object = Depends(get_admin_user)):
    """从 NCBI 搜索并导入载体

    NCBI 请求（单次超时 30s、限速间隔 0.6s）与 YAML 导出均为阻塞调用，
    移交线程池执行，避免阻塞事件循环导致导入期间全站无响应（KNOWN_ISSUES 2.1）。
    """
    from core.external_vector_importer import VectorLibraryManager

    def _import() -> int:
        manager = VectorLibraryManager()
        count = manager.import_from_ncbi(query, limit=limit)
        manager.export_to_yaml(config.settings.VECTORS_DIR)
        return count

    count = await run_in_threadpool(_import)

    invalidate_vector_library_cache()
    _invalidate_vector_cache()
    return {"imported": count, "query": query}


@router.post("/import/ncbi-id")
async def import_vector_by_ncbi_id(seq_id: str, _: object = Depends(get_admin_user)):
    """通过 NCBI 序列 ID 直接导入载体（阻塞调用移交线程池，见 /import/ncbi 说明）"""
    from core.external_vector_importer import VectorLibraryManager

    def _import() -> bool:
        manager = VectorLibraryManager()
        success = manager.import_from_ncbi_id(seq_id)
        if success:
            manager.export_to_yaml(config.settings.VECTORS_DIR)
        return success

    success = await run_in_threadpool(_import)

    if success:
        invalidate_vector_library_cache()
        _invalidate_vector_cache(f"ncbi_{seq_id}")
        return {"success": True, "seq_id": seq_id}
    return {"success": False, "error": "无法获取序列"}


@router.get("/search/ncbi")
async def search_ncbi_vectors(query: str, limit: int = 10):
    """搜索 NCBI 载体（不导入）"""
    from core.external_vector_importer import NCBIClient

    client = NCBIClient()
    ids = await run_in_threadpool(client.search, query, limit=limit)

    results = [{"id": seq_id, "url": f"https://www.ncbi.nlm.nih.gov/nuccore/{seq_id}"} for seq_id in ids]
    return {"query": query, "count": len(results), "results": results}


@router.get("/preview/ncbi/{seq_id}", response_model=VectorPreviewResponse)
async def preview_ncbi_vector(seq_id: str):
    """预览 NCBI 载体（不导入）"""
    from core.external_vector_importer import NCBIClient

    client = NCBIClient()
    vector = await run_in_threadpool(client.fetch_genbank, seq_id)

    if not vector:
        raise HTTPException(status_code=404, detail="Vector not found on NCBI")

    # 计算 GC 含量
    seq = vector.sequence.upper()
    gc_content = gc_percent(seq)

    warnings = []
    if len(seq) < 1000:
        warnings.append("Sequence is very short, may not be a complete vector")
    if gc_content < 30 or gc_content > 70:
        warnings.append(f"Unusual GC content: {gc_content:.1f}%")

    return VectorPreviewResponse(
        id=f"ncbi_{seq_id}",
        name=vector.name,
        source="ncbi",
        length=len(seq),
        description=vector.description[:200] + "..." if len(vector.description) > 200 else vector.description,
        gc_content=round(gc_content, 2),
        features_count=len(vector.features),
        warnings=warnings
    )


@router.post("/import/upload")
async def upload_vector_file(file: UploadFile = File(...), _: object = Depends(get_admin_user)):
    """上传并导入载体文件（GenBank / FASTA / SnapGene .dna）

    解析统一走 core.plasmid_map.parsers（含 SnapGene .dna）：此前用正则版
    GenBankImporter，.dna 二进制被当文本读 → 序列为空、特征全丢。
    """
    from core.plasmid_map.parsers import MapParseError, parse_file
    from app.vector_import import provenance_block, save_record

    filename = (file.filename or "").lower()
    if not filename.endswith(('.gb', '.gbk', '.genbank', '.gbff', '.fa', '.fasta', '.fna', '.dna')):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file format. Use .gb, .gbk, .fasta or .dna")

    # 保存临时文件：边写边计数，超限即中断（此前无大小限制，可被写满磁盘）
    max_upload = 20 * 1024 * 1024
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1]) as tmp:
        copied = 0
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if copied > max_upload:
                tmp.close()
                os.unlink(tmp.name)
                raise HTTPException(status_code=400, detail="文件过大（>20MB）")
            tmp.write(chunk)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as fh:
            data = fh.read()
        # 解析为 CPU 密集操作，移交线程池（KNOWN_ISSUES 2.1）；序列长度与图谱同一上限
        # （审查 L5：此前不限长，20 MB 文件可让 20 Mbp 序列入库，后续图谱/设计都会爆）
        try:
            record = await run_in_threadpool(parse_file, file.filename or "upload.gb", data,
                                             max_length=MAP_MAX_LENGTH)
        except MapParseError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - 解析器意外异常按坏文件处理，不回显内部信息
            logger.warning("载体文件解析异常：%s", type(exc).__name__, exc_info=True)
            raise HTTPException(status_code=400, detail="文件解析失败，请确认格式正确") from exc

        source = "snapgene" if record.source_format == "snapgene" else "genbank"
        try:
            result = await run_in_threadpool(
                save_record, record,
                source=source,
                vectors_dir=config.settings.VECTORS_DIR,
                attribution="来源：本地上传文件",
                provenance=provenance_block(
                    "本地上传文件", filename=file.filename or "",
                    format=record.source_format, license="用户自备文件，版权归原出处"),
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - 落盘失败记日志，返回通用文案（不含路径）
            logger.error("载体文件保存失败：%s", type(exc).__name__, exc_info=True)
            raise HTTPException(status_code=500, detail="载体保存失败，请稍后重试") from exc
        invalidate_vector_library_cache()
        _invalidate_vector_cache(result["id"])
        return {
            "success": True,
            "vector": {
                "id": result["id"],
                "name": result["name"],
                "length": result["length"],
                "features": result["features"],
            },
            "replaced": result.get("replaced", False),
            "warnings": result["warnings"],
        }
    finally:
        os.unlink(tmp_path)


@router.post("/import/from-map")
async def save_map_as_vector(request: SaveMapAsVectorRequest, _: object = Depends(get_admin_user)):
    """图谱页当前（可编辑）序列与注释 → 载体库（需管理员）

    序列与特征由图谱页直接提交，后端不再外呼：与用户眼前那份图谱是同一份数据，
    避免重新下载导致「保存的」与「看到的」不是一条序列。
    """
    from core.plasmid_map.parsers import MapParseError
    from app.vector_import import provenance_block, record_from_parts, save_record

    try:
        record = record_from_parts(
            name=request.name, sequence=request.sequence, topology=request.topology,
            features=[f.model_dump() for f in request.features], max_length=MAP_MAX_LENGTH)
    except MapParseError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    source = request.source or "map"
    result = await run_in_threadpool(
        save_record, record,
        source=source,
        vectors_dir=config.settings.VECTORS_DIR,
        url=request.url,
        description=request.description,
        provenance=provenance_block("图谱页保存", url=request.url, origin=source,
                                    license="由图谱页保存，序列与注释版权归原出处"),
    )
    invalidate_vector_library_cache()
    _invalidate_vector_cache(result["id"])
    return {"success": True, "vector": result}


def _run_batch_import(request: BatchImportRequest) -> Dict:
    """批量导入的同步实现：NCBI 拉取与 GenBank 解析均为阻塞调用，
    由端点移交线程池执行（KNOWN_ISSUES 2.1）。"""
    from core.external_vector_importer import VectorLibraryManager

    manager = VectorLibraryManager()
    results = {"ncbi": [], "files": [], "errors": []}

    # 导入 NCBI IDs
    for seq_id in request.ncbi_ids:
        try:
            success = manager.import_from_ncbi_id(seq_id)
            if success:
                results["ncbi"].append(seq_id)
            else:
                results["errors"].append(f"NCBI {seq_id}: Failed to fetch")
        except Exception as e:
            results["errors"].append(f"NCBI {seq_id}: {str(e)}")

    # 导入文件（仅限 DATA_DIR 内的路径）
    allowed_root = os.path.abspath(config.settings.DATA_DIR)
    for file_path in request.file_paths:
        try:
            abs_path = os.path.abspath(file_path)
            if not abs_path.startswith(allowed_root + os.sep):
                results["errors"].append(
                    f"File {file_path}: 路径不在允许目录内（仅支持 DATA_DIR 下的文件）"
                )
                continue
            count = manager.import_from_genbank(abs_path)
            if count > 0:
                results["files"].append(abs_path)
            else:
                results["errors"].append(f"File {abs_path}: No valid vectors")
        except Exception as e:
            results["errors"].append(f"File {file_path}: {str(e)}")

    # 导出
    if results["ncbi"] or results["files"]:
        manager.export_to_yaml(config.settings.VECTORS_DIR)

    return results


@router.post("/import/batch")
async def batch_import_vectors(request: BatchImportRequest, _: object = Depends(get_admin_user)):
    """批量导入载体

    安全说明：file_paths 仅允许 DATA_DIR 目录内的文件，
    防止通过任意服务器路径读取本地文件。
    """
    results = await run_in_threadpool(_run_batch_import, request)

    if results["ncbi"] or results["files"]:
        invalidate_vector_library_cache()
        _invalidate_vector_cache()

    return {
        "imported_ncbi": len(results["ncbi"]),
        "imported_files": len(results["files"]),
        "errors": results["errors"],
        "details": results
    }
