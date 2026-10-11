"""Sanger read 与参考序列比对 — Biopython PairwiseAligner

每条 read 与参考序列正向/反向互补各比一次（局部比对），取更高分者判定方向，
由比对坐标推导错配 / 插入 / 缺失列表。
"""

import threading
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

import numpy as np
from Bio.Align import PairwiseAligner

from core.seq_utils import revcomp  # noqa: F401  # 共享实现；测试与 pipeline 亦从此处导入

_ALIGNER = None
_ACGT = frozenset("ACGT")


def _get_aligner() -> PairwiseAligner:
    global _ALIGNER
    if _ALIGNER is None:
        al = PairwiseAligner(mode="local")
        # mismatch 罚分高于连续 indel 代价，保证 ≥3bp 插缺能以 gap 形式检出
        al.open_gap_score = -6
        al.extend_gap_score = -2
        al.match_score = 2
        al.mismatch_score = -4
        _ALIGNER = al
    return _ALIGNER


# ---------------------------------------------------------------------------
# 种子-延伸（审计 #6）：大参考上先用 k-mer 投票定位对角线，再用同一个
# PairwiseAligner（同一套打分）只比对对角线附近的窗口。整条参考的局部比对
# 时间与回溯内存都是 O(参考 × read)，200 kb 参考单条 read 峰值 RSS 约 0.7 GB。
#
# 结果与整条比对逐字段一致（差分测试覆盖），依据是下面的计数下界：
# 打分 match +2 / mismatch -4 / gap 首位 -6、后续 -2。得分 >= S、匹配 M 列的
# 局部比对，其 mismatch 与 gap 事件总数 d <= (2M - S) / 4，匹配列被切成至多
# d + 1 段，故精确 k-mer 命中数 >= M - (d + 1)(k - 1)；gap 总长 <= (2M - S) / 2，
# 即全部命中落在宽 W = (2n - S) / 2 的对角线带内。对 M ∈ [S/2, n] 取最小得 H：
# 任何得分 >= S 的比对（含同分）必在某条宽 W 的带内留下 >= H 个命中。
# 因此把命中数 >= H 的带全部开窗比对过，窗口内的最高分即全局最高分；
# 同分时 Biopython 按终点在参考上的先后取第一条，窗口互不重叠、按参考
# 坐标先后取同分者即与整条比对同序。H < 1、候选带过多、命中过多、参考
# 含与 read 共有的非 ACGT 字符或窗口贴边时，一律回退整条比对。
# ---------------------------------------------------------------------------

SEED_K = 12
SEED_MIN_REF_BP = 20000      # 小参考整条比对本就便宜，直接走原路径
SEED_MARGIN = 300            # 窗口在对角线带两侧的最小外扩
SEED_MAX_WINDOWS = 3         # 每个方向最多比对的候选窗口（重复序列取前 2-3 条）
SEED_MAX_HITS = 200000       # 低复杂度参考命中爆炸时回退
_INDEX_CACHE_SIZE = 4

_CODE = np.full(256, 255, dtype=np.uint8)
for _i, _c in enumerate(b"ACGT"):
    _CODE[_c] = _i


def _kmer_codes(seq: str, k: int) -> Tuple[np.ndarray, np.ndarray]:
    """seq 全部 k-mer 的 2-bit 编码与起点；含非 ACGT 字符的 k-mer 剔除"""
    n = len(seq) - k + 1
    if n <= 0:
        return np.zeros(0, dtype=np.uint32), np.zeros(0, dtype=np.int64)
    b = _CODE[np.frombuffer(seq.encode("ascii", "replace"), dtype=np.uint8)]
    bad = np.concatenate(([0], np.cumsum(b == 255)))
    ok = (bad[k:] - bad[:-k]) == 0
    v = np.where(b == 255, 0, b).astype(np.uint32)
    codes = np.zeros(n, dtype=np.uint32)
    for j in range(k):
        codes = (codes << np.uint32(2)) | v[j:j + n]
    pos = np.nonzero(ok)[0]
    return codes[pos], pos


class _KmerIndex:
    """参考 k-mer 索引：按编码排序的 (code, pos) 数组，numpy 实现约 8 B/bp"""

    __slots__ = ("codes", "pos", "chars")

    def __init__(self, seq: str, k: int = SEED_K):
        codes, pos = _kmer_codes(seq, k)
        order = np.argsort(codes, kind="stable")
        self.codes = codes[order]
        self.pos = pos[order].astype(np.int64)
        self.chars = frozenset(seq)


_INDEX_CACHE: "OrderedDict[object, _KmerIndex]" = OrderedDict()
_INDEX_LOCK = threading.Lock()


