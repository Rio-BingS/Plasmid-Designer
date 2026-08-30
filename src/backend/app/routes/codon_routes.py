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
    # 密码子表为静态数据，列表缓存 7 天（VECTOR_CACHE_TTL）
    tables = cache.get_codon_tables()
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
        cache.cache_codon_tables(tables)
    except Exception:
        pass

    return tables
