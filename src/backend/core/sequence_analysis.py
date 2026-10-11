"""
序列分析模块
提供限制性位点分析、ORF预测、GC分析等功能
"""
from collections.abc import Mapping
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict
import logging

from core.seq_utils import CODON_TO_AA, revcomp, gc_percent, translate

logger = logging.getLogger(__name__)


@dataclass
class RestrictionSite:
    """限制性酶切位点"""
    enzyme: str
    site: str
    cut_position: int
    recognition_start: int
    recognition_end: int
    strand: str  # '+' or '-'
    overhang: str  # '5' (5' overhang), '3' (3' overhang), or 'blunt'


@dataclass
class ORFResult:
    """ORF 预测结果"""
    start: int
    end: int
    strand: str
    length: int
    frame: int
    protein_sequence: str
    start_codon: str
    stop_codon: str
    gc_content: float
    is_complete: bool  # 是否有完整起始和终止密码子


@dataclass
class GCRegion:
    """GC 含量区域"""
    start: int
    end: int
    gc_content: float
    window_size: int
    is_extreme: bool  # 是否极端值


@dataclass
class SequenceAnalysisResult:
    """序列分析结果"""
    sequence_length: int
    gc_content: float
    gc_distribution: List[GCRegion]
    restriction_sites: List[RestrictionSite]
    orfs: List[ORFResult]
    coding_potential: float
    warnings: List[str]


# 常用限制性内切酶表（兼容视图）
#
# Name -> (recognition_seq, cut_offset, overhang_type)，形状与历史手写表
# 一致，供 design_service / primer_designer 等直接取识别序列拼引物或做
# 避让 motif 的调用方使用。数据由 core.enzyme_sites（Bio.Restriction /
# REBASE）派生，不再手写：cut_offset 为正向链切点相对识别序列起点的
# 偏移，overhang '5' / '3' / 'b'(平端)。
# 只收录识别序列为纯 ACGT 的默认菜单酶——调用方会把识别序列原样拼进
# 引物或做子串匹配，含 IUPAC 模糊码的酶（AvaI/AflIII/BsaHI/XcmI）不能
# 这样用；它们仍可在位点扫描（RestrictionSiteAnalyzer / find_enzyme_sites）
# 中按名点用。
_LEGACY_ORDER = (
    "EcoRI", "BamHI", "HindIII", "XhoI", "XbaI", "SalI", "PstI", "KpnI",
    "SmaI", "SbfI", "NotI", "NdeI", "NcoI", "SacI", "BglII", "AvrII",
    "SpeI", "AgeI", "MluI", "BsaI", "BsmBI", "BbsI", "EcoRV", "DraI",
    "PvuII", "StuI",
)
_OVERHANG_CODE = {"5prime": '5', "3prime": '3', None: 'b'}


def _legacy_entry(spec: Dict) -> Tuple[str, int, str]:
    return (spec["site"], spec["cut"][0], _OVERHANG_CODE[spec["overhang"]])


class _LazyEnzymeDict(Mapping):
    """RESTRICTION_ENZYMES：首次访问时才从 REBASE 构建（见 enzyme_sites）"""

    _data: Optional[Dict[str, Tuple[str, int, str]]] = None

    def _load(self) -> Dict[str, Tuple[str, int, str]]:
        if self._data is None:
            from core.enzyme_sites import DEFAULT_ENZYMES, get_enzyme
            order = list(_LEGACY_ORDER) + [n for n in DEFAULT_ENZYMES
                                           if n not in _LEGACY_ORDER]
            data = {}
            for name in order:
                spec = get_enzyme(name)
                if spec and all(b in "ACGT" for b in spec["site"]):
                    data[name] = _legacy_entry(spec)
            type(self)._data = data
        return self._data

    def __getitem__(self, key):
        return self._load()[key]

    def __iter__(self):
        return iter(self._load())

    def __len__(self) -> int:
        return len(self._load())

    def __repr__(self) -> str:
        return f"RESTRICTION_ENZYMES({len(self)} enzymes, REBASE-backed)"


RESTRICTION_ENZYMES = _LazyEnzymeDict()

