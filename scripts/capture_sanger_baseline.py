"""MX 真实数据基线取证（goal_sanger_full_fix_v2 纪律：先取证、再改口径）

用法：
    python scripts/capture_sanger_baseline.py work/baseline_before_T1.json

对 `plasmid-designer V1.01/sequencing results` 的真实 MX 批次按
test_real_data_regression 同款归组跑全管线，把「口径可变的一切结论形状」
（结论文本、变体清单、双峰/互检、覆盖、CDS 判读、区间裁决、poly 判读）
逐项转储成 JSON。改口径前跑一次保存，改完再跑一次，diff 逐条解释。

输出文件不入库（work/ 在 .gitignore）。数据目录不在场时报错退出（CI 无感）。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/backend"))
sys.path.insert(0, str(ROOT / "tests"))

REAL_DATA_DIR = ROOT.parent / "plasmid-designer V1.01" / "sequencing results"


def capture() -> dict:
    from core.sanger.batch import load_excel, match_files, norm_stem
    from core.sanger.pipeline import analyze
    from core.sanger.reference_parser import parse_reference

    excel_bytes = (REAL_DATA_DIR / "测序.xlsx").read_bytes()
    _wb, _header, _cols, rows = load_excel(excel_bytes)
    files = []
    for p in sorted(REAL_DATA_DIR.iterdir()):
        if p.suffix.lower() in (".ab1", ".dna", ".gb", ".gbk", ".fasta", ".fa"):
            files.append({"path": p, "name": p.name, "stem": norm_stem(p.stem),
                          "ext": p.suffix.lower(), "bytes": p.read_bytes()})
    per_plasmid, unmatched = match_files(rows, files)
    out = {"data_dir": str(REAL_DATA_DIR), "plasmids": {}, "unmatched": unmatched}
    for plasmid, slot in sorted(per_plasmid.items()):
        ref_item = slot["reference"]
        if ref_item is None or not slot["reads"]:
            continue
        ref_seq, features = parse_reference(
            ref_item["file"]["name"], ref_item["file"]["bytes"])
        ab1s = [(r["file"]["name"], r["file"]["bytes"]) for r in slot["reads"]]
        res = analyze(ab1s, ref_seq, features)
        out["plasmids"][plasmid] = {
            "ref_len": len(ref_seq),
            "n_features": len(features),
            "reads": [
                {
                    "filename": r["filename"],
                    "mean_q": r.get("mean_q"),
                    "direction": (r.get("alignment") or {}).get("direction"),
                    "ref_start": (r.get("alignment") or {}).get("ref_start"),
                    "ref_end": (r.get("alignment") or {}).get("ref_end"),
                    "identity": (r.get("alignment") or {}).get("identity"),
                    "partial_alignment": r.get("partial_alignment"),
                    "reliable_zone": {k: r["reliable_zone"][k]
                                      for k in ("start", "end", "raw_start", "raw_end")
                                      if k in (r.get("reliable_zone") or {})},
                    "mixed_class": (r.get("mixed_profile") or {}).get("class"),
                    "mixed_count": (r.get("mixed_profile") or {}).get("count"),
                    "mixed_positions": r.get("mixed_positions"),
                    "mixed_detail": r.get("mixed_detail"),
                    "slippage_positions": r.get("slippage_positions"),
                    "post_poly_dropouts": r.get("post_poly_dropouts"),
                    "poly_merged_zones": r.get("poly_merged_zones"),
                }
                for r in res["reads"]
            ],
            "errors": res.get("errors"),
            "variants": [
                {k: v.get(k) for k in (
                    "ref_pos", "type", "ref_base", "alt_base", "length",
                    "confidence", "support_reads", "read", "read_pos", "read_q",
                    "low_reason", "cross_read_resolved", "mutant_pct", "snr",
                    "features", "aa_change", "quality")}
                for v in res["variants"]
            ],
            "conclusion": res.get("conclusion"),
            "coverage_percent": (res.get("consensus") or {}).get("coverage_percent"),
            "consensus_len": len((res.get("consensus") or {}).get("sequence") or ""),
            "consensus_diffs": (res.get("consensus") or {}).get("diffs"),
            "mixed_corroboration": res.get("mixed_corroboration"),
            "resolved_regions": res.get("resolved_regions"),
            "cds_reports": [
                {k: c.get(k) for k in (
                    "name", "verdict", "coverage_status", "covered_percent",
                    "protein_identical", "analysis_target", "pending_low_confidence",
                    "n_in_cds" if "n_in_cds" in c else "pending_impact")}
                for c in res.get("cds_reports", [])
            ],
            "homopolymer_runs": [
                {k: e.get(k) for k in (
                    "base", "start", "end", "tier", "count_reliable", "run_verdict",
                    "peak_verdict", "observed_units")}
                for e in res.get("homopolymers", [])
            ],
        }
    return out


def main() -> int:
    if not REAL_DATA_DIR.is_dir() or not any(REAL_DATA_DIR.glob("*.ab1")):
        print(f"真实数据不在场（{REAL_DATA_DIR}），不取证", file=sys.stderr)
        return 1
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "work" / "baseline_before.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = capture()
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    n_plasmids = len(data["plasmids"])
    print(f"已写入 {dest}（{n_plasmids} 个质粒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
