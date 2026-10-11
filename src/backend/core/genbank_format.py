"""GenBank 文本格式的共享小工具

LOCUS 行此前在多处手拼，列位置各不相同，Biopython 解析时报
"Attempting to parse malformed locus line"。这里统一按 NCBI GenBank
LOCUS 列规范输出（1 起算的列号）：

    01-05  LOCUS
    13-28  名称（左对齐）
    30-40  序列长度（右对齐，第 40 列结束）
    42-43  bp
    48-53  分子类型（DNA/RNA/...，左对齐）
    56-63  linear（后补两个空格）或 circular
    65-67  分类（division，如 SYN）
    69-79  日期 dd-MMM-yyyy

名称过长放不进标准列时，按 GenBank 229.0 起的规则（与 Biopython 自带
写出器一致）把行加长，名称与长度之间至少留一个空格，不会互相粘连。
"""

from __future__ import annotations

import re
from datetime import date as _date, datetime
from typing import Optional, Union

# 名称 + 空格 + 长度 所占的标准宽度（第 13-40 列）
_NAME_LENGTH_WIDTH = 28
_DEFAULT_NAME_MAX = 16
_TOPOLOGIES = ("linear", "circular")


def format_locus_line(
    name: str,
    length: int,
    mol_type: str = "DNA",
    topology: str = "linear",
    division: str = "SYN",
    date: Optional[Union[datetime, _date, str]] = None,
    max_name_len: Optional[int] = _DEFAULT_NAME_MAX,
) -> str:
    """生成符合 NCBI 列规范的 LOCUS 行（不含换行符）。

    Args:
        name: 记录名。空白字符逐个替换为下划线；为空时用 "unnamed"。
        length: 序列长度（bp）。
        mol_type: 分子类型，最长 6 个字符（如 DNA、RNA、mRNA）。
        topology: "linear" 或 "circular"。
        division: 三字母分类码，默认 SYN（合成构建体）。
        date: 日期；datetime/date 自动格式化为 DD-MON-YYYY，字符串原样
            使用（大写），缺省为今天。
        max_name_len: 名称截断长度，默认 16（标准列宽，保证不与长度相撞）；
            传 None 保留全名，过长时按 Biopython 方式加长整行。
    """
    if topology not in _TOPOLOGIES:
        raise ValueError(f"topology 须为 linear 或 circular，收到 {topology!r}")
    if length < 0:
        raise ValueError(f"序列长度不能为负：{length}")
    mol_type = (mol_type or "DNA").strip()
    if not mol_type or len(mol_type) > 6 or " " in mol_type:
        raise ValueError(f"分子类型须为 1-6 个字符且无空格：{mol_type!r}")
    division = (division or "SYN").strip().upper()
    if len(division) != 3:
        raise ValueError(f"division 须为三个字符：{division!r}")

    locus = re.sub(r"\s", "_", str(name or "").strip()) or "unnamed"
    if max_name_len is not None:
        locus = locus[:max_name_len]

    if date is None:
        date = datetime.now()
    if isinstance(date, (datetime, _date)):
        date_str = date.strftime("%d-%b-%Y").upper()
    else:
        date_str = str(date).strip().upper()

    length_str = str(int(length))
    if len(locus) + 1 + len(length_str) <= _NAME_LENGTH_WIDTH:
        # 标准 80 列：名称从第 13 列左对齐，长度右对齐到第 40 列
        name_and_length = locus + length_str.rjust(_NAME_LENGTH_WIDTH - len(locus))
    else:
        # 超长名称/长度：整行后移，名称与长度之间留一个空格
        name_and_length = f"{locus} {length_str}"

    return (
        f"LOCUS       {name_and_length} bp    {mol_type:<7} "
        f"{topology:<8} {division} {date_str}"
    )
