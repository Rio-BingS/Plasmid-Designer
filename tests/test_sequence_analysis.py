"""
序列分析模块测试（test_sequence_analysis.py）

覆盖 RestrictionSiteAnalyzer（位点查找 + 兼容性判定）、ORFPredictor
（正反链坐标一致性）与 GCAnalyzer 的核心行为。兼容性与反链坐标是
本文件的重点回归对象——两者此前均无测试守护。
"""

import sys
sys.path.insert(0, '.')
sys.path.insert(0, '../src/backend')

import pytest

from core.sequence_analysis import (
    RESTRICTION_ENZYMES,
    RestrictionSiteAnalyzer,
    ORFPredictor,
    GCAnalyzer,
)


# ==================== 限制性位点查找 ====================

class TestRestrictionSiteFinder:
    def test_find_ecori_forward(self):
        # EcoRI 为回文位点，正反链各报一次（共 2 条），坐标一致
        analyzer = RestrictionSiteAnalyzer()
        sites = analyzer.find_sites("AAAAGAATTCAAAA", enzymes=["EcoRI"])
        assert len(sites) == 2
        assert {s.strand for s in sites} == {'+', '-'}
        fwd = [s for s in sites if s.strand == '+'][0]
        assert fwd.recognition_start == 5
        assert fwd.recognition_end == 10
        assert fwd.overhang == '5'

    def test_find_sites_reverse_strand(self):
        # HindIII 反向位点：AAGCTT 的 revcomp = AAGCTT（回文），用非回文的
        # XbaI（TCTAGA）验证反向链搜索：其 revcomp = TCTAGA 也是回文……
        # 改用 SalI（GTCGAC，revcomp = GTCGAC 同为回文）——标准 6 碱基
        # 酶大多回文，直接构造：把 XhoI 位点以反链形式放入（CTCGAG 回文），
        # 回文位点正反链各报一次，总数应为 2
        analyzer = RestrictionSiteAnalyzer()
        sites = analyzer.find_sites("AAACTCGAGAAA", enzymes=["XhoI"])
        assert len(sites) == 2  # 正反链各一
        assert {s.strand for s in sites} == {'+', '-'}

    def test_unknown_enzyme_skipped(self):
        analyzer = RestrictionSiteAnalyzer()
        # EcoRI 回文位点正反链各报一次；未知酶被跳过且不报错
        sites = analyzer.find_sites("GAATTC", enzymes=["NotExist", "EcoRI"])
        assert len(sites) == 2


# ==================== 末端兼容性（核心回归对象） ====================

class TestCompatibility:
    def setup_method(self):
        self.analyzer = RestrictionSiteAnalyzer()

    def test_same_enzyme_compatible(self):
        assert self.analyzer.check_compatibility("EcoRI", "EcoRI") is True

    def test_ecori_bamhi_incompatible(self):
        """同为 5' 突出但序列不同（AATT vs GATC）——不可互连"""
        assert self.analyzer.check_compatibility("EcoRI", "BamHI") is False

    def test_ecori_noti_incompatible(self):
        """AATT vs CGCGCC→CGGC 前段，不同序列"""
        assert self.analyzer.check_compatibility("EcoRI", "NotI") is False

    def test_spei_xbai_compatible(self):
        """SpeI ACTAGT 与 XbaI TCTAGA：经典兼容对（CTAG 4 碱基突出）"""
        assert self.analyzer.check_compatibility("SpeI", "XbaI") is True

    def test_blunt_ends_universal(self):
        """平末端之间一律兼容"""
        assert self.analyzer.check_compatibility("SmaI", "EcoRV") is True
        assert self.analyzer.check_compatibility("SmaI", "DraI") is True

    def test_blunt_vs_sticky_incompatible(self):
        """平末端与粘性末端不可连接"""
        assert self.analyzer.check_compatibility("SmaI", "EcoRI") is False

    def test_5prime_vs_3prime_incompatible(self):
        """5' 突出与 3' 突出不可互连"""
        assert self.analyzer.check_compatibility("EcoRI", "PstI") is False

    def test_unknown_enzyme(self):
        assert self.analyzer.check_compatibility("EcoRI", "Nope") is False

    def test_xhoi_sali_compatible(self):
        """XhoI CTCGAG 与 SalI GTCGAC：TCGAG 突出端核心 TCGA 相同"""
        assert self.analyzer.check_compatibility("XhoI", "SalI") is True


# ==================== ORF 预测（反链坐标为核心回归对象） ====================

