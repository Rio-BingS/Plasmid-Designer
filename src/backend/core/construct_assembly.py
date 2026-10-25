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

import re
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
    # 无缝连接（Gibson 同源臂越过位点、重组消费掉位点碱基）：切点已外扩到
    # 完整位点边界，构建体里没有位点序列。起始密码子语义归基因自身，
    # CDS 扩展据此跳过——不能用「head/tail 为空」判断，平末端酶保留位点时
    # head/tail 同样是空串
    seamless: bool = False
    # 自定义同源臂：解析后的臂序列（按装配链向从载体位点取正链或其反向互补）。
    # 引物设计直接消费它——用户粘贴的栏位顺序/链向可能已被自动纠正
    arm_5_seq: str = ""
    arm_3_seq: str = ""
    # 同两臂的「构建体取向」（载体正链、按左/右连接点摆放）：arm_left_seq 是
    # 构建体上左连接点外侧的正链臂、arm_right_seq 是右连接点外侧的正链臂。
    # 合成分子必须用这一对——arm_5_seq/arm_3_seq 是「基因取向」（负链装配时
    # 左右互换且各自反互补），直接拼接会把合成分子两臂装反，首/尾 oligo
    # 与图谱对不上（pET-21a 实测）
    arm_left_seq: str = ""
    arm_right_seq: str = ""
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


def restriction_site(enzyme: str) -> str:
    """酶的识别序列（大写）；未知/不可用于建模的酶返回空串（不抛错）。

    供起始密码子一致性检查用：NdeI CATATG / NcoI CCATGG 这类位点在连接处
    复原后自带 ATG，紧贴基因时通常充当起始密码子。"""
    try:
        site, _, _ = _enzyme_cut(enzyme)
        return site.upper()
    except ValueError:
        return ""


def sticky_overhang(enzyme: str) -> str:
    """酶切产生的突出单链序列（5'→3' 字母）；平末端返回空串。

    cut=(c1,c2)：c2>c1 为 5' 突出（顶链 site[c1:c2]）；c1>c2 为 3' 突出，
    突出的是底链，其 5'→3' 字母为 site[c2:c1] 的反向互补。连接配对只看
    突出链的 5'→3' 序列（回文粘端如 TCGA/TA 任意两端互配），因此两把酶
    粘端相同 = 连接方向不唯一。

    不可用于双酶切建模的酶（Type IIS 等）返回空串——调用方只在双酶切
    方案成立时才用它做方向性检查。
    """
    try:
        site, c1, c2 = _enzyme_cut(enzyme)
    except ValueError:
        return ""
    if c1 == c2:
        return ""
    if c2 > c1:
        return site[c1:c2].upper()
    from core.seq_utils import revcomp as _rc
    return _rc(site[c2:c1])


