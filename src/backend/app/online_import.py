"""在线序列来源的共享装配层（载体库 /api/vectors/import/* 与图谱页 /api/map/import/* 共用）

只做三件事，避免两个路由模块各写一份而慢慢漂移：

1. **来源清单与许可文案**：同一份数据，前端两处页签都据此渲染
2. **SnapGene 集合 / 集合索引**：取用 + 进程缓存（两端点共用同一批 `online:` 键，
   用户在载体库看过一次集合、图谱页就不必再外呼）
3. **SnapGene 单个序列**：下载 .dna → 交给项目统一解析器 → 用集合索引里的文件名命名
   （实测 .dna 内部 `record.name` 是 "REVIEWED" 这类状态标记，不是质粒名）

阻塞外呼一律由调用方放进线程池（route 层 run_in_threadpool）。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Optional, Tuple

from fastapi import Depends, HTTPException, status

from app.auth.jwt_auth import get_current_user
from app.cache import cache
from core import online_sources as src
from core.plasmid_map.parsers import ParsedRecord

logger = logging.getLogger("plasmid_designer.online_import")

# 集合/索引变化很慢，TTL 取 1 小时；预览按「来源 + 标识」的内容哈希缓存
CACHE_TTL = 3600


def cache_get(key: str):
    try:
        return cache.backend.get(key)
    except Exception:  # noqa: BLE001 - 缓存不可用不影响主流程
        return None


def cache_set(key: str, value: Any) -> None:
    try:
        cache.backend.set(key, value, CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass


def require_snapgene_enabled() -> None:
    """SnapGene 在线库开关（SNAPGENE_ONLINE_ENABLED，默认开）：关闭时一律 404 + 说明。

    放在鉴权依赖之前声明，关闭时匿名/登录用户都得到同一个明确的 404。
    """
    if not src.snapgene_online_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=src.SNAPGENE_DISABLED_MSG)


async def require_snapgene_user(_: None = Depends(require_snapgene_enabled),
                                user=Depends(get_current_user)):
    """SnapGene 在线库只读端点（集合/索引/预览/图谱打开）：开关打开 + 已登录。

    匿名访客不能触发服务器去抓 `/local`（v2.12 审查 H2：此前任何访客都能代理外呼）。
    """
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="SnapGene 在线库仅对已登录用户开放，请先登录",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


def sources_payload(*, is_admin: bool = False) -> Dict[str, Any]:
    """在线来源清单：可用性 + 许可摘要 + 导入开关（前端据此渲染页签与提示）

    `snapgene_online_enabled`：SnapGene 在线库开关（默认 false）；前端据此
    以及是否已登录决定是否显示 SnapGene 页签（后端仍是权威：关 → 404，匿名 → 401）。
    """
    snapgene_on = src.snapgene_online_enabled()
    # Addgene 会话是否配置只告诉管理员（审查 M1：匿名不该知道本实例挂了谁的账号；
    # 导入也只有管理员能做，对其他人 importable 恒为 False 才诚实）
    addgene_session = src.addgene_has_session() and is_admin
    return {
        "enabled": src.import_enabled(),
        "snapgene_online_enabled": snapgene_on,
        "disclaimer": src.DISCLAIMER,
        "sources": [
            {
                "id": "snapgene",
                "name": "SnapGene 在线质粒库",
                "kind": "online_library",
                "url": src.public_base(src.SNAPGENE_BASE),
                "license": src.SNAPGENE_LICENSE,
                "enabled": snapgene_on,
                "importable": snapgene_on,
                "requires_login": True,
                "note": ("集合含基础克隆载体/CRISPR/荧光蛋白/酵母质粒等，含完整图谱与注释；"
                         "仅登录用户可用" if snapgene_on else src.SNAPGENE_DISABLED_MSG),
            },
            {
                "id": "ensembl",
                "name": "Ensembl REST",
                "kind": "online_sequence",
                "url": src.public_base(src.ENSEMBL_BASE),
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
                "importable": addgene_session,
                "requires_login": True,
                "session_configured": addgene_session,
                "note": ("本地索引搜索 + 匿名元数据；全长序列需登录——本工具不代为登录。"
                         + ("已配置 ADDGENE_SESSION_COOKIE：管理员可用该登录态取序列"
                            if addgene_session else
                            "建议用自己的 Addgene 账号在浏览器下载序列文件后走「上传文件」导入")),
            },
            {
                "id": "uniprot",
                "name": "UniProt",
                "kind": "protein_lookup",
                "url": src.public_base(src.UNIPROT_BASE),
                "license": src.UNIPROT_LICENSE,
                "importable": False,
                "note": ("蛋白库（CC BY 4.0），不给质粒序列：按基因/蛋白名查条目，"
                         "给出 Entrez/EMBL/RefSeq/Ensembl 交叉引用，再据此去核酸来源取 DNA"),
            },
            {
                "id": "ncbi",
                "name": "NCBI GenBank（已有通道）",
                "kind": "online_sequence",
                "url": "https://www.ncbi.nlm.nih.gov/nuccore/",
                "license": "NCBI 公共数据；个别提交者可能主张权利",
                "importable": True,
                "note": "见 /api/vectors/import/ncbi、/api/map/import/ncbi；图谱页亦可按 accession 打开",
            },
        ],
    }


def snapgene_sets_payload() -> Dict[str, Any]:
    """SnapGene 在线库的集合列表（缓存）"""
    key = "online:snapgene:sets"
    hit = cache_get(key)
    if hit is not None:
        return hit
    sets = src.snapgene_sets()
    payload = {"count": len(sets), "sets": sets, "license": src.SNAPGENE_LICENSE}
    cache_set(key, payload)
    return payload


def snapgene_index_payload(set_id: str) -> Dict[str, Any]:
    """集合索引（缓存）：集合内全部序列的 id / 显示名 / 文件名"""
    sid = src.validate_set_id(set_id)
    key = f"online:snapgene:index:{sid}"
    hit = cache_get(key)
    if hit is not None:
        return hit
    rows = src.snapgene_set_index(sid)
    payload = {"set_id": sid, "count": len(rows), "plasmids": rows}
    cache_set(key, payload)
    return payload


def snapgene_plasmid_record(set_id: str, plasmid_id: str,
                            max_length: Optional[int] = None) -> Tuple[ParsedRecord, str, str]:
    """下载并解析单个 .dna，返回 (记录, 来源 URL, 显示名)。

    显示名取集合索引里的 `filename`（去 .dna 后缀）——它比文件内部名可靠。
    索引取不到时退化为 plasmid_id，不影响导入。
    `max_length` 由调用方按用途给上限（图谱页传 MAP_MAX_LENGTH，超限 413）。
    """
    sid = src.validate_set_id(set_id)
    pid = src.validate_plasmid_id(plasmid_id)
    data = src.snapgene_plasmid_bytes(sid, pid)
    url = f"{src.public_base(src.SNAPGENE_BASE)}/fetch.php?set={sid}&plasmid={pid}"
    name = pid
    try:
        name = next((str(p.get("filename") or "") for p in snapgene_index_payload(sid)["plasmids"]
                     if p.get("plasmid_id") == pid), "") or pid
    except Exception:  # noqa: BLE001 - 索引不可用时用 ID 当名字
        name = pid
    name = name[:-4] if name.lower().endswith(".dna") else name
    return src.snapgene_record(data, name=name, max_length=max_length), url, name


def preview_cache_key(source: str, *parts: str) -> str:
    """预览用的缓存键（内容哈希，避免 set_id 里的特殊字符进键）"""
    raw = "|".join([source, *parts])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"online:preview:{source}:{digest}"


def addgene_shared_session_warning() -> Optional[str]:
    """`ADDGENE_SESSION_COOKIE` 只适合单用户自托管：多用户部署时返回告警文案（否则 None）。

    审查 M2：多用户实例里，一个人的 Addgene 会话被全站间接使用（等同共享账号，
    Addgene ToS 要求账号持有人对其下全部活动负责），导入的序列也会进共享载体库。
    「多用户」的判据（任一即成立）：站点设置开放注册，或库里已有 1 个以上启用的账号。
    只读、不改任何状态；读库失败时不告警（不阻断启动）。
    """
    if not src.addgene_has_session():
        return None
    reasons = []
    try:
        from app import site_settings
        if site_settings.get_settings().get("registration_open"):
            reasons.append("开放注册")
    except Exception:  # noqa: BLE001 - 设置不可读不影响启动
        pass
    try:
        from app.database import SessionLocal, UserDB
        db = SessionLocal()
        try:
            n = db.query(UserDB).filter(UserDB.is_active.is_(True)).count()
        finally:
            db.close()
        if n > 1:
            reasons.append(f"已有 {n} 个启用账号")
    except Exception:  # noqa: BLE001
        pass
    if not reasons:
        return None
    return ("ADDGENE_SESSION_COOKIE 已配置，但本实例看起来是多用户部署（"
            + "、".join(reasons) + "）：该 Cookie 是一个人的 Addgene 登录态，只适合单用户"
            "自托管；多用户下等同共享账号，导入的序列也会对全站可见。建议清空该变量，"
            "改为在浏览器用自己的账号下载序列文件后走「上传文件」导入。")


def log_addgene_shared_session_warning() -> None:
    """启动时调用：满足多用户判据就记一条 WARNING（不回显 Cookie 内容）。"""
    msg = addgene_shared_session_warning()
    if msg:
        logger.warning(msg)
