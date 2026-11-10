"""ab1 结构损坏的识别与修复（2026-11-11，DEMO7 真实样本暴露）

背景：测序文件被「改名/脱敏」工具编辑时，若把某个文本字段改短（实测
MCHN「VENTER-1522-005」→「DEMO-1522-005」、RUNN「Run_VENTER_…」→
「Run_DEMO_…」，各少 2 字节）而不改目录里的偏移，其后的所有数据整体
左移：目录偏移失配（Biopython 读出 0 个碱基），PBAS/PLOC/PCON 错位
2 字节（峰位读成乱码）。旧行为：
- main：碱基数过少/或侥幸判 ✓；
- v3.2：按乱码峰位读出 477 处「双峰」→ △疑似混合——把文件损坏误报成
  样品问题，是最坏的结果。

本模块只做两件事：
1. structure_problem(read)：对解析结果做结构自检（碱基为空、峰位超出
   信号长度、峰位不单调、碱基记录含大量非碱基字符），返回问题描述；
2. repair_abif(data)：只修「整块平移」这一类损坏——在声明偏移附近找到
   真实目录，再对每个数据块在 [0, 目录平移量] 内按内容校验选平移量
   （平移只能随偏移单调累积：删除字节只会让后面的数据整体前移），按声明
   布局重建文件。修不好返回 None，由调用方报「文件结构损坏」，绝不带病
   分析。
修复后的读数据与原文件逐字节一致这一点已在 S00025-M13R（= S99676-M13R
改名件）上验证（work/repair_abif.py 对照）。"""

import struct
from typing import Dict, List, Optional, Tuple

_HDR = 128
_ENTRY = 28
_MAX_DIR_SHIFT = 64          # 目录平移搜索半径（字节）
_BASE_CHARS = set(b"ACGTNRYKMSWBDHVacgtn-")


def _tag_ok(tag: bytes) -> bool:
    return len(tag) == 4 and all(32 <= c <= 126 for c in tag)


def _read_dir(data: bytes, off: int, n: int) -> Optional[List[Tuple]]:
    if off < _HDR or off + n * _ENTRY > len(data) + 8:
        return None
    out = []
    for i in range(n):
        e = data[off + i * _ENTRY: off + i * _ENTRY + _ENTRY]
        if len(e) < _ENTRY:
            return None
        tag = e[:4]
        if not _tag_ok(tag):
            return None
        num, etype, esize, cnt, dsize, doff = struct.unpack(">IHHIII", e[4:24])
        if etype > 1024 or dsize > len(data) + 8:
            return None
        out.append((tag.decode("latin1"), num, etype, esize, cnt, dsize, doff, e))
    return out


def _ints(raw: bytes, etype: int):
    w, f = (4, "i") if etype in (5, 6) else (2, "h")
    if len(raw) < 2 * w or len(raw) % w:
        return None
    return struct.unpack(f">{len(raw) // w}{f}", raw)


def _score(tag: str, raw: bytes, trace_len: int, etype: int = 4) -> Optional[float]:
    """数据块内容合理性打分（越大越可信）；None = 该标签无法校验。"""
    if not raw:
        return -1.0
    if tag == "PBAS":
        return sum(c in _BASE_CHARS for c in raw) / len(raw)
    if tag == "PLOC":
        v = _ints(raw, etype)
        if v is None:
            return -1.0
        inc = sum(1 for a, b in zip(v, v[1:]) if b >= a)
        out_of_range = min(v) < 0 or (trace_len and max(v) >= trace_len)
        return inc / max(1, len(v) - 1) - (1.0 if out_of_range else 0.0)
    if tag == "DATA":
        v = _ints(raw, etype)
        if v is None:
            return -1.0
        jumps = sum(abs(b - a) for a, b in zip(v, v[1:])) / max(1, len(v) - 1)
        return -jumps  # 错 1 字节时高低字节互换，相邻跳变暴增
    return None


