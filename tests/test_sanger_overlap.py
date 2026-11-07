"""T21 重叠区分歧与双链覆盖（goal_sanger_full_fix_v2 T21；A 4.4）

同一参考位被多条可信 read 覆盖却报出不同非参考碱基且近平局 → 共识 IUPAC +
unresolved（pending），不再按投票顺序静默取一边；double_strand_fraction 写入
verdict.roi；「ROI 必须双链覆盖」策略可配置（默认关闭）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/backend"))

from core.sanger.overlap import (  # noqa: E402
    DISCORDANCE_TOP_FRAC, REQUIRE_DOUBLE_STRAND, double_strand_fraction,
    downgrade_overlap_conflicts, find_discordances, iupac_merge,
)


def _read(name, s, e, direction="+"):
    return {"filename": name,
            "alignment": {"ref_start": s, "ref_end": e, "direction": direction}}


def test_iupac_merge():
    assert iupac_merge("A", "G") == "R"
    assert iupac_merge("C", "T") == "Y"
    assert iupac_merge("A", "A") == "A"
    assert iupac_merge("A", "C") == "M"


def test_find_discordances_near_tie():
    """C>A 与 C>G 同权 → 分歧，共识 R，票差 0；各 read 调用/Q 随记录下发"""
    info = {499: [{"read": "a.ab1", "call": "A", "q": 50, "w": 5.48},
                  {"read": "b.ab1", "call": "G", "q": 50, "w": 5.48}]}
    out = find_discordances(info, "ACGT" * 400)
    assert len(out) == 1
    d = out[0]
    assert d["ref_pos"] == 500
    assert d["top_bases"] in (["A", "G"], ["G", "A"])
    assert d["consensus"] == "R"
    assert d["vote_gap"] == 0
    assert {c["read"]: c["call"] for c in d["calls"]} == {"a.ab1": "A",
                                                          "b.ab1": "G"}
    assert "重叠区 500" in d["detail"]


def test_find_discordances_majority_wins():
    """多数票（2:1）票差超阈 → 不判分歧；参考侧占优同样不判"""
    # 2A vs 1G：gap = w > 0.2 × 2w
    info = {499: [{"read": "a", "call": "A", "q": 50, "w": 5.48},
                  {"read": "b", "call": "A", "q": 50, "w": 5.48},
                  {"read": "c", "call": "G", "q": 50, "w": 5.48}]}
    assert find_discordances(info, "ACGT" * 400) == []
    # 参考票占优：2 ref vs 1A+1G（A/G 近平局但参考更高）
    info2 = {499: [{"read": "a", "call": "C", "q": 50, "w": 5.48},
                   {"read": "b", "call": "C", "q": 50, "w": 5.48},
                   {"read": "c", "call": "A", "q": 50, "w": 5.48},
                   {"read": "d", "call": "G", "q": 50, "w": 5.48}]}
    assert find_discordances(info2, "C" * 800) == []


def test_downgrade_overlap_conflicts():
    """分歧位点 confirmed 替换双方 → unresolved + reason_codes；他位不动"""
    variants = [
        {"ref_pos": 500, "type": "substitution", "status": "confirmed"},
        {"ref_pos": 500, "type": "substitution", "status": "confirmed"},
        {"ref_pos": 700, "type": "substitution", "status": "confirmed"},
        {"ref_pos": 500, "type": "deletion", "status": "confirmed"},
    ]
    n = downgrade_overlap_conflicts(
        variants, [{"ref_pos": 500, "calls": []}])
    assert n == 2
    assert all(v["status"] == "unresolved" for v in variants[:2])
    assert all("overlap_discordance" in v["reason_codes"] for v in variants[:2])
    assert variants[2]["status"] == "confirmed"
    assert variants[3]["status"] == "confirmed"


def test_double_strand_fraction():
    """± 链都覆盖 → 100%；单链 → 0；部分对侧 → 中间值；无 ROI → None"""
    reads = [_read("f1", 100, 500, "+"), _read("r1", 300, 700, "-")]
    assert double_strand_fraction(reads, 300, 500) == 100.0
    assert double_strand_fraction(reads, 100, 299) == 0.0
    # 300-500 双链、100-299 仅正链 → ROI 100-700 双链 201/601
    got = double_strand_fraction(reads, 100, 700)
    assert got == round(201 / 601 * 100, 2)
    assert double_strand_fraction(reads, None, None) is None
    # 环状跨原点 read：ref_segments 拆段后参与（用 aln_segments 同口径）
    circ = {"filename": "c", "alignment": {"ref_start": 950, "ref_end": 1050,
                                           "direction": "-", "circular": True,
                                           "ref_length": 1000}}
    # 线性 aln_segments 对 circular=True 且 e>L 拆段；此处 950-1050 拆 [950,1000]+[1,50]
    got2 = double_strand_fraction([_read("f", 1, 1000, "+"), circ], 950, 1000)
    assert got2 == 100.0


def test_require_double_strand_flag_default_off():
    """默认关闭；启用后单链 ROI → pending（unresolved_site 阻断）"""
    assert REQUIRE_DOUBLE_STRAND is False
    import core.sanger.verdict as vd
    reads = [_read("f1", 100, 500, "+")]
    vd.REQUIRE_DOUBLE_STRAND = True
    try:
        v = vd.build_verdict({
            "variants": [], "unresolved_sites": [], "primer_checks": [],
            "cds_reports": [], "errors": [], "reads": reads,
            "mixed_profiles": {}, "mixed_variants": [],
            "mixed_corroboration": {}, "consensus": {"covered_ranges": [[100, 500]]},
            "coverage": {"measured_ranges": [[100, 500]], "verified_ranges": [[100, 500]]},
            "features": [], "algo_version": "v5",
        }, 1000)
    finally:
        vd.REQUIRE_DOUBLE_STRAND = False
    assert v["roi"]["double_strand_fraction"] == 0.0
    assert any(b["kind"] == "unresolved_site" and "双链覆盖" in b["detail"]
               for b in v["blocking"])


def test_e2e_m3_shape():
    """端到端 M3 形态：双 read 同位异 alt → pending + overlap_discordance +
    共识 IUPAC + 双方变体 unresolved（对齐场景测试断言）"""
    import random
    from core.sanger.pipeline import analyze
    from sanger_scenarios import _read_from_ref
    rng = random.Random("t21e2e")
    ref = list("".join(rng.choice("ACGT") for _ in range(1200)))
    ref[499] = "C"
    ref = "".join(ref)
    r1 = _read_from_ref(ref, 1, 1000, subs=[(500, "A")], q=50)
    r2 = _read_from_ref(ref, 1, 1000, subs=[(500, "G")], q=50)
    res = analyze([("a.ab1", r1), ("b.ab1", r2)], ref, [], circular=False)
    v = res["verdict"]
    assert v["status"] == "pending"
    kinds = {b["kind"] for b in v["blocking"]}
    assert "overlap_discordance" in kinds
    assert res["consensus"]["sequence"][499] == "R"
    subs = [x for x in res["variants"] if x["ref_pos"] == 500
            and x["type"] == "substitution"]
    assert subs and all(s["status"] == "unresolved" for s in subs)
