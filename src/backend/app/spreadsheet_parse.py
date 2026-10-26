"""批量设计上传表格（.xlsx/.csv/.tsv）→ 序列清单解析。

设计意图：基因序列清单平时维护在 Excel 里（基因名+序列两列），上传后
直接进批量设计。解析在后端做（前端不引 xlsx 库——npm 上的 xlsx@0.18.5
有未修复的安全公告，修复版不在 npm 分发），返回与粘贴/FASTA 相同的
[{name, sequence}] 语义，前端预览表对三种入口一视同仁。

防护沿用 core/sanger/batch.py 的 xlsx zip 炸弹模式：条目数与解压后总
字节超限即拒绝（不解压）；行列数上限远高于正常用量（批量上限 100 条）。
"""

import csv
import io
import re
import zipfile
from typing import Dict, List, Optional, Tuple

# .xlsx 是 zip，小文件可解压出 GB 级 XML；上限远高于正常用量
MAX_SHEET_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_SHEET_ZIP_ENTRIES = 500
MAX_SHEET_ROWS = 200
MAX_SHEET_COLS = 50

UPLOAD_SUFFIXES = (".xlsx", ".xlsm", ".csv", ".tsv")

# 表头列名识别（小写、去空白后子串匹配）。中英文常见叫法都认；
# 顺序即优先级：先找序列列再找名称列（序列列是必需的）
NAME_HEADER_HINTS = ("序列名", "基因名", "名称", "name", "gene", "id")
SEQ_HEADER_HINTS = ("序列", "sequence", "seq")

_NAME_CELL_RE = re.compile(r"[\r\n\t]")

# 模板里的示例行：与解析规则同源——用户在模板上直接改，表头/列序不用记
TEMPLATE_ROWS = [
    ["序列名", "序列"],
    ["A01-1", "MGEKTKTEKTKTEKTKTEKT"],
    ["A01-2", "MGDYKDDDDKGGGG"],
]

# 填写说明（写在模板第二个工作表；首行表头与解析规则一一对应）
TEMPLATE_GUIDE = [
    ["批量设计序列清单 — 填写说明"],
    [""],
    ["1. 第一个工作表「序列清单」：A 列填序列名（可自定义，用于结果/订购表/ZIP 命名），"],
    ["   B 列填序列（氨基酸或 DNA，与提交时的「序列类型」一致）。"],
    ["2. 表头保留：解析靠「序列名/基因名/Name/Gene/序列/Sequence」等关键词识别列；"],
    ["   无表头时按第 1 列=名称、第 2 列=序列处理。"],
    ["3. 行数上限 200（批量设计单次最多 100 条）；序列列不要混入编号、备注等文本。"],
    ["4. 也支持 CSV/TSV：另存为 CSV 时请用 UTF-8 或 GBK 编码（Excel 中文环境默认可直接上传）。"],
    ["5. 填好后回到批量设计页上传本文件，在预览表里核对名称后提交。"],
]


def generate_template_xlsx() -> bytes:
    """批量上传模板 .xlsx：序列清单 + 填写说明两个工作表。"""
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "序列清单"
    bold = Font(bold=True)
    fill = PatternFill("solid", fgColor="D9E7FD")
    for row in TEMPLATE_ROWS:
        ws.append(row)
    for cell in ws[1]:
        cell.font = bold
        cell.fill = fill
    # 示例序列列给足够宽度；名称列同理
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 60
    # 预留 30 个空行（带边框感无需，空白即可），用户直接往下填
    guide = wb.create_sheet("填写说明")
    for row in TEMPLATE_GUIDE:
        guide.append(row)
    guide.column_dimensions["A"].width = 100
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _clean_cell(v) -> str:
    """单元格 → 干净字符串；数值 123.0 → '123'（防 Excel 自动转数值）"""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _check_xlsx_zip(data: bytes) -> None:
    """解析前检查 zip 目录：条目数与解压后总字节超限即拒绝（不解压）"""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_SHEET_ZIP_ENTRIES:
            raise ValueError(f"表格结构异常（压缩条目 {len(infos)} 个），请检查文件")
        total = sum(max(0, i.file_size) for i in infos)
        if total > MAX_SHEET_UNCOMPRESSED_BYTES:
            raise ValueError(
                f"表格解压后过大（>{MAX_SHEET_UNCOMPRESSED_BYTES // (1024 * 1024)}MB），"
                "请只保留清单本身后重新上传")


