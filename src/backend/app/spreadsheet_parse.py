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
# 名称列先识别：「序列名/基因名」也含「序列」二字（见 _find_columns）
NAME_HEADER_HINTS = ("序列名", "基因名", "名称", "name", "gene", "id")
SEQ_HEADER_HINTS = ("序列", "sequence", "seq")

# 参数列表头 → params 字典键（canonical）。子串匹配；**列表顺序即优先级**：
# 「载体来源」必须在「载体」前（否则其含「载体」二字被抢）；「排除酶*」必须
# 在「酶1/酶2」前（否则「排除酶切位点1」含「酶1」被抢）。exclude_enzymes
# 特殊：允许多列（排除酶切位点1/2…），命中后不 break 掉后续同类列
PARAM_HEADER_HINTS: List[Tuple[str, Tuple[str, ...]]] = [
    ("vector_source", ("载体来源", "vector_source")),
    ("vector", ("载体", "vector")),
    ("cloning_method", ("克隆方法", "克隆方式", "cloning", "assembly_method")),
    ("insert_source", ("插入来源", "片段来源", "insert_source")),
    ("linearization", ("线性化", "linearization", "linearization_mode")),
    ("exclude_enzymes", ("排除酶切位点", "排除酶", "排除位点", "exclude_enzyme")),
    ("enzyme_5", ("酶1", "酶5", "1'酶", "5'酶", "1′酶", "5′酶", "enzyme_1", "enzyme_5", "enzyme1")),
    ("enzyme_3", ("酶2", "酶3", "2'酶", "3'酶", "2′酶", "3′酶", "enzyme_2", "enzyme_3", "enzyme2")),
    ("enzyme", ("酶", "enzyme")),
    ("direction", ("方向", "direction")),
    ("species", ("物种", "species")),
]
# 允许出现在多个列的参数键（值合并为列表）
MULTI_COL_PARAM_KEYS = {"exclude_enzymes"}

# Excel 参数值 → 标准枚举值（归一只做「字符串→枚举」，合法性校验全部
# 复用 DesignOptions 现有校验器——不出现两套校验）。匹配前 lower 去空白
CLONING_METHOD_ALIASES = {
    "gibson": "gibson", "gibson装配": "gibson", "吉布森": "gibson",
    "gibson组装": "gibson",
    "golden_gate": "golden_gate", "goldengate": "golden_gate",
    "golden gate": "golden_gate", "金门": "golden_gate",
    "golden gate组装": "golden_gate",
    "restriction": "restriction", "限制性": "restriction",
    "限制性酶切": "restriction", "酶切": "restriction",
    "双酶切": "restriction", "单酶切": "restriction",
    "gene_synthesis": "gene_synthesis", "全基因合成": "gene_synthesis",
    "基因合成": "gene_synthesis", "合成": "gene_synthesis",
}
INSERT_SOURCE_ALIASES = {
    "pcr": "pcr", "pcr引物": "pcr", "pcr引物设计": "pcr", "引物": "pcr",
    "gene_synthesis": "gene_synthesis", "全基因合成": "gene_synthesis",
    "合成": "gene_synthesis", "寡核苷酸": "gene_synthesis",
}
LINEARIZATION_ALIASES = {
    "double_digest": "double_digest", "双酶切": "double_digest",
    "双酶切线性化": "double_digest",
    "single_site": "single_site", "单位点": "single_site",
    "单位点线性化": "single_site",
    "custom_arms": "custom_arms", "自定义同源臂": "custom_arms",
    "自定义臂": "custom_arms", "同源臂": "custom_arms",
}
DIRECTION_ALIASES = {
    "auto": "auto", "自动": "auto", "自动判定": "auto",
    "reverse": "reverse", "反向": "reverse", "反义": "reverse",
    "反义构建": "reverse",
    "plus": "plus", "正链": "plus", "+": "plus",
    "minus": "minus", "负链": "minus", "-": "minus",
}
VECTOR_SOURCE_ALIASES = {
    "library": "library", "载体库": "library", "库": "library",
    "inline": "inline", "自己上传载体": "inline", "自己上传": "inline",
    "上传载体": "inline", "上传": "inline",
}

_NAME_CELL_RE = re.compile(r"[\r\n\t]")

# 模板里的示例行：与解析规则同源——用户在模板上直接改，表头/列序不用记。
# 参数列（C 起）全部可留空 = 继承批量页统一设置
TEMPLATE_ROWS = [
    ["序列名", "序列", "载体来源", "载体", "克隆方法", "插入来源", "线性化", "酶1", "酶2", "方向", "目标物种", "排除酶切位点1", "排除酶切位点2"],
    ["A01-1", "MGEKTKTEKTKTEKTKTEKT", "载体库", "pET-28a", "限制性酶切", "pcr", "", "NdeI", "XhoI", "自动判定", "ecoli", "", ""],
    ["A01-2", "MGDYKDDDDKGGGG", "自己上传载体", "pET-21a", "gibson", "", "双酶切", "", "", "", "", "EcoRI", "BamHI"],
]

