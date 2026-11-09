"""目的基因模式（CDS 对比）测试：候选去重、名称匹配、CDS 提取与端点集成

匹配口径与批量归组同源（batch._match_ref_name，label 参数化）；真实数据的
候选形状与匹配路径（MX.dna→「MX」、17647 MUTPK read 名兜底）由
test_real_data_regression 的 CDS 模式断言真跑，本文件用合成 GenBank 锁口径。
"""

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from abif_utils import make_ab1  # noqa: E402
from core.sanger import cds_extract  # noqa: E402
from core.sanger.reference_parser import parse_reference_topology  # noqa: E402
from core.seq_utils import revcomp  # noqa: E402

# ==================== 合成参考（GenBank） ====================


def _mk_seq() -> str:
    """600bp：KanR(10-50) / TARGET(60-161, 完整 ORF) / NESTED(70-90, 被包含)
    / MINUS(170-190, 负链)——嵌套小 CDS 与负链 CDS 都有真实原型（6xHis、
    反向整合基因）。总长 600 使 CDS±FLANK_BP 侧翼不被质粒两端钳制"""
    fill = ("ACGT" * 15)[:59]
    target = "ATG" + "AAACAG" * 16 + "TAA"  # 102bp = 34 密码子
    mid = ("CGTACGTTGCA" * 2)[:8]
    minus = "ATG" + "GCTAAG" * 2 + "GCA" + "TAA"  # 21bp = 7 密码子
    tail = ("TTGACC" * 2)[:10]
    pad = ("AACCGGTT" * 60)[:400]  # 201..600 填充，让 TARGET/MINUS 侧翼完整
    seq = fill + target + mid + minus + tail + pad
    assert len(seq) == 600
    return seq


SEQ = _mk_seq()


def _gb() -> bytes:
    """用 Biopython 生成最小 GenBank（LOCUS 严格格式手写容易错，交给库本身）"""
    import io as _io

    from Bio import SeqIO
    from Bio.Seq import Seq
    from Bio.SeqFeature import FeatureLocation, SeqFeature
    from Bio.SeqRecord import SeqRecord

    rec = SeqRecord(Seq(SEQ), id="SYNPLASMID", name="SYNPLASMID",
                    annotations={"topology": "circular", "molecule_type": "DNA",
                                 "date": "01-JAN-2026", "data_file_division": "SYN"})

    def _feat(location_1based_start: int, end: int, ftype: str, gene: str,
              strand: int = 1) -> SeqFeature:
        loc = FeatureLocation(location_1based_start - 1, end, strand=strand)
        return SeqFeature(loc, type=ftype, qualifiers={"gene": [gene]})

    rec.features.append(_feat(10, 50, "CDS", "KanR"))
    rec.features.append(_feat(60, 161, "CDS", "TARGET"))
    rec.features.append(_feat(70, 90, "gene", "NESTED"))
    rec.features.append(_feat(170, 190, "CDS", "MINUS", strand=-1))
    buf = _io.StringIO()
    SeqIO.write(rec, buf, "genbank")
    return buf.getvalue().encode()


# 解析产物（module 级算一次）：也顺带锁 GenBank 解析 → 特征形状的契约
_gb_seq, _gb_feats, _gb_topo = parse_reference_topology("ref.gb", _gb())


def _cands():
    return cds_extract.cds_candidates(_gb_feats)


# ==================== 候选与去重 ====================


def test_candidates_nested_dedup():
    """被更大 CDS 完全包含的小特征（NESTED）不单独列为候选"""
    names = [c["name"] for c in _cands()]
    assert names == ["KanR", "TARGET", "MINUS"]


def test_candidates_fields_and_order():
    by = {c["name"]: c for c in _cands()}
    t = by["TARGET"]
    assert (t["start"], t["end"], t["length"]) == (60, 161, 102)
    assert t["strand"] == "+"
    m = by["MINUS"]
    assert m["strand"] == "-" and m["length"] == 21


# ==================== 名称匹配 ====================


def test_match_by_reference_stem():
    """参考文件名精确命中（真实原型：MX.dna → CDS「MX」）"""
    cand, note = cds_extract.resolve_auto(_cands(), "TARGET", [])
    assert cand["name"] == "TARGET"
    assert "参考文件名匹配" in note


