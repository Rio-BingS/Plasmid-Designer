"""
序列验证模块

功能：
- ORF 验证
- 移码检测
- 限制性位点检查
- GC分析
- 二级结构预测
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from core.seq_utils import CODON_TO_AA as _CODON_TO_AA, gc_percent


@dataclass
class ValidationResult:
    """验证结果"""
    is_valid: bool
    errors: List[str]
    warnings: List[str]
    details: Dict[str, any]
    
    def add_error(self, msg: str):
        self.errors.append(msg)
        self.is_valid = False
    
    def add_warning(self, msg: str):
        self.warnings.append(msg)


class SequenceValidator:
    """序列验证器"""
    
    # 起始密码子和终止密码子
    START_CODONS = ['ATG', 'GTG', 'TTG']  # ATG最常见
    STOP_CODONS = ['TAA', 'TAG', 'TGA']
    
    # 遗传密码表（收敛到 core.seq_utils；方向：密码子 → 氨基酸）
    CODON_TABLE = _CODON_TO_AA
    
    def validate(
        self,
        sequence: str,
        sequence_type: str = "dna",  # dna, amino_acid
        check_orf: bool = True,
        check_gc: bool = True,
        check_restriction_sites: bool = True,
        forbidden_sites: Optional[List[str]] = None
    ) -> ValidationResult:
        """
        执行全面验证
        
        Args:
            sequence: 输入序列
            sequence_type: 序列类型 (dna, amino_acid)
            check_orf: 是否检查ORF
            check_gc: 是否检查GC含量
            check_restriction_sites: 是否检查限制性位点
            forbidden_sites: 需要检查的限制性位点列表
        
        Returns:
            ValidationResult
        """
        result = ValidationResult(
            is_valid=True,
            errors=[],
            warnings=[],
            details={}
        )
        
        sequence = sequence.upper().replace('U', 'T').replace('\n', '').replace(' ', '')
        
        # 基本验证
        if sequence_type == "dna":
            self._validate_dna_sequence(sequence, result)
        elif sequence_type == "amino_acid":
            self._validate_aa_sequence(sequence, result)
        
        # ORF验证
        if check_orf and sequence_type == "dna":
            self._validate_orf(sequence, result)
        
        # GC含量
        if check_gc and sequence_type == "dna":
            self._validate_gc_content(sequence, result)
        
        # 限制性位点
        if check_restriction_sites and sequence_type == "dna" and forbidden_sites:
            self._validate_restriction_sites(sequence, forbidden_sites, result)
        
        return result
    
    def _validate_dna_sequence(self, sequence: str, result: ValidationResult):
        """验证DNA序列"""
        valid_bases = set('ATGC')
        
        for i, base in enumerate(sequence):
            if base not in valid_bases:
                result.add_error(f"Invalid base '{base}' at position {i+1}")
        
        result.details['sequence_length'] = len(sequence)
        result.details['sequence_type'] = 'DNA'
    
    def _validate_aa_sequence(self, sequence: str, result: ValidationResult):
        """验证氨基酸序列"""
        valid_aa = set('ACDEFGHIKLMNPQRSTVWY')
        
        for i, aa in enumerate(sequence):
            if aa not in valid_aa:
                result.add_error(f"Invalid amino acid '{aa}' at position {i+1}")
        
        result.details['sequence_length'] = len(sequence)
        result.details['sequence_type'] = 'Amino Acid'
    
    def _validate_orf(self, sequence: str, result: ValidationResult):
        """验证ORF"""
        # 检查起始密码子
        start_codon = sequence[:3]
        if start_codon not in self.START_CODONS:
            result.add_warning(f"Sequence does not start with canonical start codon (found {start_codon})")
        else:
            result.details['start_codon'] = start_codon
        
        # 检查终止密码子
        stop_codon = sequence[-3:]
        if stop_codon not in self.STOP_CODONS:
            result.add_warning(f"Sequence does not end with stop codon (found {stop_codon})")
        else:
            result.details['stop_codon'] = stop_codon
        
        # 检查长度是否为3的倍数
        if len(sequence) % 3 != 0:
            result.add_error(f"Sequence length ({len(sequence)}) is not a multiple of 3")
        else:
            # 翻译并检查内部终止密码子
            translation = self._translate(sequence)
            if '*' in translation[:-1]:
                positions = [i+1 for i, aa in enumerate(translation[:-1]) if aa == '*']
                result.add_warning(f"Internal stop codons at positions: {positions}")
            
            result.details['protein_length'] = len(translation.replace('*', ''))
            result.details['translation'] = translation
    
    def _validate_gc_content(self, sequence: str, result: ValidationResult):
        """验证GC含量"""
        gc_content = gc_percent(sequence)

        result.details['gc_content'] = gc_content
        
        if gc_content < 30:
            result.add_warning(f"GC content is low ({gc_content:.1f}%), may affect expression")
        elif gc_content > 70:
            result.add_warning(f"GC content is high ({gc_content:.1f}%), may cause cloning issues")
        
        # 检查GC分布
        gc_distribution = self._calculate_gc_distribution(sequence)
        result.details['gc_distribution'] = gc_distribution
        
        # 检查极端GC区域
        max_gc = max(gc_distribution) if gc_distribution else 0
        min_gc = min(gc_distribution) if gc_distribution else 0
        
        if max_gc > 80:
            result.add_warning(f"High GC region detected (max {max_gc:.1f}%)")
        if min_gc < 20:
            result.add_warning(f"Low GC region detected (min {min_gc:.1f}%)")
    
    def _validate_restriction_sites(
        self,
        sequence: str,
        forbidden_sites: List[str],
        result: ValidationResult
    ):
        """验证限制性位点"""
        found_sites = {}
        
        for site in forbidden_sites:
            site = site.upper()
            positions = []
            start = 0
            while True:
                pos = sequence.find(site, start)
                if pos == -1:
                    break
                positions.append(pos + 1)  # 1-indexed
                start = pos + 1
            
            if positions:
                found_sites[site] = positions
        
        if found_sites:
            for site, positions in found_sites.items():
                result.add_warning(f"Restriction site {site} found at position(s): {positions}")
        
        result.details['restriction_sites_found'] = found_sites
    
    def _translate(self, dna: str) -> str:
        """翻译DNA为氨基酸"""
        protein = []
        for i in range(0, len(dna) - 2, 3):
            codon = dna[i:i+3]
            aa = self.CODON_TABLE.get(codon, 'X')
            protein.append(aa)
        return ''.join(protein)
    
    def _calculate_gc_distribution(self, sequence: str, window: int = 50) -> List[float]:
        """计算GC分布"""
        if len(sequence) < window:
            return [gc_percent(sequence)]

        distribution = []
        for i in range(0, len(sequence) - window + 1, window // 2):
            distribution.append(gc_percent(sequence[i:i+window]))

        return distribution
    
    def find_orfs(
        self,
        sequence: str,
        min_length: int = 100
    ) -> List[Dict]:
        """
        查找所有ORF
        
        Returns:
            List of ORF info: [{start, end, frame, length, translation}]
        """
        sequence = sequence.upper()
        orfs = []
        
        for frame in range(3):
            # 查找起始密码子
            starts = []
            for i in range(frame, len(sequence) - 2, 3):
                codon = sequence[i:i+3]
                if codon in self.START_CODONS:
                    starts.append(i)
            
            # 对每个起始密码子，查找最近的终止密码子
            for start in starts:
                for i in range(start + 3, len(sequence) - 2, 3):
                    codon = sequence[i:i+3]
                    if codon in self.STOP_CODONS:
                        orf_seq = sequence[start:i+3]
                        length = len(orf_seq)
                        if length >= min_length:
                            orfs.append({
                                'start': start + 1,  # 1-indexed
                                'end': i + 3,
                                'frame': frame + 1,
                                'length': length,
                                'translation': self._translate(orf_seq)
                            })
                        break
        
        return sorted(orfs, key=lambda x: x['length'], reverse=True)
    
    def check_frame_shift(
        self,
        sequence: str,
        expected_frame: int = 1
    ) -> Dict:
        """
        检查移码情况
        
        Returns:
            {in_frame: bool, frame: int, notes: str}
        """
        # 简化版：检查序列是否能正确翻译
        frame = (len(sequence) - 3) % 3 + 1  # 减去终止密码子
        
        return {
            'in_frame': frame == expected_frame,
            'frame': frame,
            'expected_frame': expected_frame,
            'notes': 'Frame matches' if frame == expected_frame else f'Frame shift detected'
        }


def analyze_sequence_composition(sequence: str) -> Dict:
    """分析序列组成"""
    sequence = sequence.upper()

    return {
        'length': len(sequence),
        'gc_content': gc_percent(sequence),
        'at_content': (sequence.count('A') + sequence.count('T')) / len(sequence) * 100 if sequence else 0,
        'composition': {
            'A': sequence.count('A'),
            'T': sequence.count('T'),
            'G': sequence.count('G'),
            'C': sequence.count('C'),
        },
        'gc_ratio': gc_percent(sequence) / 100,
    }


def predict_tm(sequence: str) -> float:
    """预测熔解温度（简化版）"""
    sequence = sequence.upper()
    
    if len(sequence) < 14:
        # Wallace rule
        return 2 * (sequence.count('A') + sequence.count('T')) + \
               4 * (sequence.count('G') + sequence.count('C'))
    else:
        # 近似公式
        gc = sequence.count('G') + sequence.count('C')
        return 64.9 + 41 * (gc - 16.4) / len(sequence)
