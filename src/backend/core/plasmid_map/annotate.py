"""自动注释：把序列与常见元件库（data/features/common_features.json）比对

做法参考 pLannotate / SnapGene「Detect common features」的公开描述，代码为
本项目自写（pLannotate 为 GPL-3，未引用其代码）：

1. 长元件（≥ SEED_MIN_LEN bp）：库序列的 k-mer 精确种子 → 按 (元件, 链,
   对角线) 聚类 → 在候选窗口上用 Bio.Align.PairwiseAligner 做局部比对，
   得到 identity（相同列 / 比对列，含 gap）与 coverage（覆盖的元件长度占比）；
2. 短元件（启动子核心、attB、operator…）：双链精确查找；
3. 肽标签 / 蛋白酶位点：六框翻译后精确查找肽段；
4. 环状分子：查询序列末尾接上开头 (最长元件-1) bp，只保留起点在原序列内的
   命中，坐标取模；跨原点命中输出 start > end；
5. 过滤：identity < MIN_IDENTITY 丢弃；coverage < MIN_COVERAGE 丢弃；
   coverage < FRAGMENT_COVERAGE 标 fragment；
6. 去重：同一元件或同类型/同名元件重叠超过较短者一半时只留最好的
   （完整优先 → identity 高优先 → 比对长优先）。

性能边界：候选比对数封顶 MAX_ALIGNMENTS，单次比对窗口封顶元件长度 + 2×PAD。
"""

import hashlib
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from Bio.Seq import Seq

from core.seq_utils import revcomp

KMER = 14
SEED_MIN_LEN = 40
MIN_SEED_VOTES = 3
# 库内出现次数超过此值的 k-mer 视为低复杂度（重复序列），不作种子
MAX_KMER_OCCURRENCES = 6
DIAG_MERGE = 40
PAD = 30
MIN_IDENTITY = 0.90
MIN_COVERAGE = 0.30
FRAGMENT_COVERAGE = 0.95
OVERLAP_DEDUP = 0.5
MAX_ALIGNMENTS = 400

LIBRARY_NAME = "common"


@dataclass(frozen=True)
class LibraryEntry:
    id: str
    name: str
    type: str
    match: str  # "dna" | "peptide"
    sequence: str
    peptide: Optional[str]
    notes: str


@dataclass
class FeatureLibrary:
    name: str
    version: str  # 文件内容 sha256 前 12 位：进缓存键，库变即失效
    entries: Tuple[LibraryEntry, ...]
    index: Dict[str, List[Tuple[int, int]]]  # k-mer → [(entry_idx, offset)]
    max_len: int


def _candidate_paths() -> List[Path]:
    out = []
    env = os.environ.get("MAP_FEATURE_LIBRARY")
    if env:
        out.append(Path(env))
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        out.append(Path(data_dir) / "features" / "common_features.json")
    here = Path(__file__).resolve()
    # 仓库布局 <root>/src/backend/core/plasmid_map/annotate.py；镜像布局 /app/backend/core/...
    for up in (4, 3):
        if len(here.parents) > up:
            out.append(here.parents[up] / "data" / "features" / "common_features.json")
    return out


def library_path() -> Optional[Path]:
    return next((p for p in _candidate_paths() if p.is_file()), None)


@lru_cache(maxsize=4)
def _load(path_str: str, mtime: float) -> FeatureLibrary:
    raw = Path(path_str).read_bytes()
    version = hashlib.sha256(raw).hexdigest()[:12]
    data = json.loads(raw.decode("utf-8"))
    entries = []
    for e in data.get("features") or []:
        entries.append(LibraryEntry(
            id=str(e["id"]), name=str(e["name"]), type=str(e["type"]),
            match=str(e.get("match") or "dna"), sequence=str(e["sequence"]).upper(),
            peptide=e.get("peptide"), notes=str(e.get("notes") or ""),
        ))
    counts: Dict[str, int] = defaultdict(int)
    index: Dict[str, List[Tuple[int, int]]] = defaultdict(list)
    for i, e in enumerate(entries):
        if e.match != "dna" or len(e.sequence) < SEED_MIN_LEN:
            continue
        s = e.sequence
        for off in range(len(s) - KMER + 1):
            k = s[off:off + KMER]
            counts[k] += 1
            index[k].append((i, off))
    index = {k: v for k, v in index.items() if counts[k] <= MAX_KMER_OCCURRENCES}
    max_len = max((len(e.sequence) for e in entries), default=0)
    return FeatureLibrary(name=str(data.get("name") or LIBRARY_NAME), version=version,
                          entries=tuple(entries), index=index, max_len=max_len)


