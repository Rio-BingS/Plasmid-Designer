"""
密码子优化模块测试
"""

import pytest
import sys
sys.path.insert(0, '/root/.openclaw/workspace/plasmid-designer-v2/src/backend')

from core.codon_optimizer import CodonOptimizer, CodonOptimizationResult


def test_basic_optimization():
    """测试基本优化功能"""
    optimizer = CodonOptimizer(species="ecoli")
    
    # 测试简单氨基酸序列
    aa_seq = "MKVLWAALLTFLGCAATSGSQAPDRRNRLALASLLRLQGVSSVQIRCRDSDMNADADATIRR"  # 简化测试序列
    
    result = optimizer.optimize(aa_seq)
    
    # 检查结果类型
    assert isinstance(result, CodonOptimizationResult)
    
    # 检查DNA序列长度是氨基酸序列的3倍
    assert len(result.dna_sequence) == len(aa_seq) * 3
    
    # 检查GC含量在合理范围
    assert 0.2 <= result.gc_content <= 0.9  # gc_content 返回比例(0-1)
    
    # 检查CAI值
    assert 0 <= result.cai <= 1


def test_short_sequence():
    """测试短序列优化"""
    optimizer = CodonOptimizer(species="ecoli")
    
    aa_seq = "MGSSHHHHHH"  # 常见的His-tag序列
    
    result = optimizer.optimize(aa_seq)
    
    assert len(result.dna_sequence) == 30
    assert 'ATG' in result.dna_sequence  # 应该以ATG开始（Met）


def test_avoid_motifs():
    """测试避免特定motif"""
    optimizer = CodonOptimizer(species="ecoli")
    
    aa_seq = "MAAAAAAA"  # 多个Ala
    avoid = ["GCTGCT"]  # 需要避免的序列
    
    result = optimizer.optimize(aa_seq, avoid_motifs=avoid)
    
    # 检查结果中是否避免了该motif（如果可能的话）
    # 注意：某些情况下可能无法完全避免
    if not result.warnings:
        assert "GCTGCT" not in result.dna_sequence.upper()


def test_gc_content():
    """测试GC含量计算"""
    optimizer = CodonOptimizer(species="ecoli")
    
    # 测试不同GC含量的序列
    aa_seq = "GCGCGCGC"  # 高GC氨基酸序列
    result = optimizer.optimize(aa_seq)
    
    # Ala的密码子选择会影响GC
    # 应该在合理范围内
    assert 0.2 <= result.gc_content <= 0.9  # gc_content 返回比例(0-1)


def test_poly_x_handling():
    """测试连续相同碱基处理"""
    optimizer = CodonOptimizer(species="ecoli")
    
    # 设计一个可能产生poly-X的序列
    aa_seq = "KKKKKKK"  # Lys有AAA和AAG两个密码子
    
    result = optimizer.optimize(aa_seq)
    
    # 检查是否有超过4个连续相同碱基
    for base in 'ATGC':
        assert base * 5 not in result.dna_sequence


def test_cai_calculation():
    """测试CAI计算"""
    optimizer = CodonOptimizer(species="ecoli")
    
    # 使用纯高频密码子的序列应该有较高CAI
    aa_seq = "MMM"  # Met只有一个密码子
    
    result = optimizer.optimize(aa_seq)
    
    # Met的CAI应该是1.0（只有一个密码子）
    assert result.cai == 1.0 or result.cai > 0.9


def test_translate_function():
    """测试翻译功能"""
    from core.codon_optimizer import translate_dna
    
    # 测试简单DNA序列
    dna = "ATGGCTTAA"  # Met-Ala-Stop
    
    aa = translate_dna(dna)
    
    assert aa == "MA*"


