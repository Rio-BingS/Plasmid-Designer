"""克隆组装：按克隆方法真实模拟「切点 → 切除区间 → 连接处碱基」

此前设计管线只有一套组装：**把 MCS 注释区间整段换成插入片段**
（assemble_construct 的旧实现）。三种克隆方法共用它，于是

* Gibson 是在一个位点线性化、一个碱基都不切掉的，却被删掉整段 MCS；
* 双酶切的真实切点落在 MCS 注释之外时，该被切走的片段留在图谱里；
* Golden Gate 两个 Type IIS 切点之间的片段同样没被切掉。

用户看到的就是「位点没认准、图谱上残留该被酶切掉的部分」。这里按方法建模，
统一成一句话：**用 head + 基因 + tail 替换载体上 (left_cut, right_cut] 这一段**。

| 方法 | left_cut | right_cut | head / tail |
|---|---|---|---|
| Gibson | 线性化点 | 同左（不切除） | 无 |
| 双酶切 | e5 的顶链切点 | e3 的顶链切点 | site5 切点之后的残留 / site3 切点之前的残留 |
| Golden Gate | 第一个位点的顶链切点 | 第二个位点的顶链切点 | overhang5 / overhang3 |

坐标一律 1-based。「切点在 k」= 顶链在第 k 个碱基之后断开（k 可为 0），
与 core.enzyme_sites 的 cut_fwd 口径一致。

连接处的碱基为什么要补：PCR 产物两端带着酶位点（引物尾部），酶切后基因片段的
顶链一端从位点中间开始、另一端在位点中间结束；与载体连接后位点被复原。
只把「裸基因」插进切点会得到缺 5–6 bp 的错误图谱——这正是用户反馈的
「拼接不完整」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from core.enzyme_sites import find_enzyme_sites, get_enzyme, golden_gate_geometry


@dataclass
class AssemblyPlan:
    """组装方案：用 head + 基因 + tail 替换载体顶链 (left_cut, right_cut] 区间"""

    method: str
    left_cut: int                       # 1-based：保留 1..left_cut
    right_cut: int                      # 1-based：保留 right_cut+1..L
    head: str = ""                      # 基因 5' 侧补的碱基（位点残留/overhang）
    tail: str = ""                      # 基因 3' 侧补的碱基
    junction: str = ""                  # 人类可读的切点说明（写进方案文本）
    warnings: List[str] = field(default_factory=list)

    @property
    def removed(self) -> int:
        """被切走的载体碱基数（left==right 表示纯插入）"""
        return max(0, self.right_cut - self.left_cut)

    def fragment(self, insert: str) -> str:
        return self.head + insert + self.tail


def _enzyme_cut(enzyme: str) -> Tuple[str, int, int]:
    """酶 → (识别序列, 顶链切点偏移, 底链切点偏移)；不可用于双酶切时抛 ValueError"""
    spec = get_enzyme(enzyme)
    if not spec:
        raise ValueError(f"未知限制酶: {enzyme}")
    site = str(spec.get("site") or "")
    c1, c2 = spec.get("cut") or (0, 0)
    if not site or min(int(c1), int(c2)) < 0:
        raise ValueError(f"{enzyme} 的切点不在识别序列内（Type IIS/上游切割），不能用双酶切建模")
    return site, int(c1), int(c2)


def physical_sites(sequence: str, enzyme: str, *, circular: bool = True) -> List[Dict]:
    """载体的某酶位点（每个物理位点一条；回文酶正反链重复上报已去重）"""
    if not sequence:
        return []
    raw = find_enzyme_sites(sequence, [enzyme], circular=circular)
    seen = set()
    out: List[Dict] = []
    for s in raw:
        key = (s["position"], s["cut_fwd"], s["cut_rev"])
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    out.sort(key=lambda s: s["position"])
    return out


def _forward_dist(a: int, b: int, length: int, circular: bool) -> int:
    """从切点 a 走到切点 b 的距离（即被切掉的 (a, b] 长度）"""
    return (b - a) % length if circular else (b - a)


def _contains(a: int, dist: int, anchor: int, length: int, circular: bool) -> bool:
    off = (anchor - a - 1) % length if circular else (anchor - a - 1)
    return 0 <= off < dist


def _in_window(pos: int, window: Optional[Tuple[int, int]], length: int,
               circular: bool) -> bool:
    if not window:
        return True
    lo, hi = window
    if circular and lo > hi:
        return pos >= lo or pos <= hi
    return lo <= pos <= hi


def _overlaps_window(a: int, dist: int, window: Optional[Tuple[int, int]],
                     length: int, circular: bool) -> bool:
    if not window:
        return True
    lo, hi = window
    return any(_in_window((a + i) % length if circular else a + i, window, length, circular)
               for i in range(1, dist + 1))


def _pair_score(a: int, b: int, window: Optional[Tuple[int, int]],
                length: int, circular: bool) -> Optional[Tuple[int, int]]:
    """(penalty, 切除长度)；a==b（纯插入）或方向不成立返回 None"""
    d = _forward_dist(a, b, length, circular)
    if d <= 0:
        return None
    both_in = (_in_window(a, window, length, circular)
               and _in_window(b, window, length, circular))
    if both_in:
        penalty = 0
    elif _overlaps_window(a, d, window, length, circular):
        penalty = 1
    else:
        penalty = 2
    return (penalty, d)


def pick_flanking(
    cuts_a: Sequence[int], cuts_b: Sequence[int], *,
    window: Optional[Tuple[int, int]] = None, length: int, circular: bool = True,
) -> Optional[Tuple[int, int, int]]:
    """挑一对切点，返回 (left, right, penalty)

    penalty 0 = 两个切点都落在 window（MCS 区间）内；1 = 切除区间与 window 相交；
    2 = 完全在 window 之外（调用方据此告警）。

    排序按 (penalty, 切除长度)：同 penalty 下取最短。**不能只看是不是「包住
    MCS 中点」**——pET-28a 的 MCS 158–301 而 BamHI/XhoI 切点 159/199 并不包含
    中点 229，只看中点会把 5329 bp 的主链当成要切掉的废片段。

    window 为 None（载体没有 MCS 注释）时按最短区间取。
    """
    best: Optional[Tuple[Tuple[int, int], int, int]] = None
    for a in cuts_a:
        for b in cuts_b:
            score = _pair_score(a, b, window, length, circular)
            if score is None:
                continue
            if best is None or score < best[0]:
                best = (score, a, b)
    if best is not None:
        return best[1], best[2], best[0][0]

    for a in cuts_a:
        if a in cuts_b:
            return a, a, 0
    return None


def plan_restriction(
    sequence: str, enzyme_5: str, enzyme_3: str, *,
    window: Optional[Tuple[int, int]] = None, circular: bool = True,
    warnings: List[str],
) -> Optional[AssemblyPlan]:
    """双酶切：两个切点之间整段被切走，基因两端补回位点残留碱基

    载体上没有位点时返回 None（调用方回退到「按 MCS 区间替换」并保留告警）。
    """
    length = len(sequence)
    try:
        site5, k5, _ = _enzyme_cut(enzyme_5)
        site3, k3, _ = _enzyme_cut(enzyme_3)
    except ValueError as exc:
        warnings.append(f"{exc}：双酶切按 MCS 区间替换处理")
        return None

    sites5 = physical_sites(sequence, enzyme_5, circular=circular)
    sites3 = physical_sites(sequence, enzyme_3, circular=circular)
    missing = [n for n, s in ((enzyme_5, sites5), (enzyme_3, sites3)) if not s]
    if missing:
        warnings.append(
            "载体上没有 " + "、".join(missing) + " 位点：无法按双酶切切除片段，"
            "已按 MCS 区间替换构建体，请核对酶切方案")
        return None

    cuts5 = sorted({s["cut_fwd"] for s in sites5})
    cuts3 = sorted({s["cut_fwd"] for s in sites3})

    # 两个方向都要试：废片段可能在 e5 之后、e3 之前，也可能反过来。
    # 只按「e5 → e3」单向找，pET-28a 这类位点顺序与引物端指派相反的载体会
    # 把 5.3 kb 主链当成要切掉的废片段（实测会把质粒删成 40 bp）。
    best: Optional[Tuple[Tuple[int, int], int, int, str]] = None
    for cuts_a, cuts_b, left_enzyme in ((cuts5, cuts3, enzyme_5),
                                        (cuts3, cuts5, enzyme_3)):
        for a in cuts_a:
            for b in cuts_b:
                score = _pair_score(a, b, window, length, circular)
                if score is None:
                    continue
                if best is None or score < best[0]:
                    best = (score, a, b, left_enzyme)

    if best is None:
        common = sorted(set(cuts5) & set(cuts3))
        if not common:
            warnings.append(f"{enzyme_5}/{enzyme_3} 的位点排布无法围出可切除区间，"
                            "已按 MCS 区间替换构建体")
            return None
        # 单酶切/单一位点：纯插入，位点在两端各复原一次
        point = common[0]
        return AssemblyPlan(
            "restriction", point, point, head=site5[k5:], tail=site3[:k3],
            junction=f"{enzyme_5} 单切点 {point}（纯插入）", warnings=warnings)

    score, left, right, left_enzyme = best
    if score[0] == 2:
        warnings.append(
            f"{enzyme_5}/{enzyme_3} 的两个切点都不在 MCS 区间内"
            f"（将切除 {max(0, right - left)} bp），请核对酶切方案")

    # 左切点属于哪把酶：引物把 enzyme_5 的位点放在基因 5' 端，若左切点是
    # enzyme_3，说明两端酶与载体上的位点顺序相反，构建体上基因会是反向插入
    if left_enzyme == enzyme_5:
        head, tail = site5[k5:], site3[:k3]
    else:
        head, tail = site3[k3:], site5[:k5]
        warnings.append(
            f"载体上 {enzyme_3} 的切点在前、{enzyme_5} 的切点在后：按引物端的指派，"
            "插入片段在构建体上会是反向插入；如需正向表达请把两端酶对调"
            f"（5' 端用 {enzyme_3}、3' 端用 {enzyme_5}）")

    return AssemblyPlan(
        "restriction", left, right, head=head, tail=tail,
        junction=(f"{left_enzyme} 切点 {left} / 另一端切点 {right}"
                  f"（切除 {max(0, right - left)} bp，位点在连接处复原）"),
        warnings=warnings,
    )


def plan_golden_gate(
    sequence: str, enzyme: str, overhang_5: str, overhang_3: str, *,
    window: Optional[Tuple[int, int]] = None, circular: bool = True,
    warnings: List[str],
) -> Optional[AssemblyPlan]:
    """Golden Gate：两个 Type IIS 切点之间被切走，两端补 4 nt overhang"""
    length = len(sequence)
    sites = physical_sites(sequence, enzyme, circular=circular)
    if not sites:
        warnings.append(f"载体上没有 {enzyme} 位点：无法按 Golden Gate 线性化，"
                        "已按 MCS 区间替换构建体")
        return None
    geo = golden_gate_geometry(enzyme)
    if geo and str(sites[0].get("recognition") or "") and \
            geo["site"] != str(sites[0]["recognition"]):
        warnings.append(f"{enzyme} 在 REBASE 中记为上游切割，切点按扫描位置近似")

    cuts = sorted({s["cut_fwd"] for s in sites})
    if len(cuts) >= 2:
        picked = pick_flanking(cuts, cuts, window=window, length=length, circular=circular)
        if picked is None:
            warnings.append(f"{enzyme} 的位点排布无法围出可切除区间，已按 MCS 区间替换")
            return None
        left, right, penalty = picked
        if penalty == 2:
            warnings.append(f"{enzyme} 的两个切点都不在 MCS 区间内"
                            f"（将切除 {max(0, right - left)} bp），请核对酶切方案")
    else:
        left = right = cuts[0]
        warnings.append(f"载体上只有 1 个 {enzyme} 位点，按单切点纯插入组装")

    return AssemblyPlan(
        "golden_gate", left, right, head=overhang_5, tail=overhang_3,
        junction=(f"{enzyme} 切点 {left}/{right}"
                  f"（切除 {max(0, right - left)} bp，overhang {overhang_5}/{overhang_3}）"),
        warnings=warnings,
    )


def plan_gibson(sequence: str, insert_pos: int) -> AssemblyPlan:
    """Gibson：在一位点线性化，不切除任何碱基（同源臂由引物提供）"""
    length = len(sequence)
    pos = 0 if length <= 0 else max(0, min(int(insert_pos), length))
    return AssemblyPlan("gibson", pos, pos,
                        junction=f"在线性化点 {pos} bp 处插入（不切除载体碱基）")


def clamp_span(start: int, end: int, length: int) -> Optional[Tuple[int, int]]:
    """载体元件坐标裁剪到 [1, length]；完全越界返回 None"""
    s = max(1, min(int(start), length))
    e = max(1, min(int(end), length))
    return None if s > e else (s, e)


def apply_plan(
    sequence: str,
    elements: Optional[Sequence[Dict]],
    plan: AssemblyPlan,
    *,
    insert: str,
    insert_name: str = "insert",
) -> Tuple[str, List[Dict], int, int, List[str]]:
    """按方案生成构建体序列与注释 → (序列, 元件, 插入起点, 插入终点, 告警)"""
    length = len(sequence)
    left, right = plan.left_cut, plan.right_cut
    fragment = plan.fragment(insert)
    warnings = list(plan.warnings)

    if length <= 0:
        return fragment, [], 1, len(fragment), warnings
    if right < left:
        raise ValueError("cut pair wraps origin")

    removed = max(0, right - left)
    construct = sequence[:left] + fragment + sequence[right:]
    # 插入片段在构建体上的坐标只算**基因本身**：两端的位点残留/overhang 属于
    # 克隆接头，算进 CDS 会让图谱上的编码区从 "GATCC" 开始、ORF 平移
    insert_start = left + 1 + len(plan.head)
    insert_end = insert_start + len(insert) - 1
    delta = len(fragment) - removed

    features: List[Dict] = [{
        "name": insert_name or "insert",
        "type": "CDS",
        "start": insert_start,
        "end": max(insert_start, insert_end),
        "strand": "+",
        "description": "Designed insert",
    }]
    for el in elements or []:
        span = clamp_span(el.get("start", 1), el.get("end", 1), length)
        if span is None:
            continue
        s, e = span
        item = {
            "name": el.get("name") or "feature",
            "type": el.get("type") or "other",
            "start": s,
            "end": e,
            "strand": el.get("strand") or "+",
            "description": el.get("description") or "",
        }
        if left == right and s <= left < e:
            # 纯插入点落在元件内部（Gibson 常在 MCS 中间线性化）：元件被撑开，
            # 起点不变、终点后移——不能当成「跨过切点」截断
            item["end"] = e + delta
            features.append(item)
        elif e <= left:
            features.append(item)
        elif s > right:
            item["start"] = s + delta
            item["end"] = e + delta
            features.append(item)
        elif s <= left and (left - s + 1) * 2 >= (e - s + 1):
            # 跨过切点但大部分仍在载体上：截到切点
            item["end"] = left
            features.append(item)
            warnings.append(f"载体元件 {item['name']} 跨过切点，已截到 {left} bp")
        elif e > right and (e - right) * 2 >= (e - s + 1):
            item["start"] = right + 1 + delta
            item["end"] = e + delta
            features.append(item)
            warnings.append(f"载体元件 {item['name']} 跨过切点，已移位到 "
                            f"{item['start']}–{item['end']}")
        else:
            # 与切除区间重叠的部分太大（如整个 MCS 被切掉）→ 直接移除，
            # 截成 1–2 bp 的碎片只会让图谱更难读
            warnings.append(f"载体元件 {item['name']} 基本落在被切除区间，已从图谱移除")
    return construct, features, insert_start, insert_end, warnings
