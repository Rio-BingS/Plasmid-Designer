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


# ==================== 第二批元件（p15A ori / AcV5 / pUC19 ori） ====================

def _entry(name):
    return next(e for e in load_library().entries if e.name == name)


def test_library_phase2_entries_have_verifiable_sources():
    data = json.loads(library_path().read_text(encoding="utf-8"))
    by = {f["id"]: f for f in data["features"]}
    p15a = by["p15a-ori"]
    assert p15a["type"] == "rep_origin" and p15a["length"] == 546
    assert p15a["sources"][0]["accession"].startswith("X06403.")
    acv5 = by["acv5"]
    assert acv5["match"] == "peptide" and acv5["peptide"] == "SWKDASGWS"
    assert acv5["sources"][0]["accession"].startswith("MP192394.")
    # pUC19 的 AmpR / lacZα 与已有条目同序列：只追加来源，不新增条目
    assert any(s.get("vector") == "pUC19" for s in by["ampr"]["sources"])
    assert any(s.get("vector") == "pUC19" for s in by["laczalpha"]["sources"])


def test_p15a_ori_and_acv5_detected():
    rnd = random.Random(7)
    a = "".join(rnd.choice("ACGT") for _ in range(400))
    b = "".join(rnd.choice("ACGT") for _ in range(400))
    p15a = _entry("p15A ori").sequence
    acv5 = _entry("AcV5").sequence  # 27 bp，编码 SWKDASGWS
    seq = a + revcomp(p15a) + b + "ATG" + acv5 + "TAA" + a
    feats = annotate(seq, circular=False)
    got = {f["name"]: f for f in feats}
    p = got["p15A ori"]
    assert (p["start"], p["end"], p["strand"]) == (401, 946, -1)
    assert p["auto"]["identity"] == 1.0 and not p["auto"]["fragment"]
    t = got["AcV5"]
    s0 = 400 + 546 + 400 + 3 + 1
    assert (t["start"], t["end"], t["strand"], t["type"]) == (s0, s0 + 26, 1, "tag")


def test_puc19_ori_is_exact_full_length():
    ori = [f for f in annotate(_vector_seq("pUC19"), circular=True) if f["name"] == "ori"]
    assert len(ori) == 1
    o = ori[0]
    assert (o["start"], o["end"], o["strand"]) == (867, 1455, -1)
    assert o["auto"]["identity"] == 1.0 and o["auto"]["coverage"] == 1.0


# ==================== 对抗性输入：串联重复（v2.12.0 审查 H2） ====================

def _tandem(n: int, unit: int = 40) -> str:
    lib = load_library()
    e = next(x for x in lib.entries if x.name == "TcR")
    chunk = e.sequence[100:100 + unit]
    return (chunk * (n // unit + 1))[:n]


def test_tandem_repeat_alignment_windows_and_cells_bounded(monkeypatch):
    """元件片段串联重复到 300 kb：旧实现把所有种子链成一个簇、单次比对
    300 kb × 元件长（~7 s / ~750 MB）。现在每个窗口 ≤ 元件长 + 2×PAD，
    DP 单元格总量受 MAX_ALIGN_CELLS 约束，超出写告警。"""
    import core.plasmid_map.annotate as A

    seen = []
    real = A._aligner()

    class Spy:
        def align(self, w, e):
            seen.append((len(w), len(e)))
            return real.align(w, e)

    monkeypatch.setattr(A, "_aligner", lambda: Spy())
    seq = _tandem(300_000)
    warns = []
    t = time.time()
    feats = annotate(seq, True, warnings=warns)
    elapsed = time.time() - t
    assert seen and all(w <= e + 2 * A.PAD for w, e in seen)
    assert sum(w * e for w, e in seen) <= A.MAX_ALIGN_CELLS
    assert len(seen) <= A.MAX_ALIGNMENTS
    assert any("预算" in w for w in warns)
    assert all(f["auto"]["coverage"] >= A.MIN_COVERAGE for f in feats)
    assert elapsed < 8, elapsed


def test_budget_prefers_real_elements_over_repeat_noise(monkeypatch):
    """预算很紧时真实全长元件（种子覆盖跨度大）先比对，不被串联重复噪声挤掉；
    负链元件同样参与排序（旧实现正链先用完共享预算）"""
    import core.plasmid_map.annotate as A

    lib = load_library()
    tcr = next(x for x in lib.entries if x.name == "TcR")
    monkeypatch.setattr(A, "MAX_ALIGN_CELLS", 3 * (len(tcr.sequence) + 2 * A.PAD) * len(tcr.sequence))
    noise = _tandem(20_000)
    seq = noise + revcomp(tcr.sequence) + noise
    warns = []
    feats = annotate(seq, False, warnings=warns)
    hit = [f for f in feats if f["name"] == "TcR"]
    assert hit and hit[0]["strand"] == -1 and not hit[0]["auto"]["fragment"]
    assert any("预算" in w for w in warns)


def test_large_sequence_not_auto_annotated_unless_requested(monkeypatch):
    import core.plasmid_map.annotate as A
    from core.plasmid_map.document import build_document

    monkeypatch.setattr(A, "AUTO_ANNOTATE_MAX_BP", 5000)
    lib = load_library()
    tcr = next(x for x in lib.entries if x.name == "TcR")
    rnd = random.Random(7)
    seq = "".join(rnd.choice("ACGT") for _ in range(3000)) + tcr.sequence + \
        "".join(rnd.choice("ACGT") for _ in range(3000))
    doc = build_document(name="x", sequence=seq, topology="circular", source_format="raw")
    assert not doc["features"]
    assert any("未自动注释" in w for w in doc["meta"]["warnings"])
    doc = build_document(name="x", sequence=seq, topology="circular", source_format="raw",
                         annotate=True)
    assert any(f["name"] == "TcR" for f in doc["features"])
