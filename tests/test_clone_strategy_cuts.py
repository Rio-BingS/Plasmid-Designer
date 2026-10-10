"""限制性酶切克隆方案的真实切点与元件破坏告警（终审 A-09）。

此前方案步骤只有「酶切」没有坐标——用户无法核对酶在载体的哪个位置切；
切点落在载体必需元件（CDS/复制起点/抗性/启动子）内部时也不告警，
用户要自己在图谱里找。现在：
- 酶切载体步骤的中文描述带真实切割位置（find_enzyme_sites 扫描）
- 切点破坏必需元件 → warnings 明确点名
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "src" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.clone_strategy import RestrictionCloningStrategy  # noqa: E402


def _vector_with_essential_under_cut() -> str:
    """载体：EcoRI 识别序列 GAATTC 放在一段「抗性基因」区间正中。

    GAATTC 切点（cut_fwd）落在识别序列内部第 3 位后——即落在
    60-100 的抗性基因区间内。
    """
    return ("A" * 30 + "GAATTC" + "A" * 24     # 1-60：识别序列在 31-36
            + "C" * 100)                        # 61-160


def test_digest_step_describes_real_cut_positions():
    """酶切载体步骤带真实切割坐标与识别序列位置"""
    vector_seq = "A" * 30 + "GAATTC" + "A" * 24 + "CTCGAG" + "C" * 94
    s = RestrictionCloningStrategy().generate(
        "ATGAAAGGTTAG", "ins", vector_seq, "testV", "EcoRI", "XhoI")
    digest_step = next(st for st in s.steps if st.action == "Digest vector")
    assert "bp 处切割" in digest_step.description_zh
    assert "识别序列" in digest_step.description_zh
    # find_enzyme_sites 实测：EcoRI GAATTC@31 切在 31 bp，XhoI CTCGAG@61 切在 61 bp
    assert "31 bp" in digest_step.description_zh
    assert "61 bp" in digest_step.description_zh
    assert "GAATTC" in digest_step.description_zh and "CTCGAG" in digest_step.description_zh


def test_broken_essential_element_warned():
    """切点落在抗性基因内部 → 告警点名「该元件会被切断」"""
    vector_seq = "A" * 30 + "GAATTC" + "A" * 24 + "C" * 100   # EcoRI cut_fwd=33
    features = [{"name": "AmpR", "type": "resistance", "start": 20, "end": 60}]
    s = RestrictionCloningStrategy().generate(
        "ATGAAAGGTTAG", "ins", vector_seq, "testV", "EcoRI", "XhoI",
        vector_features=features)
    assert any("会被切断" in w for w in s.warnings_zh), s.warnings_zh
    assert any("AmpR" in w for w in s.warnings_zh)
    assert any("20-60" in w for w in s.warnings_zh)


def test_safe_cut_no_broken_warning():
    """切点不落在任何元件内 → 无破坏告警（只有三条常规提示）"""
    vector_seq = "A" * 30 + "GAATTC" + "A" * 24 + "C" * 100
    features = [{"name": "AmpR", "type": "resistance", "start": 80, "end": 120}]
    s = RestrictionCloningStrategy().generate(
        "ATGAAAGGTTAG", "ins", vector_seq, "testV", "EcoRI", "XhoI",
        vector_features=features)
    assert not any("会被切断" in w for w in s.warnings_zh)
    assert len(s.warnings_zh) == 3


def test_no_features_no_crash_and_enzyme_without_site_degrades():
    """无元件列表正常出方案；酶在载体无位点时描述降级为无坐标但不崩"""
    vector_seq = "A" * 100   # 无 EcoRI/XhoI 位点
    s = RestrictionCloningStrategy().generate(
        "ATGAAAGGTTAG", "ins", vector_seq, "testV", "EcoRI", "XhoI",
        vector_features=[{"name": "AmpR", "type": "resistance", "start": 10, "end": 20}])
    digest_step = next(st for st in s.steps if st.action == "Digest vector")
    assert "bp 处切割" not in digest_step.description_zh  # 无位点 → 无坐标
    assert not any("会被切断" in w for w in s.warnings_zh)
    assert s.expected_product_size == 100 + 12


def test_all_sites_per_enzyme_considered():
    """回归：每把酶只取第一个切点，落在必需元件内的第二个切点被漏报"""
    vector_seq = ("A" * 20 + "GAATTC" + "T" * 60 + "GAATTC" + "C" * 40
                  + "CTCGAG" + "G" * 20)
    features = [{"name": "AmpR", "type": "resistance", "start": 80, "end": 120}]
    s = RestrictionCloningStrategy().generate(
        "ATGAAAGGTTAG", "ins", vector_seq, "testV", "EcoRI", "XhoI",
        vector_features=features)
    # 第二个 EcoRI 切点（87 bp）落在 AmpR 内
    assert any("87 bp" in w and "AmpR" in w for w in s.warnings_zh), s.warnings_zh
    digest = next(st for st in s.steps if st.action == "Digest vector")
    assert "另有 1 个切点" in digest.description_zh and "87" in digest.description_zh
    # 回文位点正反链各命中一次，不得重复告警
    assert len([w for w in s.warnings_zh if "AmpR" in w]) == 1


def test_origin_wrapping_feature_is_checked():
    """回归：start>end 的跨原点元件此前被直接跳过，切断它不会告警"""
    vector_seq = "A" * 20 + "GAATTC" + "T" * 60 + "C" * 40 + "CTCGAG" + "G" * 20
    wrapped = [{"name": "ori", "type": "origin", "start": 140, "end": 25}]
    s = RestrictionCloningStrategy().generate(
        "ATGAAAGGTTAG", "ins", vector_seq, "testV", "EcoRI", "XhoI",
        vector_features=wrapped)
    # EcoRI 切点 21 bp 落在跨原点区间 140-146/1-25 内
    assert any("ori" in w and "跨原点" in w for w in s.warnings_zh), s.warnings_zh
    # 区间外的切点不误报
    outside = [{"name": "ori", "type": "origin", "start": 140, "end": 15}]
    s2 = RestrictionCloningStrategy().generate(
        "ATGAAAGGTTAG", "ins", vector_seq, "testV", "EcoRI", "XhoI",
        vector_features=outside)
    assert not any("ori" in w and "会被切断" in w for w in s2.warnings_zh)


def test_enzyme_missing_from_enzyme_table_still_gets_coordinates():
    """回归：只存在于 RESTRICTION_ENZYMES 的酶（PvuII 等）此前完全没有坐标"""
    from core.enzyme_sites import ENZYME_TABLE

    assert not any(e["name"] == "PvuII" for e in ENZYME_TABLE)
    vector_seq = "A" * 30 + "CAGCTG" + "T" * 40 + "CTCGAG" + "C" * 30
    s = RestrictionCloningStrategy().generate(
        "ATGAAAGGTTAG", "ins", vector_seq, "testV", "PvuII", "XhoI")
    digest = next(st for st in s.steps if st.action == "Digest vector")
    assert "CAGCTG" in digest.description_zh
    assert "33 bp 处切割" in digest.description_zh  # 平末端 CAG^CTG，与 EcoRI 同一坐标口径


def test_feature_label_matches_type_not_name_substring():
    """回归：关键词子串匹配把 "lacI repressor" 标成复制起点（"rep"）"""
    vector_seq = "A" * 20 + "GAATTC" + "T" * 60 + "C" * 40 + "CTCGAG" + "G" * 20
    features = [{"name": "lacI repressor", "type": "misc_feature",
                 "start": 10, "end": 40}]
    s = RestrictionCloningStrategy().generate(
        "ATGAAAGGTTAG", "ins", vector_seq, "testV", "EcoRI", "XhoI",
        vector_features=features)
    hit = next(w for w in s.warnings_zh if "lacI repressor" in w)
    assert "复制起点" not in hit
    assert "注释元件" in hit

    # 真正的复制起点（type=origin）仍要正确标注
    s2 = RestrictionCloningStrategy().generate(
        "ATGAAAGGTTAG", "ins", vector_seq, "testV", "EcoRI", "XhoI",
        vector_features=[{"name": "pUC ori", "type": "origin",
                          "start": 10, "end": 40}])
    assert any("复制起点" in w for w in s2.warnings_zh)