def _index_for(key, target: str, seq: Optional[str] = None
               ) -> Optional[Tuple[_KmerIndex, int]]:
    """取（必要时构建）参考索引，返回 (索引, 有效长度)；小参考返回 None

    同一参考在一批 read 间只建一次（LRU 缓存，键为参考序列本身）。"""
    if len(target) < SEED_MIN_REF_BP:
        return None
    with _INDEX_LOCK:
        idx = _INDEX_CACHE.get(key)
        if idx is not None:
            _INDEX_CACHE.move_to_end(key)
            return idx, len(target)
    idx = _KmerIndex(seq if seq is not None else target)
    with _INDEX_LOCK:
        _INDEX_CACHE[key] = idx
        _INDEX_CACHE.move_to_end(key)
        while len(_INDEX_CACHE) > _INDEX_CACHE_SIZE:
            _INDEX_CACHE.popitem(last=False)
    return idx, len(target)


def _seed_hits(query: str, index: _KmerIndex, limit: int, k: int = SEED_K
               ) -> Optional[np.ndarray]:
    """query 的精确 k-mer 命中，返回按大小排序的对角线 (ref_pos - q_pos)"""
    qc, qp = _kmer_codes(query, k)
    if qc.size == 0:
        return np.zeros(0, dtype=np.int64)
    lo = np.searchsorted(index.codes, qc, side="left")
    hi = np.searchsorted(index.codes, qc, side="right")
    cnt = hi - lo
    total = int(cnt.sum())
    if total > SEED_MAX_HITS:
        return None
    if total == 0:
        return np.zeros(0, dtype=np.int64)
    starts = np.repeat(lo - np.concatenate(([0], np.cumsum(cnt)[:-1])), cnt)
    rpos = index.pos[starts + np.arange(total)]
    qpos = np.repeat(qp, cnt)
    keep = rpos + k <= limit
    return np.sort(rpos[keep] - qpos[keep])


