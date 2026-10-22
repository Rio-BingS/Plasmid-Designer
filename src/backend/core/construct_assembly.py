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
from core.seq_utils import revcomp


@dataclass
class AssemblyPlan:
    """组装方案：用 head + 基因 + tail 替换载体顶链 (left_cut, right_cut] 区间"""

    method: str
    left_cut: int                       # 1-based：保留 1..left_cut
    right_cut: int                      # 1-based：保留 right_cut+1..L
    head: str = ""                      # 基因 5' 侧补的碱基（位点残留/overhang）
    tail: str = ""                      # 基因 3' 侧补的碱基
    junction: str = ""                  # 人类可读的切点说明（写进方案文本）
    # 插入片段是否反向（负链）插入。不是排布细节而是物理事实：粘端只在同一位点
    # 之间互补，引物端 e5 的位点在载体上位于 e3 之后时，基因只能反向插进去。
    # pET-28a 的整个表达盒（T7 promoter / RBS / His-tag）都在负链上，用 BamHI
    # 做 5'、XhoI 做 3' 克隆出来的基因必须反向才与启动子同向。
    insert_reverse: bool = False
    # 被切走的载体碱基数。切除区间跨原点时 right_cut - left_cut 是负数，
    # 不能靠坐标差反推，由规划函数显式给出（left==right 表示纯插入 = 0）
    removed_bp: int = 0
    warnings: List[str] = field(default_factory=list)

    @property
    def removed(self) -> int:
        """被切走的载体碱基数（left==right 表示纯插入）"""
        return max(0, self.removed_bp)

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


def _candidate_pairs(cuts_a: Sequence[int], cuts_b: Sequence[int],
                     length: int, circular: bool, allow_long: bool):
    """枚举候选切点对；allow_long=False 时先排除「切除区间超过半圈」的方向

    环状载体上要切掉的废片段一定比半圈短：反向的那一对切点算出来是
    `长度 - 废片段`（pET-30a 实测 188 bp 的废片段被算成 5234 bp），按它组装
    等于把整条主链当废片段扔掉。所以先只看短区间，实在没有短区间才放宽。
    """
    for a in cuts_a:
        for b in cuts_b:
            d = _forward_dist(a, b, length, circular)
            if d <= 0:
                continue
            if circular and not allow_long and d * 2 > length:
                continue
            yield a, b


def _best_ordered_pair(
    groups: Sequence[Tuple[Sequence[int], Sequence[int], str]],
    window: Optional[Tuple[int, int]], length: int, circular: bool,
) -> Optional[Tuple[Tuple[int, int], int, int, str]]:
    """在若干 (cuts_a, cuts_b, tag) 组合里挑最优切点对 → (score, a, b, tag)

    先只考虑不超过半圈的切除区间，挑不出来再放宽到整圈——放宽意味着要把
    载体大半条链当废片段切掉，调用方应据此告警。
    """
    for allow_long in (False, True):
        best: Optional[Tuple[Tuple[int, int], int, int, str]] = None
        for cuts_a, cuts_b, tag in groups:
            for a, b in _candidate_pairs(cuts_a, cuts_b, length, circular, allow_long):
                score = _pair_score(a, b, window, length, circular)
                if score is None:
                    continue
                if best is None or score < best[0]:
                    best = (score, a, b, tag)
        if best is not None:
            return best
    return None


