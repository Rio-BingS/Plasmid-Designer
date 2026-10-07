"""core.seq_utils 共享序列工具的单元测试

守护收敛后的唯一实现：IUPAC 反向互补、GC 计算、标准遗传密码表与翻译。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "backend"))

from core.seq_utils import (  # noqa: E402
    CODON_TABLE,
    CODON_TO_AA,
    gc_fraction,
    gc_percent,
    revcomp,
    translate,
)


class TestRevcomp:
    def test_basic(self):
        assert revcomp("ACGT") == "ACGT"

    def test_palindrome_and_nonpalindrome(self):
        assert revcomp("GAATTC") == "GAATTC"          # EcoRI 回文
        assert revcomp("GGTCTC") == "GAGACC"          # BsaI 非回文

    def test_iupac_ambiguity_codes(self):
        assert revcomp("R") == "Y"
        assert revcomp("RYKMSWBDHVN") == "NBDHVWSKMRY"
        # R↔Y 互补 + 字符串反向互抵 → 原串不变
        assert revcomp("RY") == "RY"

    def test_lowercase_preserved(self):
        assert revcomp("acgt") == "acgt"

    def test_unknown_chars_pass_through(self):
        assert revcomp("ACG-T") == "A-CGT"
        assert revcomp("ACG N") == "N CGT"

    def test_empty(self):
        assert revcomp("") == ""


class TestGC:
    def test_fraction(self):
        assert gc_fraction("GGCC") == pytest.approx(1.0)
        assert gc_fraction("ATAT") == pytest.approx(0.0)
        assert gc_fraction("GATC") == pytest.approx(0.5)

    def test_percent(self):
        assert gc_percent("GCGC") == pytest.approx(100.0)
        assert gc_percent("AAAA") == pytest.approx(0.0)

    def test_lowercase_and_empty(self):
        assert gc_fraction("gcgc") == pytest.approx(1.0)
        assert gc_percent("") == 0.0

    def test_u_not_counted(self):
        # U 不算 GC（与历史各副本口径一致）
        assert gc_fraction("UAGC") == pytest.approx(0.5)


class TestCodonTable:
    def test_64_codons_covered_once(self):
        all_codons = [c for codons in CODON_TABLE.values() for c in codons]
        assert len(all_codons) == 64
        assert len(set(all_codons)) == 64

    def test_reverse_map_consistent(self):
        for aa, codons in CODON_TABLE.items():
            for c in codons:
                assert CODON_TO_AA[c] == aa
        assert len(CODON_TO_AA) == 64

    def test_stop_codons(self):
        assert sorted(CODON_TABLE["*"]) == ["TAA", "TAG", "TGA"]


class TestTranslate:
    def test_orf_semantics(self):
        # ATG AAA TAA GGG → 翻译在 TAA 截断
        assert translate("ATGAAATAAGGG") == "MK"

    def test_full_semantics_keeps_internal_stop(self):
        assert translate("ATGAAATAAGGG", stop_at_stop=False) == "MK*G"
        # 供序列验证器检出内部终止密码子
        assert translate("ATGTAA", stop_at_stop=False) == "M*"

    def test_unknown_codon_is_x(self):
        assert translate("ATGNNNGGG") == "MXG"

    def test_trailing_partial_codon_ignored(self):
        assert translate("ATGAA") == "M"

    def test_u_treated_as_t(self):
        assert translate("AUG") == "M"

    def test_lowercase(self):
        assert translate("atgaaataa") == "MK"
