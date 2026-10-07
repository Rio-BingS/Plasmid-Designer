"""
高级引物设计模块
支持嵌套引物、测序引物、定点突变、融合引物等
"""

from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import re

import sys
from app.config import BACKEND_DIR
sys.path.insert(0, str(BACKEND_DIR))

from core.seq_utils import revcomp as _seq_revcomp
from core.primer_designer import PrimerDesigner, Primer, PrimerPair


class PrimerPurpose(str, Enum):
    """引物用途"""
    AMPLIFICATION = "amplification"
    SEQUENCING = "sequencing"
    MUTAGENESIS = "mutagenesis"
    NESTED = "nested"
    OVERLAP = "overlap"
    CLONING = "cloning"


@dataclass
class AdvancedPrimerPair:
    """高级引物对"""
    forward: Primer
    reverse: Primer
    purpose: PrimerPurpose
    target_region: Tuple[int, int]
    notes: str = ""


class AdvancedPrimerDesigner(PrimerDesigner):
    """高级引物设计器"""
    
    def design_sequencing_primers(
        self,
        sequence: str,
        interval: int = 500,
        read_length: int = 800,
        overlap: int = 50
    ) -> List[AdvancedPrimerPair]:
        """
        设计测序引物（双端测序，带重叠）
        
        Args:
            sequence: 待测序列
            interval: 测序间隔
            read_length: 读长
            overlap: 重叠长度
        
        Returns:
            测序引物对列表
        """
        sequence = sequence.upper()
        length = len(sequence)
        primers = []
        
        # 正向测序引物
        for pos in range(0, length, interval):
            if pos + 100 > length:
                break
            
            # 提取引物设计区域
            design_region = sequence[pos:pos + 100]
            
            # 设计正向引物
            fwd_primer = self._design_single_primer(
                design_region,
                f"Seq_F{pos}",
                min_tm=55,
                max_tm=65
            )
            
            primers.append(AdvancedPrimerPair(
                forward=fwd_primer,
                reverse=None,  # 测序通常是单向的
                purpose=PrimerPurpose.SEQUENCING,
                target_region=(pos, min(pos + read_length, length)),
                notes=f"Forward sequencing primer at position {pos}"
            ))
        
        # 反向测序引物（从末端开始）
        for pos in range(length - read_length, 0, -interval):
            if pos < 100:
                break
            
            # 提取引物设计区域（反向）
            design_region = self._reverse_complement(sequence[pos - 100:pos])
            
            rev_primer = self._design_single_primer(
                design_region,
                f"Seq_R{pos}",
                min_tm=55,
                max_tm=65
            )
            
            primers.append(AdvancedPrimerPair(
                forward=None,
                reverse=rev_primer,
                purpose=PrimerPurpose.SEQUENCING,
                target_region=(max(0, pos - read_length), pos),
                notes=f"Reverse sequencing primer at position {pos}"
            ))
        
        return primers
    
    def design_nested_primers(
        self,
        sequence: str,
        target_start: int,
        target_end: int,
        outer_margin: int = 200,
        inner_margin: int = 50
    ) -> Dict[str, AdvancedPrimerPair]:
        """
        设计嵌套 PCR 引物
        
        Args:
            sequence: 模板序列
            target_start: 目标起始位置
            target_end: 目标终止位置
            outer_margin: 外侧引物边距
            inner_margin: 内侧引物边距
        
        Returns:
            外侧和内侧引物对
        """
        # 外侧引物
        outer_fwd_start = max(0, target_start - outer_margin)
        outer_rev_end = min(len(sequence), target_end + outer_margin)
        
        outer_pair = self.design_pcr_primers(
            sequence[outer_fwd_start:outer_rev_end],
            primer_name="Outer"
        )
        
        # 内侧引物
        inner_fwd_start = max(0, target_start - inner_margin)
        inner_rev_end = min(len(sequence), target_end + inner_margin)
        
        inner_pair = self.design_pcr_primers(
            sequence[inner_fwd_start:inner_rev_end],
            primer_name="Inner"
        )
        
        return {
            "outer": AdvancedPrimerPair(
                forward=pair.forward if (pair := outer_pair) else None,
                reverse=pair.reverse if pair else None,
                purpose=PrimerPurpose.NESTED,
                target_region=(outer_fwd_start, outer_rev_end),
                notes="Outer primers for nested PCR"
            ),
            "inner": AdvancedPrimerPair(
                forward=pair.forward if (pair := inner_pair) else None,
                reverse=pair.reverse if pair else None,
                purpose=PrimerPurpose.NESTED,
                target_region=(inner_fwd_start, inner_rev_end),
                notes="Inner primers for nested PCR"
            )
        }
    
    def design_mutagenesis_primer(
        self,
        template_sequence: str,
        mutation_position: int,
        mutation_type: str,
        mutation_value: str,
        primer_length: int = 30
    ) -> AdvancedPrimerPair:
        """
        设计定点突变引物（基于 QuikChange 原理）
        
        Args:
            template_sequence: 模板序列
            mutation_position: 突变位置
            mutation_type: 突变类型 (substitution, insertion, deletion)
            mutation_value: 突变值（替换碱基或插入序列）
            primer_length: 引物总长度
        
        Returns:
            突变引物对
        """
        template = template_sequence.upper()
        
        # 构建突变序列
        flank_length = (primer_length - len(mutation_value)) // 2
        
        if mutation_type == "substitution":
            # 替换突变
            left_flank = template[max(0, mutation_position - flank_length):mutation_position]
            right_flank = template[mutation_position + 1:min(len(template), mutation_position + flank_length + 1)]
            mutated_seq = left_flank + mutation_value + right_flank
            
        elif mutation_type == "insertion":
            # 插入突变
            left_flank = template[max(0, mutation_position - flank_length):mutation_position + 1]
            right_flank = template[mutation_position + 1:min(len(template), mutation_position + flank_length + 1)]
            mutated_seq = left_flank + mutation_value + right_flank
            
        else:  # deletion
            # 缺失突变
            left_flank = template[max(0, mutation_position - flank_length):mutation_position]
            right_flank = template[mutation_position + len(mutation_value):]
            mutated_seq = left_flank + right_flank
        
        # 设计正向突变引物
        fwd_primer = Primer(
            name=f"Mut_F_{mutation_position}",
            sequence=mutated_seq[:25],
            full_sequence=mutated_seq[:25],
            tm=self._calculate_tm(mutated_seq[:25]),
            gc_content=self._calculate_gc(mutated_seq[:25]),
            length=len(mutated_seq[:25])
        )
        
        # 设计反向突变引物（互补）
        rev_sequence = self._reverse_complement(mutated_seq[-25:])
        rev_primer = Primer(
            name=f"Mut_R_{mutation_position}",
            sequence=rev_sequence,
            full_sequence=rev_sequence,
            tm=self._calculate_tm(rev_sequence),
            gc_content=self._calculate_gc(rev_sequence),
            length=len(rev_sequence)
        )
        
        return AdvancedPrimerPair(
            forward=fwd_primer,
            reverse=rev_primer,
            purpose=PrimerPurpose.MUTAGENESIS,
            target_region=(mutation_position, mutation_position + len(mutation_value)),
            notes=f"{mutation_type} mutagenesis: {mutation_value} at position {mutation_position}"
        )
    
    def design_overlap_extension_primers(
        self,
        fragment1: str,
        fragment2: str,
        overlap_length: int = 20
    ) -> Dict[str, Primer]:
        """
        设计重叠延伸 PCR 引物（用于多片段组装）
        
        Args:
            fragment1: 第一个片段
            fragment2: 第二个片段
            overlap_length: 重叠长度
        
        Returns:
            重叠引物
        """
        # Fragment1 反向引物添加 Fragment2 重叠序列
        f1_rev_seq = self._reverse_complement(fragment1[-overlap_length:])
        f2_fwd_overlap = fragment2[:overlap_length]
        
        overlap_primer_seq = f1_rev_seq + f2_fwd_overlap
        
        # Fragment2 正向引物添加 Fragment1 重叠序列
        f2_fwd_seq = fragment2[:overlap_length]
        f1_rev_overlap = self._reverse_complement(fragment1[-overlap_length:])
        
        overlap_primer_seq_alt = f2_fwd_seq + f1_rev_overlap
        
        return {
            "fragment1_reverse": Primer(
                name="F1_R_overlap",
                sequence=overlap_primer_seq[:30],
                full_sequence=overlap_primer_seq,
                tm=self._calculate_tm(overlap_primer_seq[:30]),
                gc_content=self._calculate_gc(overlap_primer_seq[:30]),
                length=len(overlap_primer_seq)
            ),
            "fragment2_forward": Primer(
                name="F2_F_overlap",
                sequence=overlap_primer_seq_alt[:30],
                full_sequence=overlap_primer_seq_alt,
                tm=self._calculate_tm(overlap_primer_seq_alt[:30]),
                gc_content=self._calculate_gc(overlap_primer_seq_alt[:30]),
                length=len(overlap_primer_seq_alt)
            )
        }
    
    def _reverse_complement(self, sequence: str) -> str:
        """反向互补（收敛到 core.seq_utils.revcomp）"""
        return _seq_revcomp(sequence.upper())
    
    def _design_single_primer(
        self,
        sequence: str,
        name: str,
        min_tm: float = 55,
        max_tm: float = 65
    ) -> Primer:
        """设计单个引物"""
        best_length = 18
        best_tm = 0
        best_gc = 0
        
        for length in range(18, 30):
            primer_seq = sequence[:length]
            tm = self._calculate_tm(primer_seq)
            gc = self._calculate_gc(primer_seq)
            
            if min_tm <= tm <= max_tm:
                best_length = length
                best_tm = tm
                best_gc = gc
                break
        
        primer_seq = sequence[:best_length]
        return Primer(
            name=name,
            sequence=primer_seq,
            full_sequence=primer_seq,
            tm=best_tm,
            gc_content=best_gc,
            length=best_length
        )
