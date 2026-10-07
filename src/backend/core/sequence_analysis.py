"""
序列分析模块
提供限制性位点分析、ORF预测、GC分析等功能
"""
import re
from typing import Dict, List, Optional, Tuple, Set
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


# 常用限制性内切酶数据库
RESTRICTION_ENZYMES = {
    # Name: (recognition_seq, cut_offset, overhang_type)
    # cut_offset: 从识别序列开始位置到切割位置的偏移
    # overhang: '5' = 5' overhang, '3' = 3' overhang, 'b' = blunt
    "EcoRI": ("GAATTC", 1, '5'),
    "BamHI": ("GGATCC", 1, '5'),
    "HindIII": ("AAGCTT", 1, '5'),
    "XhoI": ("CTCGAG", 1, '5'),
    "XbaI": ("TCTAGA", 1, '5'),
    "SalI": ("GTCGAC", 1, '5'),
    "PstI": ("CTGCAG", 5, '3'),
    "KpnI": ("GGTACC", 5, '3'),
    "SmaI": ("CCCGGG", 3, 'b'),  # blunt
    "SbfI": ("CCTGCAGG", 6, '3'),
    "NotI": ("GCGGCCGC", 2, '5'),
    "NdeI": ("CATATG", 2, '5'),  # CAT|ATG → 5' 突出 TA（REBASE）
    "NcoI": ("CCATGG", 1, '5'),
    "SacI": ("GAGCTC", 5, '3'),
    "BglII": ("AGATCT", 1, '5'),
    "AvrII": ("CCTAGG", 1, '5'),
    "SpeI": ("ACTAGT", 1, '5'),
    "AgeI": ("ACCGGT", 1, '5'),
    "MluI": ("ACGCGT", 1, '5'),
    "BsaI": ("GGTCTC", 7, '5'),  # Type IIS
    "BsmBI": ("CGTCTC", 7, '5'),  # Type IIS
    "BbsI": ("GAAGAC", 8, '5'),  # Type IIS
    "EcoRV": ("GATATC", 3, 'b'),  # blunt
    "DraI": ("TTTAAA", 3, 'b'),   # blunt
    "PvuII": ("CAGCTG", 3, 'b'),  # blunt
    "StuI": ("AGGCCT", 3, 'b'),   # blunt
}

# 起始密码子和终止密码子
START_CODONS = ["ATG", "GTG", "TTG"]
STOP_CODONS = ["TAA", "TAG", "TGA"]

# 遗传密码表收敛到 core.seq_utils（CODON_TABLE：氨基酸 → 密码子列表）
# 兼容别名：本模块历史导出方向为 密码子 → 氨基酸
CODON_TABLE = CODON_TO_AA


class RestrictionSiteAnalyzer:
    """限制性酶切位点分析器"""
    
    def __init__(self, enzymes: Dict = None):
        """
        初始化
        
        Args:
            enzymes: 自定义酶字典，默认使用内置数据库
        """
        self.enzymes = enzymes or RESTRICTION_ENZYMES
    
    def find_sites(self, sequence: str, enzymes: List[str] = None) -> List[RestrictionSite]:
        """
        查找序列中的限制性位点
        
        Args:
            sequence: DNA 序列
            enzymes: 要检测的酶列表，默认检测所有
        
        Returns:
            发现的限制性位点列表
        """
        sequence = sequence.upper()
        sites = []
        
        enzymes_to_check = enzymes if enzymes else list(self.enzymes.keys())
        
        for enzyme_name in enzymes_to_check:
            if enzyme_name not in self.enzymes:
                logger.warning(f"未知酶: {enzyme_name}")
                continue
            
            recognition_seq, cut_offset, overhang = self.enzymes[enzyme_name]
            
            # 正向链搜索
            for match in re.finditer(recognition_seq, sequence):
                site = RestrictionSite(
                    enzyme=enzyme_name,
                    site=recognition_seq,
                    cut_position=match.start() + cut_offset,
                    recognition_start=match.start() + 1,  # 1-indexed
                    recognition_end=match.end(),
                    strand='+',
                    overhang=overhang
                )
                sites.append(site)
            
            # 反向链搜索
            rev_seq = self._reverse_complement(recognition_seq)
            for match in re.finditer(rev_seq, sequence):
                site = RestrictionSite(
                    enzyme=enzyme_name,
                    site=recognition_seq,
                    cut_position=match.start() + len(recognition_seq) - cut_offset,
                    recognition_start=match.start() + 1,
                    recognition_end=match.end(),
                    strand='-',
                    overhang=overhang
                )
                sites.append(site)
        
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

        兼容的判定按**实际突出端序列**而非仅突出端类型：EcoRI（AATT）与
        BamHI（GATC）虽同为 5' 突出，末端序列不同、不可互连；SpeI/XbaI
        这类产生相同突出端（CTAG）的酶才判兼容。平末端一律互相兼容。
        同一酶自身恒兼容；Type IIS 酶（BsaI 等）的突出端由侧翼序列决定、
        无法从识别序列推导，仅同一酶名时判兼容。

        Args:
            enzyme1, enzyme2: 酶名称

        Returns:
            是否兼容（可连接）
        """
        if enzyme1 not in self.enzymes or enzyme2 not in self.enzymes:
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
        # IIS 酶标记：突出端取决于侧翼序列，不同 IIS 酶之间不预设兼容
        if overhang1 == "IIS" or overhang2 == "IIS":
            return False
        # 相同的突出端序列才可互连（5'/3' 突出端不会互连，序列方向已隐含）
        return overhang1 == overhang2

    def _overhang_sequence(self, enzyme_name: str) -> Optional[str]:
        """
        推导酶切后留下的粘性末端序列（突出端 5'→3'）。

        依据 RESTRICTION_ENZYMES 的 (识别序列, cut_offset, overhang_type)：
        - '5' 突出：取识别序列切割点右侧 overhang_len = site_len - 2*cut_offset
          个碱基（如 EcoRI GAATTC cut=1 → AATT）；
        - '3' 突出：取切割点左侧段反向互补的前 overhang_len = 2*cut_offset -
          site_len 个碱基（如 PstI CTGCAG cut=5 → TGCA）；
        - 'b'（平端）→ None；
        - IIS 酶（切点在识别序列之外）→ "IIS"（突出端随侧翼变化，不可推导）。

        无法推导（识别序列含非 ACGT 或切割点越界）时返回 None 并记警告。
        """
        if enzyme_name not in self.enzymes:
            return None
        recognition_seq, cut_offset, overhang = self.enzymes[enzyme_name]
        if overhang == 'b':
            return None

        site_len = len(recognition_seq)
        if not (0 < cut_offset < site_len):
            # 切割点在识别序列之外（Type IIS）：突出端由侧翼决定
            return "IIS"

        if overhang == '5':
            overhang_len = site_len - 2 * cut_offset
            if overhang_len <= 0:
                return None
            sticky = recognition_seq[cut_offset:cut_offset + overhang_len]
        else:  # '3'
            overhang_len = 2 * cut_offset - site_len
            if overhang_len <= 0:
                return None
            sticky = self._reverse_complement(recognition_seq[:cut_offset])[:overhang_len]

        if not sticky or any(b not in "ACGT" for b in sticky):
            logger.warning(f"酶 {enzyme_name} 突出端含非 ACGT 碱基，无法判定兼容性")
            return None
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