# 简单批量模板：只有名称+序列两列（统一参数，无参数列）
TEMPLATE_ROWS_SIMPLE = [
    ["序列名", "序列"],
    ["A01-1", "MGEKTKTEKTKTEKTKTEKT"],
    ["A01-2", "MGDYKDDDDKGGGG"],
]

# 填写说明（写在模板第二个工作表；首行表头与解析规则一一对应）
TEMPLATE_GUIDE = [
    ["批量设计序列清单（复杂批量） — 填写说明"],
    [""],
    ["1. 第一个工作表「序列清单」：A 列填序列名（可自定义，用于结果/订购表/ZIP 命名），"],
    ["   B 列填序列（氨基酸或 DNA，与提交时的「序列类型」一致）。"],
    ["2. 表头保留：解析靠「序列名/基因名/Name/Gene/序列/Sequence」等关键词识别列；"],
    ["   无表头时按第 1 列=名称、第 2 列=序列处理（此时没有参数列）。"],
    ["3. 参数列（C 列起）全部可选、留空 = 用批量页上设置的统一参数；每行可覆盖："],
    ["   载体来源：载体库 / 自己上传载体（自己上传的载体需先在批量页「批量上传」"],
    ["   区块上传，载体名=文件名去扩展名）；载体：载体名（按来源列解析）；"],
    ["   克隆方法：gibson / golden_gate / 限制性酶切 / 全基因合成（中英文均可）；"],
    ["   插入来源：pcr / 全基因合成；线性化：双酶切 / 单位点 / 自定义同源臂（Gibson 用）；"],
    ["   酶1/酶2：限制性或 Gibson 双酶切的两个酶，如 NdeI、XhoI（Golden Gate 行必须"],
    ["   在本行填 Type IIS 酶，或在页面上统一选好）；方向：自动判定 / 反向 / 正链 / 负链；"],
    ["   目标物种：ecoli / human / yeast / cho；排除酶切位点：优化序列时要避开"],
    ["   的限制酶（可填两列，仅氨基酸输入且启用密码子优化时生效）。"],
    ["4. 行数上限 200（批量设计单次最多 100 条）；序列列不要混入编号、备注等文本。"],
    ["5. 也支持 CSV/TSV：另存为 CSV 时请用 UTF-8 或 GBK 编码（Excel 中文环境默认可直接上传）。"],
    ["6. 填好后回到批量设计页（复杂批量模式）上传本文件，在预览表里核对名称与参数后提交。"],
    ["7. 所有序列用同一套参数？用「简单批量」模式下载的两列模板更简单。"],
]

# 简单批量模板的说明
TEMPLATE_GUIDE_SIMPLE = [
    ["批量设计序列清单（简单批量） — 填写说明"],
    [""],
    ["1. 第一个工作表「序列清单」：A 列填序列名（可自定义，用于结果/订购表/ZIP 命名），"],
    ["   B 列填序列（氨基酸或 DNA，与提交时的「序列类型」一致）。"],
    ["2. 表头保留：解析靠「序列名/基因名/Name/Gene/序列/Sequence」等关键词识别列；"],
    ["   无表头时按第 1 列=名称、第 2 列=序列处理。"],
    ["3. 简单批量所有序列用同一套参数（批量页上设置）。需要每条序列各自指定"],
    ["   载体/克隆方法/酶等，请切到「复杂批量」模式下载含参数列的完整模板。"],
    ["4. 行数上限 200（批量设计单次最多 100 条）；序列列不要混入编号、备注等文本。"],
    ["5. 也支持 CSV/TSV：另存为 CSV 时请用 UTF-8 或 GBK 编码（Excel 中文环境默认可直接上传）。"],
    ["6. 填好后回到批量设计页上传本文件，在预览表里核对名称后提交。"],
]


def generate_template_xlsx(variant: str = "complex") -> bytes:
    """批量上传模板 .xlsx：序列清单 + 填写说明两个工作表。

    variant="complex"（默认）= 含参数列（复杂批量）；"simple" = 两列
    （简单批量，统一参数）。"""
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    if variant == "simple":
        template_rows, guide_rows = TEMPLATE_ROWS_SIMPLE, TEMPLATE_GUIDE_SIMPLE
    else:
        template_rows, guide_rows = TEMPLATE_ROWS, TEMPLATE_GUIDE

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "序列清单"
    bold = Font(bold=True)
    fill = PatternFill("solid", fgColor="D9E7FD")
    for row in template_rows:
        ws.append(row)
    for cell in ws[1]:
        cell.font = bold
        cell.fill = fill
    # 示例序列列给足够宽度；复杂模板的参数列窄一些
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 60
    if variant != "simple":
        for col in ("C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M"):
            ws.column_dimensions[col].width = 14
    guide = wb.create_sheet("填写说明")
    for row in guide_rows:
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


