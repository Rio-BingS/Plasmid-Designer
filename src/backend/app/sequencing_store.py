"""测序分析记录持久化（全方位检查挂账项：重启/过期后结论不再丢失）

STORAGE_MODE=database（默认）时，分析记录随创建写入数据库 sequencing_analyses 表：
- 结论/reads/变体/比对/互检明细 → payload 列（JSON 文本，不含峰图）
- 峰图原始数据（四通道全分辨率采样，体积大）→ trace_data 列（zlib+base64）
内存 _ANALYSES 退化为 15 分钟 TTL 的读取缓存；数据库行不过期。
STORAGE_MODE=memory（HF 等无持久化场景）时与历史行为一致，不落库。
"""
import base64
import hashlib
import json
import logging
import os
import zlib
from datetime import datetime
from typing import Dict, List, Optional

from app.storage import STORAGE_MODE

logger = logging.getLogger(__name__)

# 数据库记录保留上限（增长管理）：payload 含比对/变体明细，trace_data 含
# 压缩峰图，长年累月会无限膨胀。按属主分桶淘汰（审查 02 M4：此前全表
# 2000 条按时间淘汰，一个匿名 IP 一小时的批量即可挤掉全站登录用户的记录）：
# - 每个登录用户各自最多 MAX_DB_RECORDS_PER_OWNER 条，超出删其本人最旧记录
# - 匿名记录（owner_id 为空）共用一个桶，最多 MAX_DB_ANON_RECORDS 条
# - 全库再设 MAX_DB_RECORDS 总上限兜底磁盘：超出时先删最旧的匿名记录，
#   仍超出再从记录最多的用户那里删其最旧记录
# 0 表示不限制。批量克隆模式一次可产生上百条，默认值给足余量。
def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)) or default)


MAX_DB_RECORDS = _env_int("SEQUENCING_DB_MAX_RECORDS", 20000)
MAX_DB_RECORDS_PER_OWNER = _env_int("SEQUENCING_DB_MAX_RECORDS_PER_OWNER", 2000)
MAX_DB_ANON_RECORDS = _env_int("SEQUENCING_DB_MAX_ANON_RECORDS", 2000)


def hash_access_token(token: str) -> str:
    """匿名记录访问令牌的存储形式：SHA-256 十六进制摘要。

    令牌本身是 128 bit 随机值，无需加盐/慢哈希；只存摘要后，数据库或
    内存快照泄露不再等于令牌泄露。"""
    return hashlib.sha256(str(token).encode("utf-8", "surrogatepass")).hexdigest()


def _hash_plaintext_token(record: Dict) -> bool:
    """把旧版记录里的明文 access_token 换成摘要（原地修改）；有改动返回 True"""
    tok = record.pop("access_token", None)
    if isinstance(tok, str) and tok and not record.get("access_token_hash"):
        record["access_token_hash"] = hash_access_token(tok)
        return True
    return tok is not None


def db_enabled() -> bool:
    return STORAGE_MODE == "database"


_table_ready = False


def _ensure_table() -> None:
    """幂等建表：init_db 在应用启动时跑，但单测/直连场景可能跳过；
    checkfirst 保证重复调用无害。"""
    global _table_ready
    if _table_ready:
        return
    from app.database import engine
    from app.database.models import SequencingAnalysisDB
    SequencingAnalysisDB.__table__.create(bind=engine, checkfirst=True)
    _table_ready = True