def test_match_by_read_token_fallback():
    """参考名未命中时 read 文件名兜底：组成段一致
    （真实原型：改名后 S99675-17647-seqF1 ⊇ CDS「17647 MUTPK」的 17647）"""
    cand, note = cds_extract.resolve_auto(_cands(), "测序验证", ["S99675-TARGET-seqF1.ab1"])
    assert cand["name"] == "TARGET"
    assert "read 文件名匹配" in note
    # 通用引物 read 不带 CDS 名：不影响（未命中 → 无候选），也不报错
    cand2, _ = cds_extract.resolve_auto(_cands(), "TARGET", ["M13F-75.ab1", "T7-Term.ab1"])
    assert cand2["name"] == "TARGET"


def test_match_ambiguous_not_guessed():
    """不同 read 指向不同候选：不猜"""
    cand, note = cds_extract.resolve_auto(
        _cands(), "", ["r-KanR-1.ab1", "r-TARGET-1.ab1"])
    assert cand is None
    assert "无法唯一确定" in note


def test_match_no_hit():
    cand, note = cds_extract.resolve_auto(_cands(), "随便", ["M13F-75.ab1"])
    assert cand is None and "未能唯一识别" in note


# ==================== W8 prepare_scope_mode（全质粒 + scope） ====================


def test_prepare_scope_mode_full_plasmid_passthrough():
    """≤ max_ref_bp：参考/features 原样返回（同一对象），scope=CDS+接合区"""
    ref2, feats2, scope, circ = cds_extract.prepare_scope_mode(
        _gb_seq, _gb_feats, "ref", ["x-TARGET-seqF1.ab1"], circular=True)
    assert ref2 is _gb_seq and feats2 is _gb_feats and circ is True
    assert scope.ranges == [(60, 161)]
    assert scope.junctions == [(30, 90), (131, 191)]     # 两端 ±30
    assert scope.source == "cds_target" and scope.elements == ["TARGET"]
    assert scope.declared is True


def test_prepare_scope_mode_wrap_junctions():
    """跨原点 CDS 的接合区窗口按折回形态给出（接合区跟随真实拓扑）"""
    rng = random.Random(7)
    ref = "".join(rng.choice("ACGT") for _ in range(400))
    # CDS join(351..400, 1..50)：起点在终点之后 → 跨原点两段
    feats = [
        dict(name="Wrap", type="CDS", start=351, end=400, strand="+",
             feature_id="w1", segment_index=0, segment_count=2),
        dict(name="Wrap", type="CDS", start=1, end=50, strand="+",
             feature_id="w1", segment_index=1, segment_count=2),
    ]
    _r, _f, scope, _c = cds_extract.prepare_scope_mode(
        ref, feats, "ref", ["w.ab1"], "Wrap", circular=True)
    assert scope.ranges == [(1, 50), (351, 400)]     # normalized 排序
    # 接合区：段端窗口 (321,381)/(370,430)→wrap、(371,30)→wrap/(20,80)——
    # 折回拆段后与相邻窗口重叠，归并成穿过原点的单一接合区
    assert scope.junctions == [(1, 80), (321, 400)]


def test_prepare_scope_mode_crop_window():
    """超 max_ref_bp：环状绕回取窗、features 平移、目标 CDS 带标记、
    拓扑转线性（W8 裁剪路径）"""
    rng = random.Random(11)
    ref = "".join(rng.choice("ACGT") for _ in range(4000))
    feats = [
        dict(name="Other", type="CDS", start=500, end=700, strand="+"),
        dict(name="Big", type="CDS", start=1800, end=2000, strand="+"),
        dict(name="FarAway", type="gene", start=3800, end=3900, strand="+"),
    ]
    ref2, feats2, scope, circ = cds_extract.prepare_scope_mode(
        ref, feats, "ref", ["b.ab1"], "Big", circular=True, max_ref_bp=3000)
    assert circ is False
    # flank=1500 → 窗口 [300, 3500]，长 3201
    assert len(ref2) == 3201 and ref2 == ref[299:3500]
    by = {f["name"]: f for f in feats2}
    assert "FarAway" not in by                       # 窗口外注释丢弃
    t = by["Big"]
    assert (t["start"], t["end"]) == (1501, 1701)    # 平移（-299）
    assert t.get("analysis_target") is True
    o = by["Other"]
    assert (o["start"], o["end"]) == (201, 401)      # 窗口内注释保留（P1-F）
    assert scope.ranges == [(1501, 1701)]
    assert scope.junctions == [(1471, 1531), (1671, 1731)]
    assert "已裁剪取窗" in scope.reason


