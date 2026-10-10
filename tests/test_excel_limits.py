"""批量测序信息表（.xlsx）资源上限回归锁"""

import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import sequencing_routes as sr


def _xlsx_bytes(rows=1):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["质粒名称", "测序引物", "测序结果"])
    for i in range(rows):
        ws.append([f"P{i}", f"T{i}", None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_excel_counted_in_batch_total_bytes(monkeypatch):
    monkeypatch.setattr(sr, "MAX_BATCH_REQUEST_BYTES", 6000)
    xlsx = _xlsx_bytes()
    assert len(xlsx) > 1000
    files = [("files", ("ref.fasta", b">r\n" + b"A" * (6000 - len(xlsx) + 100), "text/plain")),
             ("excel", ("info.xlsx", xlsx, "application/octet-stream"))]
    r = TestClient(app).post("/api/sequencing/analyze-batch", files=files)
    assert r.status_code == 413
