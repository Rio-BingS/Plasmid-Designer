"""批内同引物克隆互为对照（2026-11-11，core.sanger.batch_control）

依据：DEMO7 真实两克隆同引物次峰比 r 跨克隆 Spearman 0.76-0.85；S00024
的 2915 C/T（AF 0.267）在另一克隆同位点同次碱基 r=0.13、主峰未降。
护栏：只有「无法互检」的孤立双峰能被降级；对照也高丰度（shared_high）、
候选主峰明显变矮（真实亚群形态）、无独立对照（改名复制件）一律保持原判。

注：合成器 make_trace_ab1 的 secondary 是叠加在主峰上（主峰不降），真实
亚群的主峰互补下降用 amp_override 显式构造。
"""

import pytest

from app.sequencing_batch import run_batch_groups
from core.sanger import batch_control as bc
from core.sanger.pipeline import analyze
from tests.sanger_scenarios import _rand_seq, _read_from_ref, _rng

RP = 500


@pytest.fixture(scope="module")
def ref():
    return _rand_seq(_rng("batch_control"), 1200)


def _alt(ref):
    return next(b for b in "ACGT" if b != ref[RP - 1])


def _read(ref, seed, sec=None, amp=None):
    kw = {}
    if sec:
        kw["secondary"] = {RP - 100: (_alt(ref), sec)}
    if amp:
        kw["amp_override"] = {RP - 100: amp}
    return _read_from_ref(ref, 101, 900, seed=seed, **kw)


def _run(ref, name, blob, features=None, **kw):
    return analyze([(name, blob)], ref, features or [], circular=False, **kw)


def _evidence(ref, cand, ctrls):
    ents = [{"clone": "A", "ref_key": bc.ref_key(ref), "ref_len": len(ref), "result": cand}]
    ents += [{"clone": f"C{i}", "ref_key": bc.ref_key(ref), "ref_len": len(ref), "result": c}
             for i, c in enumerate(ctrls)]
    return bc.compute(ents).get("A")


def _verdict_at(ev):
    return {s["ref_pos"]: s["verdict"] for s in ev["sites"]} if ev else {}


def test_对照同位点同次峰_判批内背景且降级(ref):
    a = _run(ref, "A-P1.ab1", _read(ref, 1, 0.40))
    assert a["verdict"]["status"] == "pending"
    ev = _evidence(ref, a, [_run(ref, "B-P1.ab1", _read(ref, 2, 0.25))])
    assert _verdict_at(ev)[RP] == "batch_background"
    a2 = _run(ref, "A-P1.ab1", _read(ref, 1, 0.40), batch_control=ev)
    assert a2["verdict"]["status"] == "pass"
    chk = a2["mixed_corroboration"]["by_read"]["A-P1.ab1"]
    assert chk["batch"] == 1 and RP in chk["batch_ref"]
    assert a2["batch_control"] is ev


def test_对照干净_克隆特有保持待核(ref):
    a = _run(ref, "A-P1.ab1", _read(ref, 1, 0.40))
    ev = _evidence(ref, a, [_run(ref, "B-P1.ab1", _read(ref, 3))])
    assert _verdict_at(ev)[RP] == "clone_specific"
    a2 = _run(ref, "A-P1.ab1", _read(ref, 1, 0.40), batch_control=ev)
    assert a2["verdict"]["status"] == "pending"


def test_对照也高丰度_shared_high不降级(ref):
    a = _run(ref, "A-P1.ab1", _read(ref, 1, 0.40))
    ev = _evidence(ref, a, [_run(ref, "B-P1.ab1", _read(ref, 4, 0.5))])
    assert _verdict_at(ev)[RP] == "shared_high"
    a2 = _run(ref, "A-P1.ab1", _read(ref, 1, 0.40), batch_control=ev)
    assert a2["verdict"]["status"] == "pending"


def test_候选主峰明显变矮_真实亚群形态不降级(ref):
    blob = _read(ref, 1, 0.40, amp=0.6)
    a = _run(ref, "A-P1.ab1", blob)
    ev = _evidence(ref, a, [_run(ref, "B-P1.ab1", _read(ref, 2, 0.25))])
    assert _verdict_at(ev)[RP] == "clone_specific"


def test_改名复制件不算独立对照(ref):
    blob = _read(ref, 1, 0.40)
    a = _run(ref, "A-P1.ab1", blob)
    twin = _run(ref, "B-P1.ab1", blob)
    assert _evidence(ref, a, [twin]) is None


def test_不同引物不互为对照(ref):
    a = _run(ref, "A-P1.ab1", _read(ref, 1, 0.40))
    other = analyze([("B-P2.ab1", _read_from_ref(ref, 300, 1100, seed=2,
                                                  secondary={RP - 299: (_alt(ref), 0.25)}))],
                    ref, [], circular=False)
    assert _evidence(ref, a, [other]) is None