def test_result_dataclass():
    """测试结果数据结构"""
    result = CodonOptimizationResult(
        dna_sequence="ATGAAATAG",
        amino_acid_sequence="MK*",
        cai=0.85,
        gc_content=44.4,
        gc_distribution=[44.0, 45.0],
        warnings=[],
        avoided_motifs=["AAA"]
    )
    
    assert result.dna_sequence == "ATGAAATAG"
    assert result.cai == 0.85
    assert len(result.gc_distribution) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_five_prime_ramp_uses_medium_codons():
    """v2：5' 翻译起始区使用中等频率密码子，ramp 之后回到最高频。

    两层验证：①纯 ramp 语义看 _initial_optimization（不受后续 GC 平滑
    干扰）；②端到端产物中 ramp 区 CTG 占比显著低于 ramp 后（GC 平滑
    在越界时合法覆盖 ramp 选择，但不应整体抹平 ramp 设计）。"""
    from core.codon_optimizer import CodonOptimizer, RAMP_CODONS

    opt = CodonOptimizer(species="ecoli")
    aa = "MKVL" * 30  # L 在 aa 下标 3,7,11,…（i % 4 == 3）

    # ① 纯 ramp 选择：ramp 区 L 一律不取最高频 CTG
    init = opt._initial_optimization(aa, use_ramp=True)
    init_ramp_ls = [init[i * 3:(i + 1) * 3] for i in range(3, RAMP_CODONS, 4)]
    assert all(c != "CTG" for c in init_ramp_ls), f"ramp 选择不应取最高频 CTG: {init_ramp_ls}"
    # ramp 之后的 L 回到最高频 CTG
    init_post_ls = [init[i * 3:(i + 1) * 3] for i in range(RAMP_CODONS + 3, len(aa), 4)]
    assert init_post_ls and all(c == "CTG" for c in init_post_ls), init_post_ls[:5]

    # ② 端到端：GC 平滑允许个别替换，但 ramp 区 CTG 占比必须低于 ramp 后
    result = opt.optimize(aa)
    seq = result.dna_sequence
    ramp_ls = [seq[i * 3:(i + 1) * 3] for i in range(3, RAMP_CODONS, 4)]
    post_ls = [seq[i * 3:(i + 1) * 3] for i in range(RAMP_CODONS + 3, len(aa), 4)]
    ramp_ctg = sum(1 for c in ramp_ls if c == "CTG") / len(ramp_ls)
    post_ctg = sum(1 for c in post_ls if c == "CTG") / len(post_ls)
    assert ramp_ctg < post_ctg, (
        f"ramp 区 CTG 占比 {ramp_ctg:.0%} 应低于 ramp 后 {post_ctg:.0%}"
    )
    # 翻译产物不变
    from core.codon_optimizer import translate_dna
    assert translate_dna(seq).rstrip("*") == aa


def test_censor_motifs_auto_avoided():
    """v2：隐蔽调控 motif（AATAAA 等）自动并入避让列表"""
    from core.codon_optimizer import CodonOptimizer

    opt = CodonOptimizer(species="human")
    censor = opt._censor_motifs()
    assert "AATAAA" in censor and "TATAAA" in censor

    # 构造含 AATAAA（N=AAT + K=AAA）的起始 dna，迭代应将其移除
    aa = "NK"
    fixed, unsatisfied = opt._iterative_optimization("AATAAA", aa, opt._censor_motifs(), (0.4, 0.6), "balanced")
    assert "AATAAA" not in fixed
    # motif 已移除 → 不应有「收敛后仍存在 motif」的未满足约束
    assert not any("仍存在需要避免的 motif" in w for w in unsatisfied)


