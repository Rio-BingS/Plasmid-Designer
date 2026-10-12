"""Generate segment_order.json: features parsed by the backend GenBank parser
plus backend extract_feature output, so frontend translateFeature/extractFeature
can be checked against the same contract (segments in biological order).

Run from repo root (backend venv):
  python src/frontend/tests/fixtures/map/generate_segment_order.py
"""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "src" / "backend"))

from Bio.Seq import Seq  # noqa: E402

from core.plasmid_map import parse_text  # noqa: E402
from core.plasmid_map.parsers import extract_feature  # noqa: E402

OUT = Path(__file__).resolve().parent / "segment_order.json"
L = 1000
_rng = random.Random(12)
SEQ = "".join(_rng.choice("ACGT") for _ in range(L))

LOCATIONS = [
    ("minus-join-1-100-900-1000", "complement(join(1..100,900..1000))"),
    ("minus-wrap", "complement(join(901..1000,1..99))"),
    ("plus-wrap", "join(901..1000,1..99)"),
    ("minus-join", "complement(join(11..50,101..199))"),
    ("plus-join", "join(11..50,101..199)"),
    ("minus-join-wrap", "complement(join(801..900,951..1000,1..50))"),
    ("plus-join-wrap", "join(801..900,951..1000,1..50)"),
]


def genbank(topology: str) -> str:
    feats = "".join(
        f"     CDS             {loc}\n                     /label=\"{name}\"\n"
        for name, loc in LOCATIONS)
    rows = []
    for i in range(0, L, 60):
        chunk = SEQ[i:i + 60]
        rows.append(f"{i + 1:>9} " + " ".join(chunk[j:j + 10] for j in range(0, 60, 10)).strip())
    return (f"LOCUS       segorder    {L} bp    DNA     {topology}   SYN 01-JAN-2026\n"
            "FEATURES             Location/Qualifiers\n"
            f"{feats}ORIGIN\n" + "\n".join(rows) + "\n//\n")


def main() -> None:
    out = {"sequence": SEQ, "cases": []}
    rec = parse_text(genbank("circular"))
    assert rec.sequence == SEQ
    for (name, loc), f in zip(LOCATIONS, rec.features):
        dna = extract_feature(rec.sequence, f)
        protein = str(Seq(dna[: len(dna) // 3 * 3]).translate())
        feat = {k: f[k] for k in ("start", "end", "strand")}
        if f.get("segments"):
            feat["segments"] = [{"start": s["start"], "end": s["end"]} for s in f["segments"]]
        out["cases"].append({"name": name, "location": loc, "feature": feat,
                             "extract": dna, "protein": protein})
    OUT.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
