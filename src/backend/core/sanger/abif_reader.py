"""ABIF (.ab1) 测序文件解析器 — 纯标准库实现

ABIF 1.0 格式（Applied Biosystems）：
- 128 字节文件头：magic 'ABIF', 版本, 目录元素个数, 目录偏移(64-67), 目录大小
- 目录区：每个元素 28 字节描述符（tag 4B + number 4B + type 2B + size 4B + ...)
- 数据区：各元素的数据（PBAS2 碱基/质量、DATA1-4 四通道 trace、PLOC 峰位置等）

提取内容：
- bases / quality（PBAS2）
- trace 四通道（DATA 9-12，即原始通道 1-4）
- peak 位置（PLOC）
"""

import itertools
import struct
from typing import Dict, List, Tuple

MAGIC = b"ABIF"

# 染料通道候选组（不同仪器/软件把四个染料放在不同 DATA 标签）：
# 9-12 为最常见（analyzed data），1-4 为 raw 通道，105-108/205-208 见于部分机型
_CHANNEL_GROUPS = ((9, 10, 11, 12), (1, 2, 3, 4), (5, 6, 7, 8),
                   (105, 106, 107, 108), (205, 206, 207, 208))
_DYE_PERMS = tuple(itertools.permutations("ATGC"))
_MIN_MAP_SCORE = 0.5  # 通道-碱基符合率低于此值时不信任检测结果，回退惯例映射


