"""环状参考上跨原点的缺失参与 CDS 判定（审查 04 H2 回归锁）

变体 ref_pos 已折回 1..L，但缺失的 ref_pos+length-1 可以超过 L。
CDS 报告曾按线性区间判断相交、按线性坐标重建序列，注释器也只看
ref_pos：删掉 1998..2000 + 1..3（含 ATG）仍报"蛋白与设计一致"，
批量归入 正确/。
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.sanger.annotator import annotate_variant
from core.sanger.pipeline import _build_cds_reports

L = 2000
STOPS = {"TAA", "TAG", "TGA"}


def _construct():
    """2000bp 参考，CDS 1..300（ATG 起始、TAA 终止、无内部终止），
    构造与审查复现脚本 work04/t_pipe.py 一致"""
    rng = random.Random(5)
    cds = "ATG"
    while len(cds) < 3 * 99:
        c = "".join(rng.choice("ACGT") for _ in range(3))
        if c not in STOPS:
            cds += c
    cds += "TAA"
    rest = "".join(rng.choice("ACGT") for _ in range(L - 300))
    return cds + rest


FEATS = [{"name": "CDS1", "type": "CDS", "start": 1, "end": 300, "strand": "+",
          "description": ""}]


def _del(seq, pos, n):
    bases = "".join(seq[(pos - 1 + i) % len(seq)] for i in range(n))
    return {"ref_pos": pos, "type": "deletion", "ref_base": bases, "alt_base": "-",
            "length": n, "confidence": "high"}


def _full_cov():
    return {"diffs": [], "covered_ranges": [(1, L)], "coverage_percent": 100.0}


def test_cds_report_counts_origin_spanning_deletion():
    seq = _construct()
    # 删除 1998、1999、2000、1、2、3：后 3 bp 落在 CDS 内且包含 ATG
    cr = _build_cds_reports(seq, FEATS, [_del(seq, 1998, 6)], _full_cov())[0]
    assert cr["protein_identical"] is False
    assert "start_lost" in cr["consequences"]
    assert cr["frameshift_count"] == 0  # 落入 CDS 的只有 3 bp → 框内


def test_cds_report_origin_spanning_deletion_frameshift_by_in_cds_part():
    seq = _construct()
    # 1997..2000 + 1..2：全长 6 bp，但只有 2 bp 落在 CDS 内 → 移码
    cr = _build_cds_reports(seq, FEATS, [_del(seq, 1997, 6)], _full_cov())[0]
    assert cr["protein_identical"] is False
    assert cr["frameshift_count"] == 1


def test_cds_report_deletion_before_origin_does_not_touch_cds():
    seq = _construct()
    cr = _build_cds_reports(seq, FEATS, [_del(seq, 1995, 6)], _full_cov())[0]
    assert cr["protein_identical"] is True


def test_annotator_assigns_origin_spanning_deletion_to_cds():
    seq = _construct()
    v = annotate_variant(_del(seq, 1998, 6), FEATS, seq)
    assert v["features"] == [{"name": "CDS1", "type": "CDS"}]
    assert v["frameshift"] is False  # CDS 内 3 bp
    v = annotate_variant(_del(seq, 1997, 6), FEATS, seq)
    assert v["features"] == [{"name": "CDS1", "type": "CDS"}]
    assert v["frameshift"] is True  # CDS 内 2 bp


def test_annotator_linear_deletion_unchanged():
    seq = _construct()
    v = annotate_variant(_del(seq, 10, 2), FEATS, seq)
    assert v["features"] == [{"name": "CDS1", "type": "CDS"}]
    assert v["frameshift"] is True
    v = annotate_variant(_del(seq, 1990, 6), FEATS, seq)
    assert v["features"] == []


def test_analyze_and_batch_origin_spanning_deletion_not_qualified():
    """全链路：跨原点 read 删掉 ATG，CDS 报告与批量结论不得判合格"""
    from abif_utils import make_ab1
    from app.sequencing_report import _conclusion_bucket
    from core.sanger.batch import excel_conclusion
    from core.sanger.pipeline import analyze

    seq = _construct()
    read = seq[L - 200:L - 3] + seq[3:300]
    res = analyze([("r0.ab1", make_ab1(read, [40] * len(read)))], seq, FEATS,
                  circular=True)
    dels = [(v["type"], v["ref_pos"], v["length"]) for v in res["variants"]]
    assert dels == [("deletion", 1998, 6)]
    assert res["variants"][0]["features"] == [{"name": "CDS1", "type": "CDS"}]
    cr = [c for c in res["cds_reports"] if c["name"] == "CDS1"][0]
    assert cr["protein_identical"] is False
    out = excel_conclusion("P1", res, 1, True)
    assert out.startswith("不合格")
    assert _conclusion_bucket(out) != "正确"