def test_功能区高丰度mixed_variant_批内背景降为info(ref):
    feats = [{"name": "geneX", "type": "CDS", "start": 301, "end": 702, "strand": "+"}]
    a = _run(ref, "A-P1.ab1", _read(ref, 1, 0.40), feats)
    assert any(b["kind"] == "mixed_variant" and b["ref_start"] == RP
               for b in a["verdict"]["blocking"])
    ev = _evidence(ref, a, [_run(ref, "B-P1.ab1", _read(ref, 2, 0.25), feats)])
    a2 = _run(ref, "A-P1.ab1", _read(ref, 1, 0.40), feats, batch_control=ev)
    assert not any(b["kind"] == "mixed_variant" for b in a2["verdict"]["blocking"])
    assert any(i["kind"] == "mixed_variant" and "同批同引物" in i["detail"]
               for i in a2["verdict"]["info"])


def test_无批内证据时结果不变(ref):
    blob = _read(ref, 1, 0.40)
    r0 = _run(ref, "A-P1.ab1", blob)
    r1 = _run(ref, "A-P1.ab1", blob, batch_control=None)
    assert r0["verdict"] == r1["verdict"] and r0["conclusion"] == r1["conclusion"]


def test_run_batch_groups_两遍流程端到端(ref):
    fasta = (">refX\n" + ref + "\n").encode()

    def grp(clone, blob):
        return {"plasmid": "refX", "clone": clone,
                "reference": {"file": {"name": "refX.fasta", "bytes": fasta}},
                "reads": [{"file": {"name": f"{clone}-P1.ab1", "bytes": blob}}]}

    groups = [grp("A", _read(ref, 1, 0.40)), grp("B", _read(ref, 2, 0.25)),
              grp("C", _read(ref, 3))]
    seen = []

    def register(label, ref_seq, features, res):
        seen.append((label, res))
        return f"id-{len(seen)}", {"result": res}

    items, recs = run_batch_groups(groups, 20, register)
    assert [it["clone"] for it in items] == ["A", "B", "C"]
    st = {it["clone"]: it["verdict_status"] for it in items}
    # A 的孤立双峰：B 同位点同次峰 → 背景；C 干净 → 不支持。过半对照才算，
    # 1/2 不过半 → 保持待核（多对照时不允许一个对照拍板）
    assert st["A"] == "pending"
    assert len(recs) == 3 and all(it["analysis_id"] for it in items)
    res_a = next(r for l, r in seen if l.startswith("A"))
    assert res_a["batch_control"]["sites"][0]["verdict"] == "clone_specific"
    items2, _ = run_batch_groups(groups[:2], 20, register)
    st2 = {it["clone"]: it for it in items2}
    assert st2["A"]["verdict_status"] == "pass"
    assert "合格" in st2["A"]["conclusion"]


def test_跨引物干净读出_功能区mixed_variant降info(ref):
    """同一克隆另一引物在该位点高质量单峰读出设计碱基：真实亚群会同样出现在
    同一模板的另一条 read 上——互检否决口径，mixed_variant 降 info（DEMO7
    S00024 2915：M13R 干净覆盖，v3.2 仍判疑似混合，属回归）"""
    feats = [{"name": "geneX", "type": "CDS", "start": 301, "end": 702, "strand": "+"}]
    p1 = _read(ref, 1, 0.40)
    p2 = _read_from_ref(ref, 200, 1000, seed=9)
    single = analyze([("A-P1.ab1", p1)], ref, feats, circular=False)
    assert any(b["kind"] == "mixed_variant" for b in single["verdict"]["blocking"])
    both = analyze([("A-P1.ab1", p1), ("A-P2.ab1", p2)], ref, feats, circular=False)
    mv = next(m for m in both["mixed_variants"] if m["ref_pos"] == RP)
    assert mv["noise_evidence"] == ["cross_primer"]
    assert not any(b["kind"] == "mixed_variant" for b in both["verdict"]["blocking"])
    # 随机序列上的 CDS 标注不是真 ORF（orf_annotation_suspect 待定），与本条无关
    assert both["verdict"]["status"] != "mixed"


def test_跨引物同报双峰_不降级(ref):
    feats = [{"name": "geneX", "type": "CDS", "start": 301, "end": 702, "strand": "+"}]
    p1 = _read(ref, 1, 0.40)
    p2 = _read_from_ref(ref, 200, 1000, seed=9, secondary={RP - 199: (_alt(ref), 0.40)})
    both = analyze([("A-P1.ab1", p1), ("A-P2.ab1", p2)], ref, feats, circular=False)
    assert any(b["kind"] == "mixed_variant" for b in both["verdict"]["blocking"])