def test_iterative_loop_runs_until_no_progress(monkeypatch):
    """终审 A-01 回归锁：循环曾因「原地改 list + 对象不等式恒假」第一轮
    即退出——即使约束尚未满足。桩掉 _smooth_gc 模拟「每轮内容有变化但
    约束永不满足」（旧失明形态：原地改 + 返回同一对象），循环必须持续
    迭代到轮数上限，而不是一轮收工。"""
    from core.codon_optimizer import CodonOptimizer

    opt = CodonOptimizer(species="ecoli")
    aa = "MKLV"
    calls = {"smooth": 0}

    def fake_smooth(self, dna_list, aa_seq, gc_target, avoid_motifs=()):
        calls["smooth"] += 1
        # 原地改 + 返回同一对象：旧判据（对象不等式）对此恒假
        dna_list[-1] = "C" if dna_list[-1] == "A" else "A"
        return dna_list

    monkeypatch.setattr(CodonOptimizer, "_smooth_gc", fake_smooth)

    # GC 14%（0.30-0.70 区间外）且无 poly-X/发夹 → 每轮只有 GC 步可走，
    # 桩每次只翻转末位碱基，GC 永不进区间 → balanced 档应跑满 50 轮
    opt._iterative_optimization(
        "ATGAAATTAGTAAA", aa, [], (0.30, 0.70), "balanced"
    )
    assert calls["smooth"] >= 10, (
        f"迭代循环在第一轮附近就退出（smooth 仅调用 {calls['smooth']} 次）"
    )


def test_iterative_reports_unsatisfied_constraints():
    """终审 A-01 收敛检查：迭代收敛后仍未满足的约束必须结构化写出"""
    from core.codon_optimizer import CodonOptimizer

    opt = CodonOptimizer(species="ecoli")
    # W-G 相邻必然产生不可消除的 GGGG（W=UGG、G=GGG 拼接），
    # poly-X 约束在收敛后必须以告警形式透出而不是静默放弃
    aa = "WGWGWGWG"
    result = opt.optimize(aa)
    assert any("poly-X" in w or "poly" in w.lower() for w in result.warnings), result.warnings


def test_break_poly_x_respects_avoid_motifs():
    """终审 A-02 回归锁：打断同聚物不得重新引入需排除的酶切位点
    （实测曾把 HindIII AAGCTT 重新引入）"""
    from core.codon_optimizer import CodonOptimizer

    opt = CodonOptimizer(species="ecoli")
    # 富 Lys 蛋白：AAA run 打断路径高频触发
    aa = "MKKKKKKKKKKKKSSSKKKKKKKKKK"
    result = opt.optimize(aa, avoid_motifs=["AAGCTT"])
    assert "AAGCTT" not in result.dna_sequence
    # 同义性守恒
    from core.codon_optimizer import translate_dna
    assert translate_dna(result.dna_sequence).rstrip("*") == aa


def test_reverse_strand_iis_sites_avoided():
    """终审 A-03 回归锁：非回文 Type IIS 位点此前只在正链避让——
    BsaI 反链形式 GAGACC 曾在产物中残留（100 条随机蛋白 3 条中招）。
    用户给定正链记法位点后，反向互补形式同样必须被消除。"""
    from core.codon_optimizer import CodonOptimizer, translate_dna
    from core.seq_utils import revcomp

    opt = CodonOptimizer(species="ecoli")
    # 多样化蛋白（含 Lys/Ser/Leu 高频族，变异空间大）
    aa = "MKSSLLKKSSLLKKSSLLKKSSLLKKSSLLGGSSRRKKSSLL"
    for site in ("GGTCTC",   # BsaI 正链记法 → 反链 GAGACC
                 "CGTCTC",   # BsmBI → 反链 GAGACG
                 "GAAGAC"):  # BbsI → 反链 GTCTTC
        result = opt.optimize(aa, avoid_motifs=[site])
        assert site not in result.dna_sequence, site
        assert revcomp(site) not in result.dna_sequence, (
            f"{site} 的反链形式 {revcomp(site)} 残留"
        )
        assert translate_dna(result.dna_sequence).rstrip("*") == aa


