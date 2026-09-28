"""测序分析记录持久化（全方位检查挂账项：重启/过期后结论不再丢失）

STORAGE_MODE=database（默认）时，分析记录随创建写入数据库 sequencing_analyses 表：
- 结论/reads/变体/比对/互检明细 → payload 列（JSON 文本，不含峰图）
- 峰图原始数据（四通道全分辨率采样，体积大）→ trace_data 列（zlib+base64）
内存 _ANALYSES 退化为 15 分钟 TTL 的读取缓存；数据库行不过期。
STORAGE_MODE=memory（HF 等无持久化场景）时与历史行为一致，不落库。
"""
import base64
import json
import logging
import zlib
from datetime import datetime
from typing import Dict, List, Optional

from app.storage import STORAGE_MODE

logger = logging.getLogger(__name__)


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
    except Exception as e:
        db.rollback()
        # 持久化失败不阻断分析主流程：记录仍在内存缓存中可用，但必须留痕
        logger.error("测序分析记录落库失败 (%s): %s", record["analysis_id"], e)
    finally:
        db.close()


def load_record(analysis_id: str) -> Optional[Dict]:
    """按 id 从数据库取完整记录（含峰图）；无持久化或不存在返回 None"""
    if not db_enabled():
        return None
    from app.database import SessionLocal
    from app.database.models import SequencingAnalysisDB

    _ensure_table()
    db = SessionLocal()
    try:
        row = db.get(SequencingAnalysisDB, analysis_id)
        if row is None:
            return None
        record = json.loads(row.payload)
        record["_trace_data"] = _decode_trace(row.trace_data)
        return record
    except Exception:
        return None
    finally:
        db.close()


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


def list_records(user: Optional[object], is_admin: bool) -> List[Dict]:
    """历史列表元数据（不加载 payload/trace 大列），按属主过滤，时间倒序。

    属主规则与内存模式一致：管理员全可见；登录用户见自己创建的；
    无属主记录（匿名创建）公开。"""
    if not db_enabled():
        return []
    from app.database import SessionLocal
    from app.database.models import SequencingAnalysisDB
    from sqlalchemy import or_

    _ensure_table()

    db = SessionLocal()
    try:
        q = db.query(SequencingAnalysisDB)
        if not is_admin:
            cond = [SequencingAnalysisDB.owner_id.is_(None)]
            if user is not None:
                cond.append(SequencingAnalysisDB.owner_id == user.id)
            q = q.filter(or_(*cond))
        rows = q.order_by(SequencingAnalysisDB.created_at.desc()).all()
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