# 起始密码子和终止密码子
START_CODONS = ["ATG", "GTG", "TTG"]
STOP_CODONS = ["TAA", "TAG", "TGA"]

# 遗传密码表收敛到 core.seq_utils（CODON_TABLE：氨基酸 → 密码子列表）
# 兼容别名：本模块历史导出方向为 密码子 → 氨基酸
CODON_TABLE = CODON_TO_AA


class RestrictionSiteAnalyzer:
    """限制性酶切位点分析器（扫描与酶参数统一走 core.enzyme_sites）"""

    def __init__(self, enzymes: Dict = None):
        """
        初始化

        Args:
            enzymes: 自定义酶字典 {name: (site, cut_offset, overhang)}，
                默认使用内置表（REBASE 派生）；默认模式下也可按名点用
                REBASE 中其他商业酶
        """
        self._custom = bool(enzymes)
        self.enzymes = enzymes or RESTRICTION_ENZYMES

    def _spec(self, enzyme_name: str) -> Optional[Dict]:
        """酶名 → enzyme_sites 统一参数 {name, site, cut, overhang}"""
        if self._custom:
            entry = self.enzymes.get(enzyme_name)
            if not entry:
                return None
            site, cut_offset, overhang = entry
            site = site.upper()
            # 旧式三元组只给正链切点，按对称切割补出反链切点
            return {
                "name": enzyme_name,
                "site": site,
                "cut": (cut_offset, len(site) - cut_offset),
                "overhang": {'5': "5prime", '3': "3prime"}.get(overhang),
            }
        from core.enzyme_sites import get_enzyme
        return get_enzyme(enzyme_name)

    def is_known(self, enzyme_name: str) -> bool:
        return self._spec(enzyme_name) is not None

    def find_sites(self, sequence: str, enzymes: List[str] = None) -> List[RestrictionSite]:
        """
        查找序列中的限制性位点（线性扫描）

        回文识别序列的正反链匹配是同一个物理位点，只按正链报一次；
        非回文酶按实际结合方向报 '+' 或 '-'。

        Args:
            sequence: DNA 序列
            enzymes: 要检测的酶列表，默认检测所有

        Returns:
            发现的限制性位点列表
        """
        from core.enzyme_sites import scan_sites

        sequence = sequence.upper().replace("U", "T")
        sites = []

        enzymes_to_check = enzymes if enzymes else list(self.enzymes.keys())

        for enzyme_name in enzymes_to_check:
            spec = self._spec(enzyme_name)
            if spec is None:
                logger.warning(f"未知酶: {enzyme_name}")
                continue
            site_len = len(spec["site"])
            overhang = _OVERHANG_CODE[spec["overhang"]]
            for a, strand, cut_fwd, _cut_rev in scan_sites(
                    sequence, spec, palindrome_both_strands=False):
                # cut_position 一律取正向链切点的「切点前一位」（1-based），
                # 与消化模拟及 Bio.Restriction.search 同一口径。此前反链位点
                # 取反链切点，Type IIS 因突出端长度偏 4 bp（审查 04 M1）
                sites.append(RestrictionSite(
                    enzyme=enzyme_name,
                    site=spec["site"],
                    cut_position=cut_fwd,
                    recognition_start=a + 1,  # 1-indexed
                    recognition_end=a + site_len,
                    strand=strand,
                    overhang=overhang,
                ))

        # 按位置排序
        sites.sort(key=lambda x: x.recognition_start)
        return sites

    def find_unique_sites(self, sequence: str) -> Dict[str, RestrictionSite]:
        """
        查找唯一酶切位点（仅出现一次）

        Returns:
            酶名 -> 位点 的字典
        """
        all_sites = self.find_sites(sequence)

        # 统计每个酶的位点数
        enzyme_counts = defaultdict(list)
        for site in all_sites:
            enzyme_counts[site.enzyme].append(site)

        # 返回唯一的
        unique = {}
        for enzyme, sites in enzyme_counts.items():
            if len(sites) == 1:
                unique[enzyme] = sites[0]

        return unique

    def _reverse_complement(self, sequence: str) -> str:
        """获取反向互补序列（收敛到 core.seq_utils.revcomp）"""
        return revcomp(sequence)

    def check_compatibility(self, enzyme1: str, enzyme2: str) -> bool:
        """
        检查两个酶是否产生兼容的末端

        兼容的判定按**实际突出端序列与方向**而非仅突出端类型：EcoRI
        （AATT）与 BamHI（GATC）虽同为 5' 突出，末端序列不同、不可互连；
        SpeI/XbaI 这类产生相同 5' 突出端（CTAG）的酶才判兼容；HindIII
        （5' AGCT）与 SacI（3' AGCT）序列相同但方向相反，也不兼容。
        平末端一律互相兼容。同一酶自身恒兼容；Type IIS 酶（BsaI 等）与
        突出端含模糊碱基的酶，其突出端由具体序列决定、无法预先推导，
        仅同一酶名时判兼容。

        Args:
            enzyme1, enzyme2: 酶名称

        Returns:
            是否兼容（可连接）
        """
        spec1, spec2 = self._spec(enzyme1), self._spec(enzyme2)
        if spec1 is None or spec2 is None:
            return False
        if enzyme1 == enzyme2:
            return True

        overhang1 = self._overhang_sequence(enzyme1)
        overhang2 = self._overhang_sequence(enzyme2)

        # 平末端（无突出）都与平末端兼容
        if overhang1 is None and overhang2 is None:
            return True
        # 一平一粘不可连接
        if overhang1 is None or overhang2 is None:
            return False
        # IIS / 不可推导标记：不同酶之间不预设兼容
        if overhang1 == "IIS" or overhang2 == "IIS":
            return False
        # 5' 与 3' 突出端不会互连；同方向且序列相同才可互连
        return spec1["overhang"] == spec2["overhang"] and overhang1 == overhang2

    def _overhang_sequence(self, enzyme_name: str) -> Optional[str]:
        """
        推导酶切后留下的粘性末端序列（正向链 5'→3' 读出）。

        由 REBASE 切割几何直接取：正/反链切点 (c1, c2) 之间的识别序列片段
        site[min:max]（EcoRI G^AATT_C → AATT；PstI C_TGCA^G → TGCA）。
        - 平末端 → None；
        - 切点落在识别序列之外（Type IIS）或片段含模糊碱基 → "IIS"
          （突出端随具体序列变化，不可预先推导）；
        - 未知酶 → None。
        """
        spec = self._spec(enzyme_name)
        if spec is None:
            return None
        c1, c2 = spec["cut"]
        if c1 == c2:
            return None
        lo, hi = sorted((c1, c2))
        if lo < 0 or hi > len(spec["site"]):
            return "IIS"
        sticky = spec["site"][lo:hi]
        if any(b not in "ACGT" for b in sticky):
            return "IIS"
        return sticky


