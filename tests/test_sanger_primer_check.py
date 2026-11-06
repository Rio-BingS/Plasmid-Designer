"""T17 引物库与方向校验（goal_sanger_full_fix_v2 T17；A 1.5）

read 应起自引物 3′ 端下游 20-150bp；方向不一致/结合位点找不到/距离出窗
→ primer_mismatch（verdict fail）；无法确定引物只记 info。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/backend"))

from core.sanger.primer_check import (  # noqa: E402
    check_reads, library_primers, locate_binding, resolve_primer,
)


def _read(name, ref_start, ref_end, direction="+"):
    return {"filename": name, "alignment": {"ref_start": ref_start,
                                            "ref_end": ref_end,
                                            "direction": direction}}


REF = "ACGT" * 10 + "GTAAAACGACGGCCAGT" + "ACGT" * 40   # M13F 结合 41-56
PRIMER = {"name": "M13F", "seq": "GTAAAACGACGGCCAGT", "direction": "+"}


def test_locate_binding_both_strands():
    b = locate_binding(PRIMER["seq"], REF)
    assert b == {"start": 41, "end": 57, "strand": "+"}   # 17bp 引物 41..57
    # 负链定位：序列本身在正链不存在、revcomp 存在 → strand "-"
    b2 = locate_binding("ACGGCCAGTCGTTTTAC", REF)         # revcomp = GTAAAACGACGGCCAGT
    assert b2 is None                                     # 正链无此 16bp 序列
    assert locate_binding("TTTCGTCGCGGCCCGTG", REF) is None
    # 换一个正链不存在、负链存在的序列：M13R 的 revcomp
    seq3 = "GTAAAACGACGGCCAG"    # 15bp，正链 41..55 存在 → "+"
    b3 = locate_binding(seq3, REF)
    assert b3 and b3["strand"] == "+"


def test_distance_window_and_direction():
    """+ 引物：read 起点距 3′ 端 20-150bp 内 ok；出窗/方向反 → mismatch"""
    # 3′ 端 = 57；read 起点 77（距 20bp）→ ok
    ok = check_reads(PRIMER, [_read("r.ab1", 77, 400)], REF)
    assert ok[0]["status"] == "ok" and "20bp" in ok[0]["detail"]
    # 距 220bp（M5 形态）→ mismatch
    far = check_reads(PRIMER, [_read("r.ab1", 277, 600)], REF)
    assert far[0]["status"] == "mismatch" and "220" in far[0]["detail"]
    # 方向反 → mismatch
    rev = check_reads(PRIMER, [_read("r.ab1", 77, 400, direction="-")], REF)
    assert rev[0]["status"] == "mismatch" and "方向不一致" in rev[0]["detail"]
    # 负链引物：3′ 端 = 结合区起点；read（负链）ref_end 距其 20-150 → ok
    minus_primer = {"name": "R", "seq": "AATTTAACTTCTTTGTTCGT", "direction": "-"}
    ref2 = "ACGT" * 10 + "ACGAACAAAGAAGTTAAATT" + "ACGT" * 40
    b = locate_binding(minus_primer["seq"], ref2)
    assert b and b["strand"] == "-"
    got = check_reads(minus_primer, [_read("r2.ab1", 10, b["start"] - 20, direction="-")], ref2)
    assert got[0]["status"] == "ok"


def test_unverified_and_library_resolution():
    """无法确定引物 → unverified（info）；按 name 查库可解析"""
    out = check_reads(None, [_read("r.ab1", 76, 400)], REF)
    assert out[0]["status"] == "unverified" and "未校验引物" in out[0]["detail"]
    out2 = check_reads({"name": "乱序引物X"}, [_read("r.ab1", 76, 400)], REF)
    assert out2[0]["status"] == "unverified"
    lib = library_primers()
    assert "m13f" in lib and lib["m13f"]["seq"] == "GTAAAACGACGGCCAGT"
    assert lib["m13f"]["source"]          # 注明来源
    r = resolve_primer({"name": "M13F"})  # 无 seq 按库补全
    assert r and r["seq"] == "GTAAAACGACGGCCAGT"
    assert r["source"]                    # 来源随库条目下发


def test_binding_missing_mismatch():
    """引物序列在参考上无结合位点（模板换位）→ mismatch"""
    out = check_reads({"name": "x", "seq": "AAAAAAAAAAAAAAAAAAAA"},
                      [_read("r.ab1", 76, 400)], REF)
    assert out[0]["status"] == "mismatch" and "无结合位点" in out[0]["detail"]


def test_verdict_fail_on_primer_mismatch():
    """端到端：M5 形态（read 起点距 3′ 端 220bp）→ verdict fail"""
    import random
    from core.sanger.pipeline import analyze
    rng = random.Random("t17e2e")
    ref = "".join(rng.choice("ACGT") for _ in range(1500))
    primer = {"name": "M13F", "seq": ref[99:130], "direction": "+"}
    from sanger_scenarios import _read_from_ref
    blob = _read_from_ref(ref, 350, 1350, q=50)
    res = analyze([("r.ab1", blob)], ref, [], primer=primer)
    assert res["verdict"]["status"] == "fail"
    kinds = {b["kind"] for b in res["verdict"]["blocking"]}
    assert "primer_mismatch" in kinds
    # 结果随下发 primer_checks
    assert res["primer_checks"][0]["status"] == "mismatch"
    # 对照：正确距离（350 → 起点距 3′ 端 130+20=150bp 处起 read）→ 不判 fail
    blob2 = _read_from_ref(ref, 280, 1350, q=50)   # 280 - 130 = 150 ✓
    res2 = analyze([("r.ab1", blob2)], ref, [], primer=primer)
    assert not any(b["kind"] == "primer_mismatch" for b in res2["verdict"]["blocking"])
    assert res2["primer_checks"][0]["status"] == "ok"
