"""密码子表路由 — 从 main.py 提取"""

import os
from fastapi import APIRouter

from app.cache import cache
from app.config import settings

router = APIRouter(prefix="/api/codon-tables", tags=["codon-tables"])


@router.get("")
async def list_codon_tables():
    """
    列出可用的密码子表
    """
    # 密码子表基本静态：列表缓存 7 天，但键带上目录内容指纹——
    # 增删改 data/codon_tables/*.yaml 后立刻换键，不会返回旧清单
    from core.codon_optimizer import codon_tables_digest

    digest = codon_tables_digest()
    tables = cache.get_codon_tables(digest)
    if tables is not None:
        return tables

    tables_dir = settings.CODON_TABLES_DIR

    tables = []
    for file in os.listdir(tables_dir):
        if file.endswith('.yaml'):
            name = file.replace('.yaml', '')
            tables.append({
                "id": name,
                "name": name.replace('_', ' '),
                "file": file
            })

    try:
        cache.cache_codon_tables(tables, digest)
    except Exception:
        pass

    return tables
