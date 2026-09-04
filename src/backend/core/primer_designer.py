"""
引物设计模块

基于 Primer3 算法，支持：
- 普通PCR引物设计
- Gibson Assembly 引物设计（带同源臂）
- Golden Gate 引物设计（带IIS酶位点）
- 测序引物设计
"""

import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


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


class PrimerDesigner:
    """引物设计器"""
    
    def __init__(
        self,
        tm_min: float = 58.0,
        tm_max: float = 62.0,
        gc_min: float = 40.0,
        gc_max: float = 60.0,
        length_min: int = 18,
        length_max: int = 25,
        max_poly_x: int = 4,
        max_self_comp: int = 8,
    ):
        """
        初始化引物设计器
        
        Args:
            tm_min/max: Tm 范围
            gc_min/max: GC 含量范围 (%)
            length_min/max: 引物长度范围
            max_poly_x: 最大连续相同碱基数
            max_self_comp: 最大自互补碱基数
        """
        self.tm_min = tm_min
        self.tm_max = tm_max
        self.gc_min = gc_min
        self.gc_max = gc_max
        self.length_min = length_min
        self.length_max = length_max
        self.max_poly_x = max_poly_x
        self.max_self_comp = max_self_comp
    
    def design_pcr_primers(
        self,
        template: str,
        target_start: int = 0,
        target_end: Optional[int] = None,
        primer_name: str = "primer"
    ) -> PrimerPair:
        """
        设计普通PCR引物
        
        Args:
            template: 模板序列
            target_start: 目标区域起始位置 (0-indexed)
            target_end: 目标区域结束位置，None表示到序列末尾
            primer_name: 引物名称前缀
        
        Returns:
            PrimerPair 引物对
        """
        template = template.upper()
        if target_end is None:
            target_end = len(template)
        
        # 设计正向引物
        forward = self._design_forward_primer(
            template, target_start, f"{primer_name}_F"
        )
        
        # 设计反向引物
        reverse = self._design_reverse_primer(
            template, target_end, f"{primer_name}_R"
        )
        
        # 计算产物大小和推荐退火温度
        product_size = target_end - target_start
        annealing_temp = self._calculate_annealing_temp(
            forward.tm, reverse.tm
        )
        
        return PrimerPair(
            forward=forward,
            reverse=reverse,
            product_size=product_size,
            annealing_temp=annealing_temp
        )
    
    def design_gibson_primers(
        self,
        insert_seq: str,
        vector_seq: str,
        insert_start_in_vector: int,
        homology_arm: int = 20,
        primer_name: str = "gibson"
    ) -> PrimerPair:
        """
        设计Gibson Assembly引物
        
        Args:
            insert_seq: 插入片段序列
            vector_seq: 载体序列
            insert_start_in_vector: 插入位置在载体中的起始位置
            homology_arm: 同源臂长度 (bp)
            primer_name: 引物名称前缀
        
        Returns:
            PrimerPair 带同源臂的引物对
        """
        insert_seq = insert_seq.upper()
        vector_seq = vector_seq.upper()
        
        # 正向引物：载体同源臂 + 插入片段起始
        vector_upstream = vector_seq[
            max(0, insert_start_in_vector - homology_arm):insert_start_in_vector
        ]
        forward_annealing = self._design_forward_primer(insert_seq, 0, "temp")
        forward = Primer(
            name=f"{primer_name}_F",
            sequence=forward_annealing.sequence,
            primer_type=PrimerType.GIBSON_FP,
            tm=forward_annealing.tm,
            gc_content=forward_annealing.gc_content,
            length=forward_annealing.length,
            target_start=0,
            overhang=vector_upstream,
            notes=f"Gibson forward, {homology_arm}bp homology arm"
        )
        
        # 反向引物：插入片段末尾 + 载体同源臂（反向互补）
        insert_end = len(insert_seq)
        vector_downstream = vector_seq[
            insert_start_in_vector:insert_start_in_vector + homology_arm
        ]
        reverse_annealing = self._design_reverse_primer(insert_seq, insert_end, "temp")
        # 载体下游序列的反向互补作为overhang
        vector_downstream_rc = self._reverse_complement(vector_downstream)
        reverse = Primer(
            name=f"{primer_name}_R",
            sequence=reverse_annealing.sequence,
            primer_type=PrimerType.GIBSON_RP,
            tm=reverse_annealing.tm,
            gc_content=reverse_annealing.gc_content,
            length=reverse_annealing.length,
            target_end=insert_end,
            overhang=vector_downstream_rc,
            notes=f"Gibson reverse, {homology_arm}bp homology arm"
        )
        
        product_size = len(insert_seq)
        annealing_temp = self._calculate_annealing_temp(forward.tm, reverse.tm)
        
        return PrimerPair(
            forward=forward,
            reverse=reverse,
            product_size=product_size,
            annealing_temp=annealing_temp
        )
    
    def design_golden_gate_primers(
        self,
        insert_seq: str,
        enzyme_name: str,
        overhang_seq_5: str,
        overhang_seq_3: str,
        primer_name: str = "gg"
    ) -> PrimerPair:
        """
        设计Golden Gate引物
        
        Args:
            insert_seq: 插入片段序列
            enzyme_name: Type IIS 酶名称 (如 BsaI, BsmBI)
            overhang_seq_5: 5' 端4bp overhang序列
            overhang_seq_3: 3' 端4bp overhang序列
            primer_name: 引物名称前缀
        
        Returns:
            PrimerPair Golden Gate引物对
        """
        # 酶切位点序列
        enzyme_sites = {
            'BsaI': 'GGTCTC',
            'BsmBI': 'CGTCTC',
            'BbsI': 'GAAGAC',
        }
        
        enzyme_site = enzyme_sites.get(enzyme_name, 'GGTCTC')
        
        # 正向引物结构：酶切位点(1N) + overhang + 插入片段
        # 5' - [酶切位点] - N - [overhang] - [插入片段] - 3'
        forward_annealing = self._design_forward_primer(insert_seq, 0, "temp")
        forward_overhang = f"GG{enzyme_site}A{overhang_seq_5}"  # A是spacer
        
        forward = Primer(
            name=f"{primer_name}_F",
            sequence=forward_annealing.sequence,
            primer_type=PrimerType.GOLDENGATE_FP,
            tm=forward_annealing.tm,
            gc_content=forward_annealing.gc_content,
            length=forward_annealing.length,
            target_start=0,
            overhang=forward_overhang,
            restriction_site=enzyme_name,
            notes=f"Golden Gate forward, {enzyme_name}, overhang: {overhang_seq_5}"
        )
        
        # 反向引物：酶切位点的反向互补 + overhang的反向互补
        reverse_annealing = self._design_reverse_primer(insert_seq, len(insert_seq), "temp")
        enzyme_site_rc = self._reverse_complement(enzyme_site)
        overhang_3_rc = self._reverse_complement(overhang_seq_3)
        reverse_overhang = f"GG{enzyme_site_rc}A{overhang_3_rc}"
        
        reverse = Primer(
            name=f"{primer_name}_R",
            sequence=reverse_annealing.sequence,
            primer_type=PrimerType.GOLDENGATE_RP,
            tm=reverse_annealing.tm,
            gc_content=reverse_annealing.gc_content,
            length=reverse_annealing.length,
            target_end=len(insert_seq),
            overhang=reverse_overhang,
            restriction_site=enzyme_name,
            notes=f"Golden Gate reverse, {enzyme_name}, overhang: {overhang_seq_3}"
        )
        
        product_size = len(insert_seq)
        annealing_temp = self._calculate_annealing_temp(forward.tm, reverse.tm)
        
        return PrimerPair(
            forward=forward,
            reverse=reverse,
            product_size=product_size,
            annealing_temp=annealing_temp
        )

    def design_restriction_primers(
        self,
        sequence: str,
        enzyme_5: str,
        enzyme_3: str,
        primer_name: str = "res",
        anneal_length: int = 20
    ) -> PrimerPair:
        """设计双酶切克隆引物

        正向引物 5' 端加 enzyme_5 识别位点，反向引物 5' 端加 enzyme_3
        位点的反向互补链；退火区取自插入片段两端。Tm 按退火区计算。

        Args:
            sequence: 插入片段序列
            enzyme_5: 5' 端限制酶名称
            enzyme_3: 3' 端限制酶名称
            primer_name: 引物名称前缀
            anneal_length: 退火区长度 (默认20bp，超过插入片段长度时自动收缩)

        Returns:
            PrimerPair 双酶切引物对
        """
        from core.sequence_analysis import RESTRICTION_ENZYMES

        seq = sequence.upper()
        site5 = RESTRICTION_ENZYMES.get(enzyme_5, (None,))[0]
        site3 = RESTRICTION_ENZYMES.get(enzyme_3, (None,))[0]
        if not site5 or not site3:
            missing = enzyme_5 if not site5 else enzyme_3
            raise ValueError(f"未知限制酶: {missing}")

        anneal = min(anneal_length, len(seq))
        if anneal < 6:
            raise ValueError(f"插入片段过短 ({len(seq)}bp)，无法设计双酶切引物")

        fwd_anneal = seq[:anneal]
        rev_anneal = self._reverse_complement(seq[-anneal:])
        forward_seq = site5 + fwd_anneal
        reverse_seq = self._reverse_complement(site3) + rev_anneal

        tm_f = self._calculate_tm(fwd_anneal)
        tm_r = self._calculate_tm(rev_anneal)

        forward = Primer(
            name=f"{primer_name}_F",
            sequence=forward_seq,
            primer_type=PrimerType.PRIMER,
            tm=tm_f,
            gc_content=self._calculate_gc(forward_seq),
            length=len(forward_seq),
            target_start=0,
            target_end=anneal,
            notes=f"Restriction cloning (double digest): {enzyme_5} site at 5', "
                  f"annealing {anneal}bp, Tm on annealing region"
        )
        reverse = Primer(
            name=f"{primer_name}_R",
            sequence=reverse_seq,
            primer_type=PrimerType.PRIMER,
            tm=tm_r,
            gc_content=self._calculate_gc(reverse_seq),
            length=len(reverse_seq),
            target_start=len(seq) - anneal,
            target_end=len(seq),
            notes=f"Restriction cloning (double digest): {enzyme_3} site (rc) at 5', "
                  f"annealing {anneal}bp, Tm on annealing region"
        )

        return PrimerPair(
            forward=forward,
            reverse=reverse,
            product_size=len(seq),
            annealing_temp=min(tm_f, tm_r) - 5
        )

    def _design_forward_primer(
        self,
        template: str,
        start_pos: int,
        name: str
    ) -> Primer:
        """设计正向引物"""
        best_primer = None
        best_score = -1
        
        for length in range(self.length_min, self.length_max + 1):
            for offset in range(0, 5):  # 允许小范围偏移
                seq_start = start_pos + offset
                seq_end = seq_start + length
                
                if seq_end > len(template):
                    continue
                
                seq = template[seq_start:seq_end]
                
                # 检查是否满足条件
                if not self._check_primer_quality(seq):
                    continue
                
                # 计算评分
                score = self._score_primer(seq)
                
                if score > best_score:
                    best_score = score
                    best_primer = Primer(
                        name=name,
                        sequence=seq,
                        primer_type=PrimerType.PRIMER,
                        tm=self._calculate_tm(seq),
                        gc_content=self._calculate_gc(seq),
                        length=length,
                        target_start=seq_start,
                        target_end=seq_end
                    )
        
        if not best_primer:
            # 如果没找到完美引物，放宽条件
            seq = template[start_pos:start_pos + 20]
            best_primer = Primer(
                name=name,
                sequence=seq,
                primer_type=PrimerType.PRIMER,
                tm=self._calculate_tm(seq),
                gc_content=self._calculate_gc(seq),
                length=len(seq),
                target_start=start_pos,
                target_end=start_pos + len(seq),
                notes="Warning: May not meet all quality criteria"
            )
        
        return best_primer
    
    def _design_reverse_primer(
        self,
        template: str,
        end_pos: int,
        name: str
    ) -> Primer:
        """设计反向引物"""
        best_primer = None
        best_score = -1
        
        for length in range(self.length_min, self.length_max + 1):
            for offset in range(0, 5):
                seq_end = end_pos - offset
                seq_start = seq_end - length
                
                if seq_start < 0:
                    continue
                
                # 反向引物需要取反向互补
                seq = template[seq_start:seq_end]
                seq_rc = self._reverse_complement(seq)
                
                if not self._check_primer_quality(seq_rc):
                    continue
                
                score = self._score_primer(seq_rc)
                
                if score > best_score:
                    best_score = score
                    best_primer = Primer(
                        name=name,
                        sequence=seq_rc,
                        primer_type=PrimerType.PRIMER,
                        tm=self._calculate_tm(seq_rc),
                        gc_content=self._calculate_gc(seq_rc),
                        length=length,
                        target_start=seq_start,
                        target_end=seq_end
                    )
        
        if not best_primer:
            seq = template[end_pos - 20:end_pos]
            seq_rc = self._reverse_complement(seq)
            best_primer = Primer(
                name=name,
                sequence=seq_rc,
                primer_type=PrimerType.PRIMER,
                tm=self._calculate_tm(seq_rc),
                gc_content=self._calculate_gc(seq_rc),
                length=len(seq_rc),
                target_start=end_pos - 20,
                target_end=end_pos,
                notes="Warning: May not meet all quality criteria"
            )
        
        return best_primer
    
    def _check_primer_quality(self, seq: str) -> bool:
        """检查引物质量（含自互补/发夹近似）"""
        # GC含量
        gc = self._calculate_gc(seq)
        if gc < self.gc_min or gc > self.gc_max:
            return False

        # Tm
        tm = self._calculate_tm(seq)
        if tm < self.tm_min or tm > self.tm_max:
            return False

        # Poly-X
        for base in 'ATGC':
            if base * self.max_poly_x in seq:
                return False

        # 3'端稳定性（避免G/C超过3个）
        gc_at_3prime = sum(1 for b in seq[-5:] if b in 'GC')
        if gc_at_3prime > 4:
            return False

        # 自身互补过强
        if self._max_self_complementarity(seq) > self.max_self_comp:
            return False

        # 3' 端二聚体风险：末 4bp 与自身反向互补
        tail = seq[-4:]
        if tail == self._reverse_complement(tail):
            return False

        return True
    
    def _score_primer(self, seq: str) -> float:
        """引物评分（越高越好）"""
        tm = self._calculate_tm(seq)
        gc = self._calculate_gc(seq)
        
        # Tm 接近60度最好
        tm_score = 1 - abs(tm - 60) / 10
        
        # GC 接近50%最好
        gc_score = 1 - abs(gc - 50) / 20
        
        # 3'端G/C数量适中（1-2个最佳）
        gc_3prime = sum(1 for b in seq[-5:] if b in 'GC')
        gc_3prime_score = 1 if gc_3prime in [1, 2] else 0.5
        
        # 无自身互补
        self_comp = self._max_self_complementarity(seq)
        self_comp_score = 1 - min(self_comp / 10, 1)
        
        return tm_score * 0.3 + gc_score * 0.3 + gc_3prime_score * 0.2 + self_comp_score * 0.2
    
    def _calculate_tm(self, seq: str) -> float:
        """
        计算Tm值（最近邻法简化版）
        对于 < 14bp: Tm = 2*(A+T) + 4*(G+C)
        对于 >= 14bp: Tm = 64.9 + 41*(G+C-16.4)/(A+T+G+C)
        """
        seq = seq.upper()
        a_count = seq.count('A')
        t_count = seq.count('T')
        g_count = seq.count('G')
        c_count = seq.count('C')
        
        if len(seq) < 14:
            return 2 * (a_count + t_count) + 4 * (g_count + c_count)
        else:
            return 64.9 + 41 * (g_count + c_count - 16.4) / len(seq)
    
    def _calculate_gc(self, seq: str) -> float:
        """计算GC含量（百分比）"""
        seq = seq.upper()
        gc_count = seq.count('G') + seq.count('C')
        return gc_count / len(seq) * 100 if len(seq) > 0 else 0
    
    def _calculate_annealing_temp(self, tm1: float, tm2: float) -> float:
        """计算推荐退火温度"""
        # 通常比最低Tm低3-5度
        min_tm = min(tm1, tm2)
        return max(min_tm - 3, 50)  # 最低不低于50度
    
    def _reverse_complement(self, seq: str) -> str:
        """反向互补"""
        complement = {'A': 'T', 'T': 'A', 'G': 'C', 'C': 'G',
                     'a': 't', 't': 'a', 'g': 'c', 'c': 'g'}
        return ''.join(complement.get(b, b) for b in reversed(seq))
    
    def _max_self_complementarity(self, seq: str) -> int:
        """计算最大自互补碱基数"""
        seq = seq.upper()
        rc = self._reverse_complement(seq)
        max_comp = 0
        
        for i in range(len(seq)):
            for j in range(len(seq)):
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

    def design_synthesis_oligos(
        self,
        sequence: str,
        oligo_length_min: int = 40,
        oligo_length_max: int = 80,
        overlap_length: int = 20,
        primer_name: str = "synth"
    ) -> List[Primer]:
        """设计全基因合成寡核苷酸（错位交替重叠，无完全互补对）

        将目标基因按均衡步长分片，寡核苷酸沿正反链交替排列：
        奇数片取正链、偶数片取对应区域的反向互补链，相邻寡核苷酸
        仅共享 overlap 区域的互补关系——

        - 任何两条寡核苷酸都不是完全互补（避免整对优先退火成
          独立双链、破坏错位组装路径）；
        - 相邻寡核苷酸经 overlap 区退火后聚合酶延伸拼出全长；
        - 片数强制为偶数；单条长度不超过 oligo_length_max。

        Args:
            sequence: 目标基因序列
            oligo_length_min: 单条寡核苷酸最短长度（尽力目标）
            oligo_length_max: 单条寡核苷酸最长长度（硬上限）
            overlap_length: 相邻寡核苷酸重叠区长度
            primer_name: 寡核苷酸名称前缀

        Returns:
            List[Primer] 错位交替寡核苷酸列表
        """
        sequence = sequence.upper()
        seq_len = len(sequence)
        max_span = oligo_length_max - overlap_length
        if max_span <= 0:
            raise ValueError("oligo_length_max 必须大于 overlap_length")
        if seq_len == 0:
            raise ValueError("序列为空")

        # 片数取不小于理论最小值的偶数（满足偶数条要求）
        n_tiles = max(2, math.ceil((seq_len - overlap_length) / max_span))
        if n_tiles % 2 == 1:
            n_tiles += 1
        step = max(1, math.ceil((seq_len - overlap_length) / n_tiles))

        # 初始等步分片
        tiles = []
        pos = 0
        while pos < seq_len:
            end = min(pos + step + overlap_length, seq_len)
            tiles.append([pos, end])
            if end >= seq_len:
                break
            pos += step

        # DNAWorks 式 Tm 均一化：在长度范围内微调内部边界，
        # 使相邻片对的 Tm 差尽量小（退火同步性更好，合成成功率更高）
        tiles = self._tm_homogenize_boundaries(tiles, sequence, oligo_length_min, oligo_length_max)

        oligos = []
        oligo_num = 1
        for pos, end in tiles:
            sense = sequence[pos:end]

            is_sense = oligo_num % 2 == 1
            oligo_seq = sense if is_sense else self._reverse_complement(sense)

            oligos.append(Primer(
                name=f"{primer_name}_{'S' if is_sense else 'AS'}{oligo_num:02d}",
                sequence=oligo_seq,
                primer_type=PrimerType.SYNTHESIS_OLIGO,
                tm=self._calculate_tm(oligo_seq),
                gc_content=self._calculate_gc(oligo_seq),
                length=len(oligo_seq),
                target_start=pos,
                target_end=end,
                notes=f"{'Sense' if is_sense else 'Antisense'} strand oligo, "
                     f"region {pos + 1}-{end}, overlap: {overlap_length}bp",
            ))
            oligo_num += 1

        return oligos

    def _tm_homogenize_boundaries(
        self,
        tiles: List[List[int]],
        sequence: str,
        length_min: int,
        length_max: int,
        rounds: int = 3,
    ) -> List[List[int]]:
        """DNAWorks 式边界微调：移动内部边界使相邻片对的 Tm 差最小。

        每轮对每个内部边界尝试 ±1..±8 的移动（保持片长在
        [length_min, length_max] 且相邻片仍有 overlap），取使
        「相邻片 Tm 差绝对值之和」最小的组合。
        """
        tm_cache: Dict[int, float] = {}

        def tile_tm(start: int, end: int) -> float:
            key = (start, end)
            if key not in tm_cache:
                tm_cache[key] = self._calculate_tm(sequence[start:end])
            return tm_cache[key]

        def total_deviation(ts) -> float:
            return sum(
                abs(tile_tm(ts[i][0], ts[i][1]) - tile_tm(ts[i + 1][0], ts[i + 1][1]))
                for i in range(len(ts) - 1)
            )

        tiles = [list(t) for t in tiles]
        if len(tiles) < 3:
            return tiles

        for _ in range(rounds):
            moved = False
            for b in range(1, len(tiles) - 1):
                current = total_deviation(tiles)
                best_shift, best_dev = 0, current
                # 边界 b 同时是 tiles[b-1] 的 end 与 tiles[b] 的 start
                for shift in range(-8, 9):
                    if shift == 0:
                        continue
                    trial = [list(t) for t in tiles]
                    trial[b - 1][1] += shift
                    trial[b][0] += shift
                    ok = all(
                        (t[1] - t[0]) >= length_min and (t[1] - t[0]) <= length_max
                        for t in trial
                    )
                    # 相邻片仍需保持正向重叠
                    ok = ok and all(
                        trial[i + 1][0] < trial[i + 1][1] and trial[i][1] > trial[i + 1][0]
                        for i in range(len(trial) - 1)
                    )
                    if not ok:
                        continue
                    dev = total_deviation(trial)
                    if dev < best_dev - 1e-9:
                        best_shift, best_dev = shift, dev
                if best_shift != 0:
                    tiles[b - 1][1] += best_shift
                    tiles[b][0] += best_shift
                    moved = True
            if not moved:
                break
        return tiles

    def cross_hybridization_count(
        self,
        oligos: List[Primer],
        stem: int = 12,
    ) -> int:
        """交叉杂交计数：检查每条 oligo 的 3' 端 stem-mer 是否与其他
        oligo 的互补序列匹配（非预期的二聚体位点，DNAWorks 式审查）。

        目标区域重叠的 oligo 对（全基因合成的相邻片）经设计本就通过
        overlap 区退火，属预期配对，不计入交叉杂交。

        Returns:
            匹配次数（0 为理想；>0 建议调整长度范围后重新设计）
        """
        trans = str.maketrans("ATGC", "TACG")
        seqs = [o.sequence.upper() for o in oligos]
        count = 0
        for i, s in enumerate(seqs):
            tail = s[-stem:]
            rc_tail = tail.translate(trans)[::-1]
            for j, t in enumerate(seqs):
                if i == j:
                    continue
                # 预期配对：两条 oligo 的目标区域重叠（相邻片的 overlap 退火）
                ti, tj = oligos[i], oligos[j]
                if ti.target_start < tj.target_end and tj.target_start < ti.target_end:
                    continue
                if rc_tail in t:
                    count += 1
        return count


def export_primers_to_excel(primer_pairs: List[PrimerPair], output_path: str) -> None:
    """导出引物到Excel（简化版，实际可用openpyxl）"""
    import csv
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter='\t')
        writer.writerow([
            'Name', 'Forward Sequence', 'Forward Tm', 
            'Reverse Sequence', 'Reverse Tm',
            'Product Size', 'Recommended Ta'
        ])
        
        for pair in primer_pairs:
            d = pair.to_order_dict()
            writer.writerow([
                d['name'],
                d['forward_seq'],
                d['forward_tm'],
                d['reverse_seq'],
                d['reverse_tm'],
                d['product_size'],
                d['recommended_ta']
            ])
