"""数据库存储实现 — 封装 crud.py 的函数调用"""

import json
from typing import Optional, List, Dict, Any
from datetime import datetime

from .base import DesignStoreBase, BatchStoreBase

# DesignDB 上存在的可透传列（save/update 共用）
_DESIGN_COLUMN_FIELDS = (
    "optimized_sequence", "cai", "gc_content", "final_length",
    "status", "validation_passed",
)


def _as_datetime(value):
    """model_dump(mode='json') 会把 datetime 变成 ISO 字符串，落库前转回 datetime。"""
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _design_columns(data: Dict[str, Any]) -> Dict[str, Any]:
    """从 DesignResult dict 中提取可直接写入 DesignDB 列的字段。"""
    fields: Dict[str, Any] = {}
    for key in _DESIGN_COLUMN_FIELDS:
        if key in data and data[key] is not None:
            fields[key] = data[key]
    completed_at = _as_datetime(data.get("completed_at"))
    if completed_at is not None:
        fields["completed_at"] = completed_at
    return fields


class DBDesignStore(DesignStoreBase):
    """基于 SQLAlchemy 的设计存储"""

    def save(self, design_id: str, data: Dict[str, Any]) -> None:
        from app.database import SessionLocal
        from app.database.crud import (
            get_design as db_get_design,
            create_design as db_create_design,
            update_design as db_update_design,
        )
        from app.database.models import DesignDB, PrimerDB, DesignWarningDB, DesignErrorDB

        db = SessionLocal()
        try:
            existing = db_get_design(db, design_id)
            columns = _design_columns(data)
            if existing:
                db_update_design(db, design_id, **columns)
            else:
                # 关键修复：显式传入既有 design_id，避免生成新随机 id 导致查不回
                db_create_design(
                    db,
                    id=design_id,
                    input_sequence=data.get("input_sequence", ""),
                    sequence_type=data.get("sequence_type", "amino_acid"),
                    sequence_name=data.get("sequence_name", "insert"),
                    vector_id=data.get("vector_id", "pET-28a"),
                    cloning_method=data.get("cloning_method", "gibson"),
                    **columns,
                )

            # 同步关联数据（幂等：按名称/内容去重，支持任务多次 save）
            design = db.query(DesignDB).filter_by(id=design_id).first()
            if design is not None:
                known_primer_names = {p.name for p in (design.primers or [])}
                for p in data.get("primers") or []:
                    name = p.get("name") or ""
                    if not name or name in known_primer_names:
                        continue
                    db.add(PrimerDB(
                        design_id=design_id,
                        name=name,
                        sequence=p.get("sequence", ""),
                        full_sequence=p.get("full_sequence", ""),
                        tm=float(p.get("tm") or 0.0),
                        gc_content=float(p.get("gc_content") or 0.0),
                        length=int(p.get("length") or 0),
                        overhang=p.get("overhang"),
                        notes=p.get("notes"),
                    ))

                known_warnings = {w.message for w in (design.warnings or [])}
                for w in data.get("warnings") or []:
                    message = str(w)
                    if message not in known_warnings:
                        db.add(DesignWarningDB(design_id=design_id, message=message))

                known_errors = {e.message for e in (design.errors or [])}
                for e in data.get("errors") or []:
                    message = str(e)
                    if message not in known_errors:
                        db.add(DesignErrorDB(design_id=design_id, message=message))

                db.commit()
        finally:
            db.close()

    def get(self, design_id: str) -> Optional[Dict[str, Any]]:
        from app.database import SessionLocal
        from app.database.crud import get_design as db_get_design
        db = SessionLocal()
        try:
            design = db_get_design(db, design_id)
            if not design:
                return None
            return self._db_to_dict(design)
        finally:
            db.close()

    def update(self, design_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        from app.database import SessionLocal
        from app.database.crud import update_design as db_update_design
        db = SessionLocal()
        try:
            design = db_update_design(db, design_id, **kwargs)
            if not design:
                return None
            return self._db_to_dict(design)
        finally:
            db.close()

    def exists(self, design_id: str) -> bool:
        from app.database import SessionLocal
        from app.database.crud import get_design as db_get_design
        db = SessionLocal()
        try:
            return db_get_design(db, design_id) is not None
        finally:
            db.close()

    def add_primer(self, design_id: str, primer_data: Dict[str, Any]) -> None:
        from app.database import SessionLocal
        from app.database.crud import add_primer as db_add_primer
        db = SessionLocal()
        try:
            db_add_primer(
                db,
                design_id=design_id,
                name=primer_data.get("name", ""),
                sequence=primer_data.get("sequence", ""),
                full_sequence=primer_data.get("full_sequence", ""),
                tm=primer_data.get("tm", 0.0),
                gc_content=primer_data.get("gc_content", 0.0),
                length=primer_data.get("length", 0),
                overhang=primer_data.get("overhang"),
                notes=primer_data.get("notes")
            )
        finally:
            db.close()

    def add_warning(self, design_id: str, message: str) -> None:
        from app.database import SessionLocal
        from app.database.crud import add_warning as db_add_warning
        db = SessionLocal()
        try:
            db_add_warning(db, design_id, message)
        finally:
            db.close()

    def add_error(self, design_id: str, message: str) -> None:
        from app.database import SessionLocal
        from app.database.crud import add_error as db_add_error
        db = SessionLocal()
        try:
            db_add_error(db, design_id, message)
        finally:
            db.close()

    def list_designs(self, skip: int = 0, limit: int = 50) -> List[Dict[str, Any]]:
        from app.database import SessionLocal
        from app.database.models import DesignDB
        db = SessionLocal()
        try:
            designs = db.query(DesignDB).offset(skip).limit(limit).all()
            return [self._db_to_dict(d) for d in designs]
        finally:
            db.close()

    def _db_to_dict(self, design) -> Dict[str, Any]:
        """将 ORM 对象转换为字典（补齐 DesignResult 必填字段，保证可回载）"""
        created_at = design.created_at or datetime.utcnow()
        return {
            "design_id": design.id,
            "status": design.status,
            "input_sequence": design.input_sequence,
            "optimized_sequence": design.optimized_sequence,
            "cai": design.cai,
            "gc_content": design.gc_content,
            "vector_id": design.vector_id,
            "vector_name": "",
            "cloning_method": design.cloning_method,
            "final_length": design.final_length,
            "validation_passed": design.validation_passed,
            "created_at": created_at,
            "completed_at": design.completed_at,
            "primers": [
                {
                    "name": p.name,
                    "sequence": p.sequence,
                    "full_sequence": p.full_sequence,
                    "tm": p.tm,
                    "gc_content": p.gc_content,
                    "length": p.length,
                    "overhang": p.overhang,
                    "notes": p.notes
                }
                for p in (design.primers or [])
            ],
            "warnings": [w.message for w in (design.warnings or [])],
            "errors": [e.message for e in (design.errors or [])],
        }


class DBBatchStore(BatchStoreBase):
    """基于 SQLAlchemy 的批量任务存储"""

    def save(self, batch_id: str, data: Dict[str, Any]) -> None:
        from app.database import SessionLocal
        from app.database.models import BatchJobDB
        from app.database.crud import (
            create_batch_job as db_create_batch,
            add_batch_design as db_add_batch_design,
        )

        db = SessionLocal()
        try:
            batch = db.query(BatchJobDB).filter_by(id=batch_id).first()
            if batch is not None:
                # 关键修复：更新既有行而非静默返回；errors 以 JSON 文本落库
                batch.total = data.get("total", batch.total)
                batch.completed = data.get("completed", batch.completed)
                batch.failed = data.get("failed", batch.failed)
                batch.status = data.get("status", batch.status)
                batch.errors = json.dumps(data.get("errors", []), ensure_ascii=False, default=str)
                known_ids = [bd.design_id for bd in (batch.design_ids or [])]
                db.commit()
            else:
                # 关键修复：显式传既有 batch_id
                db_create_batch(db, total=data.get("total", 0), id=batch_id)
                known_ids = []

            for rid in data.get("results") or []:
                if rid not in known_ids:
                    db_add_batch_design(db, batch_id, rid)
        finally:
            db.close()

    def get(self, batch_id: str) -> Optional[Dict[str, Any]]:
        from app.database import SessionLocal
        from app.database.crud import get_batch_job as db_get_batch
        db = SessionLocal()
        try:
            batch = db_get_batch(db, batch_id)
            if not batch:
                return None
            return self._db_to_dict(batch)
        finally:
            db.close()

    def update(self, batch_id: str, **kwargs) -> Optional[Dict[str, Any]]:
        from app.database import SessionLocal
        from app.database.crud import update_batch_job as db_update_batch
        db = SessionLocal()
        try:
            batch = db_update_batch(db, batch_id, **kwargs)
            if not batch:
                return None
            return self._db_to_dict(batch)
        finally:
            db.close()

    def exists(self, batch_id: str) -> bool:
        from app.database import SessionLocal
        from app.database.crud import get_batch_job as db_get_batch
        db = SessionLocal()
        try:
            return db_get_batch(db, batch_id) is not None
        finally:
            db.close()

    def add_result(self, batch_id: str, design_id: str) -> None:
        from app.database import SessionLocal
        from app.database.crud import add_batch_design as db_add_batch_design
        db = SessionLocal()
        try:
            db_add_batch_design(db, batch_id, design_id)
        finally:
            db.close()

    def add_error(self, batch_id: str, error_data: Dict[str, Any]) -> None:
        from app.database import SessionLocal
        from app.database.models import BatchJobDB
        import json as _json

        db = SessionLocal()
        try:
            batch = db.query(BatchJobDB).filter_by(id=batch_id).first()
            if batch is None:
                return
            errors = _json.loads(batch.errors or "[]")
            errors.append(error_data)
            batch.errors = _json.dumps(errors, ensure_ascii=False, default=str)
            db.commit()
        finally:
            db.close()

    def increment_completed(self, batch_id: str) -> None:
        from app.database import SessionLocal
        from app.database.crud import get_batch_job as db_get_batch, update_batch_job as db_update_batch
        db = SessionLocal()
        try:
            batch = db_get_batch(db, batch_id)
            if batch:
                db_update_batch(db, batch_id, completed=batch.completed + 1)
        finally:
            db.close()

    def increment_failed(self, batch_id: str) -> None:
        from app.database import SessionLocal
        from app.database.crud import get_batch_job as db_get_batch, update_batch_job as db_update_batch
        db = SessionLocal()
        try:
            batch = db_get_batch(db, batch_id)
            if batch:
                db_update_batch(db, batch_id, failed=batch.failed + 1)
        finally:
            db.close()

    def _db_to_dict(self, batch) -> Dict[str, Any]:
        """将 ORM 对象转换为字典（errors 从 JSON 列还原）"""
        try:
            errors = json.loads(batch.errors or "[]")
        except (ValueError, TypeError):
            errors = []
        return {
            "batch_id": batch.id,
            "total": batch.total,
            "completed": batch.completed,
            "failed": batch.failed,
            "status": batch.status,
            "results": [bd.design_id for bd in (batch.design_ids or [])],
            "errors": errors,
        }