class ORFPredictor:
    """ORF (开放阅读框) 预测器"""
    
    def __init__(self, min_length: int = 150):
        """
        初始化
        
        Args:
            min_length: 最小 ORF 长度（碱基数）
        """
        self.min_length = min_length
    
    def find_orfs(self, sequence: str) -> List[ORFResult]:
        """
        查找所有 ORF
        
        Args:
            sequence: DNA 序列
        
        Returns:
            ORF 列表
        """
        sequence = sequence.upper()
        orfs = []
        
        # 三种阅读框，正反链
        for strand in ['+', '-']:
            for frame in range(3):
                frame_orfs = self._find_orfs_in_frame(sequence, strand, frame)
                orfs.extend(frame_orfs)
        
        # 按长度排序
        orfs.sort(key=lambda x: x.length, reverse=True)
        return orfs
    
    def find_longest_orf(self, sequence: str) -> Optional[ORFResult]:
        """查找最长 ORF"""
        orfs = self.find_orfs(sequence)
        return orfs[0] if orfs else None
    
    def _find_orfs_in_frame(self, sequence: str, strand: str, frame: int) -> List[ORFResult]:
        """在特定阅读框中查找 ORF"""
        if strand == '-':
            sequence = self._reverse_complement(sequence)
        
        orfs = []
        start_positions = []
        
        for i in range(frame, len(sequence) - 2, 3):
            codon = sequence[i:i+3]
            
            if codon in START_CODONS:
                start_positions.append(i)
            elif codon in STOP_CODONS:
                # 找到终止密码子，匹配最近的起始
                while start_positions:
                    start = start_positions.pop(0)
                    orf_length = i + 3 - start
                    
                    if orf_length >= self.min_length:
                        protein = self._translate(sequence[start:i+3])
                        gc = self._calculate_gc(sequence[start:i+3])

                        if strand == '+':
                            orf_start, orf_end = start + 1, i + 3
                        else:
                            # 反链换回参考序列坐标（1-indexed、start<end、
                            # strand='-'，与 GenBank complement 特征约定一致）：
                            # revcomp 空间 [start, i+3) 对应参考空间
                            # [L-i-2, L-start]，推导见 tests/test_sequence_analysis.py
                            orf_start = len(sequence) - i - 2
                            orf_end = len(sequence) - start
                        orf = ORFResult(
                            start=orf_start,
                            end=orf_end,
                            strand=strand,
                            length=orf_length,
                            frame=frame,
                            protein_sequence=protein,
                            start_codon=sequence[start:start+3],
                            stop_codon=codon,
                            gc_content=gc,
                            is_complete=True
                        )
                        orfs.append(orf)

        # 处理未终止的 ORF
        for start in start_positions:
            orf_length = len(sequence) - start
            if orf_length >= self.min_length:
                protein = self._translate(sequence[start:])
                gc = self._calculate_gc(sequence[start:])

                if strand == '+':
                    orf_start, orf_end = start + 1, len(sequence)
                else:
                    # 反链：revcomp 空间 [start, L) 对应参考空间 [1, L-start]
                    orf_start = 1
                    orf_end = len(sequence) - start
                orf = ORFResult(
                    start=orf_start,
                    end=orf_end,
                    strand=strand,
                    length=orf_length,
                    frame=frame,
                    protein_sequence=protein,
                    start_codon=sequence[start:start+3],
                    stop_codon="",
                    gc_content=gc,
                    is_complete=False
                )
                orfs.append(orf)
        
        return orfs
    
    def _translate(self, sequence: str) -> str:
        """翻译 DNA 序列为蛋白质（遇终止密码子截断）"""
        return translate(sequence, stop_at_stop=True)

    def _reverse_complement(self, sequence: str) -> str:
        """反向互补（收敛到 core.seq_utils.revcomp）"""
        return revcomp(sequence)

    def _calculate_gc(self, sequence: str) -> float:
        """计算 GC 含量（百分数）"""
        return gc_percent(sequence)


