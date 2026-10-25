"""引物设计的基础层：Tm 模型、序列筛查规则与数据类

从 primer_designer.py 迁出（纯搬运）：PrimerDesigner 类与其引用的
模块级常量/函数/数据类分离，便于单独复用与测试。primer_designer
保留同名 re-export，既有 `from core.primer_designer import X` 不受影响。
"""

from functools import lru_cache
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List

from Bio.SeqUtils import MeltingTemp as _mt

from core.seq_utils import gc_percent, revcomp


# ---------------------------------------------------------------------------
# Tm 模型：最近邻（SantaLucia）+ Owczarzy 盐/Mg 校正
#
# 参数即典型 PCR 缓冲液条件，显式写明而不是用库默认值，
# 以便结果可复现、也便于按实际体系调整：
TM_NN_TABLE = _mt.DNA_NN4        # SantaLucia & Hicks 2004 统一最近邻参数
TM_NA_MM = 50.0                  # Na+ 浓度 (mM)
TM_MG_MM = 1.5                   # Mg2+ 浓度 (mM)
TM_DNTPS_MM = 0.6                # dNTP 总浓度 (mM)，与 Mg2+ 螯合后参与校正
TM_PRIMER_NM = 50.0              # 引物浓度 (nM)，PCR 常用 50-500 nM
TM_TEMPLATE_NM = 0.0             # 模板链浓度 (nM)：引物大幅过量，按 0 处理
TM_SALTCORR = 7                  # Owczarzy 2008 盐校正（含 Mg/dNTP 竞争）
# 最近邻模型至少需要一个碱基对（2 nt）才有意义
TM_MIN_LENGTH = 2

# 全基因合成：相邻 oligo 重叠区（退火成双链的那段）的可变长度范围。
# 组装 PCR 的退火由「重叠双链」的 Tm 决定，所以重叠长度是优化变量，
# 不是固定常数；18-25nt 是组装 PCR 的常规区间（DNAWorks 同量级）
OVERLAP_LENGTH_MIN = 18
OVERLAP_LENGTH_MAX = 25
# 重叠双链 Tm 极差达标线（°C）：组装 PCR 的常规设计目标是 ±2°C 上下
OVERLAP_TM_SPREAD_TARGET = 5.0
# 达不到时最多把片数往上加几轮（每轮 +2，保持偶数片）
OLIGO_TILE_ESCALATIONS = 4
# ---------------------------------------------------------------------------


def calculate_tm(seq: str) -> float:
    """最近邻法 Tm（°C），全库唯一的 Tm 实现。

    用 Biopython ``MeltingTemp.Tm_NN``（SantaLucia 最近邻参数 DNA_NN4）
    并带上述 PCR 缓冲液条件的盐/Mg/dNTP/浓度校正。相比旧的
    Wallace / Marmur-Doty 经验式，18-25mer 上系统性高约 7°C，
    且能区分相同长度与 GC 下的序列上下文差异。

    有效碱基（ACGT）不足 2 个时无最近邻可言，返回 0.0；
    N 等简并碱基按 Biopython 规则处理，不会抛异常。极短序列（约 8 nt
    以下）最近邻给出负值，表示室温下不成稳定双链，统一下限钳到 0.0。
    """
    seq = (seq or "").upper()
    if sum(seq.count(b) for b in "ACGT") < TM_MIN_LENGTH:
        return 0.0
    try:
        return max(0.0, _tm_nn(seq))
    except (ValueError, ZeroDivisionError, IndexError, KeyError):
        # 简并碱基过多导致最近邻表查不到时，Tm 无法定义
        return 0.0


@lru_cache(maxsize=8192)
def _tm_nn(seq: str) -> float:
    # 同一候选序列会在质量筛选、评分、构造 Primer 时各算一次，缓存去重
    return _mt.Tm_NN(
        seq,
        nn_table=TM_NN_TABLE,
        Na=TM_NA_MM,
        Mg=TM_MG_MM,
        dNTPs=TM_DNTPS_MM,
        dnac1=TM_PRIMER_NM,
        dnac2=TM_TEMPLATE_NM,
        saltcorr=TM_SALTCORR,
    )


# ---------------------------------------------------------------------------
# Golden Gate overhang 连接保真度筛查（规则版，无外部数据依赖）
#
# 依据 Potapov 2018 / Pryor 2020 的 T4 连接保真度实验给出的通用规则：
# 回文 overhang 会与自身连接、两个 overhang 相差不足 2 个碱基会互相错连、
# 0%/100% GC 的 overhang（AAAA/TTTT 类）连接效率与特异性都差。
GG_OVERHANG_LENGTH = 4
GG_MIN_MISMATCHES = 2


def _hamming(a: str, b: str) -> int:
    return sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))


