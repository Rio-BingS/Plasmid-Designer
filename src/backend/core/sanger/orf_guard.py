"""ORF 校正护栏（goal T16；A 4.3）

CDS 特征边界常有 1-3bp 标注偏差（如起点多含上游碱基），直接按标注起点
取框翻译会整体错位、连起始密码子判定都失效——自动对齐区间内最佳 ORF
是必要的。但旧 _find_orf **自由**挑选区间内最长 ORF：标注起点差 128bp
（整个起点标错到另一读框）时会被「校正」成另一个 ORF，反而掩盖标注
错误本身（A 4.3）。

护栏：

- 校正只允许「同读框内、标注边界 ±3bp」（校正后长度仍是 3 的倍数）；
  超出 → 保留原标注并给 suspect（调用方记 orf_annotation_suspect 未决
  位点，verdict pending——标注需要人工确认，而不是被静默改写）；
- 起始密码子：GenBank /transl_table=1（标准表，真核核基因）仅 ATG；
  未给出或细菌表（11 等）按宿主惯例允许 GTG/TTG（质粒载体默认原核）；
- GenBank /codon_start（1/2/3）给出时仅在指定读框内枚举；reference_parser
  随特征下发，未给出时三框枚举。
"""

from typing import Dict, List, Optional, Tuple

from core.seq_utils import revcomp as _seq_revcomp

MAX_ANNOT_SHIFT = 3   # 起点/终点允许的标注偏差（同读框内）
STOP_CODONS = {"TAA", "TAG", "TGA"}
EUKARYOTIC_TABLES = {1}   # transl_table=1 标准表（真核核基因）
PROK_STARTS = ("ATG", "GTG", "TTG")
EUK_STARTS = ("ATG",)


def allowed_starts(transl_table: Optional[int]) -> Tuple[str, ...]:
    """宿主允许的起始密码子：真核标准表仅 ATG，原核（含缺省）加 GTG/TTG"""
    if transl_table is not None and transl_table in EUKARYOTIC_TABLES:
        return EUK_STARTS
    return PROK_STARTS


def find_guarded_orf(ref: str, start: int, end: int, strand: str,
                     transl_table: Optional[int] = None,
                     codon_start: Optional[int] = None) -> Optional[Dict]:
    """特征区间 [start,end]（1-based 闭区间）内最佳 ORF + 校正护栏。

    返回 {"orf_start", "orf_end", "suspect"}：

    - 校正在护栏内（边界偏差 ≤MAX_ANNOT_SHIFT 且同读框）→ 校正后坐标、
      suspect=False（与旧口径一致，1-3bp 偏差照常自动对齐）；
    - 最长 ORF 超出护栏 → 保留原标注、suspect=True（标注疑点，人工确认）；
    - 区间内无可锚定 ORF → None（沿用原标注，无从校正也不怀疑）。
    """
    win = ref[start - 1:end]
    if strand == "-":
        win = _seq_revcomp(win)
    N = len(win)
    starts = allowed_starts(transl_table)
    best: Optional[Tuple[tuple, int, int]] = None  # (score, a, b) win 内 0-based 半开 [a,b)

    def consider(a: int, b: int, has_stop: bool):
        nonlocal best
        atg = win[a:a + 3] == "ATG"
        # 长度主导：真实 CDS 通常是区间内最长开放框，防止随机短完整 ORF 反超
        score = (b - a, atg, has_stop)
        if best is None or score > best[0]:
            best = (score, a, b)

    frames = (range(3) if codon_start not in (1, 2, 3)
              else [int(codon_start) - 1])   # codon_start 1/2/3 → 读框 0/1/2
    for frame in frames:
        i = frame
        while i + 3 <= N:
            if win[i:i + 3] in starts:
                j = i
                stop_found = False
                while j + 3 <= N:
                    if win[j:j + 3] in STOP_CODONS:
                        consider(i, j + 3, True)
                        stop_found = True
                        break
                    j += 3
                if not stop_found:
                    consider(i, N, False)  # 有起始但区间内无终止：开放框
            i += 3
    if best is None:
        return None
    _, a, b = best
    if strand == "-":
        # 反向互补空间 [a,b) → 参考坐标：位置 i（0-based）对应参考 end-i（1-based）
        orf_start, orf_end = end - b + 1, end - a
    else:
        orf_start, orf_end = start + a, start + b - 1
    if (orf_start, orf_end) == (start, end):
        return {"orf_start": orf_start, "orf_end": orf_end, "suspect": False}
    # 护栏：两端偏差 ≤3bp（1-3bp 的边界标注偏差照常校正——起点多含上游
    # 碱基本身就是 1bp 级读框平移），且校正后区间是完整读框（长度为 3 的
    # 倍数）。超出 → 保留原标注、标疑
    guarded = (abs(orf_start - start) <= MAX_ANNOT_SHIFT
               and abs(orf_end - end) <= MAX_ANNOT_SHIFT
               and (orf_end - orf_start + 1) % 3 == 0)
    if guarded:
        return {"orf_start": orf_start, "orf_end": orf_end, "suspect": False}
    # 超出护栏：保留原标注，标疑（A 4.3——标注错误不该被静默改写）
    return {"orf_start": start, "orf_end": end, "suspect": True}


def suspect_site(feat_start: int, feat_end: int, name: str = "") -> Dict:
    """护栏拦截时的未决位点记录（kind=orf_annotation_suspect，verdict pending）"""
    return {
        "ref_pos": int(feat_start),
        "kind": "orf_annotation_suspect",
        "reads": [],
        "detail": (f"CDS {name} 标注 {feat_start}-{feat_end} 与区间内最佳 ORF "
                   f"偏差超出 ±{MAX_ANNOT_SHIFT}bp（同读框）护栏，保留原标注"
                   "——请核对特征边界或起始密码子"),
    }