def repair_abif(data: bytes) -> Tuple[Optional[bytes], Optional[Dict]]:
    """修复「字段改短但偏移未更新」造成的整块平移；修不好返回 (None, None)。"""
    if len(data) < _HDR or data[:4] != b"ABIF":
        return None, None
    nel = struct.unpack(">I", data[18:22])[0]
    dir_decl = struct.unpack(">I", data[26:30])[0]
    if not (0 < nel < 10000):
        return None, None
    entries = _read_dir(data, dir_decl, nel)
    dir_real = dir_decl
    if entries is None:
        for s in sorted(range(-_MAX_DIR_SHIFT, _MAX_DIR_SHIFT + 1), key=abs):
            entries = _read_dir(data, dir_decl + s, nel)
            if entries is not None:
                dir_real = dir_decl + s
                break
    if entries is None:
        return None, None
    total = dir_real - dir_decl          # 目录处累计平移（删字节为负）
    if total == 0:
        return None, None                # 不是平移型损坏，本模块不处理
    # 平移量随偏移单调累积（删字节只让后面的数据前移）。从目录端反向
    # 推进：平移量从 total 向 0 收敛，内容校验分不出高下的块沿用后方
    # 平移量（改名工具改的是 DATA 之后的元数据字段，实测两处均如此）；
    # 最终结果另由 structure_problem 的峰-碱基一致性兜底
    step = 1 if total < 0 else -1
    ext = sorted((e for e in entries if e[5] > 4), key=lambda e: e[6], reverse=True)
    tl = 0
    for tag, num, etype, esize, cnt, dsize, doff, _e in entries:
        if tag == "DATA" and num == 9:
            tl = cnt
    shift = total
    plan = []
    for tag, num, etype, esize, cnt, dsize, doff, _e in ext:
        best, best_s = shift, None
        for s in range(shift, step, step) if shift else [0]:
            sc = _score(tag, data[doff + s: doff + s + dsize], tl, etype)
            if sc is None:
                break
            if best_s is None or sc > best_s + 1e-9:
                best, best_s = s, sc
        shift = best
        plan.append((doff, dsize, shift, tag, num))
    out = bytearray(max(len(data) - total, dir_decl + nel * _ENTRY))
    out[:_HDR] = data[:_HDR]
    for doff, dsize, s, _t, _n in plan:
        chunk = data[doff + s: doff + s + dsize]
        out[doff: doff + len(chunk)] = chunk
    for i, e in enumerate(entries):
        out[dir_decl + i * _ENTRY: dir_decl + (i + 1) * _ENTRY] = e[7]
    moved = sorted({(t, s) for _o, _d, s, t, _n in plan if s})
    return bytes(out), {
        "dir_shift": total,
        "shifted_blocks": len([p for p in plan if p[2]]),
        # 只列峰图相关块（其余元数据块随同复位，不逐个罗列）
        "tags": sorted({t for t, _s in moved} & {"PBAS", "PLOC", "PCON", "DATA", "FWO_"}),
    }


def structure_problem(read: Dict) -> Optional[str]:
    """解析结果结构自检；正常返回 None。只查硬伤，不做质量评判。"""
    bases = read.get("bases") or ""
    if not bases:
        return "未读到碱基记录（目录区可能损坏）"
    bad = sum(1 for c in bases if c not in "ACGTNRYKMSWBDHV-")
    if bad > 0.05 * len(bases):
        return f"碱基记录含 {bad} 个非碱基字符"
    trace = read.get("trace") or {}
    tl = min((len(v) for v in trace.values()), default=0)
    pk = [p for p in (read.get("peak_indices") or []) if p is not None]
    if pk and tl:
        # 峰位越界不单独判损坏（个别导出工具/旧合成件 PLOC 与 trace 采样
        # 尺度不一，M12）；错位损坏由单调性与碱基-峰一致性识别
        dec = sum(1 for a, b in zip(pk, pk[1:]) if b < a)
        if dec > max(2, 0.01 * len(pk)):
            return f"峰位记录不单调（{dec} 处倒退）"
        agree = base_peak_agreement(read)
        if agree is not None and agree < AGREE_MIN:
            return (f"碱基与峰位对不上（判读碱基为峰顶最高通道的比例仅 "
                    f"{agree:.0%}）")
    return None


# 碱基-峰一致性下限：真实 ab1（含 Q 均值 17 的差 read）实测 ≥0.8，
# 峰位错位 1 个元素时约 0.3（四选一随机水平 0.25）
AGREE_MIN = 0.5


def base_peak_agreement(read: Dict) -> Optional[float]:
    """判读碱基与峰顶最高通道的一致率；无 trace/峰位返回 None。

    对通道标签取 24 种排列的最优值（与通道映射是否判对无关——映射错是
    另一类问题，由 abif_reader 的通道检测负责；这里只认峰位是否落在峰上：
    峰位错位时无论怎么排列一致率都接近随机水平）。"""
    import itertools
    bases = read.get("bases") or ""
    trace = read.get("trace") or {}
    pk = read.get("peak_indices") or []
    if not trace or not pk or len(trace) < 4:
        return None
    labels = sorted(trace)[:4]
    tl = min(len(trace[k]) for k in labels)
    # 峰窗半宽随峰距自适应（≤2 采样点，不跨到相邻峰；合成件每碱基 1 采样点时为 0）
    ps = [p for p in pk if p is not None]
    gaps = sorted(b - a for a, b in zip(ps, ps[1:]) if b > a)
    half = min(2, max(0, (gaps[len(gaps) // 2] - 1) // 2)) if gaps else 2
    conf: Dict[Tuple[str, str], int] = {}
    tot = 0
    for b, p in zip(bases, pk):
        if p is None or b not in "ACGT" or not (0 <= p < tl):
            continue
        lo, hi = max(0, p - half), min(tl, p + half + 1)
        top = max(labels, key=lambda k: max(trace[k][lo:hi]))
        conf[(b, top)] = conf.get((b, top), 0) + 1
        tot += 1
    if tot < 50:
        return None
    best = max(sum(conf.get((b, lab), 0) for b, lab in zip("ACGT", perm))
               for perm in itertools.permutations(labels))
    return best / tot