def _min_seed_hits(n: int, score: float, k: int = SEED_K) -> int:
    """得分 >= score 的比对在其对角线带内至少留下的 k-mer 命中数（见上文）"""
    best = None
    m0 = max(0, int(np.ceil(score / 2)))
    for m in range(m0, n + 1):
        d = int((2 * m - score) // 4)
        h = m - (d + 1) * (k - 1)
        if best is None or h < best:
            best = h
    return best if best is not None else 0


def _dense_bands(diags: np.ndarray, width: int, need: int) -> List[Tuple[int, int]]:
    """命中数 >= need 的宽 width 对角线带，合并后返回 [(最小对角线, 最大对角线)]"""
    if diags.size == 0 or need > diags.size:
        return []
    ends = np.searchsorted(diags, diags + width, side="right")
    counts = ends - np.arange(diags.size)
    bands: List[Tuple[int, int]] = []
    for i in np.nonzero(counts >= need)[0]:
        lo, hi = int(diags[i]), int(diags[ends[i] - 1])
        if bands and lo <= bands[-1][1]:
            bands[-1] = (bands[-1][0], max(bands[-1][1], hi))
        else:
            bands.append((lo, hi))
    return bands


def _densest_band(diags: np.ndarray, width: int) -> Optional[Tuple[int, int]]:
    if diags.size == 0:
        return None
    ends = np.searchsorted(diags, diags + width, side="right")
    i = int(np.argmax(ends - np.arange(diags.size)))
    return int(diags[i]), int(diags[ends[i] - 1])


def _align_seeded(ref_up: str, queries, index_info) -> Optional[Tuple]:
    """窗口化比对；无法保证与整条比对一致时返回 None（调用方回退）"""
    index, limit = index_info
    if limit != len(ref_up):
        return None
    aligner = _get_aligner()
    n_max = max(len(q) for _, q in queries)
    if n_max < SEED_K:
        return None
    # 参考与 read 共有的非 ACGT 字符（如 N 对 N）在打分里算匹配却不进索引，
    # 下界不再成立
    extra = index.chars - _ACGT
    if extra and any(extra & set(q) for _, q in queries):
        return None

    hits = {}
    for direction, query in queries:
        h = _seed_hits(query, index, limit)
        if h is None:
            return None
        hits[direction] = h

    # windows[direction] = [(ws, we, score, coords)]，同方向窗口互不重叠
    windows: Dict[str, List[Tuple[int, int, float, Optional[np.ndarray]]]] = {
        d: [] for d, _ in queries}
    qmap = dict(queries)

    def run_window(direction: str, ws: int, we: int) -> bool:
        lst = windows[direction]
        # 与已比对窗口重叠则合并为一个更大的窗口重比（保持同分时的先后顺序）
        for w in list(lst):
            if ws <= w[1] and w[0] <= we:
                ws, we = min(ws, w[0]), max(we, w[1])
                lst.remove(w)
        if len(lst) >= SEED_MAX_WINDOWS:
            return False
        if 2 * (we - ws) > len(ref_up):
            return False
        alns = aligner.align(ref_up[ws:we], qmap[direction])
        if len(alns) == 0:
            lst.append((ws, we, float("-inf"), None))
        else:
            a = alns[0]
            coords = np.array(a.coordinates, dtype=np.int64)
            # 防御：比对贴到非参考端点的窗口边界 → 可能被窗口截断
            if (coords[0][0] == 0 and ws > 0) or (coords[0][-1] == we - ws and we < len(ref_up)):
                return False
            coords[0] += ws
            lst.append((ws, we, float(a.score), coords))
        lst.sort(key=lambda w: w[0])
        return True

    def window_for(band: Tuple[int, int], n: int, width: int) -> Tuple[int, int]:
        margin = max(SEED_MARGIN, 2 * width) + 2 * SEED_K
        return max(0, band[0] - margin), min(len(ref_up), band[1] + n + margin)

    def covered(direction: str, ws: int, we: int) -> bool:
        return any(w[0] <= ws and we <= w[1] for w in windows[direction])

    # 第一轮：每个方向取命中最密的带开窗，得到当前最高分 S
    for direction, query in queries:
        band = _densest_band(hits[direction], 64)
        if band is not None:
            ws, we = window_for(band, len(query), 64)
            if not run_window(direction, ws, we):
                return None
    for _ in range(2 * SEED_MAX_WINDOWS + 2):
        scores = [w[2] for lst in windows.values() for w in lst]
        if not scores or max(scores) <= 0:
            return None
        s_best = max(scores)
        pending = []
        for direction, query in queries:
            n = len(query)
            need = _min_seed_hits(n, s_best)
            if need < 1:
                return None  # 种子投票太弱，无法排除无命中的竞争比对
            width = max(0, int((2 * n - s_best) // 2))
            for band in _dense_bands(hits[direction], width, need):
                ws, we = window_for(band, n, width)
                if not covered(direction, ws, we):
                    pending.append((direction, ws, we))
        if not pending:
            break
        for direction, ws, we in pending:
            if not run_window(direction, ws, we):
                return None
    else:
        return None

    best = None
    for direction, query in queries:
        cand = [w for w in windows[direction] if w[3] is not None]
        if not cand:
            continue
        top = max(w[2] for w in cand)
        # 同分取参考上最靠前的窗口（Biopython 按终点先后枚举局部比对）
        w = next(w for w in cand if w[2] == top)
        if best is None or top > best[0]:
            best = (w[2], direction, query, w[3])
    return best


def align_read(read: str, reference: str, quality: Optional[List[int]] = None) -> Dict:
    """将 read（自动判向）比对到参考序列

    返回 {direction: '+'/'-', ref_start, ref_end, score, identity, variants, aligned}
    variants: [{ref_pos(1-based), read_pos, type: substitution|insertion|deletion,
                ref_base, alt_base, length}]
    ambiguous: 对齐列中 read 为非 ACGT 调用（N/IUPAC）的列数——这些列不计入
    identity 的分子分母，也不产出 substitution（basecaller 没给出确定碱基，
    不构成「与参考不同」的证据）。
    坐标为正向参考链 1-based；insertion 的 ref_pos 为插入点左侧参考位置。
    aligned 为逐列对齐视图（供前端人工核对）：ref_aligned/read_aligned 等长字符串，
    '-' 表示该列在对方序列中缺失（插入/缺失）；read 一律以参考方向展示（反向
    read 显示反向互补碱基）；q_aligned 为逐列 Q 值（gap 列为 0，反向 read 的
    Q 顺序随碱基一起反转，与原始 read 质量一一对应）。
    """
    ref_up = reference.upper()
    return _align_impl(read.upper(), ref_up, quality, _index_for(ref_up, ref_up))


def _align_full(ref_up: str, queries) -> Optional[Tuple]:
    """原路径：两个方向各对整条参考做局部比对，'+' 优先、'-' 须严格更高分"""
    aligner = _get_aligner()
    best = None  # (score, direction, query, coords)
    for direction, query in queries:
        alignments = aligner.align(ref_up, query)
        if len(alignments) == 0:
            continue
        aln = alignments[0]
        score = aln.score
        if best is None or score > best[0]:
            best = (score, direction, query, aln.coordinates)
    return best


def _align_impl(read_up: str, ref_up: str, quality: Optional[List[int]],
                index: Optional[Tuple["_KmerIndex", int]]) -> Dict:
    queries = (("+", read_up), ("-", revcomp(read_up)))
    best = None
    if index is not None:
        best = _align_seeded(ref_up, queries, index)
    if best is None:
        best = _align_full(ref_up, queries)

    if best is None:
        return {"direction": "+", "ref_start": 0, "ref_end": 0, "score": 0.0,
                "identity": 0.0, "variants": [], "ambiguous": 0,
                "aligned": {"ref_start": 0, "ref_aligned": "", "read_aligned": "", "q_aligned": []}}

    score, direction, query, coords = best
    # coordinates: [[ref块起,ref块止,...],[read块起,read块止,...]]
    variants: List[Dict] = []
    matches = 0
    compared = 0
    ambiguous = 0

    ref_chars: List[str] = []
    read_chars: List[str] = []
    read_qi: List[int] = []  # 每列 read 碱基在 query 内的索引；gap 列为 -1

    for k in range(coords.shape[1] - 1):
        rs, re_ = int(coords[0][k]), int(coords[0][k + 1])
        qs, qe = int(coords[1][k]), int(coords[1][k + 1])
        ref_block = ref_up[rs:re_]
        read_block = query[qs:qe]
        if rs == re_ or qs == qe:
            # 纯插入或纯缺失
            if rs == re_:  # read 有而参考无 → insertion
                variants.append({
                    "ref_pos": max(1, rs),  # 1-based：插入点左侧参考位置
                    "read_pos": qs + 1,
                    "type": "insertion",
                    "ref_base": "-",
                    "alt_base": read_block,
                    "length": len(read_block),
                })
                ref_chars.extend("-" * len(read_block))
                read_chars.extend(read_block)
                read_qi.extend(range(qs, qe))
            else:  # 参考有而 read 无 → deletion
                variants.append({
                    "ref_pos": rs + 1,
                    "read_pos": qs + 1,
                    "type": "deletion",
                    "ref_base": ref_block,
                    "alt_base": "-",
                    "length": len(ref_block),
                })
                ref_chars.extend(ref_block)
                read_chars.extend("-" * len(ref_block))
                read_qi.extend([-1] * len(ref_block))
            continue
        for i, (rb, qb) in enumerate(zip(ref_block, read_block)):
            ref_chars.append(rb)
            read_chars.append(qb)
            read_qi.append(qs + i)
            if qb not in _ACGT:
                # N/IUPAC 调用：不比较、不出变体（wildcard 只影响打分，
                # 必须在此处排除，否则每个 N 都成一条假 substitution）
                ambiguous += 1
                continue
            compared += 1
            if rb == qb:
                matches += 1
            else:
                variants.append({
                    "ref_pos": rs + i + 1,
                    "read_pos": qs + i + 1,
                    "type": "substitution",
                    "ref_base": rb,
                    "alt_base": qb,
                    "length": 1,
                })

    # 反向 read 的 query 是 revcomp：变体 read_pos 同步镜像回原始电泳顺序，
    # 与 quality/trace/peak_indices（原始顺序）保持同一坐标系，
    # 否则 Q 值与峰级证据会取到镜像位置的错误数据
    if direction == "-":
        n = len(read_up)
        for v in variants:
            v["read_pos"] = max(1, n - v["read_pos"] + 1)

    identity = matches / compared if compared else 0.0
    # 终审 A-20：局部比对的 query 起止（0-based）此前未输出——软剪切
    # （junk/低质量端）被剪掉的碱基数丢失，下游用「对齐内相对列号」当
    # trimmed read 坐标造成整体错位（t1 实测 read2ref[130]→230 应为 200）
    query_start = int(coords[1][0])       # 0-based：对齐块在原始 query 内的起点
    query_end = int(coords[1][-1])        # 0-based 排他
    ref_start = int(coords[0][0]) + 1
    ref_end = int(coords[0][-1])

    # 逐列 Q 值：反向 read 的 query 为 revcomp，原始 read 索引 = len-1-query 索引
    q_aligned: List[int] = []
    if quality is not None:
        nq = len(quality)
        for qi in read_qi:
            if qi < 0 or qi >= nq:
                q_aligned.append(0)
            else:
                q_aligned.append(quality[qi] if direction == "+" else quality[nq - 1 - qi])

    return {
        "direction": direction,
        "ref_start": ref_start,
        "ref_end": max(ref_end, ref_start),
        # 1-based 原始 query 坐标（软剪切后对齐块的真实起止）：
        # 正向 read 对齐块覆盖原始 [query_start+1, query_end]；
        # 反向 read 的 query 是 revcomp，原始电泳坐标 = n - query 坐标 + 1，
        # 对齐块对应原始 [n-query_end+1, n-query_start]
        "query_start": (len(read_up) - query_end + 1) if direction == "-"
                       else (query_start + 1),
        "query_end": (len(read_up) - query_start) if direction == "-"
                     else query_end,
        "score": float(score),
        "identity": round(identity, 4),
        "variants": variants,
        "ambiguous": ambiguous,
        "aligned_read_len": int(coords[1][-1]) - int(coords[1][0]),
        "aligned": {
            "ref_start": ref_start,
            "ref_aligned": "".join(ref_chars),
            "read_aligned": "".join(read_chars),
            "q_aligned": q_aligned,
        },
    }


def fold_pos(pos: int, length: int) -> int:
    """环状参考坐标折回：延长参考上的 1-based 坐标 → 原参考 1..length"""
    return ((int(pos) - 1) % length) + 1 if length > 0 else int(pos)


def aln_segments(aln: Dict) -> List[Tuple[int, int]]:
    """比对块在参考上的覆盖区段（1-based 闭区间，均落在 1..L 内）

    线性比对即 [(ref_start, ref_end)]；环状比对跨越原点（ref_end > L）时
    拆成 [(ref_start, L), (1, ref_end - L)] 两段；环状无比对返回 []。
    线性比对原样返回（与此前直接取 (ref_start, ref_end) 完全一致）。"""
    s, e = int(aln.get("ref_start") or 0), int(aln.get("ref_end") or 0)
    if not aln.get("circular"):
        return [(s, e)]
    L = int(aln.get("ref_length") or 0)
    if e <= 0:
        return []
    if L and s > L:  # 起点也在展开段（如原点后段的子区间）：整体平移回 1..L
        k = (s - 1) // L * L
        s, e = s - k, e - k
    if L and e > L:
        return [(s, L), (1, e - L)]
    return [(s, e)]


def align_read_circular(read: str, reference: str,
                        quality: Optional[List[int]] = None) -> Dict:
    """环状参考比对：对「参考 + 参考[:k]」做局部比对（k = min(read 长, L)），
    跨越原点的 read 因而能整条比对上，不再被线性局部比对截掉较短一侧。

    坐标约定（下游统一口径）：
    - 去重：同一 read 在双倍区（参考头 k bp 的两份拷贝）只取一处——对齐
      块起点落在拷贝段（> L）时整体平移 -L，保证 1 <= ref_start <= L；
    - ref_start/ref_end 与 aligned.ref_start 保持「起点 <= 终点」的展开坐标，
      跨原点时 ref_end 介于 L+1..L+k（wraps_origin=True），逐列视图沿展开
      坐标递增，按 ((p-1) % L) + 1 折回；
    - variants 的 ref_pos 已折回 1..L；ref_segments 为折回后的覆盖区段
      （跨原点拆两段），ref_length/circular 标明折回所用长度。
    """
    L = len(reference)
    ext = reference + reference[:min(L, len(read))]
    ext_up = ext.upper()
    # 种子索引按「参考 + 参考」建一次（与 read 长度无关，批内复用），
    # 查询时只取落在 ext 内的命中，等价于对 ext 本身建索引
    ref_up = reference.upper()
    aln = _align_impl(read.upper(), ext_up, quality,
                      _index_for(("circular", ref_up), ext_up, ref_up + ref_up))
    aln["circular"] = True
    aln["ref_length"] = L
    if aln["ref_end"] <= 0 or L == 0:
        aln["wraps_origin"] = False
        aln["ref_segments"] = []
        return aln
    if aln["ref_start"] > L:
        aln["ref_start"] -= L
        aln["ref_end"] -= L
        av = aln.get("aligned") or {}
        if av.get("ref_start"):
            av["ref_start"] -= L
    for v in aln["variants"]:
        v["ref_pos"] = fold_pos(v["ref_pos"], L)
    aln["wraps_origin"] = aln["ref_end"] > L
    aln["ref_segments"] = [list(sp) for sp in aln_segments(aln)]
    return aln


def merge_coverage(ranges: List[Tuple[int, int]], length: int) -> List[Tuple[int, int]]:
    """合并多个 read 的覆盖区间（1-based 闭区间），返回合并后区间列表"""
    if not ranges:
        return []
    intervals = sorted((max(1, s), min(length, e)) for s, e in ranges if e >= s)
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        if s <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged
