"""载体库路由 — 从 main.py 提取

读取走进程级 VectorLibrary 缓存（与 design_service 共享），
写操作（删除/更新/上传/导入）后统一失效缓存。
"""

import os
import yaml
import tempfile
import shutil
from typing import List, Optional, Dict

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import PlainTextResponse

from app.cache import cache
from app.config import settings
from app.design_service import get_vector_library, invalidate_vector_library_cache
from app.routes.models import (
    VectorInfo, VectorUpdateRequest, VectorPreviewResponse,
    BatchImportRequest, PlasmidMapData
)

router = APIRouter(prefix="/api/vectors", tags=["vectors"])


def _invalidate_vector_cache(vector_id: Optional[str] = None) -> None:
    """载体写操作后失效响应缓存（详情键 + 全部列表键）。"""
    try:
        if vector_id:
            cache.backend.delete(f"vector:{vector_id}")
        cache.backend.clear_pattern("vector_list:*")
    except Exception:
        # 缓存失效失败不影响写操作结果（TTL 兜底）
        pass


# ==================== 路由 ====================

@router.get("", response_model=List[VectorInfo])
async def list_vectors(
    vector_type: Optional[str] = None,
    host: Optional[str] = None
):
    """列出所有可用载体"""
    filters = {"vector_type": vector_type, "host": host}

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
            mcs_enzymes=mcs_enzymes
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
        mcs_enzymes=mcs_enzymes
    )

    try:
        cache.cache_vector(vector_id, info.model_dump(mode="json"))
    except Exception:
        pass

    return info


@router.delete("/{vector_id}")
async def delete_vector(vector_id: str):
    """删除本地载体"""
    vectors_dir = settings.VECTORS_DIR

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
async def update_vector(vector_id: str, request: VectorUpdateRequest):
    """更新载体信息"""
    vectors_dir = settings.VECTORS_DIR
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
        # 生成 GenBank 格式
        lines = [f"LOCUS {vector.name[:16]:<16} {len(vector.sequence)} bp DNA"]
        lines.append(f"DEFINITION {vector.description}")
        lines.append(f"ACCESSION {vector.id}")
        lines.append("FEATURES Location/Qualifiers")
        for elem in vector.elements:
            lines.append(f" {elem.element_type.value:<12} {elem.start}..{elem.end}")
        lines.append("ORIGIN")
        seq = vector.sequence.upper()
        for i in range(0, len(seq), 60):
            chunk = seq[i:i + 60]
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


@router.get("/{vector_id}/map", response_model=PlasmidMapData)
async def get_vector_map_data(vector_id: str):
    """获取载体图谱数据"""
    library = get_vector_library()
    vector = library.get_vector(vector_id)

    if not vector:
        raise HTTPException(status_code=404, detail="Vector not found")

    features = []
    for elem in vector.elements:
        features.append({
            "name": elem.name,
            "type": elem.element_type.value,
            "start": elem.start,
            "end": elem.end,
            "strand": elem.strand,
            "description": elem.description
        })

    # 如果没有定义特征，添加基本注释
    if not features and vector.mcs:
        features.append({
            "name": vector.mcs.name,
            "type": "multiple_cloning_site",
            "start": vector.mcs.start,
            "end": vector.mcs.end,
            "strand": "+",
            "description": "Multiple Cloning Site"
        })

    return PlasmidMapData(
        name=vector.name,
        length=vector.length,
        sequence=vector.sequence[:1000] if len(vector.sequence) > 1000 else vector.sequence,
        features=features
    )


# ==================== NCBI 导入路由 ====================

@router.post("/import/ncbi")
async def import_vector_from_ncbi(query: str, limit: int = 5):
    """从 NCBI 搜索并导入载体"""
    from core.external_vector_importer import VectorLibraryManager

    manager = VectorLibraryManager()
    count = manager.import_from_ncbi(query, limit=limit)

    output_dir = settings.VECTORS_DIR
    manager.export_to_yaml(output_dir)

    invalidate_vector_library_cache()
    _invalidate_vector_cache()
    return {"imported": count, "query": query}