def _find_columns(header: List[str]) -> Tuple[Optional[int], Optional[int],
                                              Dict[str, object]]:
    """表头行 → (名称列, 序列列, 参数列{canonical 键: 列号}).

    exclude_enzymes 特殊：值是 List[int]（允许多列，排除酶切位点1/2…），
    其余键值是 int。名称列先识别（「序列名/基因名」也含「序列」二字）；
    已识别为名称/序列列的单元格不再参与参数列匹配。"""
    def _norm(cell: str) -> str:
        return (cell.lower().replace(" ", "").replace("'", "")
                .replace("′", "").replace("_", "").replace("-", ""))

    name_col = seq_col = None
    param_cols: Dict[str, object] = {}
    used: set = set()
    for i, cell in enumerate(header):
        low = _norm(cell)
        if not low:
            continue
        if name_col is None and any(h in low for h in NAME_HEADER_HINTS):
            name_col = i
            used.add(i)
        elif seq_col is None and any(h in low for h in SEQ_HEADER_HINTS):
            seq_col = i
            used.add(i)
    for i, cell in enumerate(header):
        if i in used:
            continue
        low = _norm(cell)
        if not low:
            continue
        for key, hints in PARAM_HEADER_HINTS:
            if key in MULTI_COL_PARAM_KEYS:
                # 多列参数（排除酶切位点1/2…）：每个命中的单元格各收集一次；
                # 占用该格并跳出，防止同一格再被更宽的「酶」规则抢走
                if any(_norm(h) in low for h in hints):
                    param_cols.setdefault(key, []).append(i)
                    used.add(i)
                    break
                continue
            if key in param_cols:
                continue
            if any(_norm(h) in low for h in hints):
                param_cols[key] = i
                used.add(i)
                break
    return name_col, seq_col, param_cols


def _normalize_param(key: str, value: str) -> str:
    """Excel 参数值 → 标准枚举字符串；查不到不猜，报错列出合法值。

    酶名/载体名/物种只做大小写归一（合法性由后续校验器/载体库判定）。"""
    v = value.strip().lower().replace(" ", "")
    if key == "cloning_method":
        table, label = CLONING_METHOD_ALIASES, "克隆方法"
        legal = "gibson / golden_gate / 限制性酶切 / 全基因合成"
    elif key == "insert_source":
        table, label = INSERT_SOURCE_ALIASES, "插入来源"
        legal = "pcr / 全基因合成"
    elif key == "linearization":
        table, label = LINEARIZATION_ALIASES, "线性化"
        legal = "双酶切 / 单位点 / 自定义同源臂"
    elif key == "direction":
        table, label = DIRECTION_ALIASES, "方向"
        legal = "自动判定 / 反向 / 正链 / 负链"
    elif key == "vector_source":
        table, label = VECTOR_SOURCE_ALIASES, "载体来源"
        legal = "载体库 / 自己上传载体"
    elif key in ("enzyme", "enzyme_5", "enzyme_3"):
        # 酶名归一到规范拼写（ndei → NdeI）：Bio.Restriction 查找大小写
        # 敏感，Excel 里的小写/全大写酶名曾让该行报「未知限制酶」。
        # 查不到时原样返回——错误留到设计阶段按行报，提示保留用户原文
        from core.enzyme_sites import canonical_enzyme_name

        return canonical_enzyme_name(value.strip())
    else:
        return value.strip()
    hit = table.get(v) or table.get(value.strip().lower())
    if hit is None:
        raise ValueError(f"{label}「{value}」无法识别，可用值：{legal}")
    return hit


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
    # （第1列=名称、第2列=序列）处理，第一行也当数据（没有参数列）
    header = rows[0]
    name_col, seq_col, param_cols = _find_columns(header)
    if seq_col is None:
        name_col, seq_col, param_cols = 0, 1, {}
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
        entry: Dict[str, object] = {
            "name": name[:100],
            # 序列统一转大写：设计时后端归一本来就是大写，预览行显示另一种
            # 大小写只会让人以为大小写被区别对待（曾只对 xlsx 转大写）
            "sequence": seq.upper(),
            "named": bool(row[name_col] if name_col is not None
                          and name_col < len(row) else "") and not inferred,
        }
        # 行级参数：有参数列才带 params（旧两列文件不含该键，前端兼容）；
        # 空单元格 = 该项留空 = 继承批量页统一设置
        if param_cols:
            params: Dict[str, object] = {}
            for key, col in param_cols.items():
                if key in MULTI_COL_PARAM_KEYS:
                    # 多列合并（排除酶切位点1/2…），去重保序
                    values: List[str] = []
                    for c in col if isinstance(col, list) else [col]:
                        raw = row[c] if c < len(row) else ""
                        if raw and raw not in values:
                            values.append(raw)
                    if values:
                        params[key] = values
                    continue
                raw = row[col] if isinstance(col, int) and col < len(row) else ""
                if not raw:
                    continue
                params[key] = _normalize_param(key, raw)
            if params:
                entry["params"] = params
        out.append(entry)
    if not out:
        raise ValueError("表格里没有可识别的序列行")
    return out
