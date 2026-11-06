"""split-read 二次定位（goal T19；A 2.1/2.3）

端部被软剪切的高质量段（check_unaligned_hq_segment，T6）只知道「没比对上」，
不知道「去了哪」。连接错误/质粒二聚体/载体拼接的场景里，剪下的段往往来自
参考的**另一处**（甚至另一条链、跨原点）——split-read 对该段做独立局部比对：

- 命中 → 结构断点：段落点即断点坐标（主比对位置 ≠ 段落点 = 连接异常）；
- 未命中 → 未知序列：段在参考上不存在（载体污染/引物错配扩增产物），
  detail 记「未知序列」。

比对复用 aligner 的 local 模式（同一套打分：match +2 / mismatch −4 /
gap −6/−2）与环状延长（align_read_circular）；方向自动判（− 链段的电泳
顺序序列会在 revcomp 上命中，落点折回正向参考坐标）。

测量记录（goal「先测量再改」）：六组合成压缩/低 Q/滑移/错配簇场景 180 条
read + MX 真实 3 条 read，比对落点偏差 >25bp 的错位率 0.0（B 审计「比对层
没有假错配雪崩」复证）——Q 加权打分（mismatch = −4 × min(1, Q/30)）已评估
无需改动，本模块不做 Q 加权。
"""

from typing import Dict

from core.sanger.aligner import align_read, align_read_circular

# 命中下限：段内比对覆盖 ≥80% 且身份率 ≥85%（短段随机命中的身份率远低）
MIN_IDENTITY = 0.85
MIN_COVER = 0.8
# 过短段不做二次定位（随机命中风险高、信息量低）
MIN_SEGMENT_BP = 8


def classify_segment(seg_seq: str, ref: str, circular: bool = False) -> Dict:
    """端部未比对段的 split-read 定位。

    返回 {"status": "breakpoint"|"unknown", ...}：

    - breakpoint：ref_start/ref_end 为段落点（正向参考 1-based 闭区间）、
      direction 为段相对参考的链（电泳顺序段的命中链自动判）、identity/cover
      为命中质量、wraps_origin 标明落点跨原点（环状）；
    - unknown：参考上无可信落点。
    """
    seg = (seg_seq or "").upper()
    if len(seg) < MIN_SEGMENT_BP or not ref:
        return {"status": "unknown"}
    best = None  # ((identity, cover), aln, wraps)
    for circ in ((False, True) if circular else (False,)):
        aln = align_read_circular(seg, ref) if circ else align_read(seg, ref)
        if int(aln.get("ref_end") or 0) <= 0:
            continue
        identity = aln.get("identity") or 0.0
        cover = aln.get("aligned_read_len", 0) / len(seg)
        if identity < MIN_IDENTITY or cover < MIN_COVER:
            continue
        key = (identity, cover)
        if best is None or key > best[0]:  # 平分先到先得（线性命中优先）
            best = (key, aln, bool(circ and aln.get("wraps_origin")))
    if best is None:
        return {"status": "unknown"}
    _, aln, wraps = best
    return {
        "status": "breakpoint",
        "ref_start": int(aln["ref_start"]),
        "ref_end": int(aln["ref_end"]),
        "direction": aln.get("direction"),
        "identity": aln.get("identity"),
        "cover": round(aln.get("aligned_read_len", 0) / len(seg), 3),
        "wraps_origin": wraps,
    }
