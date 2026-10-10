"""真实 .ab1 回归集（skip-if-missing，跳过条件不满足时不计失败）

背景：V1.01 交付的真实测序数据（plasmid-designer V1.01/sequencing results/，
本机目录、不入库不推 GitHub）是唯一能暴露合成数据造不出的病态峰型的资产
——染料压缩、真实拖尾、信号衰减、真实滑移。此前的回归全部基于 make_ab1
合成数据。此文件建立「真实数据在场就跑」的回归机制：
数据目录存在 → 按交付信息表走 batch 归组口径逐质粒跑管线，锁基本形状；
数据目录不存在 → skip（CI/新机器无感）。

数据换批次时只需更新 REAL_DATA_DIR 指向新文件夹，断言按新结论调整。
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

# 真实数据根目录（不入库；换批次/换机器时改这里）
REAL_DATA_DIR = Path(__file__).resolve().parents[2] / "plasmid-designer V1.01" / "sequencing results"

pytestmark = pytest.mark.skipif(
    not REAL_DATA_DIR.is_dir() or not any(REAL_DATA_DIR.glob("*.ab1")),
    reason=f"真实测序数据不在场（{REAL_DATA_DIR}），本机回归跳过")

_real_ctx = None


def _load_ctx():
    """读取真实数据：信息表 → batch 归组 → 逐组跑管线（module 级只算一次）"""
    global _real_ctx
    if _real_ctx is not None:
        return _real_ctx
    from core.sanger.batch import load_excel, match_files, norm_stem
    from core.sanger.pipeline import analyze
    from core.sanger.reference_parser import parse_reference

    excel_bytes = (REAL_DATA_DIR / "测序.xlsx").read_bytes()
    wb, _header, cols, rows = load_excel(excel_bytes)
    files = []
    for p in sorted(REAL_DATA_DIR.iterdir()):
        if p.suffix.lower() in (".ab1", ".dna", ".gb", ".gbk", ".fasta", ".fa"):
            files.append({"path": p, "name": p.name, "stem": norm_stem(p.stem),
                          "ext": p.suffix.lower(), "bytes": p.read_bytes()})
    per_plasmid, unmatched = match_files(rows, files)
    analyses = {}
    groups = []
    for plasmid, slot in per_plasmid.items():
        ref_item = slot["reference"]
        if ref_item is None or not slot["reads"]:
            continue
        groups.append({"plasmid": plasmid, "reference": ref_item,
                       "reads": slot["reads"]})
        ref_seq, features = parse_reference(
            ref_item["file"]["name"], ref_item["file"]["bytes"])
        ab1s = [(r["file"]["name"], r["file"]["bytes"]) for r in slot["reads"]]
        analyses[plasmid] = {"result": analyze(ab1s, ref_seq, features),
                             "ref_len": len(ref_seq),
                             "n_features": len(features)}
    _real_ctx = {"groups": groups, "unmatched": unmatched, "analyses": analyses,
                 "rows": rows}
    return _real_ctx


def test_real_batch_groups_match_delivery_manifest():
    """归组形状：交付 1 个质粒（MX），3 条 read 全部按信息表匹配上"""
    ctx = _load_ctx()
    assert [g["plasmid"] for g in ctx["groups"]] == ["MX"]
    assert ctx["unmatched"] == []
    assert len(ctx["groups"][0]["reads"]) == 3


def test_real_analysis_basic_shape():
    """管线基本形状（真机数据的宽幅回归锁，锁结构不锁理想值）：
    read 解析/比对成功、图谱长度与特征数、mean_q 落在真实区间、
    共识覆盖为正值。基线（2026-10-08，MX 交付批次）：7039bp / 14 特征 /
    3 read（mean_q 54.8-55.5）/ 覆盖 27.3%"""
    ctx = _load_ctx()
    ent = ctx["analyses"]["MX"]
    res = ent["result"]
    assert ent["ref_len"] == 7039
    assert ent["n_features"] >= 10
    assert len(res["reads"]) == 3
    for r in res["reads"]:
        assert 10 <= r["mean_q"] <= 60
        aln = r["alignment"]
        assert aln["ref_end"] > aln["ref_start"]
    assert 0 < res["consensus"]["coverage_percent"] < 100
    assert res["consensus"]["sequence"]


def test_real_analysis_mixed_baseline_and_corroboration():
    """真实峰型下的混合检测基线（合成数据造不出的形态）：3 条 read 各
    19-27 处双峰位点（widespread 档）、结论判「疑似混合样品」、且带跨引物
    互检章节（4 处同报位点 + 倾向噪声/待核分类齐全）。若检测口径变化
    导致此基线不成立，说明真实数据上的判定改变了——须人工核对后更新"""
    ctx = _load_ctx()
    res = ctx["analyses"]["MX"]["result"]
    md = res["mixed_detected"]
    assert len(md) == 3
    for _fname, positions in md.items():
        assert len(positions) >= 5          # widespread 档（≥5 处双峰）
    assert "疑似混合样品" in res["conclusion"]
    assert "跨引物互检" in res["conclusion"]


def test_real_analysis_cross_read_conflicts_and_cds_verdict():
    """真实数据上的置信分层与互检反证基线：5269 C>T 高置信同义、
    5076 插入（移码）致 CDS 判「MX 蛋白与设计不一致」；6653/6666 两处
    缺失仅单 read 报告且被其他 read 干净跨过 → 必须压低置信 + 结论点名
    互检矛盾且不计入 CDS 判定"""
    ctx = _load_ctx()
    res = ctx["analyses"]["MX"]["result"]
    confs = {v["ref_pos"]: v["confidence"] for v in res["variants"]}
    assert confs[5269] == "high"
    assert confs[5076] in ("medium", "high")
    assert confs[6653] == "low" and confs[6666] == "low"
    assert "互检矛盾" in res["conclusion"]
    assert "未计入判定" in res["conclusion"]
    assert "MX 蛋白与设计不一致" in res["conclusion"]


def test_real_analysis_poly_verdicts_have_votes():
    """真实 poly 结构（若有）的判读必须有据：accepted 必须带 verdict_votes，
    count_reliable=False 必须给出 peak_measured 或长度估计之一——防止
    真实峰型下判读退化成无证据断言"""
    ctx = _load_ctx()
    res = ctx["analyses"]["MX"]["result"]
    for h in res.get("homopolymers", []):
        if h["tier"] != "poly":
            continue
        if h.get("run_verdict") == "accepted":
            assert h.get("verdict_votes"), f"poly {h['start']}-{h['end']} accepted 无投票记录"
        elif h.get("count_reliable") is False:
            assert h.get("peak_measured") is not None or h.get("length_estimate") is not None, \
                f"poly {h['start']}-{h['end']} 不可靠但无实测证据"
