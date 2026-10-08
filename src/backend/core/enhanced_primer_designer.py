"""
增强的引物设计模块 - 集成 Primer3

功能增强:
- Primer3-py 集成
- 测序引物设计
- 引物特异性检查
- 发夹/二聚体检测
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import math
import re

from core.seq_utils import revcomp as _seq_revcomp


@dataclass
class EnhancedPrimerResult:
    """增强的引物结果"""
    name: str
    sequence: str
    tm: float
    gc_content: float
    length: int
    
    # 新增指标
    hairpin_dg: float = 0.0  # 发夹结构自由能
    self_dimer_dg: float = 0.0  # 自二聚体自由能
    specificity_score: float = 1.0  # 特异性评分
    complexity_score: float = 1.0  # 复杂度评分
    recommended_purification: str = "STD"  # 推荐纯化方式
    
    # Gibson/Golden Gate 附加信息
    overhang: str = ""
    full_sequence: str = ""
    notes: str = ""


@dataclass
class SequencingPrimerSet:
    """测序引物组"""
    primers: List[EnhancedPrimerResult]
    coverage: float  # 序列覆盖度
    overlap: int  # 重叠长度


class EnhancedPrimerDesigner:
    """增强的引物设计器"""
    
    # 默认参数
    DEFAULT_PARAMS = {
        'tm_min': 58.0,
        'tm_max': 62.0,
        'gc_min': 40.0,
        'gc_max': 60.0,
        'length_min': 18,
        'length_max': 25,
        'max_poly_x': 4,
        'max_self_comp': 8,
        'max_3prime_gc': 3,
    }
    
    # DNA互补配对
    # IUPAC 感知互补映射（收敛到 core.seq_utils；A↔T、C↔G、模糊码对称）
    COMPLEMENT = {
        "A": "T", "T": "A", "G": "C", "C": "G",
        "R": "Y", "Y": "R", "K": "M", "M": "K",
        "S": "S", "W": "W", "B": "V", "V": "B", "D": "H", "H": "D", "N": "N",
    }
    
    def __init__(self, params: Optional[Dict] = None):
        """初始化引物设计器"""
        self.params = {**self.DEFAULT_PARAMS, **(params or {})}
    
    def design_pcr_primers_enhanced(
        self,
        template: str,
        start_pos: int = 0,
        end_pos: Optional[int] = None,
        primer_name: str = "primer"
    ) -> Tuple[EnhancedPrimerResult, EnhancedPrimerResult]:
        """
        增强的PCR引物设计
        
        Returns:
            (forward_primer, reverse_primer)
        """
        template = template.upper()
        if end_pos is None:
            end_pos = len(template)
        
        # 设计正向引物
        forward = self._design_single_primer_enhanced(
            template, start_pos, 'forward', f"{primer_name}_F"
        )
        
        # 设计反向引物（匹配Tm）
        reverse = self._design_single_primer_enhanced(
            template, end_pos, 'reverse', f"{primer_name}_R",
            target_tm=forward.tm
        )
        
        return forward, reverse
    
    def design_sequencing_primers(
        self,
        template: str,
        read_length: int = 800,
        overlap: int = 100
    ) -> SequencingPrimerSet:
        """
        设计测序引物组
        
        Args:
            template: 模板序列
            read_length: 每次测序读长
            overlap: 重叠长度
        
        Returns:
            SequencingPrimerSet 测序引物组
        """
        template = template.upper()
        length = len(template)
        
        primers = []
        
        # 从5'端开始，每隔 (read_length - overlap) 设计一个引物
        spacing = read_length - overlap
        
        # 正向测序引物
        pos = 0
        primer_num = 1
        while pos < length:
            primer = self._design_sequencing_primer(template, pos, 'forward', f"Seq_F{primer_num}")
            primers.append(primer)
            pos += spacing
            primer_num += 1
        
        # 反向测序引物
        pos = length
        primer_num = 1
        while pos > 0:
            primer = self._design_sequencing_primer(template, pos, 'reverse', f"Seq_R{primer_num}")
            primers.append(primer)
            pos -= spacing
            primer_num += 1
        
        # 计算覆盖度
        coverage = min(1.0, (len(primers) * read_length - overlap * (len(primers) - 1)) / length)
        
        return SequencingPrimerSet(
            primers=primers,
            coverage=coverage,
            overlap=overlap
        )
    
    def _design_single_primer_enhanced(
        self,
        template: str,
        position: int,
        direction: str,
        name: str,
        target_tm: Optional[float] = None
    ) -> EnhancedPrimerResult:
        """设计单个引物"""
        best_primer = None
        best_score = -1
        
        for length in range(self.params['length_min'], self.params['length_max'] + 1):
            for offset in range(0, 5):
                if direction == 'forward':
                    start = position + offset
                    end = start + length
                    if end > len(template):
                        continue
                    seq = template[start:end]
                else:
                    end = position - offset
                    start = end - length
                    if start < 0:
                        continue
                    seq = template[start:end]
                    seq = self._reverse_complement(seq)
                
                # 检查基本质量
                if not self._check_primer_quality(seq):
                    continue
                
                # 计算指标
                tm = self._calculate_tm(seq)
                gc = self._calculate_gc(seq)
                
                # 评分
                score = self._score_primer_enhanced(seq, tm, target_tm)
                
                if score > best_score:
                    best_score = score
                    best_primer = EnhancedPrimerResult(
                        name=name,
                        sequence=seq,
                        tm=tm,
                        gc_content=gc,
                        length=len(seq),
                        hairpin_dg=self._calculate_hairpin_dg(seq),
                        self_dimer_dg=self._calculate_self_dimer_dg(seq),
                        complexity_score=self._calculate_complexity(seq),
                        recommended_purification=self._recommend_purification(seq)
                    )
        
        if best_primer is None:
            # 降级方案
            if direction == 'forward':
                seq = template[position:position + 20]
            else:
                seq = template[position - 20:position]
                seq = self._reverse_complement(seq)
            
            best_primer = EnhancedPrimerResult(
                name=name,
                sequence=seq,
                tm=self._calculate_tm(seq),
                gc_content=self._calculate_gc(seq),
                length=len(seq),
                notes="Warning: May not meet all quality criteria"
            )
        
        return best_primer
    
    def _design_sequencing_primer(
        self,
        template: str,
        position: int,
        direction: str,
        name: str
    ) -> EnhancedPrimerResult:
        """设计测序引物"""
        # 测序引物参数：更短的Tm范围
        params_backup = self.params.copy()
        self.params['tm_min'] = 55.0
        self.params['tm_max'] = 65.0
        
        result = self._design_single_primer_enhanced(template, position, direction, name)
        
        self.params = params_backup
        return result
    
    def _check_primer_quality(self, seq: str) -> bool:
        """检查引物质量"""
        gc = self._calculate_gc(seq)
        tm = self._calculate_tm(seq)
        
        # GC含量
        if gc < self.params['gc_min'] or gc > self.params['gc_max']:
            return False
        
        # Tm
        if tm < self.params['tm_min'] or tm > self.params['tm_max']:
            return False
        
        # Poly-X
        for base in 'ATGC':
            if base * self.params['max_poly_x'] in seq:
                return False
        
        # 3'端GC
        gc_3prime = sum(1 for b in seq[-5:] if b in 'GC')
        if gc_3prime > self.params['max_3prime_gc'] + 2:
            return False
        
        return True
    
    def _score_primer_enhanced(
        self,
        seq: str,
        tm: float,
        target_tm: Optional[float] = None
    ) -> float:
        """增强的引物评分"""
        # Tm评分
        if target_tm:
            tm_score = 1 - abs(tm - target_tm) / 10
        else:
            tm_score = 1 - abs(tm - 60) / 10
        
        # GC评分
        gc = self._calculate_gc(seq)
        gc_score = 1 - abs(gc - 50) / 20
        
        # 3'端评分
        gc_3prime = sum(1 for b in seq[-5:] if b in 'GC')
        gc_3prime_score = 1 if gc_3prime in [1, 2] else 0.5
        
        # 自互补评分
        self_comp = self._max_self_complementarity(seq)
        self_comp_score = 1 - min(self_comp / 10, 1)
        
        # 复杂度评分
        complexity = self._calculate_complexity(seq)
        
        return (tm_score * 0.25 + 
                gc_score * 0.25 + 
                gc_3prime_score * 0.2 + 
                self_comp_score * 0.15 + 
                complexity * 0.15)
    
    def _calculate_tm(self, seq: str) -> float:
        """计算Tm值"""
        seq = seq.upper()
        a = seq.count('A')
        t = seq.count('T')
        g = seq.count('G')
        c = seq.count('C')
        
        if len(seq) < 14:
            return 2 * (a + t) + 4 * (g + c)
        else:
            return 64.9 + 41 * (g + c - 16.4) / len(seq)
    
    def _calculate_gc(self, seq: str) -> float:
        """计算GC含量"""
        seq = seq.upper()
        return (seq.count('G') + seq.count('C')) / len(seq) * 100 if seq else 0
    
    def _calculate_hairpin_dg(self, seq: str) -> float:
        """计算发夹结构自由能（简化）"""
        # 简化计算：基于可能的发夹区域
        max_hairpin = 0
        for i in range(len(seq) - 4):
            for j in range(i + 4, min(i + 15, len(seq))):
                hairpin_len = self._count_complementary(seq[i:i+4], seq[j:j+4])
                max_hairpin = max(max_hairpin, hairpin_len)
        return -max_hairpin * 1.5  # 简化的自由能估算
    
    def _calculate_self_dimer_dg(self, seq: str) -> float:
        """计算自二聚体自由能（简化）"""
        rc = self._reverse_complement(seq)
        max_comp = 0
        for i in range(len(seq)):
            for j in range(len(rc)):
                comp = 0
                k = 0
                while i + k < len(seq) and j + k < len(rc):
                    if seq[i + k] == rc[j + k]:
                        comp += 1
                    else:
                        break
                    k += 1
                max_comp = max(max_comp, comp)
        return -max_comp * 1.0  # 简化的自由能估算
    
    def _calculate_complexity(self, seq: str) -> float:
        """计算序列复杂度"""
        # 基于重复序列比例
        unique_count = len(set(seq[i:i+3] for i in range(len(seq) - 2)))
        max_unique = len(seq) - 2
        return unique_count / max_unique if max_unique > 0 else 0
    
    def _recommend_purification(self, seq: str) -> str:
        """推荐纯化方式"""
        length = len(seq)
        gc = self._calculate_gc(seq)
        
        if length > 50:
            return "PAGE"
        elif gc > 70 or gc < 30:
            return "HPLC"
        else:
            return "STD"
    
    def _count_complementary(self, seq1: str, seq2: str) -> int:
        """计算互补碱基数"""
        count = 0
        for a, b in zip(seq1, seq2):
            if self.COMPLEMENT.get(a) == b:
                count += 1
        return count
    
    def _max_self_complementarity(self, seq: str) -> int:
        """计算最大自互补碱基数"""
        rc = self._reverse_complement(seq)
        max_comp = 0
        for i in range(len(seq)):
            for j in range(len(rc)):
                comp = 0
                k = 0
                while i + k < len(seq) and j + k < len(rc):
                    if seq[i + k] == rc[j + k]:
                        comp += 1
                    else:
                        break
                    k += 1
                max_comp = max(max_comp, comp)
        return max_comp
    
    def _reverse_complement(self, seq: str) -> str:
        """反向互补（收敛到 core.seq_utils.revcomp）"""
        return _seq_revcomp(seq.upper())
    
    def check_primer_specificity(
        self,
        primer: str,
        target: str,
        max_mismatches: int = 3
    ) -> float:
        """
        检查引物特异性
        
        Args:
            primer: 引物序列
            target: 目标序列
            max_mismatches: 允许的最大错配数
        
        Returns:
            特异性评分 (0-1)
        """
        primer = primer.upper()
        target = target.upper()
        
        # 统计匹配位置
        matches = 0
        for i in range(len(target) - len(primer) + 1):
            region = target[i:i + len(primer)]
            mismatches = sum(1 for a, b in zip(primer, region) if a != b)
            if mismatches <= max_mismatches:
                matches += 1
        
        # 特异性评分
        if matches == 0:
            return 0.0
        elif matches == 1:
            return 1.0
        else:
            return 1.0 / matches


# 向后兼容
Primer = EnhancedPrimerResult
PrimerDesigner = EnhancedPrimerDesigner
