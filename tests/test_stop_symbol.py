"""以 "*" 结尾的蛋白是正常输入（CODON_TABLE 里有终止密码子）

此前 valid_aa 显式排除 "*"，带终止符的蛋白直接报「无效的氨基酸代码」。
"""
from core.codon_optimizer import CodonOptimizer, translate_dna


def test_stop_symbol_is_accepted():
    res = CodonOptimizer(species="ecoli").optimize("MK*")
    assert translate_dna(res.dna_sequence) == "MK*"


def test_stop_symbol_in_middle_is_kept():
    res = CodonOptimizer(species="ecoli").optimize("M*K")
    assert translate_dna(res.dna_sequence) == "M*K"
