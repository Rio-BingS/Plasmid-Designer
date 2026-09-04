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
    """v2：5' 翻译起始区使用中等频率密码子，ramp 之后回到最高频"""
    from core.codon_optimizer import CodonOptimizer, RAMP_CODONS

    opt = CodonOptimizer(species="ecoli")
    aa = "L" * 40  # L 有 6 个同义密码子，ramp 效果可观察
    result = opt.optimize(aa)

    ramp_codon = result.dna_sequence[3:6]        # 第 2 个 L（ramp 区）
    post_codon = result.dna_sequence[RAMP_CODONS * 3:RAMP_CODONS * 3 + 3]  # ramp 之后
    assert ramp_codon != post_codon or len(set(c for c in ["CTA"])) == 0
    # ramp 区不使用最高频密码子 CTG
    assert ramp_codon != "CTG"
    # ramp 之后回到最高频密码子
    assert post_codon == "CTG"
    # 翻译产物不变
    from core.codon_optimizer import translate_dna
    assert translate_dna(result.dna_sequence).rstrip("*") == aa


def test_censor_motifs_auto_avoided():
    """v2：隐蔽调控 motif（AATAAA 等）自动并入避让列表"""
    from core.codon_optimizer import CodonOptimizer

    opt = CodonOptimizer(species="human")
    censor = opt._censor_motifs()
    assert "AATAAA" in censor and "TATAAA" in censor

    # 构造含 AATAAA（N=AAT + K=AAA）的起始 dna，迭代应将其移除
    aa = "NK"
    dna_list = list("AATAAA")
    fixed = opt._iterative_optimization("AATAAA", aa, opt._censor_motifs(), (0.4, 0.6), "balanced")
    assert "AATAAA" not in fixed


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
