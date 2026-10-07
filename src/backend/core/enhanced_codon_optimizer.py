"""
增强的密码子优化模块
支持更多物种和高级优化功能
"""

import os
from app.config import settings
import re
import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from core.seq_utils import CODON_TABLE as _STD_CODON_TABLE, gc_fraction


class OptimizationLevel(Enum):
    AGGRESSIVE = "aggressive"
    BALANCED = "balanced"
    CONSERVATIVE = "conservative"


@dataclass
class EnhancedOptimizationResult:
    """增强的优化结果"""
    dna_sequence: str
    amino_acid_sequence: str
    cai: float
    gc_content: float
    gc_distribution: List[float]
    codon_usage: Dict[str, List[str]]  # 每个氨基酸使用的密码子统计
    warnings: List[str]
    avoided_motifs: List[str]
    
    # 新增指标
    cpg_count: int = 0  # CpG位点数量
    rare_codon_count: int = 0  # 稀有密码子数量
    mrna_stability_score: float = 0.0  # mRNA稳定性评分


class EnhancedCodonOptimizer:
    """增强的密码子优化器"""
    
    # 稀有密码子阈值 (使用频率 < 10%)
    RARE_CODON_THRESHOLD = 0.10
    
    # 标准遗传密码表（收敛到 core.seq_utils）
    CODON_TABLE = _STD_CODON_TABLE
    
    def __init__(
        self,
        species: str = "ecoli",
        custom_codon_table: Optional[Dict[str, float]] = None
    ):
        self.species = species
        self.codon_freq = self._load_codon_frequency(species)
        if custom_codon_table:
            self.codon_freq.update(custom_codon_table)
    
    def _load_codon_frequency(self, species: str) -> Dict[str, float]:
        """加载物种特异性密码子使用频率"""
        import os
        import yaml
        
        # 密码子表文件映射
        species_files = {
            'ecoli': 'Ecoli_K12.yaml',
            'human': 'Human.yaml',
            'yeast': 'Yeast.yaml',
            'cho': 'CHO.yaml',
            's_cerevisiae': 'Yeast.yaml',
        }
        
        filename = species_files.get(species.lower(), 'Ecoli_K12.yaml')
        filepath = os.path.join(settings.CODON_TABLES_DIR, filename)
        
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                data = yaml.safe_load(f)
            
            # 提取密码子频率
            freq = {}
            for key, value in data.items():
                if len(key) == 3 and key not in ['species', 'taxonomy_id', 'optimal_codons']:
                    freq[key] = float(value) if isinstance(value, (int, float)) else 0.01
            return freq
        
        # 默认 E. coli
        return self._get_default_ecoli_freq()
    
    def _get_default_ecoli_freq(self) -> Dict[str, float]:
        """默认 E. coli 密码子频率"""
        return {
            'TTT': 0.58, 'TTC': 0.42,
            'TTA': 0.14, 'TTG': 0.13, 'CTT': 0.12, 'CTC': 0.10, 'CTA': 0.04, 'CTG': 0.47,
            'ATT': 0.49, 'ATC': 0.39, 'ATA': 0.12,
            'ATG': 1.00,
            'GTT': 0.28, 'GTC': 0.20, 'GTA': 0.17, 'GTG': 0.35,
            'TCT': 0.17, 'TCC': 0.15, 'TCA': 0.14, 'TCG': 0.14, 'AGT': 0.16, 'AGC': 0.25,
            'CCT': 0.18, 'CCC': 0.13, 'CCA': 0.20, 'CCG': 0.49,
            'ACT': 0.19, 'ACC': 0.40, 'ACA': 0.17, 'ACG': 0.25,
            'GCT': 0.18, 'GCC': 0.26, 'GCA': 0.23, 'GCG': 0.33,
            'TAT': 0.59, 'TAC': 0.41,
            'CAT': 0.57, 'CAC': 0.43,
            'CAA': 0.34, 'CAG': 0.66,
            'AAT': 0.49, 'AAC': 0.51,
            'AAA': 0.74, 'AAG': 0.26,
            'GAT': 0.63, 'GAC': 0.37,
            'GAA': 0.68, 'GAG': 0.32,
            'TGT': 0.46, 'TGC': 0.54,
            'TGG': 1.00,
            'CGT': 0.36, 'CGC': 0.36, 'CGA': 0.07, 'CGG': 0.11, 'AGA': 0.07, 'AGG': 0.04,
            'GGT': 0.35, 'GGC': 0.37, 'GGA': 0.13, 'GGG': 0.15,
            'TAA': 0.61, 'TAG': 0.09, 'TGA': 0.30,
        }
    
    def optimize_enhanced(
        self,
        amino_acid_sequence: str,
        avoid_motifs: Optional[List[str]] = None,
        gc_target: Tuple[float, float] = (0.40, 0.60),
        avoid_cpg: bool = False,
        avoid_rare_codons: bool = True,
        optimization_level: OptimizationLevel = OptimizationLevel.BALANCED
    ) -> EnhancedOptimizationResult:
        """
        增强的密码子优化
        
        Args:
            amino_acid_sequence: 氨基酸序列
            avoid_motifs: 需要避免的序列motif
            gc_target: 目标GC含量范围
            avoid_cpg: 是否避免CpG位点（哺乳动物表达）
            avoid_rare_codons: 是否避免稀有密码子
            optimization_level: 优化级别
        
        Returns:
            EnhancedOptimizationResult 增强的优化结果
        """
        if avoid_motifs is None:
            avoid_motifs = []
        
        # 验证输入
        amino_acid_sequence = amino_acid_sequence.upper().strip()
        valid_aa = set(self.CODON_TABLE.keys()) - {'*'}
        for aa in amino_acid_sequence:
            if aa not in valid_aa:
                raise ValueError(f"无效的氨基酸代码: {aa}")
        
        # 基础优化
        dna_sequence = self._initial_optimization(amino_acid_sequence)
        
        # 迭代优化
        dna_sequence = self._enhanced_iterative_optimization(
            dna_sequence,
            amino_acid_sequence,
            avoid_motifs,
            gc_target,
            avoid_cpg,
            avoid_rare_codons,
            optimization_level
        )
        
        # 计算指标
        cai = self._calculate_cai(dna_sequence, amino_acid_sequence)
        gc_content = self._calculate_gc_content(dna_sequence)
        gc_distribution = self._calculate_gc_distribution(dna_sequence)
        cpg_count = self._count_cpg(dna_sequence)
        rare_codon_count = self._count_rare_codons(dna_sequence, amino_acid_sequence)
        codon_usage = self._analyze_codon_usage(dna_sequence, amino_acid_sequence)
        
        # 警告
        warnings = []
        if cpg_count > 10 and avoid_cpg:
            warnings.append(f"检测到 {cpg_count} 个CpG位点")
        if rare_codon_count > 0 and avoid_rare_codons:
            warnings.append(f"使用了 {rare_codon_count} 个稀有密码子")
        
        return EnhancedOptimizationResult(
            dna_sequence=dna_sequence,
            amino_acid_sequence=amino_acid_sequence,
            cai=cai,
            gc_content=gc_content,
            gc_distribution=gc_distribution,
            codon_usage=codon_usage,
            warnings=warnings,
            avoided_motifs=[m for m in avoid_motifs if m.upper() not in dna_sequence.upper()],
            cpg_count=cpg_count,
            rare_codon_count=rare_codon_count,
            mrna_stability_score=self._estimate_mrna_stability(dna_sequence)
        )
    
    def _initial_optimization(self, aa_sequence: str) -> str:
        """初始优化：选择最高频密码子"""
        codons = []
        for aa in aa_sequence:
            available = self.CODON_TABLE.get(aa, [])
            best = max(available, key=lambda c: self.codon_freq.get(c, 0))
            codons.append(best)
        return ''.join(codons)
    
    def _enhanced_iterative_optimization(
        self,
        dna_seq: str,
        aa_seq: str,
        avoid_motifs: List[str],
        gc_target: Tuple[float, float],
        avoid_cpg: bool,
        avoid_rare_codons: bool,
        level: OptimizationLevel
    ) -> str:
        """增强的迭代优化"""
        dna_list = list(dna_seq)
        max_iter = {'aggressive': 100, 'balanced': 50, 'conservative': 20}[level.value]
        
        for _ in range(max_iter):
            improved = False
            
            # 避免motif
            for motif in avoid_motifs:
                pos = ''.join(dna_list).upper().find(motif.upper())
                if pos != -1:
                    new_seq = self._replace_codon_at_motif(dna_list, aa_seq, pos, motif)
                    if new_seq:
                        dna_list = list(new_seq)
                        improved = True
            
            # GC平衡
            gc = self._calculate_gc_content(''.join(dna_list))
            if gc < gc_target[0] or gc > gc_target[1]:
                self._smooth_gc(dna_list, aa_seq, gc_target)
                improved = True
            
            # 避免CpG
            if avoid_cpg:
                self._avoid_cpg_sites(dna_list, aa_seq)
            
            # 避免稀有密码子
            if avoid_rare_codons:
                self._avoid_rare_codons(dna_list, aa_seq)
            
            if not improved:
                break
        
        return ''.join(dna_list)
    
    def _replace_codon_at_motif(self, dna_list, aa_seq, pos, motif):
        """替换motif位置的密码子"""
        start_codon = pos // 3
        end_codon = (pos + len(motif) - 1) // 3
        
        for codon_idx in range(start_codon, min(end_codon + 1, len(aa_seq))):
            aa = aa_seq[codon_idx]
            current = ''.join(dna_list[codon_idx*3:(codon_idx+1)*3])
            
            for alt in self.CODON_TABLE[aa]:
                if alt != current:
                    test = dna_list.copy()
                    for i, nt in enumerate(alt):
                        test[codon_idx*3 + i] = nt
                    if motif.upper() not in ''.join(test).upper():
                        return ''.join(test)
        return None
    
    def _smooth_gc(self, dna_list, aa_seq, gc_target):
        """平滑GC含量"""
        gc = self._calculate_gc_content(''.join(dna_list))
        need_increase = gc < gc_target[0]
        
        for i, aa in enumerate(aa_seq):
            current = ''.join(dna_list[i*3:(i+1)*3])
            current_gc = (current.count('G') + current.count('C')) / 3
            
            for alt in self.CODON_TABLE[aa]:
                alt_gc = (alt.count('G') + alt.count('C')) / 3
                if need_increase and alt_gc > current_gc:
                    for j, nt in enumerate(alt):
                        dna_list[i*3 + j] = nt
                    break
                elif not need_increase and alt_gc < current_gc:
                    for j, nt in enumerate(alt):
                        dna_list[i*3 + j] = nt
                    break
    
    def _avoid_cpg_sites(self, dna_list, aa_seq):
        """避免CpG位点"""
        dna = ''.join(dna_list)
        while 'CG' in dna:
            pos = dna.find('CG')
            codon_idx = pos // 3
            if codon_idx < len(aa_seq):
                aa = aa_seq[codon_idx]
                current = ''.join(dna_list[codon_idx*3:(codon_idx+1)*3])
                for alt in self.CODON_TABLE[aa]:
                    if alt != current and 'CG' not in alt:
                        for j, nt in enumerate(alt):
                            dna_list[codon_idx*3 + j] = nt
                        break
            dna = ''.join(dna_list)
    
    def _avoid_rare_codons(self, dna_list, aa_seq):
        """避免稀有密码子"""
        for i, aa in enumerate(aa_seq):
            current = ''.join(dna_list[i*3:(i+1)*3])
            freq = self.codon_freq.get(current, 0)
            
            if freq < self.RARE_CODON_THRESHOLD:
                # 替换为更常用密码子
                alternatives = [(c, self.codon_freq.get(c, 0)) for c in self.CODON_TABLE[aa]]
                alternatives.sort(key=lambda x: -x[1])
                
                for alt, alt_freq in alternatives:
                    if alt_freq > self.RARE_CODON_THRESHOLD:
                        for j, nt in enumerate(alt):
                            dna_list[i*3 + j] = nt
                        break
    
    def _calculate_cai(self, dna_seq, aa_seq) -> float:
        """计算CAI"""
        max_freq = {}
        for aa, codons in self.CODON_TABLE.items():
            if aa != '*':
                max_freq[aa] = max(self.codon_freq.get(c, 0) for c in codons)
        
        w_values = []
        for i, aa in enumerate(aa_seq):
            codon = dna_seq[i*3:(i+1)*3]
            freq = self.codon_freq.get(codon, 0.01)
            max_f = max_freq.get(aa, 1)
            w = freq / max_f if max_f > 0 else 0.01
            w_values.append(w)
        
        log_sum = sum(math.log(w) for w in w_values)
        return math.exp(log_sum / len(w_values)) if w_values else 0
    
    def _calculate_gc_content(self, dna_seq) -> float:
        """计算GC含量（收敛到 core.seq_utils）"""
        return gc_fraction(dna_seq or "")
    
    def _calculate_gc_distribution(self, dna_seq, window=50) -> List[float]:
        """计算GC分布"""
        if len(dna_seq) < window:
            return [self._calculate_gc_content(dna_seq)]
        
        distribution = []
        for i in range(0, len(dna_seq) - window + 1, window // 2):
            segment = dna_seq[i:i+window]
            distribution.append(self._calculate_gc_content(segment))
        return distribution
    
    def _count_cpg(self, dna_seq) -> int:
        """计算CpG位点数量"""
        return len(re.findall(r'CG', dna_seq.upper()))
    
    def _count_rare_codons(self, dna_seq, aa_seq) -> int:
        """计算稀有密码子数量"""
        count = 0
        for i, aa in enumerate(aa_seq):
            codon = dna_seq[i*3:(i+1)*3]
            freq = self.codon_freq.get(codon, 0)
            if freq < self.RARE_CODON_THRESHOLD:
                count += 1
        return count
    
    def _analyze_codon_usage(self, dna_seq, aa_seq) -> Dict[str, List[str]]:
        """分析密码子使用"""
        usage = {}
        for i, aa in enumerate(aa_seq):
            codon = dna_seq[i*3:(i+1)*3]
            if aa not in usage:
                usage[aa] = []
            usage[aa].append(codon)
        return usage
    
    def _estimate_mrna_stability(self, dna_seq) -> float:
        """估计mRNA稳定性（简化版）"""
        # 基于GC含量和5'端稳定性
        gc = self._calculate_gc_content(dna_seq)
        five_prime = dna_seq[:50] if len(dna_seq) >= 50 else dna_seq
        five_prime_gc = self._calculate_gc_content(five_prime)
        
        # 稳定性评分 (0-1)
        score = 0.5 + 0.3 * (gc - 0.5) + 0.2 * (0.5 - abs(five_prime_gc - 0.5))
        return max(0, min(1, score))


# 保持向后兼容
CodonOptimizer = EnhancedCodonOptimizer
CodonOptimizationResult = EnhancedOptimizationResult