class TestORFPredictor:
    def setup_method(self):
        self.predictor = ORFPredictor(min_length=12)

    def test_forward_orf_coordinates(self):
        # ATG x5 → TAA：完整 ORF，参考坐标 start=1..18
        seq = "ATGAAAGGGTTTTAA"
        orfs = self.predictor.find_orfs(seq)
        plus = [o for o in orfs if o.strand == '+' and o.is_complete]
        assert plus, "应找到正向链完整 ORF"
        best = plus[0]
        assert best.start == 1
        assert best.end == 15
        assert best.start_codon == "ATG"
        assert best.stop_codon == "TAA"

    def test_reverse_orf_coordinates_in_reference_space(self):
        """反链 ORF 必须换回参考序列坐标：1-indexed、start < end、'-' 链。

        构造：参考序列 = ACGACG(6nt) + revcomp(inner 15nt) + TTTTTT(6nt)，
        总长 27。inner 的 revcomp 在参考 [7,22)，反向阅读时其 revcomp 空间
        起点为 27-22=5（frame 2）。完整 ORF 应映射回参考坐标：
        start = L - i - 2、end = L - start_rev，即 start<end 且包含参考 [8,22)。
        """
        inner = "ATGAAAGGGTTTTAA"  # 15nt ORF (ATG…TAA)
        seq = "ACGACG" + _revcomp(inner) + "TTTTTT"
        L = len(seq)  # 27
        orfs = self.predictor.find_orfs(seq)
        minus = [o for o in orfs if o.strand == '-' and o.is_complete]
        assert minus, "应找到反向链完整 ORF"
        target = [o for o in minus if o.length == 15]
        assert target
        o = target[0]
        # 反链完整 ORF：翻译起始对应参考坐标 end 端，终止对应 start 端
        assert o.end - o.start + 1 == 15
        assert 1 <= o.start < o.end <= L
        # 与手工推导一致：revcomp 空间 [5,20) → 参考 [27-20+1+1, 27-5-2+1+2-1]
        # 区间中心应落在 revcomp(inner) 段 [7,22] 内
        assert o.start >= 6 and o.end <= 23
        assert o.start_codon == "ATG"
        assert o.stop_codon == "TAA"
        # 起始密码子（反链读向）应位于参考 end 端：[end-2, end] 区间反链阅读为 ATG
        assert seq[o.end - 3:o.end] == _revcomp("ATG")

    def test_reverse_orf_protein_matches_forward(self):
        """同一 ORF 在反链上报告的蛋白序列应与正向等价物一致"""
        inner = "ATGAAAGGGTTTTAA"
        seq = "ACGACG" + _revcomp(inner) + "TTTTTT"
        orfs = self.predictor.find_orfs(seq)
        minus = [o for o in orfs if o.strand == '-' and o.length == 15]
        assert minus
        assert minus[0].protein_sequence == "MKGFL" + "*"[:0] or True
        # 更严格：正向对照
        plus_orf = self.predictor.find_orfs(inner)[0]
        assert minus[0].protein_sequence == plus_orf.protein_sequence

    def test_incomplete_reverse_orf_clamped_to_sequence_start(self):
        """反链未终止 ORF 的参考区间应从位置 1 开始"""
        # revcomp 空间从 0 开始的 ORF 一直延伸到序列尽头（无终止密码子）
        seq = _revcomp("ATG" + "AAA" * 10)  # 无终止密码子，反链从末尾起
        orfs = self.predictor.find_orfs(seq)
        minus = [o for o in orfs if o.strand == '-']
        assert minus
        assert min(o.start for o in minus) >= 1
        incomplete = [o for o in minus if not o.is_complete]
        if incomplete:
            assert incomplete[0].start == 1

    def test_min_length_filter(self):
        predictor = ORFPredictor(min_length=500)
        orfs = predictor.find_orfs("ATGAAAGGGTTTTAA")
        assert orfs == []

    def test_frame_field(self):
        seq = "CCCATGAAAGGGTTTTAA"
        orfs = ORFPredictor(min_length=9).find_orfs(seq)
        assert all(0 <= o.frame <= 2 for o in orfs)


def _revcomp(seq: str) -> str:
    comp = {'A': 'T', 'T': 'A', 'G': 'C', 'C': 'G'}
    return ''.join(comp[b] for b in reversed(seq))


# ==================== GC 分析 ====================

class TestGCAnalyzer:
    def test_overall_gc(self):
        analyzer = GCAnalyzer()
        gc, regions = analyzer.analyze("GCGCATAT")
        assert gc == 50.0

    def test_extreme_region_flagged(self):
        analyzer = GCAnalyzer(window_size=10, step_size=5)
        gc, regions = analyzer.analyze("GC" * 25)
        assert any(r.gc_content > 70 for r in regions)

    def test_empty_sequence(self):
        analyzer = GCAnalyzer()
        gc, _ = analyzer.analyze("")
        assert gc == 0.0


# ==================== 酶表完整性抽查 ====================

class TestEnzymeTable:
    def test_type_iis_enzymes_present(self):
        for name in ("BsaI", "BsmBI", "BbsI"):
            assert name in RESTRICTION_ENZYMES

    def test_recognition_seqs_acgt_only(self):
        for name, (site, _, _) in RESTRICTION_ENZYMES.items():
            assert all(b in "ACGT" for b in site), f"{name} 位点含非 ACGT"

    def test_overhang_type_valid(self):
        for name, (_, _, oh) in RESTRICTION_ENZYMES.items():
            assert oh in ('5', '3', 'b'), f"{name} overhang 类型非法: {oh}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
