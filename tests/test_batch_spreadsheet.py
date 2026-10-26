"""批量表格上传解析（v2.27.1）的单元与端点测试：

- 列识别：表头中英文 / 无表头位置约定 / 名称列缺失
- 格式：.xlsx / .csv（UTF-8 与 GBK）/ .tsv
- 防护：zip 炸弹预检拒绝、行数上限、非法字符报行号
- 端点：multipart 上传、格式不支持 400、解析失败 422
"""

import io

import pytest

from app.spreadsheet_parse import (
    MAX_SHEET_ROWS, parse_spreadsheet)


def _xlsx(rows):
    """测试内造 xlsx 字节流（openpyxl 正常写，不触发防护）"""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _parse(rows, suffix=".xlsx", sequence_type="amino_acid"):
    data = _xlsx(rows) if suffix == ".xlsx" else rows
    return parse_spreadsheet(data, suffix, sequence_type)


class TestColumnDetection:
    def test_chinese_header(self):
        out = _parse([["序列名", "序列"], ["A01-1", "MGEKT"], ["A01-2", "MGDYK"]])
        assert out == [
            {"name": "A01-1", "sequence": "MGEKT", "named": True},
            {"name": "A01-2", "sequence": "MGDYK", "named": True},
        ]

    def test_english_header_any_order(self):
        out = _parse([["Sequence", "Gene Name"], ["MGEKT", "geneA"]])
        assert out[0]["name"] == "geneA" and out[0]["sequence"] == "MGEKT"

    def test_no_header_positional(self):
        """识别不出序列列表头：第1列=名称、第2列=序列，首行也当数据"""
        out = _parse([["geneA", "MGEKTKTEKTKTEKTKTEKT"], ["geneB", "MGDYKDDDDKGGGG"]])
        assert [r["name"] for r in out] == ["geneA", "geneB"]
        assert out[0]["named"] is False

    def test_name_column_missing_falls_back_to_row_index(self):
        out = _parse([["序列"], ["MGEKT"], ["MGDYK"]])
        assert [r["name"] for r in out] == ["sequence_1", "sequence_2"]

    def test_sequence_column_required(self):
        """无表头且只有 1 列：没有第 2 列可当序列 → 明确报错"""
        with pytest.raises(ValueError, match="找不到序列列"):
            _parse([["MGEKTKTEKTKTEKTKTEKT"], ["MGDYKDDDDKGGGG"]])


class TestFormats:
    def test_csv_utf8(self):
        out = _parse("序列名,序列\nA01-1,MGEKT\n".encode("utf-8"), ".csv")
        assert out[0]["name"] == "A01-1" and out[0]["sequence"] == "MGEKT"

    def test_csv_gbk_fallback(self):
        out = _parse("序列名,序列\n基因甲,MGEKT\n".encode("gbk"), ".csv")
        assert out[0]["name"] == "基因甲"

    def test_tsv(self):
        out = _parse("序列名\t序列\nA01-1\tMGEKT\n".encode("utf-8"), ".tsv")
        assert out[0]["sequence"] == "MGEKT"

    def test_xlsx_numeric_cell_no_float_artifact(self):
        """数值单元格 123.0 → '123'：Excel 把纯数字基因名自动转数值的场景"""
        out = _parse([["id", "seq"], [123.0, "MGEKTKTEKTKTEKTKTEKT"]])
        assert out[0]["name"] == "123"

    def test_dna_sequence_uppercased(self):
        out = _parse([["序列名", "序列"], ["g1", "atgggcttaa"]], ".xlsx", "dna")
        assert out[0]["sequence"] == "ATGGGCTTAA"


