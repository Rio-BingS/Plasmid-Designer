"""下载文件名安全处理回归测试（app/filename.py + 各导出端点接线）

背景：ExportRequest.name 等用户可控名称曾直接拼进 Content-Disposition
响应头（引号/CR/LF 可注入响应头），中文样品名则因头值仅限 latin-1 直接
UnicodeEncodeError 500。统一收敛到 sanitize_filename + content_disposition。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.filename import content_disposition, sanitize_filename  # noqa: E402
from app.main import app  # noqa: E402


# ==================== 单元：sanitize_filename ====================

@pytest.mark.parametrize("raw,expected", [
    ("pET-28a", "pET-28a"),                          # 合法原样保留
    ('evil"; DROP', "evil_DROP"),                    # 引号/分号折叠
    ("bad\r\nSet-Cookie: x=1", "bad_Set-Cookie_x_1"),  # CRLF 头注入折叠
    ("  ", "export"),                                # 全非法 → 兜底
    ("", "export"),
    ("...", "export"),                               # 去首尾点后为空
    ("基因线路-1", "基因线路-1"),                      # CJK 放行
])
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_truncates():
    assert len(sanitize_filename("a" * 500)) == 80


# ==================== 单元：content_disposition ====================

def test_content_disposition_ascii():
    assert content_disposition("pET-28a.gb") == "attachment; filename=pET-28a.gb"


def test_content_disposition_cjk_uses_rfc5987():
    # 非 ASCII 必须走 filename*（HTTP 头仅限 latin-1），且给 ASCII 兜底名
    val = content_disposition("样品-consensus.gb", ascii_fallback="consensus.gb")
    assert val.startswith("attachment; filename=consensus.gb; filename*=UTF-8''")
    assert "%E6%A0%B7%E5%93%81" in val  # 「样品」的 percent-encoding


# ==================== 端到端：analysis 导出端点 ====================

@pytest.fixture(scope="module")
def client():
    return TestClient(app)


_SEQ = "ATGAAACGTAAACGTAAACGTAAACGT"


def _export(client, name):
    return client.post("/api/analysis/export", json={
        "name": name, "sequence": _SEQ, "format": "fasta",
    })


def test_export_header_injection_is_neutralized(client):
    """引号/CRLF 注入尝试：响应头里不允许出现裸 CR/LF 或用户引号。"""
    resp = _export(client, 'x"; injected="1')
    assert resp.status_code == 200, resp.text
    cd = resp.headers["content-disposition"]
    assert "\r" not in cd and "\n" not in cd
    assert '"' not in cd.split("filename=")[-1].split(";")[0]


def test_export_cjk_name_does_not_500(client):
    """中文序列名不再因 latin-1 头编码炸 500，走 filename* 导出。"""
    resp = _export(client, "测试序列-1")
    assert resp.status_code == 200, resp.text
    assert "filename*=UTF-8''" in resp.headers["content-disposition"]
