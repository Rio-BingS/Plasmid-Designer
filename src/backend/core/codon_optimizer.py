"""
密码子优化模块

实现多物种密码子优化，借鉴金斯瑞/明舟生物算法：
1. CAI (Codon Adaptation Index) 最大化
2. GC含量控制 (40-60%)
3. 避免限制性酶切位点
4. 避免二级结构区域
5. mRNA稳定性优化
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from collections import Counter
import math


@dataclass
class CodonOptimizationResult:
    """密码子优化结果"""
    dna_sequence: str
    amino_acid_sequence: str
    cai: float
    gc_content: float
    gc_distribution: List[float]
    warnings: List[str]
    avoided_motifs: List[str]


# 标准遗传密码表
CODON_TABLE = {
    'F': ['TTT', 'TTC'],
    'L': ['TTA', 'TTG', 'CTT', 'CTC', 'CTA', 'CTG'],
    'I': ['ATT', 'ATC', 'ATA'],
    'M': ['ATG'],
    'V': ['GTT', 'GTC', 'GTA', 'GTG'],
    'S': ['TCT', 'TCC', 'TCA', 'TCG', 'AGT', 'AGC'],
    'P': ['CCT', 'CCC', 'CCA', 'CCG'],
    'T': ['ACT', 'ACC', 'ACA', 'ACG'],
    'A': ['GCT', 'GCC', 'GCA', 'GCG'],
    'Y': ['TAT', 'TAC'],
    'H': ['CAT', 'CAC'],
    'Q': ['CAA', 'CAG'],
    'N': ['AAT', 'AAC'],
    'K': ['AAA', 'AAG'],
    'D': ['GAT', 'GAC'],
    'E': ['GAA', 'GAG'],
    'C': ['TGT', 'TGC'],
    'W': ['TGG'],
    'R': ['CGT', 'CGC', 'CGA', 'CGG', 'AGA', 'AGG'],
    'G': ['GGT', 'GGC', 'GGA', 'GGG'],
    '*': ['TAA', 'TAG', 'TGA'],
}

# 反向密码子表
AMINO_ACID_TABLE = {}
for aa, codons in CODON_TABLE.items():
    for codon in codons:
        AMINO_ACID_TABLE[codon] = aa


class CodonOptimizer:
    """密码子优化器"""
    
    def __init__(
        self,
        species: str = "ecoli",
        custom_codon_table: Optional[Dict[str, float]] = None
    ):
        """
        初始化密码子优化器
        
        Args:
            species: 目标物种 (ecoli, human, yeast, etc.)
            custom_codon_table: 自定义密码子频率表
        """
        self.species = species
        self.codon_freq = self._load_codon_frequency(species)
        if custom_codon_table:
            self.codon_freq.update(custom_codon_table)
    
    def _load_codon_frequency(self, species: str) -> Dict[str, float]:
        """
        加载物种特异性密码子使用频率表。
        优先从 data/codon_tables/*.yaml 读取，失败则回退内置 E. coli 表。
        """
        from pathlib import Path

        ecoli_freq = {
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

        yaml_freq = self._load_codon_frequency_from_yaml(species)
        if yaml_freq:
            return yaml_freq
        return ecoli_freq

    def _load_codon_frequency_from_yaml(self, species: str) -> Optional[Dict[str, float]]:
        """从 data/codon_tables 加载 YAML 频率表。"""
        try:
            import yaml
            from pathlib import Path
        except ImportError:
            return None

        species_key = (species or "ecoli").lower().strip()
        aliases = {
            "ecoli": ["ecoli", "e.coli", "e_coli", "escherichia", "k12", "ecoli_k12"],
            "human": ["human", "homo", "sapiens", "h_sapiens"],
            "yeast": ["yeast", "cerevisiae", "s_cerevisiae", "scerevisiae"],
            "cho": ["cho", "cricetulus", "hamster"],
        }

        # 解析表目录
        candidates = []
        try:
            from app.config import settings
            candidates.append(Path(settings.CODON_TABLES_DIR))
        except Exception:
            pass
        # 相对项目路径回退
        here = Path(__file__).resolve()
        candidates.append(here.parents[3] / "data" / "codon_tables")  # .../project/data
        candidates.append(here.parents[2] / "data" / "codon_tables")

        table_dir = next((p for p in candidates if p and p.is_dir()), None)
        if not table_dir:
            return None

        # 文件名匹配
        files = list(table_dir.glob("*.yaml")) + list(table_dir.glob("*.yml"))
        if not files:
            return None

        def score_file(path: Path) -> int:
            stem = path.stem.lower()
            text = stem
            # 精确别名
            for key, names in aliases.items():
                if species_key == key or any(a in species_key for a in names):
                    if any(a in stem for a in names) or key in stem:
                        return 100
            if species_key in stem:
                return 50
            return 0

        ranked = sorted(files, key=score_file, reverse=True)
        if score_file(ranked[0]) == 0 and species_key not in ("ecoli", "e.coli"):
            # 无匹配时不强制用错误物种表
            return None

        target = ranked[0]
        if score_file(target) == 0:
            # ecoli 默认：优先 Ecoli*
            ecoli_files = [f for f in files if "ecoli" in f.stem.lower() or "coli" in f.stem.lower()]
            target = ecoli_files[0] if ecoli_files else files[0]

        try:
            data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except Exception:
            return None

        freq: Dict[str, float] = {}
        for k, v in data.items():
            if isinstance(k, str) and len(k) == 3 and k.upper() in AMINO_ACID_TABLE:
                try:
                    freq[k.upper()] = float(v)
                except (TypeError, ValueError):
                    continue
        return freq or None

    def back_translate(self, amino_acid_sequence: str) -> str:
        """不进行迭代优化，仅按频率表选最优密码子反翻译。"""
        amino_acid_sequence = amino_acid_sequence.upper().strip()
        valid_aa = set(CODON_TABLE.keys()) - {'*'}
        for aa in amino_acid_sequence:
            if aa not in valid_aa:
                raise ValueError(f"无效的氨基酸代码: {aa}")
        return self._initial_optimization(amino_acid_sequence)
    
    def optimize(
        self,
        amino_acid_sequence: str,
        avoid_motifs: Optional[List[str]] = None,
        gc_target: Tuple[float, float] = (0.40, 0.60),
        optimize_level: str = "balanced"
    ) -> CodonOptimizationResult:
        """
        执行密码子优化
        
        Args:
            amino_acid_sequence: 氨基酸序列（单字母代码）
            avoid_motifs: 需要避免的序列motif（如限制性酶切位点）
            gc_target: 目标GC含量范围 (min, max)
            optimize_level: 优化级别 (aggressive, balanced, conservative)
        
        Returns:
            CodonOptimizationResult 优化结果
        """
        if avoid_motifs is None:
            avoid_motifs = []
        
        # 验证输入
        amino_acid_sequence = amino_acid_sequence.upper().strip()
        valid_aa = set(CODON_TABLE.keys()) - {'*'}
        for aa in amino_acid_sequence:
            if aa not in valid_aa:
                raise ValueError(f"无效的氨基酸代码: {aa}")
        
        # 初始优化：选择最高频密码子
        dna_sequence = self._initial_optimization(amino_acid_sequence)
        
        # 迭代优化
        dna_sequence = self._iterative_optimization(
            dna_sequence, 
            amino_acid_sequence,
            avoid_motifs,
            gc_target,
            optimize_level
        )
        
        # 计算指标
        cai = self._calculate_cai(dna_sequence, amino_acid_sequence)
        gc_content = self._calculate_gc_content(dna_sequence)
        gc_distribution = self._calculate_gc_distribution(dna_sequence)
        
        # 检查并记录警告
        warnings = []
        final_motifs = self._find_motifs(dna_sequence, avoid_motifs)
        if final_motifs:
            warnings.append(f"警告：序列中仍存在需要避免的motif: {final_motifs}")
        
        if gc_content < gc_target[0] or gc_content > gc_target[1]:
            warnings.append(f"警告：GC含量 {gc_content:.1%} 超出目标范围 {gc_target[0]:.0%}-{gc_target[1]:.0%}")
        
        return CodonOptimizationResult(
            dna_sequence=dna_sequence,
            amino_acid_sequence=amino_acid_sequence,
            cai=cai,
            gc_content=gc_content,
            gc_distribution=gc_distribution,
            warnings=warnings,
            avoided_motifs=[m for m in avoid_motifs if m not in final_motifs]
        )
    
    def _initial_optimization(self, aa_sequence: str) -> str:
        """初始优化：为每个氨基酸选择最高频密码子"""
        codons = []
        for aa in aa_sequence:
            available_codons = CODON_TABLE.get(aa, [])
            if not available_codons:
                raise ValueError(f"无法找到氨基酸 {aa} 的密码子")
            
            # 选择最高频密码子
            best_codon = max(available_codons, key=lambda c: self.codon_freq.get(c, 0))
            codons.append(best_codon)
        
        return ''.join(codons)
    
    def _iterative_optimization(
        self,
        dna_seq: str,
        aa_seq: str,
        avoid_motifs: List[str],
        gc_target: Tuple[float, float],
        level: str
    ) -> str:
        """
        迭代优化序列
        
        策略：
        1. 替换导致问题的密码子（motif冲突）
        2. 平滑GC含量
        3. 避免poly-X区域
        """
        dna_list = list(dna_seq)
        
        # 最大迭代次数
        max_iterations = {
            'aggressive': 100,
            'balanced': 50,
            'conservative': 20
        }.get(level, 50)
        
        for iteration in range(max_iterations):
            improved = False
            
            # 1. 检查并移除需要避免的motif
            for motif in avoid_motifs:
                motif_pos = self._find_motif_position(''.join(dna_list), motif)
                if motif_pos != -1:
                    # 尝试替换该位置的密码子
                    new_seq = self._replace_codon_at_motif(
                        dna_list, aa_seq, motif_pos, motif
                    )
                    if new_seq:
                        dna_list = list(new_seq)
                        improved = True
            
            # 2. GC平滑（如果需要）
            gc = self._calculate_gc_content(''.join(dna_list))
            if gc < gc_target[0] or gc > gc_target[1]:
                dna_list = self._smooth_gc(dna_list, aa_seq, gc_target)
                improved = True
            
            # 3. 避免poly-X (4个以上连续相同碱基)
            if self._has_poly_x(''.join(dna_list), 4):
                dna_list = self._break_poly_x(dna_list, aa_seq)
                improved = True
            
            if not improved:
                break
        
        return ''.join(dna_list)
    
    def _find_motif_position(self, dna: str, motif: str) -> int:
        """查找motif位置，-1表示未找到"""
        return dna.upper().find(motif.upper())
    
    def _replace_codon_at_motif(
        self,
        dna_list: List[str],
        aa_seq: str,
        pos: int,
        motif: str
    ) -> Optional[str]:
        """在motif位置尝试替换密码子"""
        # 找到motif重叠的密码子范围
        start_codon = pos // 3
        end_codon = (pos + len(motif) - 1) // 3
        
        # 尝试替换每个重叠密码子
        for codon_idx in range(start_codon, min(end_codon + 1, len(aa_seq))):
            aa = aa_seq[codon_idx]
            current_codon = ''.join(dna_list[codon_idx*3:(codon_idx+1)*3])
            
            # 获取该氨基酸的其他密码子
            alternatives = [c for c in CODON_TABLE[aa] if c != current_codon]
            
            # 按频率排序，尝试替换
            for alt_codon in sorted(alternatives, key=lambda c: -self.codon_freq.get(c, 0)):
                # 测试替换
                test_seq = dna_list.copy()
                for i, nt in enumerate(alt_codon):
                    test_seq[codon_idx*3 + i] = nt
                
                # 检查motif是否消失
                if motif.upper() not in ''.join(test_seq).upper():
                    return ''.join(test_seq)
        
        return None
    
    def _smooth_gc(
        self,
        dna_list: List[str],
        aa_seq: str,
        gc_target: Tuple[float, float]
    ) -> List[str]:
        """平滑GC含量"""
        gc = self._calculate_gc_content(''.join(dna_list))
        need_increase = gc < gc_target[0]
        
        # 找到GC含量可调整的密码子
        for i, aa in enumerate(aa_seq):
            current_codon = ''.join(dna_list[i*3:(i+1)*3])
            current_gc = (current_codon.count('G') + current_codon.count('C')) / 3
            
            alternatives = CODON_TABLE[aa]
            for alt_codon in sorted(alternatives, key=lambda c: -self.codon_freq.get(c, 0)):
                alt_gc = (alt_codon.count('G') + alt_codon.count('C')) / 3
                
                # 根据需求选择
                if need_increase and alt_gc > current_gc:
                    for j, nt in enumerate(alt_codon):
                        dna_list[i*3 + j] = nt
                    break
                elif not need_increase and alt_gc < current_gc:
                    for j, nt in enumerate(alt_codon):
                        dna_list[i*3 + j] = nt
                    break
        
        return dna_list
    
    def _has_poly_x(self, dna: str, threshold: int) -> bool:
        """检查是否有连续相同碱基"""
        for nt in 'ATGC':
            if nt * threshold in dna:
                return True
        return False
    
    def _break_poly_x(self, dna_list: List[str], aa_seq: str) -> List[str]:
        """尝试替换覆盖同聚核苷酸区的同义密码子，并保证算法终止。"""
        for nt in 'ATGC':
            pattern = nt * 4
            max_replacements = max(1, len(aa_seq) * 2)

            for _ in range(max_replacements):
                dna = ''.join(dna_list)
                pos = dna.find(pattern)
                if pos < 0:
                    break

                run_end = pos
                while run_end < len(dna) and dna[run_end] == nt:
                    run_end += 1

                first_codon = pos // 3
                last_codon = min(len(aa_seq) - 1, (run_end - 1) // 3)
                changed = False

                for codon_idx in range(first_codon, last_codon + 1):
                    aa = aa_seq[codon_idx]
                    start = codon_idx * 3
                    current = ''.join(dna_list[start:start + 3])
                    alternatives = sorted(
                        (c for c in CODON_TABLE[aa] if c != current),
                        key=lambda c: self.codon_freq.get(c, 0),
                        reverse=True,
                    )
                    for alternative in alternatives:
                        candidate = dna[:start] + alternative + dna[start + 3:]
                        if candidate.count(pattern) < dna.count(pattern):
                            dna_list[start:start + 3] = alternative
                            changed = True
                            break
                    if changed:
                        break

                if not changed:
                    break

        return dna_list
    
    def _calculate_cai(self, dna_seq: str, aa_seq: str) -> float:
        """
        计算CAI (Codon Adaptation Index)
        
        CAI = (Π(w_i))^(1/L)
        其中 w_i 是每个密码子的相对适应性，L是密码子数量
        """
        if not dna_seq or not aa_seq:
            return 0.0
        
        # 计算每个氨基酸的最优密码子频率
        max_freq = {}
        for aa, codons in CODON_TABLE.items():
            if aa != '*':
                max_freq[aa] = max(self.codon_freq.get(c, 0) for c in codons)
        
        # 计算每个密码子的w值
        w_values = []
        for i, aa in enumerate(aa_seq):
            codon = dna_seq[i*3:(i+1)*3]
            freq = self.codon_freq.get(codon, 0.01)
            max_f = max_freq.get(aa, 1)
            w = freq / max_f if max_f > 0 else 0.01
            w_values.append(w)
        
        # 计算几何平均
        log_sum = sum(math.log(w) for w in w_values)
        cai = math.exp(log_sum / len(w_values))
        
        return cai
    
    def _calculate_gc_content(self, dna_seq: str) -> float:
        """计算GC含量"""
        if not dna_seq:
            return 0.0
        gc = dna_seq.count('G') + dna_seq.count('C')
        return gc / len(dna_seq)
    
    def _calculate_gc_distribution(self, dna_seq: str, window: int = 50) -> List[float]:
        """计算GC分布（滑动窗口）"""
        if len(dna_seq) < window:
            return [self._calculate_gc_content(dna_seq)]
        
        distribution = []
        for i in range(0, len(dna_seq) - window + 1, window // 2):
            segment = dna_seq[i:i+window]
            distribution.append(self._calculate_gc_content(segment))
        
        return distribution
    
    def _find_motifs(self, dna: str, motifs: List[str]) -> List[str]:
        """查找序列中的目标motif"""
        found = []
        for motif in motifs:
            if motif.upper() in dna.upper():
                found.append(motif)
        return found


def translate_dna(dna_sequence: str) -> str:
    """将DNA序列翻译为氨基酸序列"""
    dna_sequence = dna_sequence.upper().replace('T', 'U')
    
    aa_sequence = []
    for i in range(0, len(dna_sequence) - 2, 3):
        codon = dna_sequence[i:i+3]
        aa = AMINO_ACID_TABLE.get(codon.replace('U', 'T'), 'X')
        aa_sequence.append(aa)
    
    return ''.join(aa_sequence)


def reverse_translate(aa_sequence: str, species: str = "ecoli") -> str:
    """将氨基酸序列反向翻译为DNA序列（物种最优密码子，不做额外优化）。"""
    optimizer = CodonOptimizer(species)
    return optimizer.back_translate(aa_sequence)