def screen_gg_overhang_pair(overhang_5: str, overhang_3: str) -> List[str]:
    """筛查一对 Golden Gate overhang，返回告警文案（空列表表示通过）。

    只做规则判定，不加载连接保真度矩阵（那需要额外依赖与离线数据）。
    调用方应「告警而非报错」：严格拒绝会让某些载体完全无法设计。
    """
    issues: List[str] = []
    pair = (("5'", overhang_5.upper()), ("3'", overhang_3.upper()))

    for side, oh in pair:
        if len(oh) != GG_OVERHANG_LENGTH or any(b not in "ATGC" for b in oh):
            issues.append(f"{side} overhang {oh or '(空)'} 不是合法的 4nt 序列")
            continue
        if oh == revcomp(oh):
            issues.append(f"{side} overhang {oh} 是回文，会与自身连接成反向插入")
        gc = gc_percent(oh)
        if gc == 0 or gc == 100:
            issues.append(f"{side} overhang {oh} 的 GC 为 {gc:.0f}%，连接效率与特异性都差")

    a, b = pair[0][1], pair[1][1]
    if len(a) == len(b) == GG_OVERHANG_LENGTH:
        # 与对方、以及对方的反向互补（实际连接面）都要有足够差异
        dist = min(_hamming(a, b), _hamming(a, revcomp(b)))
        if dist < GG_MIN_MISMATCHES:
            issues.append(
                f"5' overhang {a} 与 3' overhang {b} 仅相差 {dist} 个碱基"
                f"（需 ≥{GG_MIN_MISMATCHES}），两端会互相错连"
            )
    return issues


class PrimerType(Enum):
    PRIMER = "primer"
    GIBSON_FP = "gibson_forward"
    GIBSON_RP = "gibson_reverse"
    GOLDENGATE_FP = "goldengate_forward"
    GOLDENGATE_RP = "goldengate_reverse"
    SYNTHESIS_OLIGO = "synthesis_oligo"
    SEQUENCING = "sequencing"


@dataclass
class Primer:
    """引物"""
    name: str
    sequence: str
    primer_type: PrimerType
    tm: float
    gc_content: float
    length: int
    target_start: int = 0  # 目标序列上的起始位置 (0-indexed)
    target_end: int = 0    # 目标序列上的结束位置
    overhang: str = ""     # 5' 突出端（如 Gibson 臂）
    restriction_site: str = "" # Golden Gate 酶切位点名称
    notes: str = ""
    
    @property
    def full_sequence(self) -> str:
        """完整引物序列（包含overhang）"""
        return self.overhang + self.sequence
    
    @property
    def annealing_region(self) -> str:
        """退火区域序列"""
        return self.sequence


@dataclass
class PrimerPair:
    """引物对"""
    forward: Primer
    reverse: Primer
    product_size: int
    annealing_temp: float  # 推荐退火温度
    
    def to_order_dict(self) -> Dict[str, str]:
        """转换为订单格式"""
        return {
            'name': self.forward.name.replace('_F', ''),
            'forward_seq': self.forward.full_sequence,
            'forward_tm': f"{self.forward.tm:.1f}",
            'reverse_seq': self.reverse.full_sequence,
            'reverse_tm': f"{self.reverse.tm:.1f}",
            'product_size': self.product_size,
            'recommended_ta': f"{self.annealing_temp:.1f}"
        }


# ==================== Golden Gate 拼接处位点检查 ====================

def gg_site_count(seq: str, enzyme_name: str) -> int:
    """识别序列出现次数（双链）。按识别序列计而不是按切点：线性片段末端的
    位点切点可能落在片段外而被 find_enzyme_sites 丢掉，拼在扩增子里却会切"""
    from core.enzyme_sites import _site_regex, get_enzyme

    spec = get_enzyme(enzyme_name)
    if spec is None:
        return 0
    site = spec["site"]
    rx = _site_regex(site)
    n = sum(1 for _ in rx.finditer(seq))
    if revcomp(site) != site:
        n += sum(1 for _ in rx.finditer(revcomp(seq)))
    return n


def _gg_spacer(enzyme_name: str, site: str, n: int, overhang: str, downstream: str) -> str:
    """挑选 n 个间隔碱基，使「GG + 位点 + 间隔 + overhang + 下游（插入片段一端）」
    的拼接区只含引物尾里那一个识别位点。优先 A*n（保持既有引物不变），
    其次其他均聚物与少量确定性组合；都不行退回 A*n（由调用方告警）。"""
    if n <= 0:
        return ""
    head = downstream[:max(0, len(site) - 1)]
    tried = set()

    def candidates():
        for b in "ATCG":
            yield b * n
        import itertools
        for k, combo in enumerate(itertools.product("ATCG", repeat=min(n, 4))):
            if k >= 256:
                break
            yield ("".join(combo) * (n // len(combo) + 1))[:n]

    for sp in candidates():
        if sp in tried:
            continue
        tried.add(sp)
        if gg_site_count(f"GG{site}{sp}{overhang}{head}", enzyme_name) == 1:
            return sp
    return "A" * n


def gg_junction_extra_sites(insert_seq: str, enzyme_name: str, forward_tail: str,
                            reverse_tail: str) -> int:
    """完整扩增子（F 尾 + 插入片段 + rc(R 尾)）里，除两端引物尾各一个与插入片段
    内部原有的位点之外，拼接处另外形成的识别位点数"""
    amplicon = forward_tail + insert_seq + revcomp(reverse_tail)
    return max(0, gg_site_count(amplicon, enzyme_name) - 2
               - gg_site_count(insert_seq, enzyme_name))


# ==================== 公开化别名（跨层契约原为私有名）====================
_gg_site_count = gg_site_count  # 公开化前的私有名（历史引用兼容）