def _encode_trace(trace: Optional[Dict]) -> Optional[str]:
    """峰图原始数据压缩存储：整型采样点重复度高，zlib 压缩比可观"""
    if not trace:
        return None
    raw = json.dumps(trace, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.b64encode(zlib.compress(raw, 6)).decode()


def _decode_trace(blob: Optional[str]) -> Dict:
    """JSON 把 int 键转成了字符串，读回时转回 int（/trace/{read_index} 按 int 取）"""
    if not blob:
        return {}
    data = json.loads(zlib.decompress(base64.b64decode(blob)))
    return {int(k): v for k, v in data.items()}


def persist_record(record: Dict) -> None:
    """创建时把记录写入数据库（同 id 重复注册覆盖；失败不阻断主流程）"""
    if not db_enabled():
        return
    from app.database import SessionLocal
    from app.database.models import SequencingAnalysisDB

    payload = {k: v for k, v in record.items()
               if k not in ("_trace_data", "_created_ts", "traces")}
    row = SequencingAnalysisDB(
        id=record["analysis_id"],
        owner_id=record.get("owner_id"),
        sample_name=(record.get("sample_name") or "")[:200],
        created_at=datetime.fromisoformat(record["created_at"]),
        engine=record.get("engine", ""),
        conclusion=record.get("conclusion", ""),
        read_count=len(record.get("reads") or []),
        variant_count=len(record.get("variants") or []),
        coverage_percent=float((record.get("consensus") or {}).get("coverage_percent") or 0.0),
        reference_length=len(record.get("reference") or ""),
        payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        trace_data=_encode_trace(record.get("_trace_data") or {}),
    )
    db = SessionLocal()
    try:
        db.merge(row)
        db.commit()
        _prune(db, record.get("owner_id"))
    except Exception as e:
        db.rollback()
        # 持久化失败不阻断分析主流程：记录仍在内存缓存中可用，但必须留痕
        logger.error("测序分析记录落库失败 (%s): %s", record["analysis_id"], e)
    finally:
        db.close()


def _owner_filter(model, owner_id):
    return model.owner_id.is_(None) if owner_id is None else model.owner_id == owner_id


def _delete_oldest(db, owner_id, n: int) -> int:
    """删除某属主桶（None 为匿名桶）最旧的 n 条，返回实际删除数"""
    if n <= 0:
        return 0
    from app.database.models import SequencingAnalysisDB as M
    ids = [x[0] for x in (db.query(M.id).filter(_owner_filter(M, owner_id))
                          .order_by(M.created_at.asc()).limit(n).all())]
    if ids:
        db.query(M).filter(M.id.in_(ids)).delete(synchronize_session=False)
    return len(ids)


def _prune(db, owner_id=None) -> None:
    """保留上限外的最旧记录淘汰（persist 提交后调用，失败仅留痕）。

    先按写入者所在的桶（登录用户本人 / 匿名桶）裁到桶上限，再检查全库
    总上限；一个属主写得再多也只会淘汰自己桶里的记录。"""
    from sqlalchemy import func
    from app.database.models import SequencingAnalysisDB as M

    removed = 0
    cap = MAX_DB_ANON_RECORDS if owner_id is None else MAX_DB_RECORDS_PER_OWNER
    if cap > 0:
        count = db.query(M.id).filter(_owner_filter(M, owner_id)).count()
        removed += _delete_oldest(db, owner_id, count - cap)

    if MAX_DB_RECORDS > 0:
        excess = db.query(M.id).count() - MAX_DB_RECORDS
        if excess > 0:
            n = _delete_oldest(db, None, excess)
            removed += n
            excess -= n
        while excess > 0:
            # 记录最多的用户先让出：删到与第二多者持平或补足超额
            top = (db.query(M.owner_id, func.count(M.id).label("n"))
                   .filter(M.owner_id.isnot(None)).group_by(M.owner_id)
                   .order_by(func.count(M.id).desc()).limit(2).all())
            if not top:
                break
            gap = top[0][1] - (top[1][1] if len(top) > 1 else 0)
            n = _delete_oldest(db, top[0][0], min(excess, max(1, gap)))
            if n == 0:
                break
            removed += n
            excess -= n

    if removed:
        db.commit()
        logger.info("测序分析记录超出保留上限，淘汰最旧 %s 条（属主桶 %s）",
                    removed, "匿名" if owner_id is None else owner_id)


def load_record_meta(analysis_id: str) -> Optional[Dict]:
    """按 id 取记录本体（payload，含属主与令牌摘要），不读峰图大列。

    鉴权只需要 owner_id / access_token_hash：先用这一步判定权限，通过后
    再 load_trace，无权请求不会触发峰图解压（审查 02 M3）。"""
    if not db_enabled():
        return None
    from app.database import SessionLocal
    from app.database.models import SequencingAnalysisDB

    _ensure_table()
    db = SessionLocal()
    try:
        row = (db.query(SequencingAnalysisDB.payload)
               .filter(SequencingAnalysisDB.id == analysis_id).first())
        if row is None:
            return None
        record = json.loads(row[0])
        _hash_plaintext_token(record)  # 未迁移的旧行：读出即转摘要
        return record
    except Exception:
        return None
    finally:
        db.close()


def load_trace(analysis_id: str) -> Dict:
    """按 id 取解压后的峰图（read 序号 → 四通道数据）；无则空 dict"""
    if not db_enabled():
        return {}
    from app.database import SessionLocal
    from app.database.models import SequencingAnalysisDB

    _ensure_table()
    db = SessionLocal()
    try:
        row = (db.query(SequencingAnalysisDB.trace_data)
               .filter(SequencingAnalysisDB.id == analysis_id).first())
        return _decode_trace(row[0]) if row is not None else {}
    except Exception:
        return {}
    finally:
        db.close()


def load_record(analysis_id: str) -> Optional[Dict]:
    """按 id 从数据库取完整记录（含峰图）；无持久化或不存在返回 None"""
    record = load_record_meta(analysis_id)
    if record is None:
        return None
    record["_trace_data"] = load_trace(analysis_id)
    return record


def migrate_plaintext_tokens() -> int:
    """启动迁移：把库中匿名记录 payload 里的明文 access_token 改存 SHA-256
    摘要（幂等；返回改写条数）。旧客户端持有的令牌照常可用。"""
    if not db_enabled():
        return 0
    from app.database import SessionLocal
    from app.database.models import SequencingAnalysisDB

    _ensure_table()
    db = SessionLocal()
    changed = 0
    try:
        rows = (db.query(SequencingAnalysisDB)
                .filter(SequencingAnalysisDB.owner_id.is_(None))
                .filter(SequencingAnalysisDB.payload.like('%"access_token":%'))
                .all())
        for row in rows:
            try:
                payload = json.loads(row.payload)
            except ValueError:
                continue
            if _hash_plaintext_token(payload):
                row.payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                changed += 1
        db.commit()
        if changed:
            logger.info("测序分析记录明文访问令牌已迁移为摘要: %s 条", changed)
    except Exception as e:
        db.rollback()
        logger.error("测序分析记录令牌迁移失败: %s", e)
    finally:
        db.close()
    return changed


def delete_record(analysis_id: str) -> None:
    if not db_enabled():
        return
    from app.database import SessionLocal
    from app.database.models import SequencingAnalysisDB

    _ensure_table()
    db = SessionLocal()
    try:
        row = db.get(SequencingAnalysisDB, analysis_id)
        if row is not None:
            db.delete(row)
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def list_records(user: Optional[object], is_admin: bool,
                 limit: int = 200, offset: int = 0) -> List[Dict]:
    """历史列表元数据（不加载 payload/trace 大列），按属主过滤，时间倒序。

    分页：limit/offset 由路由透传（默认 200 条，0 表示不限制）。
    属主规则与内存模式一致：管理员全可见；登录用户见自己创建的；
    无属主记录（匿名创建）公开。"""
    if not db_enabled():
        return []
    from app.database import SessionLocal
    from app.database.models import SequencingAnalysisDB

    _ensure_table()

    db = SessionLocal()
    try:
        q = db.query(SequencingAnalysisDB)
        if not is_admin:
            # 终审 B-02：匿名访客列表为空（风险来自列表接口主动交出 ID）；
            # 登录用户只见自己创建的——匿名记录凭 access_token 直接访问
            if user is None:
                return []
            q = q.filter(SequencingAnalysisDB.owner_id == user.id)
        q = q.order_by(SequencingAnalysisDB.created_at.desc())
        if offset:
            q = q.offset(int(offset))
        if limit:
            q = q.limit(min(int(limit), 1000))
        rows = q.all()
        return [
            {
                "analysis_id": r.id,
                "sample_name": r.sample_name or "",
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "engine": r.engine or "",
                "conclusion": r.conclusion or "",
                "read_count": r.read_count or 0,
                "variant_count": r.variant_count or 0,
                "coverage_percent": r.coverage_percent or 0.0,
                "reference_length": r.reference_length or 0,
            }
            for r in rows
        ]
    except Exception:
        return []
    finally:
        db.close()