class TestGuards:
    def test_row_limit(self):
        rows = [["序列名", "序列"]] + [["g", "MGEKTKTEKTKTEKTKTEKT"]] * (MAX_SHEET_ROWS + 1)
        with pytest.raises(ValueError, match="行数超过上限"):
            _parse(rows)

    def test_invalid_sequence_chars_report_row(self):
        with pytest.raises(ValueError, match="第 1 行序列含非法字符"):
            _parse([["序列名", "序列"], ["g1", "这不是序列"]])

    def test_empty_sequence_rows_skipped(self):
        out = _parse([["序列名", "序列"], ["g1", "MGEKT"], ["g2", ""]])
        assert [r["name"] for r in out] == ["g1"]

    def test_unsupported_suffix(self):
        with pytest.raises(ValueError, match="仅支持"):
            _parse(b"PK\x03\x04", ".docx")

    def test_empty_table(self):
        with pytest.raises(ValueError, match="表格为空"):
            _parse(b"", ".csv")


class TestEndpoint:
    def test_upload_and_parse(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        data = _xlsx([["序列名", "序列"], ["A01-1", "MGEKTKTEKTKTEKTKTEKT"]])
        resp = client.post(
            "/api/design/batch/parse-spreadsheet",
            files={"file": ("genes.xlsx", data,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"sequence_type": "amino_acid"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == 1
        assert body["sequences"][0]["name"] == "A01-1"

    def test_unsupported_format_400(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        resp = client.post(
            "/api/design/batch/parse-spreadsheet",
            files={"file": ("genes.docx", b"PK\x03\x04", "application/octet-stream")})
        assert resp.status_code == 400

    def test_bad_table_422(self):
        """表头能认出序列列、但数据是文本 → 422 带行号"""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        data = _xlsx([["序列名", "序列"], ["g1", "备注文本"]])
        resp = client.post(
            "/api/design/batch/parse-spreadsheet",
            files={"file": ("genes.xlsx", data,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        assert resp.status_code == 422
        assert "非法字符" in resp.json()["detail"]


class TestParamColumns:
    """v2.29.0：Excel 参数列（每行覆盖载体/克隆方法/酶等）"""

    def test_chinese_param_headers(self):
        rows = [["序列名", "序列", "载体", "克隆方法", "酶5", "酶3", "方向"],
                ["A01-1", "MGEKTKTEKTKTEKTKTEKT", "pET-28a", "限制性酶切", "NdeI", "XhoI", "自动"],
                ["A01-2", "MGDYKDDDDKGGGG", "", "gibson", "", "", ""]]
        out = _parse(rows)
        assert out[0]["params"] == {
            "vector": "pET-28a", "cloning_method": "restriction",
            "enzyme_5": "NdeI", "enzyme_3": "XhoI", "direction": "auto"}
        # 只填克隆方法的行：params 只含该键（其余留空 = 继承批级）
        assert out[1]["params"] == {"cloning_method": "gibson"}

    def test_english_param_headers_and_aliases(self):
        rows = [["Name", "Sequence", "Vector", "Cloning Method", "Insert Source",
                 "Linearization", "Direction", "Species"],
                ["g1", "MGEKTKTEKTKTEKTKTEKT", "pET-21a", "Golden Gate", "全基因合成",
                 "双酶切", "反向", "human"]]
        out = _parse(rows)
        assert out[0]["params"] == {
            "vector": "pET-21a", "cloning_method": "golden_gate",
            "insert_source": "gene_synthesis", "linearization": "double_digest",
            "direction": "reverse", "species": "human"}

    def test_single_enzyme_column(self):
        """单「酶」列：更具体的酶5/酶3 列先被占用，剩余列归 enzyme"""
        rows = [["序列名", "序列", "酶"],
                ["g1", "MGEKTKTEKTKTEKTKTEKT", "BsaI"]]
        out = _parse(rows)
        assert out[0]["params"] == {"enzyme": "BsaI"}

    def test_legacy_two_column_file_has_no_params(self):
        out = _parse([["序列名", "序列"], ["g1", "MGEKTKTEKTKTEKTKTEKT"]])
        assert "params" not in out[0]

    def test_unknown_cloning_method_lists_legal_values(self):
        with pytest.raises(ValueError, match="无法识别"):
            _parse([["序列名", "序列", "克隆方法"],
                    ["g1", "MGEKTKTEKTKTEKTKTEKT", "魔法组装"]])
        try:
            _parse([["序列名", "序列", "克隆方法"],
                    ["g1", "MGEKTKTEKTKTEKTKTEKT", "魔法组装"]])
        except ValueError as e:
            assert "gibson" in str(e) and "限制性酶切" in str(e)

    def test_param_error_reports_row(self):
        with pytest.raises(ValueError, match="克隆方法"):
            _parse([["序列名", "序列", "克隆方法"],
                    ["g1", "MGEKTKTEKTKTEKTKTEKT", "ok"],
                    ["g2", "MGDYKDDDDKGGGG", "错误值"]])

    def test_template_has_param_columns_roundtrip(self):
        """模板含参数列，且模板自身可被解析出参数（与解析规则同源闭环）"""
        import openpyxl
        from app.spreadsheet_parse import generate_template_xlsx
        wb = openpyxl.load_workbook(io.BytesIO(generate_template_xlsx()))
        header = [c.value for c in wb["序列清单"][1]]
        assert header[:2] == ["序列名", "序列"]
        assert "载体来源" in header and "载体" in header and "克隆方法" in header
        assert "酶1" in header and "酶2" in header
        assert "排除酶切位点1" in header and "排除酶切位点2" in header
        rows = parse_spreadsheet(generate_template_xlsx(), ".xlsx", "amino_acid")
        assert rows[0]["params"]["cloning_method"] == "restriction"
        assert rows[0]["params"]["enzyme_5"] == "NdeI"
        assert rows[0]["params"]["vector_source"] == "library"
        # 第二行示范大部分参数留空 = 继承批级；排除酶两列合并为列表
        assert rows[1]["params"] == {"vector_source": "inline",
                                     "vector": "pET-21a",
                                     "cloning_method": "gibson",
                                     "linearization": "double_digest",
                                     "exclude_enzymes": ["EcoRI", "BamHI"]}


class TestTemplate:
    def test_simple_template_two_columns_roundtrip(self):
        """简单批量模板只有名称+序列两列，解析结果无 params（与解析规则同源闭环）"""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.spreadsheet_parse import parse_spreadsheet
        client = TestClient(app)
        resp = client.get("/api/design/batch/template", params={"variant": "simple"})
        assert resp.status_code == 200
        assert "template_simple" in resp.headers["content-disposition"]
        rows = parse_spreadsheet(resp.content, ".xlsx", "amino_acid")
        assert [r["name"] for r in rows] == ["A01-1", "A01-2"]
        assert all("params" not in r for r in rows)

    def test_complex_template_keeps_param_columns(self):
        from fastapi.testclient import TestClient
        from app.main import app
        import openpyxl
        client = TestClient(app)
        resp = client.get("/api/design/batch/template", params={"variant": "complex"})
        wb = openpyxl.load_workbook(io.BytesIO(resp.content))
        header = [c.value for c in wb["序列清单"][1]]
        assert "载体" in header and "克隆方法" in header
        resp2 = client.get("/api/design/batch/template")
        wb2 = openpyxl.load_workbook(io.BytesIO(resp2.content))
        header2 = [c.value for c in wb2["序列清单"][1]]
        assert header2 == header        # 缺省 variant = complex

    def test_unknown_variant_rejected_or_defaults(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        resp = client.get("/api/design/batch/template", params={"variant": "bogus"})
        # 未知 variant 按 complex 处理（含参数列），不 500
        assert resp.status_code == 200

    def test_template_download_and_roundtrip(self):
        """模板可下载，且模板本身能被自家解析器解析出示例行（闭环）"""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.spreadsheet_parse import parse_spreadsheet
        client = TestClient(app)
        resp = client.get("/api/design/batch/template")
        assert resp.status_code == 200
        assert "spreadsheetml" in resp.headers["content-type"]
        rows = parse_spreadsheet(resp.content, ".xlsx", "amino_acid")
        assert [r["name"] for r in rows] == ["A01-1", "A01-2"]
        assert rows[0]["sequence"] == "MGEKTKTEKTKTEKTKTEKT"

    def test_template_has_guide_sheet(self):
        import openpyxl
        from app.spreadsheet_parse import generate_template_xlsx
        wb = openpyxl.load_workbook(io.BytesIO(generate_template_xlsx()))
        assert wb.sheetnames == ["序列清单", "填写说明"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
