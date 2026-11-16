"""S7 第 4 片：CDS 判读文案（render_cds 只读 cds_facts）与 poly dropout 汇总"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "backend"))

from core.sanger import render_cds, render_poly  # noqa: E402
from core.sanger.homopolymer import collect_post_poly_dropouts  # noqa: E402


def _facts(**kw):
    f = {"name": "GFP", "cov_pct": 100.0, "frame": None, "protein_identical": True,
         "n_in_cds": 0, "n_positions": [], "aa_diffs": [], "ref_len": 239, "alt_len": 239,
         "frameshifts": [], "first_diff": 239, "stop_idx": -1, "start_lost": None,
         "stop_lost": False, "inframe_ins": 0, "inframe_del": 0, "inframe_bp": 0,
         "synonymous": 0, "confirmed_subs": [], "pending_impact": None,
         "pending_plain": [], "pending_conflicted": []}
    f.update(kw)
    return f


def test_identical_full_and_frame_note():
    t = render_cds.verdict_text(_facts(synonymous=2, frame={"kind": "len_not_mult3", "length": 718}))
    assert t.startswith("CDS 已完整覆盖。GFP 蛋白与设计一致（239 aa）；另有 2 处同义突变")
    assert t.endswith("（注意：该特征长度 718 bp 不是 3 的倍数，翻译按参考阅读框截断）")


def test_frameshift_with_stop_subs_and_pending():
    f = _facts(protein_identical=False, cov_pct=80.0,
               frameshifts=[{"ref_pos": 120, "type": "deletion", "length": 1}],
               first_diff=30, stop_idx=40, alt_len=40,
               confirmed_subs=[{"ref_pos": 200, "ref_base": "A", "alt_base": "G"}],
               pending_impact="no_effect",
               pending_plain=[{"ref_pos": 300, "type": "substitution", "ref_base": "C",
                               "alt_base": "T", "low_reason": "Q 值偏低"}],
               pending_conflicted=[{"ref_pos": 310, "type": "insertion", "reads": ["r2.ab1"]}])
    t = render_cds.verdict_text(f)
    assert t.startswith("CDS 覆盖 80.0%（未覆盖部分按参考填充、未验证）。GFP 蛋白与设计不一致：")
    assert "120 处的缺失使阅读框自第 31 位起移码，翻译在第 41 位提前终止" in t
    assert "另有 1 处确证的碱基替换（200 A>G）" in t
    assert "另有 1 处低置信差异，不影响以上判定：300 替换 C>T（Q 值偏低）" in t
    assert "310 处的插入（r2.ab1 跨过同一位点未见差异）" in t


def test_pending_n_and_conclusion_lines():
    t = render_cds.verdict_text(_facts(protein_identical=None, n_in_cds=2, n_positions=[5, 6]))
    assert "GFP 蛋白判定待定：CDS 内 2 处 N/模糊调用（参考位置 5–6）" in t
    reps = [{"name": "A", "coverage_status": "uncovered", "verdict": render_cds.uncovered_text()},
            {"name": "B", "coverage_status": "full", "verdict": "x"}]
    assert render_cds.conclusion_lines(reps) == ["【B CDS】x"]


def test_dropout_collect_dedup_and_lines():
    rr = [{"filename": "a", "post_poly_dropouts": [
              {"base": "A", "ref_start": 10, "ref_end": 20, "ratio": 0.31, "recovered": False},
              {"base": "T", "ref_start": 50, "ref_end": 60, "ratio": 0.2, "end_truncation": True}]},
          {"filename": "b", "post_poly_dropouts": [
              {"base": "A", "ref_start": 10, "ref_end": 20, "ratio": 0.5, "recovered": True}]}]
    ds = collect_post_poly_dropouts(rr)
    assert len(ds) == 1 and ds[0]["read"] == "a"
    assert render_poly.dropout_lines(ds) == [
        "  ↳ poly(A) 同聚物 10-20 下游检测到信号骤降（降至约 31%且未见恢复）："
        "该段碱基判读不可信（区别于未覆盖），建议人工核对峰图或换引物复测"]