def pick_flanking(
    cuts_a: Sequence[int], cuts_b: Sequence[int], *,
    window: Optional[Tuple[int, int]] = None, length: int, circular: bool = True,
) -> Optional[Tuple[int, int, int]]:
    """挑一对切点，返回 (left, right, penalty)

    penalty 0 = 两个切点都落在 window（MCS 区间）内；1 = 切除区间与 window 相交；
    2 = 完全在 window 之外（调用方据此告警）。

    排序按 (penalty, 切除长度)：同 penalty 下取最短。**不能只看是不是「包住
    MCS 中点」**——pET-28a 的 MCS 158–301 而 BamHI/XhoI 切点 159/199 并不包含
    中点 229，只看中点会把 5329 bp 的主链当成要切掉的废片段。也**不能奖励
    「MCS 点落在整圈长区间里」**：那方向正好是反的（见 _candidate_pairs）。

    window 为 None（载体没有 MCS 注释）时按最短区间取。
    """
    best = _best_ordered_pair([(cuts_a, cuts_b, '')], window, length, circular)
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
    # 同一对切点反着算就是「整圈减去废片段」，所以还要先排除超过半圈的方向：
    # 一次性载体的推测 MCS 是个点，那个点正好落在某个切点上时，旧的 penalty
    # 会让整圈长区间胜出（pET-30a 实测：188 bp 的废片段被算成 5234 bp，
    # 再因「切点跨原点」回退成 MCS 整段替换，基因落在位点之外）。
    best = _best_ordered_pair(
        ((cuts5, cuts3, enzyme_5), (cuts3, cuts5, enzyme_3)),
        window, length, circular)

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
    cut_length = _forward_dist(left, right, length, circular)
    if score[0] == 2:
        warnings.append(
            f"{enzyme_5}/{enzyme_3} 的两个切点都不在 MCS 区间内"
            f"（将切除 {cut_length} bp），请核对酶切方案")
    if circular and cut_length * 2 > length:
        warnings.append(
            f"切除区间 {cut_length} bp 超过载体一半（{length} bp）：只有当两个方向"
            "都超过半圈时才会这样切，请核对酶切方案")

    # 左切点属于哪把酶：引物把 enzyme_5 的位点放在基因 5' 端。左切点是
    # enzyme_3 时，两个连接处的粘端是「反着」配的——基因只能以**反向互补**
    # 的形式插进去（pET-28a 的表达盒在负链上，BamHI 5' / XhoI 3' 的基因
    # 正是这种情况）。这不是可选的排布，是粘端配对的物理结果。
    reverse = left_enzyme != enzyme_5
    if not reverse:
        head, tail = site5[k5:], site3[:k3]
    else:
        head, tail = site3[k3:], site5[:k5]
        warnings.append(
            f"载体上 {enzyme_3} 的切点在前、{enzyme_5} 的切点在后：插入片段按"
            "负链（-）插入构建体（粘端只在同一位点间互补）；如需插入正链请把"
            f"两端酶对调（5' 端用 {enzyme_3}、3' 端用 {enzyme_5}）")

    return AssemblyPlan(
        "restriction", left, right, head=head, tail=tail,
        insert_reverse=reverse, removed_bp=cut_length,
        junction=(f"{left_enzyme} 切点 {left} / 另一端切点 {right}"
                  f"（切除 {cut_length} bp，位点在连接处复原"
                  + ("，插入片段在负链" if reverse else "") + "）"),
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
        cut_length = _forward_dist(left, right, length, circular)
        if penalty == 2:
            warnings.append(f"{enzyme} 的两个切点都不在 MCS 区间内"
                            f"（将切除 {cut_length} bp），请核对酶切方案")
    else:
        left = right = cuts[0]
        cut_length = 0
        warnings.append(f"载体上只有 1 个 {enzyme} 位点，按单切点纯插入组装")

    return AssemblyPlan(
        "golden_gate", left, right, head=overhang_5, tail=overhang_3,
        removed_bp=cut_length,
        junction=(f"{enzyme} 切点 {left}/{right}"
                  f"（切除 {cut_length} bp，overhang {overhang_5}/{overhang_3}）"),
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
    circular: bool = True,
) -> Tuple[str, List[Dict], int, int, List[str]]:
    """按方案生成构建体序列与注释 → (序列, 元件, 插入起点, 插入终点, 告警)

    切除区间跨过原点（right < left，载体编号不在 MCS 处起始时很常见）时不再
    直接放弃：把载体旋转到切除区间不跨原点、走同一套逻辑，构建体按旋转后的
    坐标输出（旧编号的 1 号碱基本来就在被切走的废片段里，无法保留），元件坐标
    同步旋转，图谱与序列始终自洽。
    """
    length = len(sequence)
    left, right = plan.left_cut, plan.right_cut
    # 反向插入：粘端只在同一位点间互补，指派与载体位点顺序相反时，构建体正链上
    # 放的是基因的反向互补（注释标负链），否则图谱上的 CDS/ORF 会整个反过来
    body = revcomp(insert) if plan.insert_reverse else insert
    fragment = plan.head + body + plan.tail
    strand = "-" if plan.insert_reverse else "+"
    warnings = list(plan.warnings)

    if length <= 0:
        return fragment, [], 1, len(fragment), warnings
    if right < left:
        if not circular:
            raise ValueError("cut pair wraps origin")
        # 旋转：新坐标 right+1 → 1，切除区间变成 (left-shift, length]
        shift = right
        rotated: List[Dict] = []
        for el in elements or []:
            start = int(el.get("start", 1))
            end = int(el.get("end", 1))
            rotated.append({
                **el,
                "start": ((start - shift - 1) % length) + 1,
                "end": ((end - shift - 1) % length) + 1,
            })
        sequence = sequence[shift:] + sequence[:shift]
        left, right = left - shift, length
        elements = rotated

    # 跨原点时坐标差为负，规划函数已把真实切除长度写进 removed_bp
    removed = plan.removed or max(0, right - left)
    construct = sequence[:left] + fragment + sequence[right:]
    # 插入片段在构建体上的坐标只算**基因本身**：两端的位点残留/overhang 属于
    # 克隆接头，算进 CDS 会让图谱上的编码区从 "GATCC" 开始、ORF 平移
    insert_start = left + 1 + len(plan.head)
    insert_end = insert_start + len(body) - 1
    delta = len(fragment) - removed

    features: List[Dict] = [{
        "name": insert_name or "insert",
        "type": "CDS",
        "start": insert_start,
        "end": max(insert_start, insert_end),
        "strand": strand,
        "description": ("Designed insert (reverse complement on the minus strand)"
                        if plan.insert_reverse else "Designed insert"),
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

# ==================== 表达方向自检 ====================
#
# 用户把 5'/3' 端的两把酶填反是**最常见**的误操作：两个输入框都叫「限制酶」，
# 谁也没写清是「基因的 5' 端」还是「载体上左边那个位点」。填反之后粘端照样配得
# 上、构建体照样拼得出来——只是基因整个反着进质粒，转录出来的是一条反义 RNA。
# 图谱上 CDS 箭头朝了另一头，用户要打开导出文件才发现。
#
# 这里用载体自己的注释做一次自检：启动子紧邻插入区间、且转录方向指向区间时，
# 基因就只能按那条链插进去（pET-30a 的 T7 promoter 在负链、位于 NdeI 一侧，
# 所以基因必须 NdeI → XhoI，即负链）。


def _is_promoter(element: Dict) -> bool:
    """元件是不是启动子（GenBank 里可能标成 promoter/regulatory，名字里也常带）"""
    kind = str(element.get("type") or "").strip().lower()
    if kind == "promoter":
        return True
    name = str(element.get("name") or "").lower()
    return "promoter" in name or "启动子" in name


def cassette_direction(
    elements: Optional[Sequence[Dict]],
    left_cut: int,
    right_cut: int,
    *,
    max_gap: int = 1500,
) -> Tuple[Optional[str], Optional[Dict]]:
    """载体启动子要求插入片段以哪条链组装 → (strand, 启动子元件)

    插入片段占据被切走的区间 (left_cut, right_cut]。判定口径：

    * 正链启动子在区间**上游**（end <= left_cut，间距 <= max_gap）：转录向
      正方向读取，基因 5' 端必须贴在 left_cut 上 → "+"；
    * 负链启动子在区间**下游**（start > right_cut，间距 <= max_gap）：转录向
      负方向读取，基因 5' 端必须贴在 right_cut 上 → "-"。

    两个方向都有启动子（双向表达盒）、一个都没有、或切点跨原点时返回
    (None, None)——证据不足就不猜，交给调用方维持原行为。
    """
    if not elements or right_cut <= left_cut:
        return None, None
    plus: List[Tuple[int, Dict]] = []
    minus: List[Tuple[int, Dict]] = []
    for element in elements:
        if not _is_promoter(element):
            continue
        try:
            start = int(element.get("start") or 0)
            end = int(element.get("end") or 0)
        except (TypeError, ValueError):
            continue
        if start <= 0 or end <= 0:
            continue
        if str(element.get("strand") or "+") == "-":
            gap = start - right_cut
            if 0 < gap <= max_gap:
                minus.append((gap, element))
        else:
            gap = left_cut - end
            if 0 <= gap <= max_gap:
                plus.append((gap, element))
    if plus and not minus:
        return "+", min(plus, key=lambda item: item[0])[1]
    if minus and not plus:
        return "-", min(minus, key=lambda item: item[0])[1]
    return None, None