def test_prepare_scope_mode_crop_cross_origin():
    """跨原点 CDS 的裁剪窗口穿过原点（等价「旋转使 CDS 连续」），
    两段在窗口内连续"""
    rng = random.Random(13)
    ref = "".join(rng.choice("ACGT") for _ in range(4000))
    feats = [
        dict(name="Cross", type="CDS", start=3901, end=4000, strand="+",
             feature_id="c1", segment_index=0, segment_count=2),
        dict(name="Cross", type="CDS", start=1, end=100, strand="+",
             feature_id="c1", segment_index=1, segment_count=2),
    ]
    ref2, feats2, scope, circ = cds_extract.prepare_scope_mode(
        ref, feats, "ref", ["c.ab1"], "Cross", circular=True, max_ref_bp=3000)
    assert circ is False
    # 窗口起点 3901-1500=2401，跨原点 span=3200：2401..4000 + 1..1600
    assert len(ref2) == 3200
    assert ref2 == ref[2400:] + ref[:1600]
    assert scope.ranges == [(1501, 1700)]            # 两段窗口内连续
    tgts = [f for f in feats2 if f.get("analysis_target")]
    assert len(tgts) == 2 and {f["start"] for f in tgts} == {1501, 1601}


# ==================== 提取 ====================


def test_extract_plus_strand_with_flanks():
    """正链 CDS：提取 = CDS ± 150bp 侧翼；伪 CDS 特征区间即目标 CDS，
    5′/3′ 侧翼以 misc_feature 标注（TARGET 60-161 → 区段 1-311）"""
    ext, feats = cds_extract.resolve_reference(_gb_seq, _gb_feats, "TARGET", [], "TARGET")
    assert ext == SEQ[:311]
    assert [(f["name"], f["start"], f["end"], f["type"]) for f in feats] == [
        ("5′侧翼", 1, 59, "misc_feature"),
        ("TARGET", 60, 161, "CDS"),
        ("3′侧翼", 162, 311, "misc_feature"),
    ]
    assert feats[1]["strand"] == "+"
    assert "侧翼" in feats[1]["description"]


def test_extract_minus_strand_revcomp():
    """负链 CDS：整段（含侧翼）反向互补成编码方向，CDS 区间随之换算
    （MINUS 170-190 → 区段 20-340 的 revcomp，CDS 落在 151-171）"""
    ext, feats = cds_extract.resolve_reference(_gb_seq, _gb_feats, "", [], "MINUS")
    assert ext == revcomp(SEQ[19:340])
    cds_feat = next(f for f in feats if f["type"] == "CDS")
    assert (cds_feat["name"], cds_feat["start"], cds_feat["end"], cds_feat["strand"]) == \
        ("MINUS", 151, 171, "+")
    # CDS 区段截取 + 再反补应还原参考原序（编码方向切片 = 原参考正链切片的 revcomp）
    assert revcomp(ext[150:171]) == SEQ[169:190]


def test_extract_explicit_unknown_and_ambiguous_errors():
    with pytest.raises(cds_extract.CdsResolveError) as e:
        cds_extract.resolve_reference(_gb_seq, _gb_feats, "", [], "不存在")
    assert len(e.value.candidates) == 3
    with pytest.raises(cds_extract.CdsResolveError) as e2:
        cds_extract.resolve_reference(_gb_seq, _gb_feats, "随便", ["M13F.ab1"])
    assert len(e2.value.candidates) == 3


def test_extract_empty_features():
    with pytest.raises(cds_extract.CdsResolveError):
        cds_extract.resolve_reference(_gb_seq, [], "TARGET", [])


# ==================== 端点集成 ====================


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _all_features_open(monkeypatch):
    """端点门控读站点设置——打桩为默认全开放（与 test_sequencing_routes 同款）"""
    from app import site_settings
    from app.features import ALL_FEATURES as _ALL

    state = {"registration_open": True, "email_verification_required": False,
             "anonymous_features": list(_ALL), "user_features": list(_ALL)}
    monkeypatch.setattr(site_settings, "get_settings",
                        lambda force_refresh=False: json.loads(json.dumps(state)))


def _ref_file():
    return ("ref.gb", _gb(), "application/octet-stream")


