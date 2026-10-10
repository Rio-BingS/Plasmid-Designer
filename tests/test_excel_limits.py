"""批量测序信息表（.xlsx）资源上限回归锁：解压炸弹 / 行数 / 计入请求总字节"""

import io
import zipfile

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import sequencing_routes as sr
from core.sanger import batch as sb


def _xlsx_bytes(rows=1):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["质粒名称", "测序引物", "测序结果"])
    for i in range(rows):
        ws.append([f"P{i}", f"T{i}", None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _with_bomb(xlsx: bytes, size: int) -> bytes:
    """往合法 xlsx 里塞一个高压缩比的大条目（解压后 size 字节）"""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(xlsx)) as src, \
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info))
        dst.writestr("xl/media/pad.bin", b"\0" * size)
    return out.getvalue()


def test_normal_sheet_parses():
    _wb, _h, cols, rows = sb.load_excel(_xlsx_bytes(3))
    assert len(rows) == 3 and "plasmid" in cols


def test_decompression_bomb_rejected_before_parse(monkeypatch):
    monkeypatch.setattr(sb, "MAX_EXCEL_UNCOMPRESSED_BYTES", 1024 * 1024)
    bomb = _with_bomb(_xlsx_bytes(), 4 * 1024 * 1024)
    assert len(bomb) < 64 * 1024  # 上传体积很小
    with pytest.raises(ValueError, match="解压后过大"):
        sb.load_excel(bomb)


def test_too_many_rows_rejected(monkeypatch):
    monkeypatch.setattr(sb, "MAX_EXCEL_ROWS", 50)
    with pytest.raises(ValueError, match="行列数"):
        sb.load_excel(_xlsx_bytes(100))


def test_excel_counted_in_batch_total_bytes(monkeypatch):
    monkeypatch.setattr(sr, "MAX_BATCH_REQUEST_BYTES", 6000)
    xlsx = _xlsx_bytes()
    assert len(xlsx) > 1000
    files = [("files", ("ref.fasta", b">r\n" + b"A" * (6000 - len(xlsx) + 100), "text/plain")),
             ("excel", ("info.xlsx", xlsx, "application/octet-stream"))]
    r = TestClient(app).post("/api/sequencing/analyze-batch", files=files)
    assert r.status_code == 413


def test_bomb_upload_returns_400(monkeypatch):
    monkeypatch.setattr(sb, "MAX_EXCEL_UNCOMPRESSED_BYTES", 1024 * 1024)
    files = [("files", ("ref.fasta", b">r\n" + b"ACGT" * 50, "text/plain")),
             ("excel", ("info.xlsx", _with_bomb(_xlsx_bytes(), 4 * 1024 * 1024),
                        "application/octet-stream"))]
    r = TestClient(app).post("/api/sequencing/analyze-batch", files=files)
    assert r.status_code == 400 and "解压后过大" in r.json()["detail"]
