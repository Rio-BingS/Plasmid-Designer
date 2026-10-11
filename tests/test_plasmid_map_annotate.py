"""常见元件库自动注释：库文件完整性、pUC19 / pET-28a 检出、环状跨原点、阈值、性能"""

import json
import random
import time
from pathlib import Path

import yaml
from Bio.Seq import Seq

from core.plasmid_map.annotate import annotate, library_path, load_library
from core.plasmid_map.types import FEATURE_TYPE_SET
from core.seq_utils import revcomp

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "data" / "vectors"


def _vector_seq(vid):
    for p in VECTORS.glob("*.yaml"):
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        if d.get("id") == vid:
            return d["sequence"].upper()
    raise KeyError(vid)


def _names(feats):
    return {(f["name"], f["start"], f["end"], f["strand"]) for f in feats}


# ==================== 库文件 ====================

def test_library_file_integrity():
    path = library_path()
    assert path == ROOT / "data" / "features" / "common_features.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    feats = data["features"]
    assert len(feats) >= 50
    ids = [f["id"] for f in feats]
    assert len(ids) == len(set(ids))
    for f in feats:
        assert f["type"] in FEATURE_TYPE_SET, f
        assert set(f["sequence"]) <= set("ACGT") and len(f["sequence"]) == f["length"]
        assert f["sources"], f"{f['id']} 缺来源"
        for s in f["sources"]:
            assert s["kind"] in ("vector_library", "ncbi_genbank")
            assert s.get("accession") or s.get("file")
        if f["match"] == "peptide":
            assert str(Seq(f["sequence"]).translate()) == f["peptide"]
    names = {f["name"] for f in feats}
    for must in ("AmpR", "KanR", "CmR", "HygR", "PuroR", "BSD", "ZeoR", "NeoR/KanR",
                 "EGFP", "mCherry", "T7 promoter", "T7 terminator", "lac operator",
                 "CMV promoter", "CMV enhancer", "SV40 ori", "f1 ori", "ori", "bGH poly(A) signal",
                 "WPRE", "5' LTR", "attB1", "attB2", "6xHis", "FLAG", "HA", "Myc", "GST", "MBP",
                 "TEV site", "thrombin site", "PreScission site", "lacZα"):
        assert must in names, must


def test_library_version_is_content_hash():
    import hashlib
    lib = load_library()
    assert lib.version == hashlib.sha256(library_path().read_bytes()).hexdigest()[:12]


# ==================== 检出 ====================

def test_puc19_annotation():
    seq = _vector_seq("pUC19")
    t = time.perf_counter()
    feats = annotate(seq, circular=True)
    elapsed = time.perf_counter() - t
    got = _names(feats)
    assert ("lacZα", 146, 469, -1) in got
    assert ("AmpR", 1626, 2486, -1) in got
    assert ("lac operator", 489, 505, -1) in got
    assert any(n == "ori" for n, *_ in got)
    for f in feats:
        assert f["source"] == "auto" and f["auto"]["library"] == "common"
        assert f["auto"]["identity"] >= 0.9
    assert elapsed < 1.0


def test_pet28a_annotation_matches_curated_vector_features():
    seq = _vector_seq("pET-28a")
    feats = annotate(seq, circular=True)
    by = {}
    for f in feats:
        by.setdefault(f["name"], []).append(f)
    assert [(f["start"], f["end"], f["strand"]) for f in by["KanR"]] == [(3995, 4810, 1)]
    assert [(f["start"], f["end"]) for f in by["lacI"]] == [(773, 1855)]
    assert [(f["start"], f["end"], f["strand"]) for f in by["T7 promoter"]] == [(368, 386, -1)]
    assert sorted((f["start"], f["end"]) for f in by["6xHis"]) == [(140, 157), (270, 287)]
    assert by["thrombin site"][0]["type"] == "misc_feature"
    assert by["f1 ori"][0]["auto"]["fragment"] is False
    # 截短的 AmpR promoter（pET-28a 只剩一半）标 fragment
    amp_p = by.get("AmpR promoter")
    assert amp_p and amp_p[0]["auto"]["fragment"] is True and amp_p[0]["auto"]["coverage"] < 0.95


def test_circular_feature_across_origin_reported_once():
    seq = _vector_seq("pET-28a")
    cut = 4400  # KanR 3995..4810 被原点切开
    rot = seq[cut:] + seq[:cut]
    feats = [f for f in annotate(rot, circular=True) if f["name"] == "KanR"]
    assert len(feats) == 1
    k = feats[0]
    assert k["start"] > k["end"]
    assert (k["start"], k["end"]) == (3995 - cut + len(seq), 4810 - cut)
    assert k["auto"]["coverage"] == 1.0 and not k["auto"]["fragment"]
    # 线性解读时被切成两半：各自 coverage < 0.95 → fragment
    lin = [f for f in annotate(rot, circular=False) if f["name"] == "KanR"]
    assert lin and all(f["auto"]["fragment"] for f in lin)


def test_identity_threshold_and_reverse_strand():
    lib = load_library()
    egfp = next(e for e in lib.entries if e.name == "EGFP").sequence
    rnd = random.Random(1)
    filler = "".join(rnd.choice("ACGT") for _ in range(600))

    def mutate(s, frac):
        s = list(s)
        for i in rnd.sample(range(len(s)), int(len(s) * frac)):
            s[i] = {"A": "C", "C": "G", "G": "T", "T": "A"}[s[i]]
        return "".join(s)

    ok = filler + revcomp(mutate(egfp, 0.03)) + filler
    hits = [f for f in annotate(ok, circular=False) if f["name"] == "EGFP"]
    assert len(hits) == 1 and hits[0]["strand"] == -1
    assert 0.9 <= hits[0]["auto"]["identity"] < 1.0
    assert (hits[0]["start"], hits[0]["end"]) == (601, 600 + len(egfp))
    bad = filler + mutate(egfp, 0.2) + filler
    assert not [f for f in annotate(bad, circular=False) if f["name"] == "EGFP"]


def test_peptide_tag_translated_search():
    # FLAG 用与库不同的同义密码子编码：DNA 精确匹配不到，翻译检索能找到
    flag = "GATTATAAAGATGATGATGATAAA"  # D Y K D D D D K
    assert str(Seq(flag).translate()) == "DYKDDDDK"
    seq = "ATGGCC" + flag + "TAA" + "C" * 60
    hits = [f for f in annotate(seq, circular=False) if f["name"] == "FLAG"]
    assert [(f["start"], f["end"], f["strand"], f["type"]) for f in hits] == [(7, 30, 1, "tag")]


def test_annotation_is_fast_on_10kb_and_bounded_on_random():
    rnd = random.Random(3)
    seq = _vector_seq("pLVX") + "".join(rnd.choice("ACGT") for _ in range(2000))
    t = time.perf_counter()
    annotate(seq, circular=True)
    assert time.perf_counter() - t < 1.0
    junk = "".join(rnd.choice("ACGT") for _ in range(20000))
    assert annotate(junk, circular=True) == []