def _load_rows(data: bytes, suffix: str) -> List[List[str]]:
    """表格文件 → 字符串行（全部按文本读，不做类型转换）"""
    if suffix in (".csv", ".tsv"):
        delim = "\t" if suffix == ".tsv" else ","
        rows = None
        # Excel 导出的 CSV 有三种常见编码：UTF-8（带 BOM）、GBK（中文环境
        # 默认）、ASCII。UTF-8 解码失败或解出替换符时回退 GBK
        for encoding in ("utf-8-sig", "gbk"):
            try:
                text = data.decode(encoding)
                parsed = list(csv.reader(io.StringIO(text), delimiter=delim))
            except (UnicodeDecodeError, csv.Error):
                continue
            rows = parsed
            if not any("\ufffd" in c for row in rows for c in row):
                break
        if rows is None:
            raise ValueError("文件编码无法识别：请另存为 UTF-8 或 UTF-8 with BOM 的 CSV")
        return rows
    _check_xlsx_zip(data)
    import openpyxl
    wb = openpyxl.load_workbook(
        io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb.active
        rows: List[List[str]] = []
        for row in ws.iter_rows(max_row=MAX_SHEET_ROWS + 1, max_col=MAX_SHEET_COLS):
            rows.append([_clean_cell(c.value) for c in row])
        return rows
    finally:
        wb.close()


def _find_columns(header: List[str]) -> Tuple[Optional[int], Optional[int]]:
    """表头行 → (名称列, 序列列)；识别不出表头返回 (None, None)。

    名称列先识别：「序列名/基因名」也含「序列」二字，序列列若先匹配会把
    名称列抢走。已识别为名称列的单元格不再参与序列列匹配。"""
    name_col = seq_col = None
    for i, cell in enumerate(header):
        low = cell.lower().replace(" ", "")
        if not low:
            continue
        if name_col is None and any(h in low for h in NAME_HEADER_HINTS):
            name_col = i
        elif seq_col is None and any(h in low for h in SEQ_HEADER_HINTS):
            seq_col = i
    return name_col, seq_col


def _looks_like_sequence(cell: str, dna: Optional[bool] = None) -> bool:
    """单元格是否像序列：字母（可能带 * 终止符）且无空白/数字混合特征"""
    if not cell or len(cell) < 4:
        return False
    return bool(re.fullmatch(r"[A-Za-z*\-]+", cell))


def parse_spreadsheet(data: bytes, suffix: str,
                      sequence_type: str = "amino_acid") -> List[Dict[str, object]]:
    """表格 → [{name, sequence}]；序列列必需，名称列缺失时按行序推断名。

    raises ValueError：格式不支持 / 行列超限 / 找不到序列列 / 序列含非法字符。
    """
    if suffix not in UPLOAD_SUFFIXES:
        raise ValueError("仅支持 .xlsx/.xlsm/.csv/.tsv 表格")
    rows = _load_rows(data, suffix)
    rows = [r for r in rows if any(c for c in r)]        # 去全空行
    if not rows:
        raise ValueError("表格为空")
    if len(rows) > MAX_SHEET_ROWS:
        raise ValueError(f"表格行数超过上限（{MAX_SHEET_ROWS} 行），请分批提交")

    # 表头识别：第一行能同时认出序列列才算表头；否则按位置约定
    # （第1列=名称、第2列=序列）处理，第一行也当数据
    header = rows[0]
    name_col, seq_col = _find_columns(header)
    if seq_col is None:
        name_col, seq_col = 0, 1
        data_rows = rows
        inferred = True
    else:
        data_rows = rows[1:]
        inferred = False

    # xlsx 读取会把所有行填充到 max_col 宽度（补空串），按长度判断会失真；
    # 直接看序列列上有没有任何非空内容
    if not any(len(r) > seq_col and r[seq_col] for r in data_rows):
        raise ValueError("找不到序列列：请确认表格含「序列」列（或第 2 列为序列）")

    out: List[Dict[str, object]] = []
    for i, row in enumerate(data_rows):
        seq = row[seq_col] if seq_col < len(row) else ""
        name = (row[name_col] if name_col is not None and name_col < len(row)
                else "") or f"sequence_{i + 1}"
        if _NAME_CELL_RE.search(name):
            name = _NAME_CELL_RE.sub("_", name)
        if not seq:
            continue                                     # 序列为空的行跳过
        if not _looks_like_sequence(seq):
            col = seq_col + 1
            raise ValueError(
                f"第 {i + 1} 行序列含非法字符（{col} 列）——请确认序列列没有"
                "混入编号、备注等文本")
        out.append({
            "name": name[:100],
            "sequence": seq.upper() if suffix not in (".csv", ".tsv") else seq,
            "named": bool(row[name_col] if name_col is not None
                          and name_col < len(row) else "") and not inferred,
        })
    if not out:
        raise ValueError("表格里没有可识别的序列行")
    return out
