"""S7 第 2 片：低置信原因结构化（分析阶段）+ 文案（呈现层）的往返与措辞"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "backend"))

from core.sanger.evidence import low_confidence_cause, _low_confidence_reason  # noqa: E402
from core.sanger import render_site  # noqa: E402
from core.sanger.reliable_zone import zone_cause, zone_reason  # noqa: E402


def _cases():
    base = {"read_q": 18, "read_pos": 200, "support_reads": 1,
            "type": "substitution", "alt_base": "A"}
    yield dict(base, cross_read_conflict={"reads": ["r1", "r2", "r3"]}), set(), None
    yield dict(base, merged_zone_artifact=True), set(), None
    yield dict(base, slippage_artifact=True), set(), None
    yield dict(base, alt_base="N"), set(), None
    yield dict(base), {200}, None
    yield dict(base, type="deletion",
               homopolymer={"peak_amplitude_ratio": 0.31}), set(), None
    for r in (0.1, 0.45, 0.8):
        yield dict(base, type="insertion"), set(), {"insertion_peak_ratio": r}
    for ev in ({"mutant_pct": 20}, {"mutant_pct": 50}, {"mutant_pct": 90, "snr": 3.2}):
        yield dict(base), set(), ev
    yield dict(base), set(), None
    yield dict(base, support_reads=2), set(), None


def test_cause_is_structured_and_text_matches():
    codes = []
    for v, mixed, ev in _cases():
        c = low_confidence_cause(v, mixed, ev)
        assert isinstance(c, dict) and "code" in c
        assert render_site.cause_text(c) == _low_confidence_reason(v, mixed, ev)
        codes.append(c["code"])
    assert codes == ["cross_conflict", "merged_zone", "slippage", "n_call", "mixed_bg",
                     "poly_compressed", "ins_none", "ins_weak", "ins_visible",
                     "mutant_weak", "mutant_equal", "low_snr", "low_q", "low_q"]
    c = low_confidence_cause({"cross_read_conflict": {"reads": ["a", "b", "c"]}}, set())
    assert render_site.cause_text(c) == "互检矛盾：a、b 在同一位点读出设计碱基"


def test_zone_cause_kinds_and_wording():
    zone = {"start": 50, "end": 600, "raw_start": 50, "raw_end": 600,
            "front_reason": "首端信号未稳", "tail_reason": "尾端信号衰减",
            "params": {"front_hard": 30, "front_gray": 60,
                       "tail_gray": 700, "tail_hard": 900}}
    kinds = {zone_cause(zone, rp)["zone_kind"] for rp in (10, 40)}
    assert kinds <= {"front_hard", "front", "tail_ext"}
    for rp in (10, 40, 650):
        assert zone_reason(zone, rp) == render_site.zone_text_of(zone_cause(zone, rp))


def test_low_reason_text_suffixes():
    v = {"low_cause": {"code": "low_q", "q": 12, "single": True},
         "dye_blob": {"channel": "G", "start": 80, "end": 84},
         "in_mixed_template": {"read": "r1", "breakpoint": 300},
         "cross_read_weak": [{"read": "r2", "region_cols": 21, "region_mean_q": 18.5,
                              "region_hq_frac": 0.4}]}
    assert render_site.low_reason_text(v) == (
        "Q 值偏低，Q12，仅单条 read 支持；位于染料团区（G 通道宽峰，read 第 80–84 位）"
        "；r1 在此处套峰（第 300 位起整体套峰），主峰差异不能作确证"
        "；r2 读出设计碱基，但该区 21bp 平均 Q18.5、≥Q20 占 40%，质量不足以互检")
