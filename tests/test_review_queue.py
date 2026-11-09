# -*- coding: utf-8 -*-
"""goal v3 W14（PL §F）：复核队列与一键确认

- build_queue：只收确证差异 / 范围内缺口 / 未决位点；beyond_primer 不入队；
  按 fail > read_shortfall > pending 排序；已确认位点剔除
- build_verdict(review_decisions=...)：伪影移出 blocking（info 留痕），
  突变升级确证（fail_impact）；headline 追加「人工确认 N 处」；
  reviewed_count 下发；不带 decisions 时行为与旧口径完全一致
- apply_review_decision：留痕 by/at
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "backend"))

from core.sanger.review import build_queue, apply_review_decision  # noqa: E402
from core.sanger.verdict import build_verdict  # noqa: E402
from tests.test_sanger_scenarios import _run  # noqa: E402  SC8 真实场景


def _res(blocking, gaps=None, expected=None):
    """最小 analyze 结果形状：verdict + coverage（实测/验证全覆盖，避免
    无覆盖分支判 insufficient 干扰断言焦点）"""
    return {
        "verdict": {"blocking": blocking, "info": []},
        "coverage": {"gaps": gaps or [],
                     "expected_ranges": expected or [],
                     "verified_ranges": [[1, 2000]]},
        "variants": [], "unresolved_sites": [], "cds_reports": [],
        "errors": [], "reads": [], "mixed_profiles": {},
        "features": [], "consensus": {"covered_ranges": [[1, 2000]]},
    }


def _blk(kind, start=None, end=None, fail=False, **extra):
    b = {"kind": kind, "ref_start": start, "ref_end": end,
         "element": None, "detail": "d", "action": "a", "refs": []}
    if fail:
        b["fail_impact"] = True
    b.update(extra)
    return b


# ==================== build_queue ====================

def test_queue_keeps_three_categories_sorted():
    """三类入队且按 fail > read_shortfall > pending 排序"""
    res = _res([
        _blk("unresolved_site", 500),                     # pending
        _blk("scope_read_shortfall", 900, 1000),          # 缺口
        _blk("critical_element_variant", 300, fail=True),  # 确证差异
    ], gaps=[{"start": 900, "end": 1000, "length": 100,
              "cause": "read_shortfall", "detail": "x"}])
    q = build_queue(res)
    assert [(i["prio"], i["ref_start"]) for i in q] == [
        ("fail", 300), ("read_shortfall", 900), ("pending", 500)]
    # 缺口项带 E 归因证据
    gap_ev = q[1]["evidence"]["gap"]
    assert gap_ev["cause"] == "read_shortfall" and gap_ev["length"] == 100


def test_queue_excludes_beyond_primer_and_misc():
    """beyond_primer / 混合 / 读取失败不入队（goal W14/PL F1）"""
    res = _res([
        _blk("scope_beyond_primer", 100),
        _blk("mixed_sample", None),
        _blk("read_failed", None),
        _blk("partial_alignment", None),
    ])
    assert build_queue(res) == []


def test_queue_drops_confirmed_sites():
    """人工确认过的位点不再进队列（突变条目由 decisions 剔除）"""
    res = _res([
        _blk("unresolved_site", 500),
        _blk("unresolved_variant", 700),
    ])
    q = build_queue(res, [{"ref_pos": 500, "decision": "mutation"}])
    assert [i["ref_start"] for i in q] == [700]


# ==================== build_verdict × review_decisions ====================
# SC8（真实场景）：范围外 ori 内 3500 确证替换 → pending
# （critical_element_variant，非 fail）；其 ref_start 动态取自 blocking


def _sc8_record():
    spec, res = _run("SC8")
    assert res["verdict"]["status"] == "pending"
    blk = [b for b in res["verdict"]["blocking"]
           if b.get("kind") == "critical_element_variant"]
    assert blk and blk[0]["ref_start"] is not None
    return spec, res, blk[0]["ref_start"]


def test_artifact_confirmed_removed_and_traced():
    """伪影确认：pending 条目移出 blocking、info 留痕、headline 带人工确认"""
    spec, res, pos = _sc8_record()
    vd = build_verdict(res, len(spec["reference"]), scope=res["verdict"]["scope"],
                       review_decisions=[{"ref_pos": pos, "decision": "artifact",
                                          "by": "alice", "at": "2026-11-10T10:00:00"}])
    assert not [b for b in vd["blocking"] if b.get("ref_start") == pos]
    assert vd["status"] == "pass"  # 无其余阻断 → 收口
    assert vd["reviewed_count"] == 1
    art = [i for i in vd["info"] if i["kind"] == "reviewed_artifact"]
    assert art and art[0]["ref_start"] == pos
    assert "人工确认为测序伪影" in art[0]["detail"]
    assert "alice" in art[0]["detail"] and str(pos) in art[0]["detail"]
    assert vd["headline"].endswith("人工确认 1 处")


def test_mutation_confirmed_upgrades_to_fail():
    """突变确认：条目升级确证（fail_impact），status → fail，留痕 reviewed"""
    spec, res, pos = _sc8_record()
    vd = build_verdict(res, len(spec["reference"]), scope=res["verdict"]["scope"],
                       review_decisions=[{"ref_pos": pos, "decision": "mutation",
                                          "by": "bob", "at": "2026-11-10T10:00:00"}])
    assert vd["status"] == "fail"
    b = [x for x in vd["blocking"] if x.get("ref_start") == pos][0]
    assert b["fail_impact"] and b["reviewed"]["by"] == "bob"
    assert "人工确认为真实突变" in b["detail"]
    assert vd["reviewed_count"] == 1
    assert "人工确认 1 处" in vd["headline"]


def test_no_decisions_behaves_as_before():
    """不带 decisions：headline/reviewed_count 与旧口径一致（W14 不改默认行为）"""
    spec, res, _pos = _sc8_record()
    vd = build_verdict(res, len(spec["reference"]), scope=res["verdict"]["scope"])
    assert vd["reviewed_count"] == 0
    assert "人工确认" not in vd["headline"]
    assert vd["status"] == "pending"
    # 纯函数可复现：同一份 result 重算两次 verdict 一致
    vd2 = build_verdict(res, len(spec["reference"]), scope=res["verdict"]["scope"])
    assert vd == vd2


def test_review_decision_trace_helper():
    """apply_review_decision：追加 {ref_pos, decision, by, at(, note)}"""
    ds: list = []
    item = apply_review_decision(ds, 500, "artifact", "carol", note="峰图重叠")
    assert ds[-1] is item
    assert item["ref_pos"] == 500 and item["decision"] == "artifact"
    assert item["by"] == "carol" and item["at"] and item["note"] == "峰图重叠"
