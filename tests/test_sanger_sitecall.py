"""S4 SiteCall 唯一判定表：一期 strangler 双跑契约

- 每个 analyze 场景：SiteCall 反写旧字段（status/confidence/action、
  mixed 的 noise_evidence）与旧路径 0 差异；
- 规则表单元：只有 ART_ 反证能导出 ARTIFACT；硬阻断不能 CONFIRMED；
  mixed 有反证 → ARTIFACT，否则 MIXED 并带 af。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/backend"))
sys.path.insert(0, str(ROOT))

from core.sanger import sitecall as sc  # noqa: E402
from tests.sanger_scenarios import SCENARIOS  # noqa: E402

NOT_VIA_ANALYZE = {"M5", "M8", "M11"}


@pytest.mark.parametrize("spec", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_dual_run_equivalent(spec):
    if spec.id in NOT_VIA_ANALYZE:
        pytest.skip("不经 analyze")
    from tests.test_sanger_scenarios import _run
    _spec, res = _run(spec.id)
    diffs = sc.dual_run_diffs(res.get("variants"), res.get("mixed_variants"))
    assert diffs == [], f"{spec.id}: {diffs[:3]}"


def _v(**kw):
    base = {"ref_pos": 100, "type": "substitution", "alt_base": "A",
            "support_reads": 1, "reason_codes": []}
    base.update(kw)
    return base


def test_artifact_needs_counter_evidence():
    low = sc.call_variant(_v(p_real=0.001))
    assert low["state"] == sc.UNRESOLVED
    art = sc.call_variant(_v(p_real=0.001, cross_read_resolved=True,
                             reason_codes=["cross_check_clean"]))
    assert art["state"] == sc.ARTIFACT and art["primary_reason"] == "ART_CROSS_READ_REF"
    assert sc.to_legacy(art)["action"] == "exclude"


def test_confirmed_and_hard_block():
    ok = sc.call_variant(_v(p_real=0.995, support_reads=2))
    assert ok["state"] == sc.CONFIRMED and ok["primary_reason"] == "SUP_MULTI_READ"
    assert sc.to_legacy(ok) == {"status": "confirmed", "confidence": "high",
                                "action": "accept"}
    blk = sc.call_variant(_v(p_real=0.995, reason_codes=["overlap_discordance"]))
    assert blk["state"] == sc.UNRESOLVED
    assert blk["primary_reason"] == "UNR_OVERLAP_DISCORDANCE"
    # 旧路径分歧降级保留评分字段：confidence 仍按 p_real 分档
    assert sc.to_legacy(blk)["confidence"] == "high"


def test_unresolved_action_by_peak_evidence():
    a = sc.call_variant(_v(p_real=0.3, peak_evidence={"mutant_pct": 50}))
    b = sc.call_variant(_v(p_real=0.3))
    assert a["action"] == "review_trace" and b["action"] == "resequence"


def test_mixed_states():
    m = sc.call_mixed({"ref_pos": 5, "called_base": "A", "secondary_base": "G",
                       "af": 0.31, "reads": ["r1"]})
    assert m["state"] == sc.MIXED and m["af"] == 0.31
    n = sc.call_mixed({"ref_pos": 5, "called_base": "A", "secondary_base": "G",
                       "af": 0.2, "reads": ["r1"],
                       "noise_evidence": ["batch_background"]})
    assert n["state"] == sc.ARTIFACT and n["primary_reason"] == "ART_BATCH_CONTROL"


def test_review_overlay_keeps_machine_state():
    r = sc.call_variant(_v(p_real=0.3, review={"state": sc.CONFIRMED}))
    assert r["state"] == sc.CONFIRMED and r["machine_state"] == sc.UNRESOLVED
