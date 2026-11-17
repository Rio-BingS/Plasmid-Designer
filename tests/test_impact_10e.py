"""二期 10e：元件补全、设计酶集合（方案 C）、影响分级、导出酶标注读回"""
import os

from core.sanger.elements import category_of, complete_elements, infer_host
from core.sanger.design_enzymes import build_design_enzymes, avoid_from_comment
from core.sanger.impact import build_impact_items
from core.sanger.render_impact import impact_lines, with_impact_lines


def test_category_promoter_not_resistance():
    assert category_of("promoter", "AmpR promoter") == "promoter"
    assert category_of("CDS", "AmpR") == "resistance"
    assert category_of("rep_origin", "pUC ori") == "ori"
    assert category_of("gene", "30-100A to 106A") == "other"
    assert category_of("misc_feature", "6xHis") == "tag"


def test_host_inference():
    p = [{"category": "promoter", "name": "T7 promoter"}]
    e = [{"category": "promoter", "name": "CMV promoter"}]
    assert infer_host(p) == "prokaryotic"
    assert infer_host(e) == "eukaryotic"
    assert infer_host(p + e) == "unknown"


def test_inferred_sd_window_capped(monkeypatch):
    monkeypatch.setenv("SANGER_ELEMENTS_AUTO", "0")
    ref = "A" * 300 + "ATG" + "GCT" * 30 + "TAA" + "A" * 100
    feats = [{"name": "T7 promoter", "type": "promoter", "start": 200, "end": 219},
             {"name": "geneX", "type": "CDS", "start": 301, "end": 396, "strand": "+"}]
    out = complete_elements(ref, feats, False)
    assert out["host"] == "prokaryotic"
    sd = [e for e in out["elements"] if e["category"] == "rbs"]
    assert sd and sd[0]["start"] == 286 and sd[0]["end"] == 296 and sd[0]["capped"]


def test_design_enzymes_levels():
    ref = "TTTT" * 50 + "GAATTC" + "TTTT" * 50 + "GGATCC" + "TTTT" * 50
    d = build_design_enzymes(ref, [], False, None, user_enzymes="ecori")
    assert d["level"] == "user" and d["enzymes"][0]["name"] == "EcoRI"
    d = build_design_enzymes(ref, [], False, [(210, 400)])
    assert d["level"] == "flank" and {e["name"] for e in d["enzymes"]} >= {"EcoRI", "BamHI"}
    d = build_design_enzymes(ref, [], False, None)
    assert d["level"] == "plasmid"
    assert avoid_from_comment("pd_design_enzymes: avoid=BsaI, ecori") == ["BsaI", "EcoRI"]


def _el(name, cat, s, e, src="annotated", capped=False):
    return {"name": name, "category": cat, "start": s, "end": e, "source": src,
            "capped": capped, "importance": 0}


def test_impact_levels_and_merge():
    els = [_el("AmpR", "resistance", 100, 900), _el("ori", "ori", 1000, 1600, "auto", True)]
    vs = [{"ref_pos": 200, "type": "substitution", "status": "confirmed", "aa_change": "AmpR:Q10*"},
          {"ref_pos": 300, "type": "substitution", "status": "unresolved"},
          {"ref_pos": 1200, "type": "substitution", "status": "confirmed"},
          {"ref_pos": 2000, "type": "substitution", "status": "confirmed"},
          {"ref_pos": 2100, "type": "substitution", "status": "rejected"}]
    items = build_impact_items(vs, els, None)
    by = {i["target"]["name"]: i for i in items}
    assert by["AmpR"]["level"] == "P1" and by["AmpR"]["certainty"] == "confirmed"
    assert by["AmpR"]["site_ids"] == [200, 300]
    assert by["ori"]["level"] == "P2" and by["ori"]["rule"] == "IMP_CAPPED" and by["ori"]["cap_applied"]
    assert by["骨架"]["level"] == "P3"
    assert len(items) == 3


def test_impact_enzyme_destroyed_and_created():
    design = {"enzymes": [{"name": "EcoRI", "sites": [50]}], "avoid": ["BsaI"]}
    vs = [{"ref_pos": 52, "type": "substitution", "status": "confirmed",
           "enzyme_sites_lost": ["EcoRI"], "enzyme_sites_gained": ["BsaI", "DraI"]}]
    items = build_impact_items(vs, [], design)
    rules = {i["target"]["name"]: i["rule"] for i in items}
    assert rules == {"EcoRI": "IMP_ENZYME_DESTROYED", "BsaI": "IMP_ENZYME_CREATED"}


def test_render_and_rejudge_replace():
    v = {"impact": {"items": [{"item_id": "a", "target": {"kind": "element", "name": "AmpR",
                                                         "category": "resistance"},
                               "source": "annotated", "coverage": "partial",
                               "consequences": [{"ref_pos": 200, "status": "confirmed",
                                                 "consequence": "无义"}],
                               "level": "P1", "rule": "IMP_RESISTANCE", "cap_applied": False,
                               "certainty": "confirmed"}],
                     "shown": ["a"], "advisories": ["a"], "full_ok": 2, "unassessed": []}}
    lines = impact_lines(v)
    assert lines[0].startswith("⚠ 范围外发现 1 处")
    assert "部分覆盖" in lines[1] and lines[2] == "注意：另有 2 个元件完整覆盖，未见异常"
    again = with_impact_lines({"impact": None}, ["x"] + lines)
    assert again == ["x"]


def test_export_writes_enzyme_annotations_and_reads_back():
    from core.export_formats import GenBankExporter, create_export_data_from_design
    from core.sanger.reference_parser import parse_reference_meta, parse_reference_topology
    seq = "ATG" + "TTTT" * 20 + "GAATTC" + "TTTT" * 20 + "TAA"
    data = create_export_data_from_design({
        "design_id": "d1", "optimized_sequence": seq, "cloning_enzymes": ["EcoRI"],
        "cloning_method": "restriction", "exclude_enzymes": ["BsaI"]})
    gb = GenBankExporter.export(data)
    assert 'pd_enzyme role=cloning' in gb and "pd_design_enzymes: avoid=BsaI" in gb
    s, feats, _c = parse_reference_topology("x.gb", gb.encode())
    meta = parse_reference_meta("x.gb", gb.encode())
    d = build_design_enzymes(s, feats, True, None, comment=meta.get("design_comment"))
    assert d["level"] == "user" and d["enzymes"][0]["name"] == "EcoRI"
    assert d["avoid"] == ["BsaI"]