class GCAnalyzer:
    """GC 含量分析器"""
    
    def __init__(self, window_size: int = 100, step_size: int = 50):
        """
        初始化
        
        Args:
            window_size: 滑动窗口大小
            step_size: 步长
        """
        self.window_size = window_size
        self.step_size = step_size
    
    def analyze(self, sequence: str) -> Tuple[float, List[GCRegion]]:
        """
        分析 GC 含量
        
        Args:
            sequence: DNA 序列
        
        Returns:
            (整体GC含量, GC分布列表)
        """
        sequence = sequence.upper()
        
        # 整体 GC 含量
        total_gc = self._calculate_gc(sequence)
        
        # 滑动窗口分析
        regions = []
        for i in range(0, len(sequence) - self.window_size + 1, self.step_size):
            window = sequence[i:i+self.window_size]
            gc = self._calculate_gc(window)
            
            # 标记极端值 (<30% 或 >70%)
            is_extreme = gc < 30 or gc > 70
            
            region = GCRegion(
                start=i + 1,
                end=i + self.window_size,
                gc_content=gc,
                window_size=self.window_size,
                is_extreme=is_extreme
            )
            regions.append(region)
        
        return total_gc, regions
    
    def find_gc_extremes(self, sequence: str, threshold: float = 0.2) -> List[GCRegion]:
        """
        查找极端 GC 区域
        
        Args:
            sequence: DNA 序列
            threshold: 与平均值偏差阈值（百分比）
        
        Returns:
            极端 GC 区域列表
        """
        total_gc, regions = self.analyze(sequence)
        
        extremes = []
        for region in regions:
            if abs(region.gc_content - total_gc) > threshold * 100:
                extremes.append(region)
        
        return extremes
    
    def _calculate_gc(self, sequence: str) -> float:
        """计算 GC 含量（收敛到 core.seq_utils）"""
        return gc_percent(sequence)


