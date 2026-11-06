"""统一 read↔参考坐标映射（goal T9；A 2.2；B P1-12）

ReadRefMap 是 read 坐标换算的唯一定义：参考区间 → read 峰窗、参考位点 →
引物内坐标等换算一律经它，不再各自线性推算——上游 indel、首尾软剪切、
反向镜像任一存在时，`ref_start ± 线性偏移` 都会错位。

列遍历口径以 _read_ref_maps 为准（T10 自 homopolymer 迁入本模块——
坐标映射是 readmap 的职责本位）：软剪切经 query_start/query_end 修正、
反向 read 电泳坐标 = query_end - 列内序号、环状跨原点折回 1..L。变体
read_pos（aligner）已镜像回电泳链并含同样修正，与 ref2call 的 read_pos
同帧（M10 契约）。后续任务的坐标换算一律走本模块。
"""

from typing import Dict, Optional, Tuple

from core.sanger.aligner import fold_pos


class ReadRefMap:
    """单条 read 的两张坐标表 + 换算入口。

    - read2ref: 电泳 read 坐标(1-based, 修剪后) -> 参考坐标（插入列不入表）
    - ref2call: 参考坐标 -> {base, q, read_pos}（read_pos 为电泳链 1-based）
    """

    __slots__ = ("read2ref", "ref2call", "direction", "trim_len", "aln")

    def __init__(self, read2ref: Dict[int, int], ref2call: Dict[int, Dict],
                 direction: str, trim_len: int, aln: Optional[Dict] = None):
        self.read2ref = read2ref
        self.ref2call = ref2call
        self.direction = direction or "+"
        self.trim_len = trim_len
        self.aln = aln or {}

    @classmethod
    def from_alignment(cls, r: Dict, fold: bool = True) -> "ReadRefMap":
        """从 analyze 的单 read 结果构建（列遍历口径 = _read_ref_maps）。

        fold=True（默认）参考坐标折回 1..L（环状跨原点 read 的常规查询）；
        fold=False 保留展开坐标（单调，供沿参考线性扫描的调用方）。
        旧记录无 aligned 视图时两张表为空——调用方按空表回退。"""
        r2ref, ref2call = _read_ref_maps(r, fold=fold)
        aln = r.get("alignment") or {}
        return cls(r2ref, ref2call, aln.get("direction", "+"),
                   len(r.get("trimmed_bases") or ""), aln)

    def ref_to_read(self, ref_pos: int) -> Optional[int]:
        """参考坐标 → 电泳链 read 坐标（1-based，修剪后帧）；无对齐列 None"""
        ent = self.ref2call.get(ref_pos)
        return ent["read_pos"] if ent else None

    def read_to_ref(self, read_pos: int) -> Optional[int]:
        """电泳链 read 坐标 → 参考坐标（环状折回后）；插入列/无映射 None"""
        return self.read2ref.get(read_pos)

    def primer_coord(self, ref_pos: int) -> Optional[int]:
        """参考位点 → 引物内坐标（电泳链 1-based，修剪后帧）。

        Sanger 电泳自引物 3′ 端延伸，电泳序即引物内序；反向 read 的
        read_pos 已由 aligner 镜像回电泳链（含软剪切修正），因此本表与
        ref_to_read 同映射——独立命名是为了让调用方表达「引物内」语义。
        旧的 `trim_len - read_pos + 1` 是镜像时代的二次折算（M10）。"""
        return self.ref_to_read(ref_pos)

    def run_window(self, cs: int, ce: int) -> Optional[Tuple[int, int]]:
        """参考区间 [cs,ce] 对应的电泳峰窗（0-based 端点）；区间内无对齐
        列时 None。峰表按电泳序存放，0-based = read_pos - 1，正反 read
        同帧（峰窗调用方不再区分方向）。环状 read 请传折回后的坐标
        （ref2call 键即 1..L 折回口径）。"""
        ps = [e["read_pos"] for p, e in self.ref2call.items()
              if cs <= p <= ce and e.get("read_pos")]
        if not ps:
            return None
        return min(ps) - 1, max(ps) - 1


def _circ_len(aln: Dict) -> int:
    """环状比对的参考长度（线性比对返回 0：坐标不折回）"""
    return int(aln.get("ref_length") or 0) if aln.get("circular") else 0


def _read_ref_maps(r: Dict, fold: bool = True) -> Tuple[Dict[int, int], Dict[int, Dict]]:
    """单条 read 的两张坐标映射（基于 alignment_view 的参考方向逐列视图）：

    - read2ref: 原始电泳 read 坐标(1-based, 修剪后) -> 参考坐标(1-based)；
      插入列无参考坐标，不入表
    - ref2call: 参考坐标 -> {base, q, read_pos}（base 为参考方向判读碱基，
      q 为该列 Q 值；同位点多次比对列取首列）

    反向 read 的 query 是 revcomp：query 列索引 qi ↔ 原始电泳坐标 n-qi
    （1-based，与 aligner 的 read_pos 镜像同式），mixed_detail 的 pos 即
    原始电泳坐标，经此表即可与参考坐标互换。

    终审 A-20：局部比对会把 read 端部的 junk/低质量段软剪切掉——对齐内
    相对列号不是 trimmed read 坐标，必须加上 query_start 偏移（aligner
    已输出对齐块在原始 query 内的 1-based 起止）。

    环状参考跨原点的 read：逐列视图沿展开坐标（可 > L）递增，fold=True
    时两张表的参考坐标折回 1..L；fold=False 保留展开坐标（单调，供
    线性内插用）。"""
    aligned = (r.get("alignment") or {}).get("aligned") or {}
    ref_s = (aligned.get("ref_aligned") or "").upper()
    read_s = (aligned.get("read_aligned") or "").upper()
    if not ref_s or len(ref_s) != len(read_s):
        return {}, {}
    aln = r.get("alignment") or {}
    L = _circ_len(aln) if fold else 0
    direction = aln.get("direction", "+")
    n = len(r.get("trimmed_bases") or "")
    q_aligned = aligned.get("q_aligned") or []
    ref_pos = int(aligned.get("ref_start") or aln.get("ref_start") or 1)
    # 对齐块在原始（修剪后）read 内的 1-based 起点：正向 = query_start；
    # 反向 read 的 query 是 revcomp，对齐块第 0 列对应原始电泳的 query_end 位置
    qs = aln.get("query_start")
    qe = aln.get("query_end")
    if qs is not None and qe is not None:
        block_first = int(qs) if direction == "+" else int(qe)
    else:
        block_first = 1  # 旧记录回退（无软剪切时恰为 1）
    read2ref: Dict[int, int] = {}
    ref2call: Dict[int, Dict] = {}
    qi = -1
    for i, rb in enumerate(ref_s):
        qb = read_s[i]
        if qb != "-":
            qi += 1
            # 原始电泳 1-based 坐标：正向从 block_first 递增；
            # 反向 read 对齐块第 0 列位于 block_first（=query_end），向左递减
            orig = (block_first - qi) if direction == "-" else (block_first + qi)
        else:
            orig = None
        if rb != "-":
            if qb != "-" and orig is not None:
                rp = fold_pos(ref_pos, L) if L else ref_pos
                read2ref[orig] = rp
                if rp not in ref2call:
                    ref2call[rp] = {
                        "base": qb,
                        "q": q_aligned[i] if i < len(q_aligned) else 0,
                        "read_pos": orig,
                    }
            ref_pos += 1
    return read2ref, ref2call