def gibson_junction_fusion(elements, pos: int) -> Optional[Dict]:
    """Gibson 线性化点是否落在正链 CDS/tag 元件内或紧贴其末端（融合克隆）。

    pos 与 plan_gibson 的 left_cut 同义（1-based「保留到该位」），插入片段
    占据 (pos, ...]：元件 start <= pos <= end 时，该元件的碱基直接续接插入
    片段——基因与元件（如 His-tag）融合，读码框与内部 Met 语义取代
    「起始密码子」语义。负链元件与正链插入片段不融合，不算。
    """
    for el in elements or []:
        kind = str(el.get("type") or "").strip().lower()
        if kind not in ("cds", "tag"):
            continue
        if str(el.get("strand") or "+") != "+":
            continue
        try:
            s, e = int(el.get("start") or 0), int(el.get("end") or 0)
        except (TypeError, ValueError):
            continue
        if s > 0 and s <= pos <= e:
            return el
    return None


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
    warnings: List[str], join: str = "sticky", keep_sites: bool = True,
) -> Optional[AssemblyPlan]:
    """双酶切：两个切点之间整段被切走，基因两端补回位点残留碱基

    载体上没有位点时返回 None（调用方回退到「按 MCS 区间替换」并保留告警）。
    join="sticky"（粘端连接，默认）或 "gibson"（双酶切线性化 + Gibson 同源臂
    连接）：两者几何完全一致，仅连接处的物理约束不同——粘端配对强制基因
    取向，Gibson 取向由同源臂设计决定，反向排布的告警文案随之区分。
    keep_sites=False 仅对 join="gibson" 有意义：同源臂越过位点、重组把位点
    碱基消费掉（无缝连接），切点各向外扩到完整位点边界，构建体里不再出现
    位点序列——此时位点无法充当起始密码子，起始密码子语义归基因自身。
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
        if join == "gibson" and not keep_sites:
            # 无缝纯插入：位点两侧序列一并去除，切点外扩到各自完整位点边界
            # （与双切点无缝同一套域归一规则）
            left = point - k5
            right = point + len(site3) - k3
            if circular:
                if length:
                    left = ((left - 1) % length) + 1
                    right = ((right - 1) % length) + 1
            elif left < 0 or right > length:
                warnings.append(
                    f"{enzyme_5}/{enzyme_3} 位点紧贴载体末端，无缝外扩越界："
                    "已按 MCS 区间替换构建体")
                return None
            removed = _forward_dist(left, right, length, circular)
            warnings.append(
                f"酶切位点已从构建体去除（无缝连接）：连接处不再保留 "
                f"{enzyme_5}/{enzyme_3} 位点序列，位点无法再次酶切")
            return AssemblyPlan(
                "restriction", left, right, head="", tail="",
                removed_bp=removed, seamless=True,
                junction=(f"{enzyme_5} 单切点 {point}（纯插入，位点已去除，"
                          "无缝连接，Gibson 同源臂连接）"),
                warnings=warnings)
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
        if join == "gibson":
            warnings.append(
                f"载体上 {enzyme_3} 的切点在前、{enzyme_5} 的切点在后：插入片段按"
                "负链（-）组装。Gibson 的基因取向由同源臂设计决定，请确认引物"
                "同源臂使基因 5′ 端位于该端；如需反义构建请勾选「反向」重新设计")
        else:
            warnings.append(
                f"载体上 {enzyme_3} 的切点在前、{enzyme_5} 的切点在后：插入片段按"
                "负链（-）插入构建体（粘端只在同一位点间互补）；如需反义构建，"
                "请勾选「反向」后重新设计")

    seamless = join == "gibson" and not keep_sites
    if seamless:
        # 无缝连接：同源臂越过位点、重组把位点碱基消费掉——切点各向外扩到
        # 完整位点边界，构建体里不再出现位点序列（head/tail 置空）。
        # 切点定义域是 [1..length]（0 ≡ length ≡ 原点），外扩是裸算术，
        # 跨原点位点扩完会越界（负数 / 超过 length）；不归一的话 apply_plan
        # 的 Python 切片会静默产出错误构建体（实测多出上百 bp）
        k_left, site_left = (k5, site5) if not reverse else (k3, site3)
        k_right, site_right = (k3, site3) if not reverse else (k5, site5)
        left -= k_left
        right += len(site_right) - k_right
        if circular:
            if length:
                left = ((left - 1) % length) + 1
                right = ((right - 1) % length) + 1
        elif left < 0 or right > length:
            warnings.append(
                f"{enzyme_5}/{enzyme_3} 位点紧贴载体末端，无缝外扩越界："
                "已按 MCS 区间替换构建体")
            return None
        head, tail = "", ""
        cut_length = _forward_dist(left, right, length, circular)
        warnings.append(
            f"酶切位点已从构建体去除（无缝连接）：连接处不再保留 "
            f"{enzyme_5}/{enzyme_3} 位点序列，位点无法再次酶切")

    if seamless:
        junction = (f"双酶切线性化（切除 {cut_length} bp，"
                    f"{enzyme_5}/{enzyme_3} 位点已去除，无缝连接，Gibson 同源臂连接"
                    + ("，插入片段在负链" if reverse else "") + "）")
    else:
        junction = (f"{left_enzyme} 切点 {left} / 另一端切点 {right}"
                    f"（切除 {cut_length} bp，位点在连接处复原"
                    + ("，插入片段在负链" if reverse else "")
                    + ("，Gibson 同源臂连接）" if join == "gibson" else "）"))

    return AssemblyPlan(
        "restriction", left, right, head=head, tail=tail,
        insert_reverse=reverse, removed_bp=cut_length,
        seamless=seamless,
        junction=junction,
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


def plan_custom_arms(
    backbone: str, arm_a: str, arm_b: str, *,
    circular: bool = True, min_len: int = 15, max_len: int = 200,
    direction: str = "auto", elements=None,
) -> AssemblyPlan:
    """用户自定义同源臂的 Gibson 定位：由臂序列反推线性化边界。

    **顺序与链向无关**（与「填两把酶切位点」同一体验）：两条臂贴进哪个栏、
    按正链还是反向互补贴都不影响结果——

    1. 各自在载体上定位（正反两种取向都试，须唯一精确匹配）；
    2. 按位置自动分左右：上游同源区成为臂5 侧、下游成为臂3 侧
       （用户栏位贴反时自动纠正并说明）；
    3. 装配链向按「用户显式方向 > 载体启动子（cassette_hint）> 粘贴链向
       共识（两条都按反向互补匹配 = 负链）> 默认正链」决定，臂序列按
       链向从载体位点重建（负链时臂5 = 右端同源区的反向互补），写入
       plan.arm_5_seq / plan.arm_3_seq 供引物设计直接消费。

    定位失败/多处匹配/两臂重叠围不出区间时抛 ValueError（用户输入问题
    应显式失败）。v1 限制：匹配不跨线性编号原点（跨原点臂请旋转载体
    坐标后重试）。
    """
    def _clean(name: str, seq: str) -> str:
        seq = re.sub(r"\s+", "", str(seq or "")).upper()
        if not seq:
            raise ValueError(f"{name}为空：请填写重组臂序列")
        if len(seq) < min_len:
            raise ValueError(
                f"{name}仅 {len(seq)} bp：短于 {min_len}bp 的同源臂组装效率低，"
                f"请使用 ≥{min_len}bp 的臂")
        if len(seq) > max_len:
            raise ValueError(f"{name}长 {len(seq)} bp：超过上限 {max_len}bp，"
                             "请确认没有误粘贴整段序列")
        bad = set(seq) - set("ACGT")
        if bad:
            raise ValueError(f"{name}含非 ACGT 碱基：{''.join(sorted(bad))}")
        return seq

    a = _clean("臂①（原 5′ 臂栏）", arm_a)
    b = _clean("臂②（原 3′ 臂栏）", arm_b)
    length = len(backbone)

    def _find_all(query: str) -> List[int]:
        """非跨原点的全部精确匹配起点（0-based）"""
        out, start = [], 0
        while True:
            i = backbone.find(query, start)
            if i < 0:
                return out
            out.append(i)
            start = i + 1

    from core.seq_utils import revcomp as _revcomp

    def _locate(seq: str, label: str) -> Tuple[int, bool]:
        """两个取向的全部匹配：(起点, 是否反向互补匹配)。

        回文臂自身与其反向互补在同一位点重复命中，按同位点去重（记正链）；
        不同位点的多处命中仍是不唯一，显式报错。
        """
        by_pos: Dict[int, bool] = {}
        for p in _find_all(seq):
            by_pos.setdefault(p, False)
        for p in _find_all(_revcomp(seq)):
            if p not in by_pos:
                by_pos[p] = True
        if len(by_pos) > 1:
            pos_desc = "、".join(
                f"{p + 1}（{'负链' if rc else '正链'}）" for p, rc in sorted(by_pos.items()))
            raise ValueError(
                f"{label}在载体上匹配到 {len(by_pos)} 处（{pos_desc}）："
                "定位不唯一，请加长臂序列或调整臂的位置")
        if not by_pos:
            raise ValueError(
                f"{label}在载体上无精确匹配：请确认臂序列来自该载体"
                "（两种取向均已尝试；跨线性编号原点的臂请旋转载体坐标）")
        return next(iter(by_pos.items()))

    pa, rc_a = _locate(a, "臂①（原 5′ 臂栏）")
    pb, rc_b = _locate(b, "臂②（原 3′ 臂栏）")

    # 按位置分左右：上游同源区 = 臂5 侧，下游 = 臂3 侧（栏位顺序无关）
    if pa + len(a) <= pb:
        left_pos, left_len, left_rc = pa, len(a), rc_a
        right_pos, right_len, right_rc = pb, len(b), rc_b
        left_label, right_label = "臂①", "臂②"
    elif pb + len(b) <= pa:
        left_pos, left_len, left_rc = pb, len(b), rc_b
        right_pos, right_len, right_rc = pa, len(a), rc_a
        left_label, right_label = "臂②", "臂①"
    else:
        raise ValueError(
            "两条重组臂的相对位置无法围出插入区间："
            f"臂① 匹配在 {pa + 1}（{'负链' if rc_a else '正链'}）、"
            f"臂② 匹配在 {pb + 1}（{'负链' if rc_b else '正链'}）——"
            "两臂同源区重叠，请确认各自位于插入区间的两侧")

    left = left_pos + left_len
    right = right_pos
    removed = right - left

    # 装配链向：显式方向 > 启动子 > 粘贴链向共识 > 默认正链
    # （相邻臂 = 纯插入时区间为空，cassette_hint 会提前返回——用
    # right_hint 技巧给一个 1bp 伪区间，让启动子判定照常工作）
    right_hint = right if right > left else left + 1
    hint = cassette_hint(elements or [], left, right_hint)
    hint_strand = hint.get("strand")
    if direction == "plus":
        strand = "+"
    elif direction == "minus":
        strand = "-"
    elif direction == "reverse":
        strand = "-" if (hint_strand or "+") == "+" else "+"
    elif hint_strand:
        strand = hint_strand
    elif rc_a and rc_b:
        strand = "-"      # 两条都按反向互补匹配：沿用粘贴链向共识
    else:
        strand = "+"

    seq_left_plus = backbone[left_pos:left_pos + left_len].upper()
    seq_right_plus = backbone[right_pos:right_pos + right_len].upper()
    # 构建体取向的臂（左连接点外侧正链 / 右连接点外侧正链）：合成分子直接用
    arm_left_seq, arm_right_seq = seq_left_plus, seq_right_plus
    if strand == "+":
        arm_5_seq, arm_3_seq = seq_left_plus, seq_right_plus
    else:
        # 基因取向（引物用）：负链装配时基因 5′ 在右连接点，臂随基因反互补
        arm_5_seq, arm_3_seq = _revcomp(seq_right_plus), _revcomp(seq_left_plus)

    notes: List[str] = []
    if left_label != "臂①":
        notes.append(
            f"两条臂的栏位顺序与载体位置相反，已自动对调："
            f"{left_label} 栏的序列在上游（充当臂5 侧）、"
            f"{right_label} 栏的序列在下游（充当臂3 侧）——同源臂填写顺序无关")
    for label, pasted, resolved, pasted_rc in (
            ("臂①", a, arm_5_seq, rc_a), ("臂②", b, arm_3_seq, rc_b)):
        if resolved.upper() != pasted.upper():
            notes.append(
                f"{label}按装配链向取自载体同源区"
                f"（{'反向互补' if pasted_rc else '正链'}匹配）：引物中的臂序列"
                "以解析结果为准，粘贴的链向/栏位不影响构建体")

    if hint_strand:
        notes.append(
            f"装配方向按载体启动子判定：基因按"
            f"{'负链' if strand == '-' else '正链'}插入"
            + ("（与粘贴链向不同，已自动纠正）"
               if (rc_a and rc_b) != (strand == "-") else ""))
    elif direction in ("plus", "minus", "reverse"):
        notes.append(f"装配方向按你的选择：基因按"
                     f"{'负链' if strand == '-' else '正链'}插入")
    elif not (rc_a and rc_b):
        notes.append("载体上没有可判定的启动子/RBS，装配方向按默认（正链）处理；"
                     "若与预期不符，请在表单勾选「反向」重新设计")

    junction = (f"自定义同源臂定位：臂5 末端 {left} / 臂3 起点 {right + 1}"
                f"（替换 {removed} bp，无缝，"
                f"{'负链' if strand == '-' else '正链'}装配）")
    return AssemblyPlan("gibson", left, right, head="", tail="",
                        insert_reverse=(strand == "-"), removed_bp=removed,
                        arm_5_seq=arm_5_seq, arm_3_seq=arm_3_seq,
                        arm_left_seq=arm_left_seq, arm_right_seq=arm_right_seq,
                        junction=junction, warnings=notes)


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
    if left < 0 or right > length:
        # 切点越出 [0, length] 切片域：Python 负索引/越界切片会静默给出
        # 错误序列，宁可显式失败走 MCS 回退（调用方已有 ValueError 分支）
        raise ValueError("cut pair wraps origin")
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

# ==================== 表达方向判定 ====================
#
# 基因该读哪条链，是载体表达盒的物理事实，不该由用户填的「5'/3' 端」决定：
# pET-30a 的 T7 promoter 在负链、位于 NdeI 一侧，基因只能 NdeI → XhoI。
# 两端酶填反了照样拼得出构建体，只是转录出一条反义 RNA——图上箭头反了，
# 而结果页原先一声不响（用户靠把导出的 .gb 拖进 SnapGene 才发现）。
#
# 判定顺序：紧邻插入区间的启动子（首选）→ RBS / 终止子等方向性特征（双向时
# 用来打破平局）→ 都没有就让用户按预览图自己选。判定只在证据唯一时给结论。


def is_promoter(element: Dict) -> bool:
    """元件是不是启动子（GenBank 里可能标成 promoter/regulatory，名字里也常带）"""
    kind = str(element.get("type") or "").strip().lower()
    if kind == "promoter":
        return True
    name = str(element.get("name") or "").lower()
    return "promoter" in name or "启动子" in name


def _is_rbs(element: Dict) -> bool:
    """核糖体结合位点：紧贴基因起始端的强方向性证据"""
    kind = str(element.get("type") or "").strip().lower()
    if kind in ("rbs", "ribosome_binding_site"):
        return True
    name = str(element.get("name") or "").lower()
    return "rbs" in name or "核糖体结合" in name or "shine" in name


def _is_terminator(element: Dict) -> bool:
    """转录终止子：紧贴基因 3' 端，用来确认另一端"""
    kind = str(element.get("type") or "").strip().lower()
    if kind in ("terminator", "stop_codon"):
        return True
    name = str(element.get("name") or "").lower()
    return "terminator" in name or "终止" in name