class SequenceAnalyzer:
    """综合序列分析器"""
    
    def __init__(self):
        self.restriction_analyzer = RestrictionSiteAnalyzer()
        self.orf_predictor = ORFPredictor()
        self.gc_analyzer = GCAnalyzer()
    
    def analyze(
        self,
        sequence: str,
        check_restriction: bool = True,
        check_orf: bool = True,
        check_gc: bool = True,
        enzymes: List[str] = None
    ) -> SequenceAnalysisResult:
        """
        综合序列分析
        
        Args:
            sequence: DNA 序列
            check_restriction: 是否检测限制性位点
            check_orf: 是否预测 ORF
            check_gc: 是否分析 GC 含量
            enzymes: 要检测的酶列表
        
        Returns:
            分析结果
        """
        sequence = sequence.upper().replace('\n', '').replace(' ', '')
        warnings = []
        
        # 基本统计
        seq_length = len(sequence)
        
        # GC 分析
        gc_content = 0.0
        gc_regions = []
        if check_gc:
            gc_content, gc_regions = self.gc_analyzer.analyze(sequence)
            extremes = [r for r in gc_regions if r.is_extreme]
            if extremes:
                warnings.append(f"发现 {len(extremes)} 个极端 GC 区域")
        
        # 限制性位点分析
        restriction_sites = []
        if check_restriction:
            restriction_sites = self.restriction_analyzer.find_sites(sequence, enzymes)
            if len(restriction_sites) > 10:
                warnings.append(f"检测到 {len(restriction_sites)} 个限制性位点，可能影响克隆")
        
        # ORF 预测
        orfs = []
        coding_potential = 0.0
        if check_orf:
            orfs = self.orf_predictor.find_orfs(sequence)
            if orfs:
                # 计算编码潜力：最长 ORF 占序列比例
                longest = orfs[0]
                coding_potential = longest.length / seq_length * 100
                if not longest.is_complete:
                    warnings.append("最长 ORF 缺少终止密码子")
        
        # 其他警告
        if seq_length < 100:
            warnings.append("序列过短")
        if 'N' in sequence:
            warnings.append(f"序列包含 {sequence.count('N')} 个未知碱基 (N)")
        
        return SequenceAnalysisResult(
            sequence_length=seq_length,
            gc_content=round(gc_content, 2),
            gc_distribution=gc_regions,
            restriction_sites=restriction_sites,
            orfs=orfs,
            coding_potential=round(coding_potential, 2),
            warnings=warnings
        )
    
    def check_cloning_compatibility(
        self,
        insert_sequence: str,
        vector_sequence: str,
        enzymes: List[str]
    ) -> Dict:
        """
        检查插入片段与载体的克隆兼容性
        
        Args:
            insert_sequence: 插入片段序列
            vector_sequence: 载体序列
            enzymes: 计划使用的酶列表
        
        Returns:
            兼容性分析结果
        """
        result = {
            "compatible": True,
            "issues": [],
            "recommendations": []
        }
        
        # 检查插入片段中的位点
        insert_sites = self.restriction_analyzer.find_sites(insert_sequence, enzymes)
        
        for site in insert_sites:
            result["issues"].append(
                f"插入片段包含 {site.enzyme} 位点（位置 {site.recognition_start}）"
            )
            result["compatible"] = False
        
        # 检查载体中的位点数量
        for enzyme in enzymes:
            vector_sites = self.restriction_analyzer.find_sites(vector_sequence, [enzyme])
            if len(vector_sites) == 0:
                result["issues"].append(f"载体不含 {enzyme} 位点")
                result["compatible"] = False
            elif len(vector_sites) > 1:
                result["recommendations"].append(
                    f"{enzyme} 在载体中有多个位点，建议使用其他酶"
                )
        
        return result
