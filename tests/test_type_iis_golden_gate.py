"""Type IIS 判定与 Golden Gate 候选酶（REBASE 数据驱动）"""

import pytest

from core.enzyme_sites import (
    get_enzyme, golden_gate_enzymes, golden_gate_geometry, is_type_iis,
    type_iis_enzymes,
)
from core.plasmid_map.enzymes import scan_enzyme
from core.primer_designer import PrimerDesigner
from core.seq_utils import revcomp


@pytest.mark.parametrize("name", [
    "BsaI", "BsmBI", "BbsI", "SapI", "BsmAI", "Esp3I", "PaqCI", "FokI",
    "MlyI",   # GAGTC(5/5)：平端但切在位点外
    "MmeI",   # 含简并碱基 TCCRAC(20/18)
    "MnlI",   # 4 碱基 CCTC(7/6)
])
def test_type_iis_positive(name):
    assert is_type_iis(get_enzyme(name))


@pytest.mark.parametrize("name", [
    "EcoRI", "BamHI", "NotI", "SmaI",
    "BglI",    # 回文且含 N：GCCNNNN^NGGC
    "BssSI",   # 非回文，但切点在位点内（C^ACGAG）
    "MspJI",   # 修饰依赖型（CNNR，确定碱基不足 4 个）
    "FspEI",
])
def test_type_iis_negative(name):
    assert not is_type_iis(get_enzyme(name))


def test_type_iis_cut_twice_and_unknown_are_excluded():
    assert get_enzyme("BaeI") is None  # 两侧双切（Type IIB）
    assert not is_type_iis(None)


def test_type_iis_rule_on_synthetic_specs():
    """规则本身：上游切也算；一条链切在位点内不算；恰在边界的平端不算"""
    assert is_type_iis({"site": "GGTCTC", "cut": (-8, -4)})
    assert not is_type_iis({"site": "GGTCTC", "cut": (-2, 3)})
    assert not is_type_iis({"site": "GGTCTC", "cut": (6, 6)})
    assert is_type_iis({"site": "GGTCTC", "cut": (6, 7)})


def test_type_iis_set_is_data_driven():
    names = type_iis_enzymes()
    assert len(names) > 50
    assert {"BsaI", "BsmBI", "BbsI", "SapI", "PaqCI", "Esp3I"} <= set(names)
    assert "EcoRI" not in names and "BssSI" not in names
    assert all(get_enzyme(n) and is_type_iis(get_enzyme(n)) for n in names)


def test_golden_gate_candidates_order_and_filter():
    names = golden_gate_enzymes()
    assert names[:3] == ("BsaI", "BsmBI", "BbsI")
    # 同切酶也在（Thermo 的 Eco31I/BpiI 等），SapI（3 nt）、MlyI（平端）不在
    assert {"Esp3I", "PaqCI", "BsmAI", "Eco31I", "BpiI", "AarI"} <= set(names)
    assert "SapI" not in names and "MlyI" not in names and "EcoRI" not in names
    rest = list(names[3:])
    assert rest == sorted(rest, key=str.lower)
    for n in names:
        geo = golden_gate_geometry(n)
        assert geo and geo["overhang_len"] == 4 and set(geo["site"]) <= set("ACGT")


def test_golden_gate_geometry_spacers():
    assert golden_gate_geometry("BsaI") == {"name": "BsaI", "site": "GGTCTC", "spacer": 1, "overhang_len": 4}
    assert golden_gate_geometry("BbsI")["spacer"] == 2
    assert golden_gate_geometry("PaqCI")["spacer"] == 4
    assert golden_gate_geometry("SapI") is None
    assert golden_gate_geometry("EcoRI") is None
    assert golden_gate_geometry("NoSuchEnzyme") is None


INSERT = "ATGGCTAGCAAAGGAGAAGAACTTTTCACTGGAGTTGTCCCAATTCTTGTTGAATTAGATGGTGATGTTAATGGGCACAAATTTTCTGTCTAA"


@pytest.mark.parametrize("enzyme", ["BsaI", "BsmBI", "BbsI", "Esp3I", "PaqCI", "BsmAI", "BtgZI", "AarI"])
def test_golden_gate_primers_release_requested_overhangs(enzyme):
    """把引物尾巴装回扩增产物再用同一把酶扫描：两端切下的必须正是请求的
    4 nt overhang。回归：间隔此前一律 1 个 A，BbsI（N2/N6）会错位一位"""
    oh5, oh3 = "AATG", "GCTT"
    pair = PrimerDesigner().design_golden_gate_primers(INSERT, enzyme, oh5, oh3)
    product = pair.forward.overhang + INSERT + revcomp(pair.reverse.overhang)
    if get_enzyme(enzyme)["site"] in INSERT or revcomp(get_enzyme(enzyme)["site"]) in INSERT:
        pytest.skip("插入片段自带该酶位点")
    res = scan_enzyme(product, enzyme, circular=False)
    overhangs = sorted(s["overhang"] for s in res["sites"])
    assert overhangs == sorted([oh5, oh3])


def test_golden_gate_unsupported_enzyme_message():
    with pytest.raises(ValueError, match="不支持"):
        PrimerDesigner().design_golden_gate_primers(INSERT, "SapI", "AATG", "GCTT")


def test_restriction_primers_reject_any_type_iis():
    """经典双酶切拒绝 Type IIS：名单之外的（如 FokI）也按 REBASE 判定拒绝"""
    with pytest.raises(ValueError, match="Type IIS"):
        PrimerDesigner().design_restriction_primers(INSERT, "FokI", "XhoI")


def test_design_request_validates_golden_gate_enzyme():
    from pydantic import ValidationError

    from app.routes.models import DesignRequest

    ok = DesignRequest(sequence=INSERT, cloning_method="golden_gate", enzyme="PaqCI")
    assert ok.enzyme == "PaqCI"
    with pytest.raises(ValidationError, match="不支持的 Golden Gate 酶"):
        DesignRequest(sequence=INSERT, cloning_method="golden_gate", enzyme="EcoRI")


def test_golden_gate_site_warning_uses_rebase():
    from app.design_service import _golden_gate_site_warning

    seq = "AAAA" + "CACCTGC" + "AAAAAAAAAAAA"
    assert "PaqCI" in _golden_gate_site_warning(seq, "PaqCI")
    assert _golden_gate_site_warning("A" * 30, "PaqCI") is None


def test_analysis_enzymes_lists_golden_gate_candidates():
    import asyncio

    from app.analysis_routes import list_enzymes
    from app.cache import cache

    cache.backend.clear_pattern("analysis_enzymes:*")
    data = asyncio.run(list_enzymes())
    gg = data["golden_gate_enzymes"]
    assert [e["name"] for e in gg[:3]] == ["BsaI", "BsmBI", "BbsI"]
    assert {"name": "PaqCI", "recognition_sequence": "CACCTGC"} in gg
    assert data["enzymes"]["BsaI"]["is_type_iis"] is True
    assert data["enzymes"]["EcoRI"]["is_type_iis"] is False
