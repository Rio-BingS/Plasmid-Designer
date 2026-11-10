"""测序范围（scope）：用户声明「需要测哪一段」的统一载体（goal v3 §3.1）。

一次分析、三个范围（PL §1）：R=需测范围（本模块）、E=引物理论覆盖
（coverage.expected_ranges，W6）、V=实际验证覆盖（coverage.verified_ranges）。
判定（build_verdict）是纯函数：同一份 result 在不同 scope 下可随时重算，
换范围不重跑分析（goal v3 §0.6）。

Scope 来源（W10 接线前的优先级，PL §C2）：
    user > design_insert > cds_target > filename_match > primer_window
    > measured（都没有 → 未声明，只描述实测区段，禁止输出「构建与设计
    一致」，goal v3 §0.1）。

parse_scope_cell 支持（goal v3 §5 W10）：元件名（特征名精确/唯一包含）、
「301-900」数字区间（- – ~ 三种连接符）、多段（; ；分隔）、「插入片段」
「全质粒」「接合区」关键字。解析失败返回 (None, 错误说明)，不抛异常——
批量信息表里解析失败的行回填提示、不阻塞其他样品。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# 接合区半宽（goal v3 T5 沿用：载体-基因接合区 ±30bp；verdict 侧引用本常量）
JUNCTION_HALF_WIDTH = 30

# scope 来源枚举（§3.1）；declared = source != "measured"
SOURCE_USER = "user"
SOURCE_DESIGN_INSERT = "design_insert"
SOURCE_CDS_TARGET = "cds_target"
SOURCE_FILENAME_MATCH = "filename_match"
SOURCE_PRIMER_WINDOW = "primer_window"
SOURCE_MEASURED = "measured"
DECLARED_SOURCES = frozenset({
    SOURCE_USER, SOURCE_DESIGN_INSERT, SOURCE_CDS_TARGET,
    SOURCE_FILENAME_MATCH, SOURCE_PRIMER_WINDOW,
})

_RANGE_SEPARATORS = ("；", ";")
_RANGE_DASHES = ("-", "–", "~", "—")


def _norm_text(s: str) -> str:
    """比对用归一化：去空白、统一小写与全角连接符"""
    return (s or "").strip().lower().replace("（", "(").replace("）", ")")


def _normalize_ranges(ranges: List[Tuple[int, int]],
                      ref_length: int) -> List[Tuple[int, int]]:
    """区间裁剪到 [1, L] 并按起点排序合并（跨原点段保留 start > end 形态）"""
    out: List[Tuple[int, int]] = []
    for s, e in ranges:
        if s <= e:                      # 普通段
            s2, e2 = max(1, s), min(ref_length, e)
            if s2 <= e2:
                out.append((s2, e2))
        else:                           # 跨原点：拆 [s, L] + [1, e]
            if s <= ref_length:
                out.append((s, ref_length))
            if e >= 1:
                out.append((1, min(e, ref_length)))
    out.sort()
    merged: List[Tuple[int, int]] = []
    for s, e in out:
        if merged and s <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


@dataclass
class Scope:
    """需测范围 R（goal v3 §3.1）。ranges 为 1-based 闭区间、可多段，
    环状跨原点用 start > end 表示折回；elements 是范围内的元件名（报告与
    头行用）；reason 是自动推断的依据文案（如「参考文件名匹配 GeneX」）。

    junctions（W8）：接合区窗口（CDS 目标两端 ±JUNCTION_HALF_WIDTH）——
    属于需测范围（覆盖缺口按其判定、区内变异判 junction_variant），但不
    并入 ranges：头行/区间渲染只显示 CDS 主段（cds_target 与全质粒
    +scope 两种路径的结论才能逐字同源，CM5）。"""
    ranges: List[Tuple[int, int]] = field(default_factory=list)
    source: str = SOURCE_MEASURED
    declared: bool = False
    require_double_strand: bool = False
    elements: List[str] = field(default_factory=list)
    reason: str = ""
    junctions: List[Tuple[int, int]] = field(default_factory=list)
    # 沿用上次范围（goal v3.1 X5）：source 保留原来源，只标记「这次是记忆带出」
    remembered: bool = False

    def __post_init__(self) -> None:
        self.ranges = [(int(s), int(e)) for s, e in (self.ranges or [])]
        self.junctions = [(int(s), int(e)) for s, e in (self.junctions or [])]
        if self.source == SOURCE_MEASURED:
            self.declared = False
        elif self.source in DECLARED_SOURCES:
            self.declared = True

    def to_dict(self) -> Dict:
        return {
            "ranges": [[s, e] for s, e in self.ranges],
            "source": self.source,
            "declared": bool(self.declared),
            "require_double_strand": bool(self.require_double_strand),
            "elements": list(self.elements),
            "reason": self.reason,
            "junctions": [[s, e] for s, e in self.junctions],
            "remembered": bool(self.remembered),
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict]) -> "Scope":
        if not data:
            return cls()
        ranges = data.get("ranges") or []
        return cls(
            ranges=[(r[0], r[1]) for r in ranges if len(r) >= 2],
            source=str(data.get("source") or SOURCE_MEASURED),
            declared=bool(data.get("declared")),
            require_double_strand=bool(data.get("require_double_strand")),
            elements=[str(x) for x in (data.get("elements") or [])],
            reason=str(data.get("reason") or ""),
            junctions=[(j[0], j[1]) for j in (data.get("junctions") or [])
                       if len(j) >= 2],
            remembered=bool(data.get("remembered")),
        )

    def normalized(self, ref_length: int) -> "Scope":
        """裁剪到 [1, L] 并按起点排序合并（跨原点段保留 start > end 形态）"""
        if not ref_length:
            return self
        if self.ranges:
            self.ranges = _normalize_ranges(self.ranges, ref_length)
        if self.junctions:
            self.junctions = _normalize_ranges(self.junctions, ref_length)
        return self

    def merged_ranges(self) -> List[Tuple[int, int]]:
        """需测范围的并集区间（ranges + junctions 合并排序去重叠）。

        接合区窗口与 CDS 主段重叠 ±30bp——覆盖分数/缺口的分子分母都必须
        按并集计，否则重叠段在分母被重复计入（约 60bp/CDS）。"""
        out = sorted(self.ranges + self.junctions)
        merged: List[Tuple[int, int]] = []
        for s, e in out:
            if merged and s <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        return merged

    def total_bp(self) -> int:
        """需测总 bp（并集口径，含接合区——接合区属于需测范围，W8）"""
        return sum(max(0, e - s + 1) for s, e in self.merged_ranges())

    def contains(self, start: int, end: Optional[int] = None) -> bool:
        """[start, end]（缺省单点）是否完整落在范围内"""
        end = start if end is None else end
        return any(s <= start and end <= e for s, e in self.ranges)

    def bounding_box(self) -> Tuple[int, int]:
        if not self.ranges:
            return (1, 0)
        return (min(s for s, _ in self.ranges), max(e for _, e in self.ranges))

    def spans_text(self, sep: str = "、") -> str:
        return sep.join(f"{s}–{e}" for s, e in self.ranges) if self.ranges else ""


def junction_ranges(features: List[Dict]) -> List[Tuple[int, int, str]]:
    """接合区（goal T5 沿用；从 verdict 迁入本模块统一管理）：
    analysis_target CDS 两端 ±JUNCTION_HALF_WIDTH。
    返回 [(start, end, element)]。跨原点 CDS 的两端接合区按折回坐标给出
    （W8 全质粒比对后接合区跟随真实拓扑）。"""
    out: List[Tuple[int, int, str]] = []
    for f in features or []:
        if not f.get("analysis_target") or f.get("type") != "CDS":
            continue
        s, e = int(f["start"]), int(f["end"])
        name = f.get("name") or "CDS"
        out.append((max(1, s - JUNCTION_HALF_WIDTH), s + JUNCTION_HALF_WIDTH, name))
        out.append((e - JUNCTION_HALF_WIDTH, e + JUNCTION_HALF_WIDTH, name))
    return out


def infer_scope(ref_stem: str, read_names: List[str], features: List[Dict],
                insert_range: Optional[Tuple[int, int]] = None,
                ref_length: int = 0) -> Optional["Scope"]:
    """按 §3.1 来源优先级推断需测范围（goal v3 W10，PL §C1/§C2）。

    user 由调用方显式给出不走这里；cds_target 由目的基因模式直接构造。
    本函数覆盖中间两级：

    - design_insert：设计记录的插入边界（insert_range 给出时），接合区
      两端 ±30 另算（junctions 字段，W8 口径）；
    - filename_match：复用 cds_extract.resolve_auto——参考/read 文件名
      唯一命中 CDS/gene 时以其为需测范围（+ 接合区）。

    primer_window 需引物结合窗（W7 映射产出、E 的并集），接线在批量/
    前端侧，这里不推断。都取不到 → None（调用方退回未声明 measured 口径）。
    ref_length>0 时输出已 normalized。"""
    if insert_range:
        s, e = int(insert_range[0]), int(insert_range[1])
        scope = Scope(
            ranges=[(s, e)],
            junctions=[(s - JUNCTION_HALF_WIDTH, s + JUNCTION_HALF_WIDTH),
                       (e - JUNCTION_HALF_WIDTH, e + JUNCTION_HALF_WIDTH)],
            source=SOURCE_DESIGN_INSERT,
            reason="设计记录插入边界（两端 ±30 接合区）")
        return scope.normalized(ref_length) if ref_length else scope

    from core.sanger.cds_extract import cds_candidates, resolve_auto
    cands = cds_candidates(features)
    if cands:
        cand, how = resolve_auto(cands, ref_stem, read_names)
        if cand is not None:
            ranges = [(int(p["start"]), int(p["end"])) for p in cand["parts"]]
            junctions: List[Tuple[int, int]] = []
            for s_, e_ in ranges:
                junctions.extend(
                    [(s_ - JUNCTION_HALF_WIDTH, s_ + JUNCTION_HALF_WIDTH),
                     (e_ - JUNCTION_HALF_WIDTH, e_ + JUNCTION_HALF_WIDTH)])
            scope = Scope(ranges=ranges, junctions=junctions,
                          source=SOURCE_FILENAME_MATCH,
                          elements=[cand["name"]],
                          reason=f"文件名匹配目标 CDS（{how}）")
            return scope.normalized(ref_length) if ref_length else scope
    return None


def _match_element(token: str, features: List[Dict]) -> Optional[Tuple[int, int, str]]:
    """元件名 → 区间。精确（忽略大小写/空白）优先，其次唯一包含；
    0 或多命中返回 None（调用方给错误说明）"""
    feats = [f for f in (features or [])
             if f.get("name") and int(f.get("start") or 0) >= 1]
    tok = _norm_text(token)
    exact = [f for f in feats if _norm_text(f["name"]) == tok]
    if len(exact) == 1:
        f = exact[0]
        return int(f["start"]), int(f["end"]), f["name"]
    if len(exact) > 1:
        return None
    contains = [f for f in feats if tok and tok in _norm_text(f["name"])]
    if len(contains) == 1:
        f = contains[0]
        return int(f["start"]), int(f["end"]), f["name"]
    return None


def parse_scope_cell(text: str, features: List[Dict], ref_length: int = 0,
                     insert_range: Optional[Tuple[int, int]] = None
                     ) -> Tuple[Optional[Scope], str]:
    """解析范围单元格/表单文本（goal v3 W10）。

    支持：元件名 / 「301-900」数字区间 / 多段（; ；分隔）/ 关键字
    「插入片段」「全质粒」「接合区」。返回 (Scope, "") 或 (None, 错误说明)，
    不抛异常。空文本 → (None, "")（调用方走来源推断）。"""
    raw = (text or "").strip()
    if not raw:
        return None, ""
    if ref_length <= 0:
        return None, "参考序列长度未知，无法解析范围"

    def _num(tok: str) -> Optional[Tuple[int, int]]:
        t = tok.strip()
        for d in _RANGE_DASHES:
            if d in t:
                a, _, b = t.partition(d)
                if a.strip().isdigit() and b.strip().isdigit():
                    lo, hi = int(a), int(b)
                    if 1 <= lo <= hi <= ref_length:
                        return lo, hi
                    if lo > hi and lo <= ref_length:   # 环状跨原点
                        return lo, hi
                    return None
        if t.isdigit():
            p = int(t)
            if 1 <= p <= ref_length:
                return p, p
        return None

    ranges: List[Tuple[int, int]] = []
    elements: List[str] = []
    for part_raw in raw.replace("；", ";").split(";"):
        part = part_raw.strip()
        if not part:
            continue
        low = _norm_text(part)
        if low in ("全质粒", "whole", "整条质粒", "整个质粒"):
            ranges.append((1, ref_length))
            continue
        if low in ("插入片段", "insert", "设计插入片段"):
            if not insert_range:
                return None, "「插入片段」需要设计记录的插入边界（当前分析未提供）"
            ranges.append((int(insert_range[0]), int(insert_range[1])))
            continue
        if low in ("接合区", "junction", "接合位点"):
            jr = junction_ranges(features)
            if not jr:
                return None, "参考未标注分析目标 CDS，无法确定接合区"
            ranges.extend((s, e) for s, e, _n in jr)
            continue
        num = _num(part)
        if num:
            ranges.append(num)
            continue
        hit = _match_element(part, features)
        if hit:
            s, e, name = hit
            ranges.append((s, e))
            if name not in elements:
                elements.append(name)
            continue
        names = "、".join(sorted({f.get("name") or "" for f in features
                                  if f.get("name")})[:8])
        return None, f"范围无法识别：「{part}」（可用元件名：{names}…）"
    if not ranges:
        return None, f"范围无法识别：「{raw}」"
    scope = Scope(ranges=ranges, source=SOURCE_USER, elements=elements,
                  reason="用户指定")
    return scope.normalized(ref_length), ""


def coerce_scope(scope, features: List[Dict], ref_length: int = 0,
                 insert_range: Optional[Tuple[int, int]] = None
                 ) -> Optional[Scope]:
    """analyze/build_verdict 的 scope 入参归一：None / Scope / dict / str。

    str 按 parse_scope_cell 解析（解析失败原样返回 None——范围无法识别时
    由调用方回填错误，判定退回未声明口径，不抛异常）。"""
    if scope is None:
        return None
    if isinstance(scope, Scope):
        out = scope
    elif isinstance(scope, dict):
        out = Scope.from_dict(scope)
    elif isinstance(scope, str):
        parsed, _err = parse_scope_cell(scope, features, ref_length,
                                        insert_range)
        out = parsed
    else:
        return None
    if out is not None:
        out = out.normalized(ref_length) if ref_length else out
    return out