def peak_window(peak_indices: List[int], i: int, n: int,
                default_half: int = 6) -> Tuple[int, int]:
    """第 i 个碱基峰在 trace 数据中的取样窗口 [lo, hi)

    PLOC 峰坐标是 trace 数据点坐标（每碱基占多个采样点，非碱基索引）。
    半宽取相邻峰间距的一半（覆盖本峰面积、不跨入邻峰）；间距为 1
    （每碱基单采样点，常见于合成数据）时退化为单点窗口。
    """
    pk = peak_indices[i]
    gaps = []
    if i > 0:
        gaps.append(max(1, pk - peak_indices[i - 1]))
    if i + 1 < len(peak_indices):
        gaps.append(max(1, peak_indices[i + 1] - pk))
    half = min(default_half, min(gaps) // 2) if gaps else default_half
    return max(0, pk - half), min(n, pk + half + 1)

# ABIF 数据类型 → struct 格式与字节宽
_TYPE_FMT = {
    1: ("b", 1),    # byte
    2: ("s", 1),    # char
    3: ("H", 2),    # word (uint16)
    4: ("h", 2),    # short (int16)
    5: ("I", 4),    # long (uint32)
    6: ("i", 4),    # long long (int32)
    7: ("f", 4),    # float
    8: ("d", 8),    # double
    10: ("H", 2),   # pString (前缀长度字节)
    11: ("H", 2),   # cString
    12: ("B", 1),   # byte array
}
_TYPE_BYTE_SIZES = {1: 1, 2: 1, 3: 2, 4: 2, 5: 4, 6: 4, 7: 4, 8: 8, 10: 1, 11: 1, 12: 1}


class AbiParseError(Exception):
    pass


def parse_abif(data: bytes) -> Dict:
    """解析 ABIF 文件，返回 tag → 数据 字典（key 形如 'PBAS2' 或 'DATA9'）"""
    if len(data) < 128 or data[:4] != MAGIC:
        raise AbiParseError("不是有效的 ABIF 文件（缺少 ABIF magic）")

    version = struct.unpack(">H", data[4:6])[0]
    # ABIF header 本身是一个类 DirEntry 结构（v1.4+）：
    # 16-17 目录项大小, 18-21 目录项个数, 26-29 目录区偏移
    dir_entry_size = struct.unpack(">H", data[16:18])[0]
    dir_entry_count = struct.unpack(">I", data[18:22])[0]
    dir_offset = struct.unpack(">I", data[26:30])[0]
    if dir_entry_size < 28:
        dir_entry_size = 28

    records: Dict[Tuple[str, int], object] = {}

    for i in range(dir_entry_count):
        entry = data[dir_offset + i * dir_entry_size: dir_offset + i * dir_entry_size + 28]
        if len(entry) < 28:
            break
        tag = entry[0:4].decode("ascii", errors="replace")
        number = struct.unpack(">I", entry[4:8])[0]
        elem_type = struct.unpack(">H", entry[8:10])[0]
        elem_size = _TYPE_BYTE_SIZES.get(elem_type, 1)
        elem_count = struct.unpack(">I", entry[12:16])[0]
        data_size = struct.unpack(">I", entry[16:20])[0]
        data_offset = struct.unpack(">I", entry[20:24])[0]

        if data_size <= 4:
            # 数据内联在目录项的 20-23 字节（dataOffset 字段位置）
            payload = entry[20:24][:data_size]
        else:
            payload = data[data_offset: data_offset + data_size]

        try:
            records[(tag, number)] = _decode(elem_type, elem_count, payload)
        except struct.error:
            continue

    return {
        "version": version,
        "records": records,
    }


def _decode(elem_type: int, elem_count: int, payload: bytes):
    if elem_type == 2:  # char 数组：保留原始字节，由调用方按语义解释（序列/质量值）
        return payload
    if elem_type in (10, 11):  # pString / cString
        if not payload:
            return ""
        str_len = payload[0]
        return payload[1:1 + str_len].decode("ascii", errors="replace")
    fmt = _TYPE_FMT.get(elem_type)
    if fmt is None:
        return payload
    code, width = fmt
    n = min(elem_count, len(payload) // width) if width > 1 else elem_count
    return list(struct.unpack(f">{n}{code}", payload[: n * width]))


def _detect_trace_mapping(channel_data: Dict[int, List[int]], bases: str,
                          peak_indices: List[int]) -> Dict[str, List[int]]:
    """按信号内容自动检测染料通道映射

    机器染料组不同，四个染料可能存于不同 DATA 标签且顺序各异（不是所有
    文件都遵循 DATA9-12 → A/T/G/C 惯例）。映射错了，峰级证据会把突变峰
    记到错误通道（mutant_pct 假性 0/50%），真实突变被误判低置信。

    检测方法：无论映射如何，每个碱基调用峰的「主通道」应与调用碱基一致。
    在候选通道组 × 24 种染料排列中选符合率最高的组合；低于阈值时回退
    DATA9-12 → A/T/G/C 惯例。
    """
    default = {b: [int(v) for v in channel_data.get(n, [])]
               for b, n in zip("ATGC", (9, 10, 11, 12))}
    if not channel_data or not bases or not peak_indices:
        return default

    # 采样：均匀取 ≤150 个有效碱基位（速度可控且覆盖全 read）
    usable = [i for i, b in enumerate(bases)
              if b in "ACGT" and i < len(peak_indices)
              and peak_indices[i] is not None and peak_indices[i] >= 0]
    if not usable:
        return default
    if len(usable) > 150:
        step = len(usable) / 150
        usable = [usable[int(j * step)] for j in range(150)]

    best_score, best_group, best_perm = 0.0, None, None
    for group in _CHANNEL_GROUPS:
        arrays = [channel_data.get(n) for n in group]
        if any(a is None for a in arrays) or len({len(a) for a in arrays}) != 1:
            continue
        for perm in _DYE_PERMS:  # perm[k] = group 第 k 个通道对应的碱基
            ok = total = 0
            for i in usable:
                lo, hi = peak_window(peak_indices, i, len(arrays[0]))
                areas = [sum(a[lo:hi]) for a in arrays]
                if max(areas) <= 0:
                    continue
                total += 1
                if perm[areas.index(max(areas))] == bases[i]:
                    ok += 1
            score = ok / total if total else 0.0
            if score > best_score:
                best_score, best_group, best_perm = score, group, perm

    if best_group is None or best_score < _MIN_MAP_SCORE:
        return default
    return {b: [int(v) for v in channel_data[n]] for b, n in zip(best_perm, best_group)}


def extract_read(data: bytes) -> Dict:
    """从 ab1 字节流提取碱基/质量/trace 数据

    主路径：Biopython Bio.SeqIO("abi") —— 官方持续维护的 ABIF 解析接口，
    经大量真实 ab1 文件验证（Bio.Sequencing.Abi 已在 1.73 移除，由 SeqIO abi 接管）。
    回退路径：内置纯标准库解析器（无 Biopython 环境时）。

    返回 {bases, quality, trace: {A,T,G,C}, peak_indices, sample_name}
    trace 通道映射按信号内容自动检测（机器染料组各异，不是所有文件都是
    DATA9-12 → A/T/G/C），检测不可靠时回退该惯例。
    """
    try:
        return _extract_read_biopython(data)
    except ImportError:
        return _extract_read_internal(data)


def _extract_read_biopython(data: bytes) -> Dict:
    import io

    from Bio import SeqIO  # noqa: 惰性导入，缺失时回退内置解析器

    record = SeqIO.read(io.BytesIO(data), "abi")
    bases = str(record.seq).upper()
    quality = list(record.letter_annotations.get("phred_quality", []))
    raw = record.annotations.get("abif_raw", {})

    channel_data: Dict[int, List[int]] = {}
    for key, values in raw.items():
        if key.startswith("DATA") and key[4:].isdigit() and isinstance(values, (list, tuple)):
            channel_data[int(key[4:])] = list(values)

    ploc = raw.get("PLOC2", raw.get("PLOC1", [])) or []
    peak_indices = [int(p) - 1 for p in ploc[: len(bases)]]

    trace = _detect_trace_mapping(channel_data, bases, peak_indices)

    sample_name = raw.get("SPNM1", "") or ""
    if isinstance(sample_name, list):
        sample_name = ""

    return {
        "bases": bases,
        "quality": quality[: len(bases)],
        "trace": trace,
        "peak_indices": peak_indices,
        "sample_name": str(sample_name),
        "lane": raw.get("LANE1", raw.get("LANE", 0)),
        "dye": str(raw.get("DLAB1", "") or ""),
    }


def _extract_read_internal(data: bytes) -> Dict:
    parsed = parse_abif(data)
    records = parsed["records"]

    pbas = records.get(("PBAS", 2)) or records.get(("PBAS", 1))
    if pbas is None:
        raise AbiParseError("文件缺少 PBAS2（碱基）记录")

    # PBAS2 官方为 type 2（char 数组）碱基；质量值在 PCON2
    if isinstance(pbas, str):
        bases = "".join(c for c in pbas if c in "ACGTN")
    elif isinstance(pbas, (bytes, bytearray)):
        bases = pbas.decode("ascii", errors="replace")
    else:
        bases = "".join(str(c) for c in pbas[:1])

    pcon = records.get(("PCON", 2)) or records.get(("PCON", 1)) or []
    if isinstance(pcon, (bytes, bytearray)):
        quality = list(pcon)[: len(bases)]
    elif isinstance(pcon, str):
        quality = [ord(c) for c in pcon][: len(bases)]
    else:
        quality = [int(q) for q in pcon][: len(bases)]

    # PCON2: peak 附近的质量/强度（此处提取峰强度供展示，缺省则跳过）
    channel_data: Dict[int, List[int]] = {}
    for (tag, num), values in records.items():
        if tag in ("DATA", "DYEP") and isinstance(values, (list, tuple)):
            channel_data.setdefault(num if tag == "DATA" else num + 8, list(values))

    ploc = records.get(("PLOC", 2), records.get(("PLOC", 1))) or []
    peak_indices = [int(p) - 1 for p in ploc[: len(bases)]]  # 1-based → 0-based

    trace = _detect_trace_mapping(channel_data, bases, peak_indices)

    sample_name = records.get(("SPNM", 1), "") or records.get(("LAbN", 1), "") or ""
    if isinstance(sample_name, list):
        sample_name = ""

    lane = records.get(("LANE", 1), 0)
    dye = records.get(("DLAB1", 1), "")

    return {
        "bases": bases,
        "quality": quality,
        "trace": trace,
        "peak_indices": peak_indices,
        "sample_name": str(sample_name),
        "lane": lane,
        "dye": str(dye) if not isinstance(dye, list) else "",
    }
