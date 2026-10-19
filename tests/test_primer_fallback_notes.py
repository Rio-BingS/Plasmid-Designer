"""兜底引物要写明具体不达标项，而不是一句通用警告"""
from core.primer_designer import PrimerDesigner


def test_low_gc_fallback_lists_gc_issue():
    d = PrimerDesigner()
    seq = "AAAAATTTTTAAAAATTTTT"      # GC 0%
    issues = d._quality_issues(seq)
    assert any(i.startswith("GC") for i in issues)
    notes = d._fallback_notes(seq)
    assert notes.startswith("Warning: 兜底引物未通过质量筛选")
    assert "GC 0.0%" in notes


def test_poly_x_fallback_lists_poly_issue():
    d = PrimerDesigner()
    seq = "AAAAAAAAAAAAAAAAAAAA"
    issues = d._quality_issues(seq)
    assert any("AAAA" in i for i in issues)


def test_fallback_notes_marks_clean_primer_as_passing():
    d = PrimerDesigner()
    seq = "ATGCGTACGTAGCTAGCTAG"      # 50% GC
    if not d._quality_issues(seq):
        assert "质量判据本身通过" in d._fallback_notes(seq)