def test_parse_reference_endpoint(client):
    resp = client.post(
        "/api/sequencing/parse-reference",
        files={"reference": _ref_file()},
        data={"read_names": ["S99675-TARGET-seqF1.ab1", "M13F-75.ab1"]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    names = [c["name"] for c in data["candidates"]]
    assert "TARGET" in names and "NESTED" not in names
    assert data["reference_length"] == 600
    assert data["circular"] is True  # 参考图谱拓扑（CDS 模式分析时才换线性）
    assert data["auto_match"]["name"] == "TARGET"
    assert data["auto_match"]["note"]


def test_parse_reference_endpoint_no_match(client):
    resp = client.post(
        "/api/sequencing/parse-reference",
        files={"reference": _ref_file()},
        data={"read_names": ["M13F-75.ab1"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["auto_match"] is None
    assert data["note"]


def test_analyze_cds_mode(client):
    """cds_mode（W8 全质粒口径）：参考不再裁剪、全注释保留，变体坐标即
    质粒坐标，scope = 目标 CDS + 接合区（source=cds_target）"""
    read = list(SEQ[59:161])
    read[30] = "A" if read[30] != "A" else "G"
    blob = make_ab1("".join(read), [40] * 102)
    resp = client.post(
        "/api/sequencing/analyze",
        files={"reference": _ref_file(),
               "reads": ("r1-TARGET-seqF1.ab1", blob, "application/octet-stream")},
        data={"cds_mode": "true"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reference_length"] == 600      # W8：不再裁参考
    names = {f["name"] for f in data["features"]}
    assert names == {"KanR", "TARGET", "NESTED", "MINUS"}   # 全注释保留（P1-F）
    # 变体质粒坐标落在目标 CDS 区间内且被 cds_reports 覆盖
    assert len(data["variants"]) == 1
    assert 60 <= data["variants"][0]["ref_pos"] <= 161
    assert "TARGET" in [c["name"] for c in data["cds_reports"]]
    # scope：目标 CDS + 接合区（两端 ±30，W8）
    sc = data["verdict"]["scope"]
    assert sc["declared"] is True and sc["source"] == "cds_target"
    assert sc["ranges"] == [[60, 161]]
    assert [30, 90] in sc["junctions"] and [131, 191] in sc["junctions"]
    # read 只覆盖 CDS、接合区两侧在需测范围内未测 → 缺口归因加引物，
    # 且确证替换 → fail
    vd = data["verdict"]
    assert vd["status"] == "fail"
    kinds = {b["kind"] for b in vd["blocking"]}
    assert "scope_beyond_primer" in kinds


def test_analyze_cds_mode_explicit_name_and_400(client):
    blob = make_ab1(SEQ[59:161], [40] * 102)
    # 显式指定存在名称 → 正常（W8：全质粒参考，不再裁剪）
    resp = client.post(
        "/api/sequencing/analyze",
        files={"reference": _ref_file(),
               "reads": ("r1.ab1", blob, "application/octet-stream")},
        data={"cds_mode": "true", "cds_name": "TARGET"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["reference_length"] == 600
    # 显式指定不存在名称 → 400 带候选清单
    resp = client.post(
        "/api/sequencing/analyze",
        files={"reference": _ref_file(),
               "reads": ("r1.ab1", blob, "application/octet-stream")},
        data={"cds_mode": "true", "cds_name": "不存在"},
    )
    assert resp.status_code == 400
    assert "可选 CDS" in resp.json()["detail"]


def test_analyze_cds_mode_off_unchanged(client):
    """开关关闭（默认）：行为与旧模式一致——参考仍是全质粒"""
    blob = make_ab1(SEQ[59:161], [40] * 102)
    resp = client.post(
        "/api/sequencing/analyze",
        files={"reference": _ref_file(),
               "reads": ("r1.ab1", blob, "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reference_length"] == 600
    assert {f["name"] for f in data["features"]} >= {"TARGET", "KanR"}


# ==================== 结论覆盖资格门 ====================


def _rpt(**over):
    base = {"name": "TARGET", "coverage_status": "full", "covered_percent": 100.0,
            "protein_identical": True}
    base.update(over)
    return base


def test_conclusion_target_uncovered_downgrades():
    """引物没覆盖到目的 CDS：结论头 △ 未能验证，不给 ✓（覆盖资格门）"""
    from core.sanger.pipeline import _conclusion_summary
    out = _conclusion_summary([], [_rpt(coverage_status="uncovered",
                                        covered_percent=0, protein_identical=None,
                                        analysis_target=True)], has_mixed=False)
    assert out.startswith("△") and "✓" not in out
    assert "未被任何 read 覆盖" in out and "未能验证" in out
    assert "已比对区域与设计一致" in out
    # 低置信变体（T1 未决）存在时同样不给 ✓
    out2 = _conclusion_summary([{"confidence": "low"}],
                               [_rpt(coverage_status="uncovered", covered_percent=0,
                                     protein_identical=None, analysis_target=True)],
                               has_mixed=False)
    assert out2.startswith("△") and "未决" in out2


def test_conclusion_target_uncovered_with_confirmed_diff():
    """目标零覆盖但侧翼有确认差异：✗ + 未能验证提示"""
    from core.sanger.pipeline import _conclusion_summary
    out = _conclusion_summary([{"confidence": "high"}],
                              [_rpt(coverage_status="uncovered", covered_percent=0,
                                    protein_identical=None, analysis_target=True)],
                              has_mixed=False)
    assert "✗" in out and "确认差异" in out and "未能验证" in out


def test_conclusion_target_partial_qualifies():
    """部分覆盖：✓ 保留但强制追加覆盖提示"""
    from core.sanger.pipeline import _conclusion_summary
    out = _conclusion_summary([], [_rpt(coverage_status="partial",
                                        covered_percent=42.4,
                                        analysis_target=True)], has_mixed=False)
    assert "✓" in out and "覆盖 42%" in out and "未验证" in out


def test_conclusion_target_full_unchanged():
    """目的基因模式完整覆盖：结论头用定稿措辞「目的基因测序正确」
    （2026-11-05；全质粒模式无标记仍为「构建与设计一致」）"""
    from core.sanger.pipeline import _conclusion_summary
    assert _conclusion_summary([], [_rpt(analysis_target=True)], False) == \
        "✓ 结论：目的基因测序正确，未检出差异"


def test_conclusion_no_marker_unchanged():
    """全质粒模式（无 analysis_target 标记）：所有分支文案逐字不变"""
    from core.sanger.pipeline import _conclusion_summary
    assert _conclusion_summary([], [], False) == "✓ 结论：构建与设计一致，未检出差异"
    assert _conclusion_summary([], [], True) == \
        "△ 结论：疑似混合样品（部分位点双峰），主峰序列按多数碱基判读——建议重新挑单克隆复测"
    assert _conclusion_summary([{"confidence": "high"}], [], False) == \
        "✗ 结论：检出 1 处确认差异"


def test_analyze_cds_mode_reads_miss_target(client):
    """端到端：read 全部落在目的 CDS 之外 → 结论不 ✓，目标 CDS 零覆盖，
    缺口按 E 归因加引物（W8：接合区属需测范围，CDS 未测 → partial）"""
    blob = make_ab1(SEQ[164:260], [40] * 96)
    resp = client.post(
        "/api/sequencing/analyze",
        files={"reference": _ref_file(),
               "reads": ("r1-other.ab1", blob, "application/octet-stream")},
        data={"cds_mode": "true", "cds_name": "TARGET"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "✓" not in data["conclusion"]
    vd = data["verdict"]
    # partial（纯缺口）或 pending（本 fixture 的 21bp MINUS CDS 会触发
    # ORF 护栏位点、落在接合区窗口判 junction_variant）——都不是 ✓
    assert vd["status"] in ("partial", "pending")
    kinds = {b["kind"] for b in vd["blocking"]}
    assert "scope_beyond_primer" in kinds
    target = next(c for c in data["cds_reports"] if c["name"] == "TARGET")
    assert target["coverage_status"] == "uncovered"


# ==================== W10 范围输入 ====================


def test_analyze_form_scope_user_source(client):
    """W10：Form scope 声明 → verdict.scope source=user（user > cds_target）"""
    blob = make_ab1(SEQ[59:161], [40] * 102)
    resp = client.post(
        "/api/sequencing/analyze",
        files={"reference": _ref_file(),
               "reads": ("r1.ab1", blob, "application/octet-stream")},
        data={"scope": "TARGET"},
    )
    assert resp.status_code == 200, resp.text
    sc = resp.json()["verdict"]["scope"]
    assert sc["source"] == "user" and sc["declared"] is True
    assert sc["ranges"] == [[60, 161]]


def test_analyze_form_scope_parse_error_400(client):
    blob = make_ab1(SEQ[59:161], [40] * 102)
    resp = client.post(
        "/api/sequencing/analyze",
        files={"reference": _ref_file(),
               "reads": ("r1.ab1", blob, "application/octet-stream")},
        data={"scope": "乱写的范围"},
    )
    assert resp.status_code == 400
    assert "无法识别" in resp.json()["detail"]


def test_parse_reference_scope_presets(client):
    """W10（PL §C4）：parse-reference 返回范围预设与默认项（附 reason）"""
    resp = client.post(
        "/api/sequencing/parse-reference",
        files={"reference": _ref_file()},
        data={"read_names": ["r1-TARGET-seqF1.ab1"]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    values = [p["value"] for p in data["scope_presets"]]
    assert "全质粒" in values and "TARGET" in values
    dp = data["default_preset"]
    assert dp["value"] == "TARGET" and dp["reason"]