def load_library() -> Optional[FeatureLibrary]:
    path = library_path()
    if path is None:
        return None
    return _load(str(path), path.stat().st_mtime)


def library_version() -> str:
    lib = load_library()
    return lib.version if lib else "none"


@lru_cache(maxsize=1)
def _aligner():
    from Bio.Align import PairwiseAligner

    al = PairwiseAligner()
    al.mode = "local"
    al.match_score = 2
    al.mismatch_score = -3
    al.open_gap_score = -5
    al.extend_gap_score = -2
    return al


@dataclass
class Hit:
    entry: int
    strand: int
    start0: int   # 原序列 0-based 起点（正向链最左碱基）
    length: int   # 命中区段长度（正向链 bp）
    identity: float
    coverage: float
    aligned: int  # 比对列数

    @property
    def fragment(self) -> bool:
        return self.coverage < FRAGMENT_COVERAGE


def _ext(seq: str, circular: bool, extra: int) -> str:
    if not circular or extra <= 0:
        return seq
    L = len(seq)
    return seq + (seq * (extra // L + 1))[:extra]


def _to_fwd(strand: int, a: int, b: int, n_ext: int) -> Tuple[int, int]:
    """查询链坐标 [a, b) → 扩展正向链坐标 [a', b')"""
    if strand == 1:
        return a, b
    return n_ext - b, n_ext - a


def _seed_hits(lib: FeatureLibrary, q: str, strand: int, n_ext: int,
               seq_len: int, budget: List[int]) -> List[Hit]:
    votes: Dict[Tuple[int, int], int] = defaultdict(int)
    index = lib.index
    for i in range(len(q) - KMER + 1):
        hits = index.get(q[i:i + KMER])
        if hits:
            for eidx, off in hits:
                votes[(eidx, i - off)] += 1
    by_entry: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
    for (eidx, diag), v in votes.items():
        by_entry[eidx].append((diag, v))
    out: List[Hit] = []
    aligner = _aligner()
    for eidx, diags in by_entry.items():
        diags.sort()
        clusters, cur = [], [diags[0]]
        for d in diags[1:]:
            if d[0] - cur[-1][0] <= DIAG_MERGE:
                cur.append(d)
            else:
                clusters.append(cur)
                cur = [d]
        clusters.append(cur)
        entry = lib.entries[eidx]
        elen = len(entry.sequence)
        for cl in clusters:
            if sum(v for _, v in cl) < MIN_SEED_VOTES:
                continue
            if budget[0] <= 0:
                return out
            budget[0] -= 1
            lo = max(0, cl[0][0] - PAD)
            hi = min(len(q), cl[-1][0] + elen + PAD)
            window = q[lo:hi]
            if len(window) < KMER:
                continue
            aln = aligner.align(window, entry.sequence)[0]
            blocks_t, blocks_q = aln.aligned  # target=window, query=entry
            if len(blocks_t) == 0:
                continue
            w_a, w_b = int(blocks_t[0][0]), int(blocks_t[-1][1])
            e_a, e_b = int(blocks_q[0][0]), int(blocks_q[-1][1])
            matches = 0
            for (ta, tb), (qa, _qb) in zip(blocks_t, blocks_q):
                ta, tb, qa = int(ta), int(tb), int(qa)
                seg_w = window[ta:tb]
                seg_e = entry.sequence[qa:qa + (tb - ta)]
                matches += sum(1 for x, y in zip(seg_w, seg_e) if x == y)
            columns = (w_b - w_a) + (e_b - e_a) - sum(int(tb) - int(ta) for ta, tb in blocks_t)
            if columns <= 0:
                continue
            identity = matches / columns
            coverage = (e_b - e_a) / elen
            a, b = _to_fwd(strand, lo + w_a, lo + w_b, n_ext)
            if a >= seq_len:
                continue  # 起点在扩展段：由原序列内的同一命中代表
            out.append(Hit(eidx, strand, a, b - a, identity, coverage, columns))
    return out


def _exact_hits(lib: FeatureLibrary, qs: Dict[int, str], n_ext: int, seq_len: int) -> List[Hit]:
    out = []
    for eidx, e in enumerate(lib.entries):
        if e.match != "dna" or len(e.sequence) >= SEED_MIN_LEN:
            continue
        n = len(e.sequence)
        for strand, q in qs.items():
            i = q.find(e.sequence)
            while i >= 0:
                a, _b = _to_fwd(strand, i, i + n, n_ext)
                if a < seq_len:
                    out.append(Hit(eidx, strand, a, n, 1.0, 1.0, n))
                i = q.find(e.sequence, i + 1)
    return out


def _peptide_hits(lib: FeatureLibrary, qs: Dict[int, str], n_ext: int, seq_len: int) -> List[Hit]:
    peps = [(i, e) for i, e in enumerate(lib.entries) if e.match == "peptide" and e.peptide]
    if not peps:
        return []
    out = []
    for strand, q in qs.items():
        for frame in range(3):
            sub = q[frame:]
            sub = sub[: len(sub) - len(sub) % 3]
            prot = str(Seq(sub).translate(table=1)) if sub else ""
            for eidx, e in peps:
                pep = e.peptide
                i = prot.find(pep)
                while i >= 0:
                    a_q = frame + 3 * i
                    n = 3 * len(pep)
                    a, _b = _to_fwd(strand, a_q, a_q + n, n_ext)
                    if a < seq_len:
                        out.append(Hit(eidx, strand, a, n, 1.0, 1.0, n))
                    i = prot.find(pep, i + len(pep))  # 不报重叠实例（His 串）
    return out


def circular_overlap(a0: int, alen: int, b0: int, blen: int, L: int, circular: bool) -> int:
    """两段 [a0, a0+alen) 与 [b0, b0+blen) 的重叠 bp（环状取模）"""
    def segs(s, n):
        if not circular or s + n <= L:
            return [(s, min(s + n, L) if circular else s + n)]
        return [(s, L), (0, (s + n) - L)]
    total = 0
    for x0, x1 in segs(a0, alen):
        for y0, y1 in segs(b0, blen):
            total += max(0, min(x1, y1) - max(x0, y0))
    return total


def _quality(h: Hit):
    return (not h.fragment, round(h.identity, 3), h.aligned)


def annotate(seq: str, circular: bool, lib: Optional[FeatureLibrary] = None) -> List[Dict]:
    """返回契约 MapFeature（source=auto，尚无 id）列表，按起点排序"""
    lib = lib or load_library()
    seq = (seq or "").upper()
    L = len(seq)
    if lib is None or not lib.entries or L < 8:
        return []
    ext = _ext(seq, circular, lib.max_len - 1)
    n_ext = len(ext)
    qs = {1: ext, -1: revcomp(ext)}

    budget = [MAX_ALIGNMENTS]
    hits: List[Hit] = []
    for strand, q in qs.items():
        hits.extend(_seed_hits(lib, q, strand, n_ext, L, budget))
    hits.extend(_exact_hits(lib, qs, n_ext, L))
    hits.extend(_peptide_hits(lib, qs, n_ext, L))

    hits = [h for h in hits if h.identity >= MIN_IDENTITY and h.coverage >= MIN_COVERAGE]
    hits.sort(key=_quality, reverse=True)
    kept: List[Hit] = []
    for h in hits:
        e = lib.entries[h.entry]
        clash = False
        for k in kept:
            ke = lib.entries[k.entry]
            if not (k.entry == h.entry or ke.type == e.type or ke.name == e.name):
                continue
            ov = circular_overlap(h.start0, h.length, k.start0, k.length, L, circular)
            if ov > OVERLAP_DEDUP * min(h.length, k.length):
                clash = True
                break
        if not clash:
            kept.append(h)

    out = []
    for h in kept:
        e = lib.entries[h.entry]
        start = h.start0 + 1
        end = h.start0 + h.length
        if circular:
            end = ((end - 1) % L) + 1
        elif end > L:  # pragma: no cover - 线性不扩展，理论上不会越界
            continue
        feat = {
            "name": e.name,
            "type": e.type,
            "start": start,
            "end": end,
            "strand": h.strand,
            "source": "auto",
            "auto": {
                "library": lib.name,
                "identity": round(h.identity, 3),
                "coverage": round(min(1.0, h.coverage), 3),
                "fragment": h.fragment,
            },
            "qualifiers": {"library_feature": e.id, "library_version": lib.version},
        }
        if e.notes:
            feat["notes"] = e.notes
        out.append(feat)
    out.sort(key=lambda f: (f["start"], f["end"]))
    return out
