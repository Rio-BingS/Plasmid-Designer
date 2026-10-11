"""Generate MapDocument (plasmid-map/1) fixtures from data/vectors/*.yaml.

Test/dev helper only: the real documents come from the backend
(/api/vectors/{id}/map). Enzyme sites are scanned here with Bio.Restriction
recognition data (DEFAULT_ENZYMES subset) so fixtures look like real output.

Run from repo root:
  python src/frontend/tests/fixtures/map/generate.py
"""
import json
import re
from pathlib import Path

import yaml
from Bio import Restriction
from Bio.Seq import Seq

ROOT = Path(__file__).resolve().parents[5]
OUT = Path(__file__).resolve().parent
VECTORS = ["pUC19", "pET-28a"]
ENZYMES = [
    "EcoRI", "BamHI", "HindIII", "NotI", "XhoI", "SalI", "PstI", "SmaI",
    "XbaI", "SpeI", "NheI", "KpnI", "SacI", "SphI", "NcoI", "NdeI", "NruI",
    "PvuII", "ScaI", "BsaI", "BsmBI", "BbsI", "EcoRV", "ApaI",
]
TYPE_MAP = {
    "CDS": "cds", "gene": "cds", "promoter": "promoter", "terminator": "terminator",
    "origin": "rep_origin", "rep_origin": "rep_origin", "resistance": "marker",
    "tag": "tag", "multiple_cloning_site": "mcs", "MCS": "mcs", "RBS": "rbs",
    "rbs": "rbs", "regulatory": "protein_bind", "misc_feature": "misc_feature",
    "primer_bind": "primer_bind", "protein_bind": "protein_bind",
}
IUPAC = {"A": "A", "C": "C", "G": "G", "T": "T", "R": "[AG]", "Y": "[CT]",
         "S": "[GC]", "W": "[AT]", "K": "[GT]", "M": "[AC]", "B": "[CGT]",
         "D": "[AGT]", "H": "[ACT]", "V": "[ACG]", "N": "[ACGT]"}


def rc(s):
    return str(Seq(s).reverse_complement())


def enzyme_doc(name, seq):
    enz = getattr(Restriction, name)
    site = str(enz.site)
    n = len(site)
    eluc = enz.elucidate()
    # only enzymes whose elucidate string starts at the site (no upstream cut)
    if eluc[0] not in "ACGTRYSWKMBDHVN" or eluc.startswith("N"):
        return None
    top_off = eluc.replace("_", "").index("^")
    bot_off = eluc.replace("^", "").index("_")
    L = len(seq)
    ext = seq + seq[: n - 1]
    pat = "".join(IUPAC[c] for c in site)
    rpat = "".join(IUPAC[c] for c in rc(site))
    sites = []
    for m in re.finditer(f"(?={pat})", ext):
        s = m.start()
        if s >= L:
            continue
        sites.append({"position": s + 1, "cut_top": (s + top_off) % L,
                      "cut_bottom": (s + bot_off) % L, "strand": 1})
    if rpat != pat:
        for m in re.finditer(f"(?={rpat})", ext):
            s = m.start()
            if s >= L:
                continue
            sites.append({"position": s + 1, "cut_top": (s + n - bot_off) % L,
                          "cut_bottom": (s + n - top_off) % L, "strand": -1})
    if not sites:
        return None
    sites.sort(key=lambda x: x["position"])
    ovhg = enz.ovhgseq if enz.ovhgseq and "?" not in str(enz.ovhgseq) else None
    ends = "blunt" if enz.is_blunt() else ("5'" if enz.is_5overhang() else "3'")
    rec = eluc.replace("_", "")
    return {"name": name, "recognition": rec, "ends": ends,
            "overhang": None if ends == "blunt" else (str(ovhg) if ovhg else None),
            "cut_count": len(sites), "sites": sites}


STOPS = {"TAA", "TAG", "TGA"}


def orfs(seq, min_aa=100):
    out = []
    L = len(seq)
    for strand, s in ((1, seq), (-1, rc(seq))):
        for frame in range(3):
            i = frame
            while i + 3 <= L:
                if s[i:i + 3] == "ATG":
                    j = i
                    while j + 3 <= L and s[j:j + 3] not in STOPS:
                        j += 3
                    if j + 3 <= L:
                        aa = (j - i) // 3
                        if aa >= min_aa:
                            if strand == 1:
                                out.append({"start": i + 1, "end": j + 3, "strand": 1,
                                            "frame": frame, "aa_length": aa})
                            else:
                                out.append({"start": L - (j + 3) + 1, "end": L - i,
                                            "strand": -1, "frame": frame, "aa_length": aa})
                    i = j + 3
                else:
                    i += 3
    return sorted(out, key=lambda o: o["start"])


for vid in VECTORS:
    data = yaml.safe_load((ROOT / "data" / "vectors" / f"{vid}.yaml").read_text("utf-8"))
    seq = data["sequence"].upper()
    feats = []
    for i, f in enumerate(data.get("features") or []):
        feats.append({
            "id": f"f{i + 1}", "name": f["name"],
            "type": TYPE_MAP.get(f["type"], "misc_feature"),
            "start": int(f["start"]), "end": int(f["end"]),
            "strand": -1 if f.get("strand") == "-" else 1,
            "source": "vector", "notes": f.get("description") or None,
        })
    enzymes = [e for e in (enzyme_doc(n, seq) for n in ENZYMES) if e]
    doc = {
        "schema": "plasmid-map/1", "name": data["name"], "topology": "circular",
        "length": len(seq), "sequence": seq, "features": feats, "enzymes": enzymes,
        "orfs": orfs(seq), "primers": [],
        "meta": {"source_format": "vector", "enzyme_set": "common", "warnings": []},
    }
    for f in doc["features"]:
        if f["notes"] is None:
            del f["notes"]
    (OUT / f"{vid}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(vid, len(seq), len(feats), len(enzymes), len(doc["orfs"]))
