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
    from core.sanger.reference_parser import parse_reference_topology

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
        ref_seq, features, topo = parse_reference_topology(
            ref_item["file"]["name"], ref_item["file"]["bytes"])
        ab1s = [(r["file"]["name"], r["file"]["bytes"]) for r in slot["reads"]]
        # T25.2：拓扑读参考（GenBank LOCUS / SnapGene 标记；未标明默认环状，
        # 与 app.sequencing_batch.reference_circular 同口径）
        analyses[plasmid] = {"result": analyze(ab1s, ref_seq, features,
                                               circular=topo is not False),
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
    """真实峰型下的混合检测基线（goal T12 信号驱动可信区口径）：三条 read
    都判出双峰 15 / 25 / 35 处。口径演变链：v2.16.0 之前（只扣首尾 20bp）
    是 22 / 31 / 16；v2.16.0 700 硬顶后 4 / 9 / 10（尾部双峰静默忽略）；
    T12 起可信区由 read 自身信号决定（zone_signal 四指标逐窗判定），
    三条 read 的尾端信号实测可信 → zone 延伸至 975 / 965 / 940，
    797–957 段的真实双峰形态位点重新计入（快照 work/after_T12.json，
    新增位点全部落在旧 700 硬顶之外）；T24.1 起 PLOC 取 0 基峰心原值
    （不再减 1，快照 work/after_T24.json）：峰窗以峰心对称取面积后，
    滞后于主峰的染料 blob/衰减段肩峰占比实测下降（如 pET-Up 位点
    0.62→0.45、0.52→0.36），贴 0.30 地板的伪影条目不再检出，
    9/18/19 → 8/16/17，MX 核心判定（2 确证差异/CDS/覆盖）逐字节不变。
    R3 起（2026-11-08 复审）ratio_floor 0.30→0.18（af=r/(1+r)，0.30 卡的是
    AF 0.231，MINOR_AF_MIN=0.15 对应比值 0.176 是死代码——复审 P0-3）：
    af 0.153-0.23 带的肩峰/衰减噪声位点重新进入检测（快照
    work/after_R3.json，verdict/coverage 与 T25 逐字段零 diff），
    8/16/17 → 15/25/35；新增位点 Q 比判别器仍全部归因 noise，
    「疑似混合样品」升级不触发；互检待核集合 pET-Up 不变（分母 16→25）、
    T7-Term 3→4（T24.1 掉档的 6873 在低地板下重新检出且待核）。

    同时锁住两条硬约束：报出的双峰位点必须落在该 read 的可信区内；
    read 级「疑似混合样品」升级不得因尾端位点计入而误触发。"""
    ctx = _load_ctx()
    res = ctx["analyses"]["MX"]["result"]
    md = res["mixed_detected"]
    assert len(md) == 3
    zones = {r["filename"]: r["reliable_zone"] for r in res["reads"]}
    counts = []
    for _fname, positions in md.items():
        z = zones[_fname]
        assert all(z["start"] <= p <= z["end"] for p in positions), _fname
        counts.append(len(positions))
    assert sorted(counts) == [15, 25, 35]
    # T11+T12 口径基线（goal T11/T12；A 3.1，先取证再改断言，快照
    # work/after_T11.json → after_T12.json）：read 级「疑似混合样品」升级
    # 走证据形态学（mixed_model.classify_mixed）。T12 后位点集扩容
    # （thirds [9,0,10] 型、span 0.85–0.92），站点 Q 中位仅 read 中位的
    # 0.36–0.46 倍（<0.7）→ 三条 read 均归因 low_site_q → noise：
    # 尾端低 Q 双峰是衰减噪声而非混合克隆，Q 比判别器正确挡下升级。
    # verdict 仍 fail（5269/5076 确证变异驱动）。
    assert "疑似混合样品" not in res["conclusion"]
    assert "双峰 25 处中 9 处无其他引物覆盖待核" in res["conclusion"]
    assert "双峰 15 处中 4 处无其他引物覆盖待核" in res["conclusion"]
    # 2026-11-05 定稿：跨引物互检汇总只保留「多引物同报双峰」信号行；
    # MX 批次 multi_sites 为空（双峰全是各 read 独立信号噪声）→ 不出该行
    assert "同报双峰" not in res["conclusion"]
    # T8+T12 互检口径基线（先取证再改断言）：待核 = 互检 uncovered（验证
    # read 可信区内对应列未读出设计碱基）。T12 可信区延伸后互检覆盖面
    # 扩大：seqF 的双峰全部被其他 read 延伸后的可信区高质量覆盖 → seqF
    # 待核行消失；pET-Up 保留 9 处待核（5075–5085，引物 5' 端互检盲区，
    # T24.1 后分母 18→16 但待核集合不变）；T7-Term 尾端 3 处无法互检
    # （6868–6872，T24.1 后 6873 掉档）→ 待核行仍为 2 条，逐字锁新基线
    concl_lines = res["conclusion"].splitlines()
    pending = [x for x in concl_lines if x.strip().startswith("↳") and "待核" in x]
    assert len(pending) == 2
    assert any("SZ15674-pET-Up.ab1" in x and "25 处中 9 处" in x
               and "5075–5079、5082–5085" in x for x in pending)
    assert any("SZ15674-T7-Term.ab1" in x and "15 处中 4 处" in x
               and "6868–6869、6872–6873" in x for x in pending)


def test_real_analysis_cross_read_conflicts_and_cds_verdict():
    """真实数据上的置信分层与互检反证基线：5269 C>T 高置信同义、5076 插入（移码）
    致 CDS 判「MX 蛋白与设计不一致」；6653/6666 两处缺失仅单 read 报告（Q17 / Q20），
    其他引物在 ±25bp 高质量区读出设计碱基 → 保持 low 置信。结论口径（2026-11-05
    瘦身定稿）：互检排除只留一句汇总，逐条位置（6653/6666 等）在变体表核对——
    变体表每条带 cross_read_resolved 标注与互检证据，结论不再点名。"""
    ctx = _load_ctx()
    res = ctx["analyses"]["MX"]["result"]
    confs = {v["ref_pos"]: v["confidence"] for v in res["variants"]}
    assert confs[5269] == "high"
    assert confs[5076] in ("medium", "high")
    assert confs[6653] == "low" and confs[6666] == "low"
    concl = res["conclusion"]
    # read×区段 注记口径（2026-11-05 定稿）：逐条证据行删，按源 read 归并
    assert "报出" in concl and "高质量覆盖排除" in concl and "无需核对" in concl
    # 位置级核对走变体清单的结构化标注
    resolved = {v["ref_pos"] for v in res["variants"] if v.get("cross_read_resolved")}
    assert {6653, 6666} <= resolved
    assert "↳ 位置 6653" not in concl and "↳ 位置 6666" not in concl
    assert "互检矛盾" not in concl                     # v2.15.1 起改为直接排除
    assert "MX 蛋白与设计不一致" in concl  # 头行（has_mixed 分支）


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


def test_real_cds_mode_mx():
    """目的基因模式（W8 全质粒口径）：MX.dna 按参考文件名识别 CDS「MX」
    （read 名 SZ15674-* 不带 MX，靠参考名兜底——真实交付形态），
    prepare_scope_mode 产 scope（CDS+接合区）→ analyze 全质粒按真实拓扑比对。

    等价性锁定（本用例的存在理由，W8 从「变体集合一致」扩展到「结论一致」）：
    同组 read、同一参考，cds_target 路径与「全质粒 + 手搭同范围 scope」的
    verdict.status / headline / blocking kind 集合必须逐字一致；变体集合与
    全质粒未声明口径逐一相同（scope 不改比对）。历史教训保留为口径注记：
    首版零侧翼裁参考实现曾把贴着 CDS 首端的插入（参考 5076）吞进软
    clipping——全质粒比对下该插入在需测范围内必然检出。"""
    from core.sanger import cds_extract
    from core.sanger.pipeline import analyze
    from core.sanger.reference_parser import parse_reference
    from core.sanger.scope import Scope

    ctx = _load_ctx()
    full = ctx["analyses"]["MX"]["result"]
    ref_path = REAL_DATA_DIR / "MX.dna"
    seq, feats = parse_reference(ref_path.name, ref_path.read_bytes())
    read_names = ["SZ15674-10570-2-seqF.ab1", "SZ15674-T7-Term.ab1",
                  "SZ15674-pET-Up.ab1"]

    cands = cds_extract.cds_candidates(feats)
    cand, note = cds_extract.resolve_auto(cands, "MX", read_names)
    assert cand is not None and cand["name"] == "MX", note
    assert (cand["start"], cand["end"]) == (5075, 6877)

    ref2, feats2, scope, circular = cds_extract.prepare_scope_mode(
        seq, feats, "MX", read_names)
    assert ref2 is seq and feats2 is feats     # W8：不裁参考、全注释保留（P1-F）
    assert scope.ranges == [(5075, 6877)]
    assert scope.junctions == [(5045, 5105), (6847, 6907)]   # CDS 两端 ±30
    assert scope.source == "cds_target" and scope.elements == ["MX"]

    mx_reads = next(g["reads"] for g in ctx["groups"] if g["plasmid"] == "MX")
    ab1s = [(r["file"]["name"], r["file"]["bytes"]) for r in mx_reads]
    res = analyze(ab1s, ref2, feats2, scope=scope, circular=circular)

    # 结论一致（W8）：与手搭同范围 scope（同 R 同接合区）逐字一致
    manual = Scope(ranges=[(5075, 6877)], junctions=[(5045, 5105), (6847, 6907)],
                   source="user", elements=["MX"], reason="对照")
    res_manual = analyze(ab1s, seq, feats, scope=manual, circular=True)
    assert res["verdict"]["status"] == res_manual["verdict"]["status"]
    assert res["verdict"]["headline"] == res_manual["verdict"]["headline"]
    assert ({b["kind"] for b in res["verdict"]["blocking"]}
            == {b["kind"] for b in res_manual["verdict"]["blocking"]})
    # 变体集合与全质粒未声明口径逐一相同（同参考同 read，scope 不改比对）
    assert sorted((v["ref_pos"], v["type"], v["confidence"])
                  for v in res["variants"]) == \
        sorted((v["ref_pos"], v["type"], v["confidence"])
               for v in full["variants"])

    mx = next(c for c in res["cds_reports"] if c["name"] == "MX")
    assert mx["coverage_status"] in ("full", "partial")
    assert mx["covered_percent"] > 90  # 三条 read 覆盖 CDS 主体
    full_mx = next(c for c in full["cds_reports"] if c["name"] == "MX")
    assert mx.get("protein_identical") == full_mx.get("protein_identical")
    assert len(mx.get("aa_changes") or []) == len(full_mx.get("aa_changes") or [])
