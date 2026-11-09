"""T22 证据包（goal_sanger_full_fix_v2 T22；A 5.4）

blocking 项与 review_trace/resequence 变体 → 逐 read ±10 峰四通道 SVG 小图
+ 调用/Q/峰比/P(真实) 表与判定理由；JSON（前端面板）与 Markdown（下载）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/backend"))

from core.sanger.evidence_pack import (  # noqa: E402
    build_pack_data, collect_sites, render_markdown, render_site_svg,
)


def _trace_entry():
    """make_trace_ab1 → _trace_data 形态 {bases, channels, peak_indices,
    trim_start}（q50 无修剪，trim_start=0）"""
    from sanger_scenarios import _read_from_ref
    from core.sanger.abif_reader import extract_read
    import random
    rng = random.Random("t22")
    ref = "".join(rng.choice("ACGT") for _ in range(600))
    blob = _read_from_ref(ref, 1, 500, q=50)
    read = extract_read(blob)
    return {"bases": read["bases"], "channels": read["trace"],
            "peak_indices": read["peak_indices"], "trim_start": 0}


def test_render_site_svg_structure():
    te = _trace_entry()
    svg = render_site_svg(te, 100)
    assert svg and svg.startswith("<svg") and svg.endswith("</svg>")
    assert svg.count("<polyline") == 4          # 四通道
    assert 'fill="#D0342C"' in svg              # 位点高亮带
    assert "<text" in svg                        # 碱基标注
    # 越界 / 无数据 → None
    assert render_site_svg(te, 0) is None
    assert render_site_svg(te, 10 ** 6) is None
    assert render_site_svg({"bases": "", "channels": {}, "peak_indices": [],
                            "trim_start": 0}, 1) is None


def test_collect_sites_dedupe_and_cap():
    record = {
        "verdict": {"blocking": [
            {"kind": "confirmed_variant", "ref_start": 500, "detail": "a"},
            {"kind": "confirmed_variant", "ref_start": 500, "detail": "a 复"},  # 去重
            {"kind": "unresolved_site", "ref_start": 300, "detail": "b"},
            {"kind": "read_failed", "ref_start": None, "detail": "无坐标跳过"},
        ]},
        "variants": [
            {"ref_pos": 500, "type": "substitution", "action": "accept"},
            {"ref_pos": 620, "type": "substitution", "action": "review_trace"},
            {"ref_pos": 640, "type": "substitution", "action": "resequence"},
        ],
    }
    sites = collect_sites(record)
    poss = [(s["ref_pos"], s["kind"]) for s in sites]
    assert poss == [(300, "unresolved_site"), (500, "confirmed_variant"),
                    (620, "variant_review_trace"), (640, "variant_resequence")]
    # 上限截断
    big = {"verdict": {"blocking": [
        {"kind": "unresolved_site", "ref_start": i, "detail": "x"}
        for i in range(1, 200)]}, "variants": []}
    assert len(collect_sites(big)) == 60


def test_build_pack_and_markdown_e2e():
    """端到端：analyze 结果组装 record → 位点含逐 read 小图与证据表 →
    Markdown 内联 SVG 与表格"""
    import random
    from core.sanger.pipeline import analyze
    from sanger_scenarios import _read_from_ref
    rng = random.Random("t22e2e")
    ref = "".join(rng.choice("ACGT") for _ in range(600))
    blob = _read_from_ref(ref, 1, 500, q=50,
                          subs=[(300, "G" if ref[299] != "G" else "T")])
    res = analyze([("r1.ab1", blob)], ref, [], circular=False)
    record = {**res, "_trace_data": {i: t for i, t in enumerate(res["traces"])}}
    pack = build_pack_data(record)
    assert pack["verdict_status"] == res["verdict"]["status"]
    assert pack["sites"], "确证变体应出图"
    site = pack["sites"][0]
    assert site["ref_pos"] == 300
    rows = site["reads"]
    assert rows and rows[0]["read"] == "r1.ab1"
    assert rows[0]["call"] and rows[0]["q"] is not None
    assert rows[0]["svg"] and rows[0]["svg"].startswith("<svg")
    # W15（PL G2）：rows 带 tier 三档（与 variant confidence 同源），
    # p_real 数值仍保留（内部排序用），对外展示一律三档
    assert rows[0].get("tier") in ("high", "medium", "low")
    vd_var = next(v for v in res["variants"] if v.get("ref_pos") == 300)
    assert rows[0]["tier"] == vd_var.get("confidence")
    md = render_markdown(pack)
    assert "## 1. 位点 300" in md
    assert "<svg" in md
    assert "| r1.ab1 |" in md
    assert "可信度" in md  # W15（PL G2）：p_real 不当概率，展示改三档
    assert "P(真实)" not in md