def cassette_hint(
    elements: Optional[Sequence[Dict]],
    left_cut: int,
    right_cut: int,
    *,
    max_gap: int = 3000,
    rbs_gap: int = 120,
    terminator_gap: int = 800,
) -> Dict:
    """按载体注释推断基因该读哪条链 → 判定结果

    插入片段占据被切走的区间 (left_cut, right_cut]。正链启动子在区间**上游**
    （end <= left_cut）时转录向正方向读进区间，基因 5' 端必须贴 left_cut；
    负链启动子在区间**下游**（start > right_cut）时基因 5' 端必须贴 right_cut。
    两侧都有启动子（双向表达盒）时用 RBS / 终止子的位置打破平局：

    * RBS 贴在某一侧 → 该侧是表达盒（+2）；
    * 终止子贴在某一侧 → 转录从该侧读出去，属另一侧（+2）。

    max_gap 取 3000：启动子与插入位点之间常隔大标签/MCS/接头（pET 系列的
    His-tag、 thrombin、 dozens bp 的 MCS），1500 会把合法表达盒判成
    「无证据」→ 方向回落默认正链（实测用户载体启动子距切点 ~1600bp 时
    方向判定失效）。返回 {"strand", "score", "promoter", "evidence",
    "ambiguous"}。strand 为 None 表示证据不唯一或不足——调用方此时应让
    用户手选，不要瞎猜。
    """
    hint: Dict = {"strand": None, "score": {"+": 0, "-": 0}, "promoter": None,
                  "evidence": [], "ambiguous": False}
    if not elements or right_cut <= left_cut:
        return hint

    def _promoter_weight(gap: int) -> int:
        """启动子按邻近度加权：贴着插入位点的启动子是表达盒主证（3），
        隔了大标签/长 MCS 的仍是强证据（2），窗口边缘的只能算弱证据（1）。

        没有权重时，窗口内一个**无关表达盒**的启动子（恰好朝插入区读）
        与真正的近处表达盒平票甚至压过它——3000 窗口放宽后这不再是
        理论风险。权重差 ≥2 的对立证据不再触发歧义，近处启动子稳定胜出；
        近邻对近邻（3 对 3）仍然歧义、交给用户手选。
        """
        if gap <= 300:
            return 3
        if gap <= 1000:
            return 2
        return 1

    def _span(element: Dict) -> Optional[Tuple[int, int]]:
        try:
            start = int(element.get("start") or 0)
            end = int(element.get("end") or 0)
        except (TypeError, ValueError):
            return None
        return (start, end) if start > 0 and end > 0 else None

    plus_promoters: List[Tuple[int, Dict]] = []
    minus_promoters: List[Tuple[int, Dict]] = []
    for element in elements:
        span = _span(element)
        if span is None:
            continue
        start, end = span
        if is_promoter(element):
            if str(element.get("strand") or "+") == "-":
                gap = start - right_cut
                if 0 < gap <= max_gap:
                    minus_promoters.append((gap, element))
            else:
                gap = left_cut - end
                if 0 <= gap <= max_gap:
                    plus_promoters.append((gap, element))
        elif _is_rbs(element):
            if min(abs(start - right_cut), abs(end - right_cut)) <= rbs_gap:
                hint["score"]["-"] += 2
                hint["evidence"].append({"kind": "rbs", "side": "-",
                                         "name": element.get("name") or "RBS"})
            elif min(abs(start - left_cut), abs(end - left_cut)) <= rbs_gap:
                hint["score"]["+"] += 2
                hint["evidence"].append({"kind": "rbs", "side": "+",
                                         "name": element.get("name") or "RBS"})
        elif _is_terminator(element):
            # 终止子在基因 3' 端之后：贴在 left 外 → 转录从 left 读出去 → 负链
            if end <= left_cut and left_cut - end <= terminator_gap:
                hint["score"]["-"] += 2
                hint["evidence"].append({"kind": "terminator", "side": "-",
                                         "name": element.get("name") or "terminator"})
            elif start >= right_cut and start - right_cut <= terminator_gap:
                hint["score"]["+"] += 2
                hint["evidence"].append({"kind": "terminator", "side": "+",
                                         "name": element.get("name") or "terminator"})

    for gap, element in plus_promoters:
        hint["score"]["+"] += _promoter_weight(gap)
        hint["evidence"].append({"kind": "promoter", "side": "+", "gap": gap,
                                 "name": element.get("name") or "promoter",
                                 "start": element.get("start"), "end": element.get("end"),
                                 "strand": str(element.get("strand") or "+")})
    for gap, element in minus_promoters:
        hint["score"]["-"] += _promoter_weight(gap)
        hint["evidence"].append({"kind": "promoter", "side": "-", "gap": gap,
                                 "name": element.get("name") or "promoter",
                                 "start": element.get("start"), "end": element.get("end"),
                                 "strand": str(element.get("strand") or "+")})

    plus, minus = hint["score"]["+"] , hint["score"]["-"]
    if plus or minus:
        if plus and minus and abs(plus - minus) < 2:
            # 两侧证据相当：双向表达盒或注释打架，交给用户判断
            hint["ambiguous"] = True
            return hint
        chosen = "+" if plus > minus else "-"
        hint["strand"] = chosen
        if chosen == "+" and plus_promoters:
            hint["promoter"] = min(plus_promoters, key=lambda item: item[0])[1]
        elif chosen == "-" and minus_promoters:
            hint["promoter"] = min(minus_promoters, key=lambda item: item[0])[1]
    return hint


def cassette_direction(
    elements: Optional[Sequence[Dict]],
    left_cut: int,
    right_cut: int,
    *,
    max_gap: int = 1500,
) -> Tuple[Optional[str], Optional[Dict]]:
    """兼容入口：→ (strand, 起决定作用的启动子元件)，口径见 cassette_hint"""
    hint = cassette_hint(elements, left_cut, right_cut, max_gap=max_gap)
    return hint["strand"], hint["promoter"]


# ==================== 公开化别名（跨层契约原为私有名）====================
_is_promoter = is_promoter  # 公开化前的私有名（历史引用兼容）