def test_unknown_species_raises_instead_of_silent_fallback():
    """终审 A-05 回归锁：未知物种曾静默回退大肠杆菌表（pichia/insect/
    bacillus/martian 全部静默 COMPLETED 无告警）——现在必须显式报错；
    合法物种带 codon_table_used 透明字段。"""
    import pytest as _pytest
    from core.codon_optimizer import CodonOptimizer

    for bad in ("pichia", "insect", "bacillus", "martian"):
        with _pytest.raises(ValueError, match="未知目标物种"):
            CodonOptimizer(species=bad)

    # 合法物种解析出实际表名（透明度）
    for sp, expect in (("ecoli", "Ecoli_K12"), ("human", "Human"), ("cho", "CHO"), ("yeast", "Yeast")):
        opt = CodonOptimizer(species=sp)
        assert opt.codon_table_used == expect, (sp, opt.codon_table_used)
        r = opt.optimize("MKV")
        assert r.codon_table_used == expect

    # 别名同样命中（E.coli / s_cerevisiae）
    assert CodonOptimizer(species="E.coli").codon_table_used == "Ecoli_K12"
    assert CodonOptimizer(species="s_cerevisiae").codon_table_used == "Yeast"


def test_result_has_score_and_hairpin_reduction():
    """v2：结果带综合评分；5' 发夹计数不应高于基线（正确 rc 下基线常为 0，旧版断言依赖错误 maketrans 的假阳性）"""
    from core.codon_optimizer import CodonOptimizer

    opt = CodonOptimizer(species="ecoli")
    aa = "MAAAAAAAAAGGGGGGGGSSSSSSSSS"

    baseline = opt._initial_optimization(aa, use_ramp=True)
    base_hair = opt._five_prime_hairpin_count(baseline)

    result = opt.optimize(aa)
    assert 0 <= result.score <= 100

    hair = opt._five_prime_hairpin_count(result.dna_sequence)
    assert hair <= base_hair, f"优化不应增加 5' 发夹计数（基线 {base_hair}，实际 {hair}）"


def test_gc_smoothing_efficient():
    """v2：GC 平滑后全局 GC 进入目标范围且 CAI 损失可控"""
    from core.codon_optimizer import CodonOptimizer

    opt = CodonOptimizer(species="ecoli")
    aa = "A" * 30 + "D" * 10  # 富 AT/富 GC 混合
    result = opt.optimize(aa)
    assert 0.38 <= result.gc_content <= 0.62, f"GC {result.gc_content:.2f} 应进入目标范围附近"
    assert result.cai >= 0.5


def test_five_prime_hairpin_count_detects_gc_stems():
    """回归：反向互补曾用错误的 maketrans("ATGC","TAGC")（G/C 映射到自身），
    导致 GC 茎区发夹漏检。正确实现应检出含 G/C 的茎。"""
    opt = CodonOptimizer(species="ecoli")
    # 5' 窗口内构造茎区含 G/C 的发夹：GGAC ... GTCC（GTCC 为 GGAC 的反向互补）
    with_hairpin = "GGAC" + "A" * 8 + "GTCC" + "A" * 40
    assert opt._five_prime_hairpin_count(with_hairpin) >= 1


def test_ramp_does_not_prefer_rare_codons_for_two_codon_families():
    """回归：ramp 中等频率曾用「排序取中位」，对双密码子家族（12 种氨基酸）
    固定选到低频密码子——与 ramp「避开稀有密码子」目标相反。

    Lys 家族 E. coli 频率 AAA=0.74 / AAG=0.26：频率中位 0.5 下
    ramp 区应选更接近中位的 AAA（0.26 的高频侧），而非低频 AAG；
    ramp 结束后回到最高频 AAA。
    """
    from core.codon_optimizer import CodonOptimizer, RAMP_CODONS

    opt = CodonOptimizer(species="ecoli")
    dna = opt._initial_optimization("K" * (RAMP_CODONS + 5), use_ramp=True)
    ramp_codon = dna[3:6]  # 第 2 个 Lys（ramp 区）
    assert ramp_codon == "AAA", (
        f"ramp 区对双密码子家族应选频率中位附近的高频侧 AAA，实际 {ramp_codon}"
    )
    post_codon = dna[RAMP_CODONS * 3:RAMP_CODONS * 3 + 3]
    assert post_codon == "AAA"  # ramp 后回到最高频
