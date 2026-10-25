"""引物设计（PCR / Gibson / Golden Gate / 基因合成 oligo）

Tm 模型、序列筛查规则与数据类在 core/primer_base.py；本文件是设计策略
（PrimerDesigner 类）。历史导入路径（from core.primer_designer import
Primer / calculate_tm / screen_gg_overhang_pair 等）经下方 re-export 保持兼容。
"""

import math
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from core.seq_utils import gc_percent, revcomp
from core.primer_base import (  # noqa: F401  re-export 兼容既有导入路径
    GG_MIN_MISMATCHES, GG_OVERHANG_LENGTH,
    OVERLAP_LENGTH_MAX, OVERLAP_LENGTH_MIN, OLIGO_TILE_ESCALATIONS,
    OVERLAP_TM_SPREAD_TARGET, Primer, PrimerPair, PrimerType,
    TM_DNTPS_MM, TM_MIN_LENGTH, TM_MG_MM, TM_NA_MM, TM_NN_TABLE,
    TM_PRIMER_NM, TM_SALTCORR, TM_TEMPLATE_NM,
    _gg_site_count, _gg_spacer, _hamming, calculate_tm,
    gg_junction_extra_sites, _tm_nn,
    screen_gg_overhang_pair, gg_site_count,
)


def _vector_slice(seq: str, start: int, length: int, circular: bool) -> str:
    """从载体序列取连续片段。环状时 start 越过首尾自动绕回（跨原点的
    同源臂是真实的载体末端序列）；线性时负起点截到 0（同源臂允许比
    请求的短），越界尾巴自然为空。"""
    n = len(seq)
    if length <= 0 or n == 0:
        return ""
    if circular:
        start %= n
        if start + length <= n:
            return seq[start:start + length]
        return seq[start:] + seq[:start + length - n]
    if start < 0:
        start = 0
    return seq[start:start + length]


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
        left_cut: int,
        right_cut: Optional[int] = None,
        homology_arm: int = 20,
        primer_name: str = "gibson",
        circular: bool = True,
    ) -> PrimerPair:
        """
        设计Gibson Assembly引物（支持双酶切线性化的两个切点）

        Args:
            insert_seq: 插入分子序列（双酶切线性化时含两端位点残留；
                负链装配时应传 head + revcomp(基因) + tail）
            vector_seq: 载体序列
            left_cut: 线性化载体左切点（apply_plan 的 0 基边界口径），
                正向引物同源臂取其上游 homology_arm bp
            right_cut: 右切点；反向引物同源臂取其下游的互补链。
                单点线性化两切点相同（缺省 = left_cut，与旧行为一致）。
                双酶切时两个切点不同，反向臂必须锚定右切点下游——
                取左切点下游会落进被切除的废片段，引物在线性化载体上
                无处退火
            homology_arm: 同源臂长度 (bp)
            primer_name: 引物名称前缀
            circular: 载体是否环状（环状时同源臂切片越过首尾自动绕回）

        Returns:
            PrimerPair 带同源臂的引物对
        """
        if right_cut is None:
            right_cut = left_cut
        insert_seq = insert_seq.upper()
        vector_seq = vector_seq.upper()

        # 正向引物：载体同源臂 + 插入片段起始（退火区锚定插入片段 5' 端，A-07）
        vector_upstream = _vector_slice(
            vector_seq, left_cut - homology_arm, homology_arm, circular)
        forward_annealing = self._design_forward_primer(insert_seq, 0, "temp", anchor=True)
        forward = Primer(
            name=f"{primer_name}_F",
            sequence=forward_annealing.sequence,
            primer_type=PrimerType.GIBSON_FP,
            tm=forward_annealing.tm,
            gc_content=forward_annealing.gc_content,
            length=forward_annealing.length,
            target_start=forward_annealing.target_start,
            target_end=forward_annealing.target_end,
            overhang=vector_upstream,
            notes=f"Gibson forward, {homology_arm}bp homology arm"
        )
        
        # 反向引物：插入片段末尾 + 载体同源臂（反向互补，锚定右切点下游）
        insert_end = len(insert_seq)
        vector_downstream = _vector_slice(
            vector_seq, right_cut, homology_arm, circular)
        reverse_annealing = self._design_reverse_primer(insert_seq, insert_end, "temp", anchor=True)
        # 载体下游序列的反向互补作为overhang
        vector_downstream_rc = self._reverse_complement(vector_downstream)
        reverse = Primer(
            name=f"{primer_name}_R",
            sequence=reverse_annealing.sequence,
            primer_type=PrimerType.GIBSON_RP,
            tm=reverse_annealing.tm,
            gc_content=reverse_annealing.gc_content,
            length=reverse_annealing.length,
            target_start=reverse_annealing.target_start,
            target_end=reverse_annealing.target_end,
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
    
    def design_custom_arm_primers(
        self,
        insert_seq: str,
        arm_5: str,
        arm_3: str,
        primer_name: str = "gibson"
    ) -> PrimerPair:
        """用户自定义同源臂的 Gibson 引物。

        arm_5 / arm_3 为用户提供的重组臂（基因读法方向）：正向引物 = 5′ 臂 +
        基因前段退火区；反向引物 = 臂3 的反向互补 + 基因后段退火区。
        插入分子（PCR 产物）= 臂5 + 基因 + 臂3，两臂与载体连接点同源。
        """
        insert_seq = insert_seq.upper()
        forward_annealing = self._design_forward_primer(insert_seq, 0, "temp", anchor=True)
        forward = Primer(
            name=f"{primer_name}_F",
            sequence=forward_annealing.sequence,
            primer_type=PrimerType.GIBSON_FP,
            tm=forward_annealing.tm,
            gc_content=forward_annealing.gc_content,
            length=forward_annealing.length,
            target_start=forward_annealing.target_start,
            target_end=forward_annealing.target_end,
            overhang=arm_5.upper(),
            notes=f"Gibson forward, user-provided {len(arm_5)}bp homology arm"
        )
        reverse_annealing = self._design_reverse_primer(
            insert_seq, len(insert_seq), "temp", anchor=True)
        reverse = Primer(
            name=f"{primer_name}_R",
            sequence=reverse_annealing.sequence,
            primer_type=PrimerType.GIBSON_RP,
            tm=reverse_annealing.tm,
            gc_content=reverse_annealing.gc_content,
            length=reverse_annealing.length,
            target_start=reverse_annealing.target_start,
            target_end=reverse_annealing.target_end,
            overhang=self._reverse_complement(arm_3.upper()),
            notes=f"Gibson reverse, user-provided {len(arm_3)}bp homology arm"
        )
        return PrimerPair(
            forward=forward,
            reverse=reverse,
            product_size=len(insert_seq),
            annealing_temp=self._calculate_annealing_temp(forward.tm, reverse.tm)
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
        # 酶参数取自 REBASE（core.enzyme_sites.golden_gate_geometry）：任何
        # 可商购、切出 4 nt 5' 突出端的 Type IIS 酶都可用。识别序列与切点
        # 之间的间隔碱基数按酶的切割偏移给出——此前一律写 1 个 A，只对
        # BsaI/BsmBI（N1/N5）正确，BbsI（N2/N6）切下的 overhang 会错位一位
        from core.enzyme_sites import golden_gate_enzymes, golden_gate_geometry

        geo = golden_gate_geometry(enzyme_name)
        if geo is None:
            # 不静默回退 BsaI：传错酶名会得到完全错误的引物而不被察觉
            head = ", ".join(golden_gate_enzymes()[:6])
            raise ValueError(
                f"不支持的 Golden Gate 酶: {enzyme_name}（需可商购、切出 4 nt 5' 突出端的 "
                f"Type IIS 酶），可选如: {head} 等"
            )
        enzyme_site = geo["site"]
        # 间隔碱基按两端各自的拼接上下文挑选：固定 A*n 时 spacer/overhang/
        # 插入片段的拼接处可能新生出同一把酶的位点（BbvI、BcoDI 等随机
        # overhang 下出现第 3 个切点），挑一个不形成额外位点的组合
        spacer_f = _gg_spacer(enzyme_name, enzyme_site, geo["spacer"], overhang_seq_5,
                              insert_seq)
        spacer_r = _gg_spacer(enzyme_name, enzyme_site, geo["spacer"],
                              self._reverse_complement(overhang_seq_3),
                              self._reverse_complement(insert_seq))

        # 正向引物结构：保护碱基 + 酶切位点 + 间隔 + overhang + 插入片段
        # 5' - GG - [酶切位点] - [间隔] - [overhang] - [插入片段] - 3'
        # 退火区锚定插入片段两端（A-07）
        forward_annealing = self._design_forward_primer(insert_seq, 0, "temp", anchor=True)
        forward_overhang = f"GG{enzyme_site}{spacer_f}{overhang_seq_5}"

        forward = Primer(
            name=f"{primer_name}_F",
            sequence=forward_annealing.sequence,
            primer_type=PrimerType.GOLDENGATE_FP,
            tm=forward_annealing.tm,
            gc_content=forward_annealing.gc_content,
            length=forward_annealing.length,
            target_start=forward_annealing.target_start,
            target_end=forward_annealing.target_end,
            overhang=forward_overhang,
            restriction_site=enzyme_name,
            notes=f"Golden Gate forward, {enzyme_name}, overhang: {overhang_seq_5}"
        )

        # 反向引物：位点按引物自身 5'->3' 书写（与正向引物同向），
        # 这样在扩增产物上它落在负链、朝内切割，才能释放 3' 端 overhang；
        # 写成识别序列的反向互补会让位点朝外，overhang 永远切不下来
        reverse_annealing = self._design_reverse_primer(insert_seq, len(insert_seq), "temp", anchor=True)
        overhang_3_rc = self._reverse_complement(overhang_seq_3)
        reverse_overhang = f"GG{enzyme_site}{spacer_r}{overhang_3_rc}"

        reverse = Primer(
            name=f"{primer_name}_R",
            sequence=reverse_annealing.sequence,
            primer_type=PrimerType.GOLDENGATE_RP,
            tm=reverse_annealing.tm,
            gc_content=reverse_annealing.gc_content,
            length=reverse_annealing.length,
            target_start=reverse_annealing.target_start,
            target_end=reverse_annealing.target_end,
            overhang=reverse_overhang,
            restriction_site=enzyme_name,
            notes=f"Golden Gate reverse, {enzyme_name}, overhang: {overhang_seq_3}"
        )
        
        extra = gg_junction_extra_sites(insert_seq, enzyme_name, forward_overhang,
                                        reverse_overhang)
        if extra:
            warn = (f"；警告：引物尾部与插入片段拼接处另形成 {extra} 个 {enzyme_name} "
                    "识别位点（换间隔碱基也避不开），酶切会多切")
            forward.notes += warn
            reverse.notes += warn

        # overhang 连接保真度筛查：不通过只告警，不拒绝设计
        gg_issues = screen_gg_overhang_pair(overhang_seq_5, overhang_seq_3)
        if gg_issues:
            warn = "；警告：overhang 连接保真度筛查未通过：" + "；".join(gg_issues)
            forward.notes += warn
            reverse.notes += warn

        product_size = len(insert_seq)
        annealing_temp = self._calculate_annealing_temp(forward.tm, reverse.tm)
        
        return PrimerPair(
            forward=forward,
            reverse=reverse,
            product_size=product_size,
            annealing_temp=annealing_temp
        )

    # 经典双酶切引物 5' 端保护碱基：酶在 DNA 末端切割效率极低，识别位点
    # 前需留几个碱基。TTTT 非回文、不与常用识别序列拼出新位点
    PROTECTIVE_SPACER = "TTTT"

    # Type IIS（切割位点在识别序列之外）不适用经典双酶切克隆：
    # 它不在识别序列内切，产物末端不含酶位点，按双酶切流程做会得到错误构建。
    # 判定以 REBASE 数据（core.enzyme_sites.is_type_iis）为准，这里的名单
    # 只作兜底（含非商品化/别名写法）
    TYPE_IIS_ENZYMES = ("BsaI", "BsmBI", "BbsI", "BsmAI", "BspQI", "SapI",
                        "AarI", "Esp3I", "BtgZI", "BspMI", "PaqCI")

    def design_restriction_primers(
        self,
        sequence: str,
        enzyme_5: str,
        enzyme_3: str,
        primer_name: str = "res",
        anneal_length: int = 20,
        spacer: Optional[str] = None,
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
            spacer: 5' 端保护碱基，None 用 PROTECTIVE_SPACER，"" 显式关闭

        Returns:
            PrimerPair 双酶切引物对

        Raises:
            ValueError: 酶名未知、传入 Type IIS 酶或插入片段过短
        """
        from core.sequence_analysis import RESTRICTION_ENZYMES

        from core.enzyme_sites import get_enzyme, is_type_iis

        seq = sequence.upper()
        for enz in (enzyme_5, enzyme_3):
            if enz in self.TYPE_IIS_ENZYMES or is_type_iis(get_enzyme(enz)):
                raise ValueError(
                    f"{enz} 是 Type IIS 酶（在识别序列之外切割），"
                    f"不能用于经典双酶切克隆；请改用 Golden Gate，"
                    f"或选择 EcoRI/BamHI/XhoI 等在识别序列内切割的酶"
                )
        site5 = RESTRICTION_ENZYMES.get(enzyme_5, (None,))[0]
        site3 = RESTRICTION_ENZYMES.get(enzyme_3, (None,))[0]
        if not site5 or not site3:
            missing = enzyme_5 if not site5 else enzyme_3
            raise ValueError(f"未知限制酶: {missing}")

        anneal = min(anneal_length, len(seq))
        if anneal < 6:
            raise ValueError(f"插入片段过短 ({len(seq)}bp)，无法设计双酶切引物")

        pad = self.PROTECTIVE_SPACER if spacer is None else spacer

        # 插入片段内部含所选酶的识别位点时，切下来的会是片段而非完整插入
        internal = [
            enz for enz, site in ((enzyme_5, site5), (enzyme_3, site3))
            if site in seq or self._reverse_complement(site) in seq
        ]
        warn = ""
        if internal:
            warn = ("；警告：插入片段内部含 "
                    + "、".join(sorted(set(internal)))
                    + " 识别位点，酶切会切断插入片段，请换酶")

        fwd_anneal = seq[:anneal]
        rev_anneal = self._reverse_complement(seq[-anneal:])
        forward_seq = pad + site5 + fwd_anneal
        reverse_seq = pad + self._reverse_complement(site3) + rev_anneal

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
            notes=f"Restriction cloning (double digest): {enzyme_5} site at 5' "
                  f"with {len(pad)}nt protective spacer, "
                  f"annealing {anneal}bp, Tm on annealing region{warn}"
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
            notes=f"Restriction cloning (double digest): {enzyme_3} site (rc) at 5' "
                  f"with {len(pad)}nt protective spacer, "
                  f"annealing {anneal}bp, Tm on annealing region{warn}"
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
        name: str,
        anchor: bool = False
    ) -> Primer:
        """设计正向引物

        终审 A-07：anchor=True 时退火区 5' 端严格锚定在 start_pos（只枚举
        长度、不枚举偏移）——克隆引物（Gibson/Golden Gate/双酶切）的退火区
        若被允许整体平移 0–4 nt，会静默截短插入片段首尾碱基（实测 200 次
        5' 端 52 次、3' 端 49 次，示例删掉的正是 ATG 起始密码子）。
        """
        best_primer = None
        best_score = -1

        for length in range(self.length_min, self.length_max + 1):
            if anchor:
                seq_start = start_pos  # 锚定：只枚举长度
            else:
                # 预扫描只在本长度内选最优偏移，不能抬高全局 best_score，
                # 否则下方同一序列的复评永远不会胜出，best_primer 恒为 None
                seq_start = None
                offset_best = -1
                for offset in range(0, 5):  # 允许小范围偏移
                    cand_start = start_pos + offset
                    cand_seq = template[cand_start:cand_start + length]
                    if len(cand_seq) == length and self._check_primer_quality(cand_seq):
                        cand_score = self._score_primer(cand_seq)
                        if cand_score > offset_best:
                            offset_best = cand_score
                            seq_start = cand_start
                if seq_start is None:
                    continue

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
            # 如果没找到完美引物，放宽条件（边界钳制在模板内）
            fb_start = max(0, start_pos)
            seq = template[fb_start:fb_start + 20]
            # 普通 PCR 不能给出短于最短长度的"引物"；锚定的克隆引物
            # 退火区可覆盖整个短插入片段（另有 5' 尾），仅空片段报错
            if not seq or (not anchor and len(seq) < self.length_min):
                raise ValueError(
                    f"模板过短：位置 {start_pos} 起仅剩 {len(seq)}bp，"
                    f"不足最短引物长度 {self.length_min}bp"
                )
            start_pos = fb_start
            best_primer = Primer(
                name=name,
                sequence=seq,
                primer_type=PrimerType.PRIMER,
                tm=self._calculate_tm(seq),
                gc_content=self._calculate_gc(seq),
                length=len(seq),
                target_start=start_pos,
                target_end=start_pos + len(seq),
                notes=self._fallback_notes(seq)
            )
        
        return best_primer
    
    def _design_reverse_primer(
        self,
        template: str,
        end_pos: int,
        name: str,
        anchor: bool = False
    ) -> Primer:
        """设计反向引物（anchor=True 时 3' 侧严格锚定 end_pos，见 A-07）"""
        best_primer = None
        best_score = -1

        for length in range(self.length_min, self.length_max + 1):
            if anchor:
                seq_end = end_pos  # 锚定：只枚举长度
            else:
                # 同正向：预扫描用局部变量，不抬高全局 best_score
                seq_end = None
                offset_best = -1
                for offset in range(0, 5):
                    cand_end = end_pos - offset
                    cand_start = cand_end - length
                    if cand_start < 0:
                        continue
                    cand_seq = template[cand_start:cand_end]
                    cand_rc = self._reverse_complement(cand_seq)
                    if self._check_primer_quality(cand_rc):
                        cand_score = self._score_primer(cand_rc)
                        if cand_score > offset_best:
                            offset_best = cand_score
                            seq_end = cand_end
                if seq_end is None:
                    continue

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
            # 边界钳制：负索引切片会得到错误片段与负的 target_start（R2）
            fb_end = min(end_pos, len(template))
            fb_start = max(0, fb_end - 20)
            seq = template[fb_start:fb_end]
            if not seq or (not anchor and len(seq) < self.length_min):
                raise ValueError(
                    f"模板过短：位置 {end_pos} 前仅有 {len(seq)}bp，"
                    f"不足最短引物长度 {self.length_min}bp"
                )
            seq_rc = self._reverse_complement(seq)
            best_primer = Primer(
                name=name,
                sequence=seq_rc,
                primer_type=PrimerType.PRIMER,
                tm=self._calculate_tm(seq_rc),
                gc_content=self._calculate_gc(seq_rc),
                length=len(seq_rc),
                target_start=fb_start,
                target_end=fb_end,
                notes=self._fallback_notes(seq_rc)
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

    def _quality_issues(self, seq: str) -> List[str]:
        """逐项列出不达标的引物质量指标（兜底引物用）。

        兜底路径不走候选筛选，但不该只留一句「可能不符合质量要求」——
        具体是哪项超标（Tm/GC/poly-X/3' 端 GC/自互补/二聚体）要能让
        用户直接判断这条引物能不能用。"""
        issues: List[str] = []
        gc = self._calculate_gc(seq)
        if gc < self.gc_min or gc > self.gc_max:
            issues.append(f"GC {gc:.1f}% 超出 {self.gc_min:g}-{self.gc_max:g}%")
        tm = self._calculate_tm(seq)
        if tm < self.tm_min or tm > self.tm_max:
            issues.append(f"Tm {tm:.1f}℃ 超出 {self.tm_min:g}-{self.tm_max:g}℃")
        for base in "ATGC":
            if base * self.max_poly_x in seq:
                issues.append(f"含 {base * self.max_poly_x} 连续碱基")
        if sum(1 for b in seq[-5:] if b in "GC") > 4:
            issues.append("3' 端 5bp 内 GC 过多")
        if self._max_self_complementarity(seq) > self.max_self_comp:
            issues.append("自身互补过强")
        tail = seq[-4:]
        if len(tail) == 4 and tail == self._reverse_complement(tail):
            issues.append("末 4bp 自互补（引物二聚体风险）")
        return issues

    def _fallback_notes(self, seq: str) -> str:
        """兜底引物的 notes：写明具体不达标项；全部通过则说明它是「合格但非最优」"""
        issues = self._quality_issues(seq)
        if not issues:
            return "兜底引物（候选筛选未命中，按模板直接截取；质量判据本身通过）"
        return "Warning: 兜底引物未通过质量筛选：" + "；".join(issues)

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
        """计算 Tm（最近邻法，见模块级 calculate_tm）。"""
        return calculate_tm(seq)
    
    def _calculate_gc(self, seq: str) -> float:
        """计算GC含量（百分比，收敛到 core.seq_utils）"""
        return gc_percent(seq)

    def _calculate_annealing_temp(self, tm1: float, tm2: float) -> float:
        """计算推荐退火温度"""
        # 通常比最低Tm低3-5度
        min_tm = min(tm1, tm2)
        return max(min_tm - 3, 50)  # 最低不低于50度

    def _reverse_complement(self, seq: str) -> str:
        """反向互补（收敛到 core.seq_utils.revcomp）"""
        return revcomp(seq)
    
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

    @staticmethod
    def _equal_step_tiles(seq_len: int, n_tiles: int, overlap: int) -> List[List[int]]:
        """等步长初始分片（均一化前的起点）：片数恒等于 n_tiles。

        此前按 ceil 步长逐片前进（step = ceil((len-overlap)/n)），会在第 n
        片之前就到达末尾——实测 2570bp/52 片只得 51 片，而「片数恒偶」是
        错位交替（无完全互补对）的前提。改为按比例分配边界：片数由参数
        决定，取整余量全部落在末片。"""
        if n_tiles <= 0 or seq_len <= 0:
            return []
        if n_tiles == 1:
            return [[0, seq_len]]
        span = max(1.0, (seq_len - overlap) / n_tiles)
        tiles: List[List[int]] = []
        for i in range(n_tiles):
            start = int(round(i * span))
            start = max(0, min(start, seq_len - 1))
            end = seq_len if i == n_tiles - 1 else int(round((i + 1) * span)) + overlap
            end = min(max(end, start + 1), seq_len)
            tiles.append([start, end])
        return tiles

    def _overlap_tm_spread(self, tiles: List[List[int]], sequence: str) -> float:
        """相邻 oligo 重叠双链的 Tm 极差（组装 PCR 退火同步性指标）"""
        tms = [
            self._calculate_tm(sequence[tiles[i + 1][0]:tiles[i][1]])
            for i in range(len(tiles) - 1)
        ]
        return max(tms) - min(tms) if tms else 0.0

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
        - 片数强制为偶数；单条长度不超过 oligo_length_max；
        - 边界与重叠长度按「重叠双链 Tm」均一化（见
          _tm_homogenize_boundaries），退火同步性由这个量决定。

        Args:
            sequence: 目标基因序列
            oligo_length_min: 单条寡核苷酸最短长度（尽力目标）
            oligo_length_max: 单条寡核苷酸最长长度（硬上限）
            overlap_length: 相邻寡核苷酸重叠区的标称长度（起始值）。落在
                [OVERLAP_LENGTH_MIN, OVERLAP_LENGTH_MAX] 内时，重叠长度会
                在该区间内按重叠双链 Tm 均一化自动调整；显式给出区间外的
                值（如 10 或 40）则固定为该长度不做调整
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
        n_min = max(2, math.ceil((seq_len - overlap_length) / max_span))
        if n_min % 2 == 1:
            n_min += 1

        # DNAWorks 式 Tm 均一化：在长度范围内微调内部边界与重叠长度，
        # 使各重叠双链的 Tm 尽量一致（退火同步性更好，合成成功率更高）
        if OVERLAP_LENGTH_MIN <= overlap_length <= OVERLAP_LENGTH_MAX:
            ov_lo, ov_hi = OVERLAP_LENGTH_MIN, OVERLAP_LENGTH_MAX
        else:
            # 调用方显式要求区间外的重叠长度时按其固定值处理
            ov_lo = ov_hi = overlap_length

        # 最少片数时每片都顶着长度上限，边界几乎无处可动——重叠 Tm 常
        # 压不下来。按 +2 递增片数（保持偶数）换取调整余量，一旦达标就停，
        # 既不无谓增加 oligo 条数，也不把 Tm 极差留在不可接受的水平
        best_tiles, best_spread = None, float("inf")
        for n_tiles in range(n_min, n_min + 2 * OLIGO_TILE_ESCALATIONS + 1, 2):
            tiles = self._equal_step_tiles(seq_len, n_tiles, overlap_length)
            tiles = self._tm_homogenize_boundaries(
                tiles, sequence, oligo_length_min, oligo_length_max,
                overlap_min=ov_lo, overlap_max=ov_hi,
            )
            spread = self._overlap_tm_spread(tiles, sequence)
            if spread < best_spread:
                best_tiles, best_spread = tiles, spread
            if spread <= OVERLAP_TM_SPREAD_TARGET:
                break
        tiles = best_tiles

        oligos = []
        oligo_num = 1
        for idx, (pos, end) in enumerate(tiles):
            left_ov = tiles[idx - 1][1] - pos if idx > 0 else 0
            right_ov = end - tiles[idx + 1][0] if idx + 1 < len(tiles) else 0
            sense = sequence[pos:end]

            is_sense = oligo_num % 2 == 1
            oligo_seq = sense if is_sense else self._reverse_complement(sense)
            # 反义 oligo 取的是反向互补链：它的 5' 端落在右侧重叠上、3' 端落在
            # 左侧——重叠标注要按 oligo 自身方向写，不能照抄正链的左右
            ov5, ov3 = (left_ov, right_ov) if is_sense else (right_ov, left_ov)

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
                     f"region {pos + 1}-{end}, "
                     f"overlap: 5'{ov5}bp/3'{ov3}bp",
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
        overlap_min: int = OVERLAP_LENGTH_MIN,
        overlap_max: int = OVERLAP_LENGTH_MAX,
    ) -> List[List[int]]:
        """DNAWorks 式边界均一化：让各「重叠双链」的最近邻 Tm 尽量一致。

        决定组装 PCR 退火的是相邻 oligo 之间那段重叠双链的 Tm，不是整条
        oligo 的 Tm——旧版优化的是后者，重叠长度被钉死，真正要紧的量
        根本动不了（实测 1.2kb 基因的重叠 Tm 极差中位数 22°C）。

        这里每个内部边界有两个自由变量：边界位置（±8nt）与重叠长度
        （[overlap_min, overlap_max]）。以「各重叠 Tm 与目标值的偏差之和」
        为目标做坐标下降，目标值在若干候选上各跑一遍取极差最小的解。
        片长始终保持在 [length_min, length_max] 内（初始分片就越界的片
        只要求不变得更差），首片起点与末片终点不动，重叠区互不重叠。
        """
        tiles = [list(t) for t in tiles]
        n = len(tiles)
        if n < 2 or overlap_max < overlap_min:
            return tiles

        tm_cache: Dict[Tuple[int, int], float] = {}

        def ov_tm(start: int, end: int) -> float:
            key = (start, end)
            if key not in tm_cache:
                tm_cache[key] = self._calculate_tm(sequence[start:end])
            return tm_cache[key]

        def tile_len_ok(length: int, original: int) -> bool:
            if length > length_max:
                return False
            if length < length_min:
                # 初始就短于下限（末片常见）时只要求不再更短
                return length >= min(length_min, original)
            return True

        orig_len = [t[1] - t[0] for t in tiles]
        shift_range = 8

        def candidates(j: int, ts: List[List[int]]) -> List[Tuple[int, int, float]]:
            """junction j（tiles[j] 与 tiles[j+1] 之间）的可行 (s, e, Tm)"""
            e0 = ts[j][1]
            out = []
            for e in range(e0 - shift_range, e0 + shift_range + 1):
                if e > ts[j + 1][1] or e <= ts[j][0]:
                    continue
                if not tile_len_ok(e - ts[j][0], orig_len[j]):
                    continue
                for ov in range(overlap_min, overlap_max + 1):
                    s_pos = e - ov
                    if s_pos <= ts[j][0] or s_pos >= ts[j + 1][1]:
                        continue
                    if not tile_len_ok(ts[j + 1][1] - s_pos, orig_len[j + 1]):
                        continue
                    # 重叠区之间不得互相重叠（否则一片会被两侧邻居盖满）
                    if j > 0 and s_pos < ts[j - 1][1]:
                        continue
                    if j + 2 < n and e > ts[j + 2][0]:
                        continue
                    out.append((s_pos, e, ov_tm(s_pos, e)))
            return out

        def spread(ts: List[List[int]]) -> float:
            tms = [ov_tm(ts[i + 1][0], ts[i][1]) for i in range(len(ts) - 1)]
            return max(tms) - min(tms) if tms else 0.0

        # 目标 Tm 候选：各 junction 可达 Tm 的分位点
        reachable = [[c[2] for c in candidates(j, tiles)] for j in range(n - 1)]
        pool = sorted(t for lst in reachable for t in lst)
        if not pool:
            return tiles
        targets = sorted({pool[int(q * (len(pool) - 1))]
                          for q in (0.2, 0.35, 0.5, 0.65, 0.8)})

        best_tiles, best_spread = tiles, spread(tiles)
        for target in targets:
            trial = [list(t) for t in tiles]
            for _ in range(rounds):
                moved = False
                for j in range(n - 1):
                    cand = candidates(j, trial)
                    if not cand:
                        continue
                    s_pos, e, _tm = min(cand, key=lambda c: (abs(c[2] - target), abs(c[1] - trial[j][1])))
                    if (s_pos, e) != (trial[j + 1][0], trial[j][1]):
                        trial[j][1] = e
                        trial[j + 1][0] = s_pos
                        moved = True
                if not moved:
                    break
            sp = spread(trial)
            if sp < best_spread - 1e-9:
                best_tiles, best_spread = trial, sp
        return best_tiles

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
                # 预期配对：两条 oligo 的目标区域重叠（相邻片的 overlap 退火）。
                # 坐标缺失（target_start/target_end 为 None，如克隆引物）时
                # 无法判定为预期配对，按交叉杂交正常计数
                ti, tj = oligos[i], oligos[j]
                ts_i, te_i = getattr(ti, "target_start", None), getattr(ti, "target_end", None)
                ts_j, te_j = getattr(tj, "target_start", None), getattr(tj, "target_end", None)
                if None not in (ts_i, te_i, ts_j, te_j) and ts_i < te_j and ts_j < te_i:
                    continue
                if rc_tail in t:
                    count += 1
        return count