@router.post("/import/ncbi-id")
async def import_vector_by_ncbi_id(seq_id: str):
    """通过 NCBI 序列 ID 直接导入载体"""
    from core.external_vector_importer import VectorLibraryManager

    manager = VectorLibraryManager()
    success = manager.import_from_ncbi_id(seq_id)

    if success:
        output_dir = settings.VECTORS_DIR
        manager.export_to_yaml(output_dir)

        invalidate_vector_library_cache()
        _invalidate_vector_cache(f"ncbi_{seq_id}")
        return {"success": True, "seq_id": seq_id}
    return {"success": False, "error": "无法获取序列"}


@router.get("/search/ncbi")
async def search_ncbi_vectors(query: str, limit: int = 10):
    """搜索 NCBI 载体（不导入）"""
    from core.external_vector_importer import NCBIClient

    client = NCBIClient()
    ids = client.search(query, limit=limit)

    results = [{"id": seq_id, "url": f"https://www.ncbi.nlm.nih.gov/nuccore/{seq_id}"} for seq_id in ids]
    return {"query": query, "count": len(results), "results": results}


@router.get("/preview/ncbi/{seq_id}", response_model=VectorPreviewResponse)
async def preview_ncbi_vector(seq_id: str):
    """预览 NCBI 载体（不导入）"""
    from core.external_vector_importer import NCBIClient

    client = NCBIClient()
    vector = client.fetch_genbank(seq_id)

    if not vector:
        raise HTTPException(status_code=404, detail="Vector not found on NCBI")

    # 计算 GC 含量
    seq = vector.sequence.upper()
    gc_count = seq.count('G') + seq.count('C')
    gc_content = (gc_count / len(seq) * 100) if seq else 0

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
async def upload_vector_file(file: UploadFile = File(...)):
    """上传并导入载体文件（GenBank/SnapGene）"""
    from core.external_vector_importer import GenBankImporter

    # 检查文件类型
    filename = file.filename.lower()
    if not (filename.endswith('.gb') or filename.endswith('.gbk') or filename.endswith('.dna')):
        raise HTTPException(status_code=400, detail="Unsupported file format. Use .gb, .gbk, or .dna")

    # 保存临时文件
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1]) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        importer = GenBankImporter()
        vector = importer.import_file(tmp_path)

        # 保存到载体库
        output_dir = settings.VECTORS_DIR
        os.makedirs(output_dir, exist_ok=True)

        output_file = os.path.join(output_dir, f"{vector.name.replace('/', '_')}.yaml")
        data = {
            'id': vector.id,
            'name': vector.name,
            'source': 'upload',
            'sequence': vector.sequence,
            'description': vector.description,
            'features': vector.features
        }
        with open(output_file, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

        invalidate_vector_library_cache()
        _invalidate_vector_cache(vector.id)
        return {
            "success": True,
            "vector": {
                "id": vector.id,
                "name": vector.name,
                "length": len(vector.sequence),
                "features": len(vector.features)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {str(e)}")
    finally:
        os.unlink(tmp_path)


@router.post("/import/batch")
async def batch_import_vectors(request: BatchImportRequest):
    """批量导入载体

    安全说明：file_paths 仅允许 DATA_DIR 目录内的文件，
    防止通过任意服务器路径读取本地文件。
    """
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
    allowed_root = os.path.abspath(settings.DATA_DIR)
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
        output_dir = settings.VECTORS_DIR
        manager.export_to_yaml(output_dir)
        invalidate_vector_library_cache()
        _invalidate_vector_cache()

    return {
        "imported_ncbi": len(results["ncbi"]),
        "imported_files": len(results["files"]),
        "errors": results["errors"],
        "details": results
    }
