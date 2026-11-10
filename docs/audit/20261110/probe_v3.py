"""b39b46a 复审探针：生产路径（prepare_scope_mode）+ 新风险点"""
import sys, random
import sys, random
from pathlib import Path
R = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(R), str(R / "src" / "backend"), str(R / "tests")]
from tests.abif_utils import make_trace_ab1
from core.sanger.pipeline import analyze
def rs(seed, n):
    r = random.Random(seed); return "".join(r.choice("ACGT") for _ in range(n))
def mk(ref, s, e, subs=(), **kw):
    mol = list(ref[s-1:e])
    for p, a in subs: mol[p - s] = a
    return make_trace_ab1("".join(mol), **kw)
from core.sanger.batch import excel_conclusion
from core.sanger import cds_extract as CE
from core.seq_utils import revcomp
from pathlib import Path

def put_orf(ref, s, e, seed):
    r = random.Random(seed); L = e - s + 1; k = L // 3
    cod = ["ATG"] + ["".join(r.choice("ACGT") for _ in range(3)) for _ in range(k-2)] + ["TAA"]
    cod = [c if c not in ("TAA","TAG","TGA") or i == k-1 else "GCT" for i, c in enumerate(cod)]
    ref = list(ref); ref[s-1:e] = list("".join(cod)); return "".join(ref)

def show(tag, res):
    vd = res["verdict"]
    print("==", tag, "|", vd["status"], "| scope", (vd.get("scope") or {}).get("source"), (vd.get("scope") or {}).get("ranges"))
    print("  headline:", vd.get("headline"))
    print("  conclusion:", res["conclusion"].split("\n")[0][:160])
    print("  excel:", excel_conclusion("P", res, len(res["reads"]), True)[:160])
    print("  blocking:", [(b["kind"], (b.get("detail") or "")[:60]) for b in vd["blocking"]][:6], "n=", len(vd["blocking"]))
    print("  vars:", [(v["ref_pos"], v["type"], v.get("status"), v.get("ref"), v.get("alt")) for v in res["variants"]][:6])

def cds_run(tag, ab1, ref, feats, name, circ=True):
    r2, f2, sc, c2 = CE.prepare_scope_mode(ref, feats, name, [n for n, _ in ab1], None, circular=circ)
    show(tag, analyze(ab1, r2, f2, scope=sc, circular=c2))

# --- CM 生产路径
ref = rs("cmA", 4000); ref = put_orf(ref, 1001, 1600, 3)
feats = [dict(name="GeneX", type="CDS", start=1001, end=1600, strand="+")]
cds_run("CM long reads 700-1600 + rc 1000-1900", [("GeneX_F.ab1", mk(ref, 700, 1600, q=45)),
        ("GeneX_R.ab1", make_trace_ab1(revcomp(ref[999:1900]), q=45, seed=3))], ref, feats, "p")
# 1bp 插入在 CDS 内部（移码）
mol = ref[699:1300] + "A" + ref[1300:1700]
if ref[1299] == "A" or ref[1300] == "A":
    mol = ref[699:1300] + "C" + ref[1300:1700]
cds_run("CM 1bp ins inside CDS @1300/1301 (frameshift)", [("GeneX_F.ab1", make_trace_ab1(mol, q=45))], ref, feats, "p")
# 跨原点
ref2 = rs("cmB", 4000); body = put_orf("N"*600, 1, 600, 5)
ref2 = ref2[:300].replace(ref2[:300], body[300:]) + ref2[300:3700] + body[:300]
feats2 = [dict(name="Cross", type="CDS", start=3701, end=4000, strand="+", feature_id="c", segment_index=0, segment_count=2),
          dict(name="Cross", type="CDS", start=1, end=300, strand="+", feature_id="c", segment_index=1, segment_count=2)]
ins = next(b for b in "ACGT" if b not in (ref2[3699], ref2[3700]))
m2 = ref2[3299:3700] + ins + ref2[3700:4000] + ref2[0:500]
cds_run("CM cross-origin ins before ATG (single fwd read)", [("Cross_F.ab1", make_trace_ab1(m2, q=45))], ref2, feats2, "p")
cds_run("CM cross-origin clean", [("Cross_F.ab1", make_trace_ab1(ref2[3299:4000] + ref2[0:500], q=45))], ref2, feats2, "p")

# --- SC6 原始构造：峰形退化尾（真实常见），scope=GeneX
ref3 = rs("sc6", 4000); ref3 = put_orf(ref3, 301, 900, 1)
f3 = [dict(name="GeneX", type="CDS", start=301, end=900, strand="+"),
      dict(name="AmpR", type="CDS", start=2001, end=2861, strand="+")]
for tw in (2.0, 2.5, 3.0):
    show(f"degraded tail width x{tw} from read pos 400, scope=GeneX",
         analyze([("F.ab1", make_trace_ab1(ref3[200:1101], q=50, tail_width=(400, tw)))], ref3, f3, scope="GeneX", circular=True))
show("degraded tail x3.0, undeclared",
     analyze([("F.ab1", make_trace_ab1(ref3[200:1101], q=50, tail_width=(400, 3.0)))], ref3, f3, circular=True))

# --- 真实子集：未声明 vs 声明范围，minor_af_site / 复核量
from core.sanger.reference_parser import parse_reference_topology
D = R / "tests" / "data" / "real_ab1"
rs_, fs_, topo = parse_reference_topology("MUTPK.dna", (D / "MUTPK.dna").read_bytes())
ab = [(p.name, p.read_bytes()) for p in sorted(D.glob("*.ab1"))]
print("real feats:", [(f["name"], f["type"], f["start"], f["end"]) for f in fs_ if f["type"] in ("CDS", "gene", "rep_origin", "promoter")][:12])
from collections import Counter
for sc in (None, "全质粒"):
    try:
        res = analyze(ab, rs_, fs_, circular=topo is not False, scope=sc)
    except Exception as e:
        print("scope", sc, "ERR", e); continue
    vd = res["verdict"]
    print("== REAL scope=", sc, vd["status"], "| headline:", vd.get("headline"))
    print("  blocking kinds:", Counter(b["kind"] for b in vd["blocking"]))
    print("  info kinds:", Counter(b["kind"] for b in vd.get("info") or []))
    try:
        from core.sanger.review import build_queue
        q = build_queue(res)
        print("  review queue:", len(q), Counter(x.get("kind") for x in q))
    except Exception as e:
        print("  queue err", e)
    print("  excel:", excel_conclusion("P", res, len(res["reads"]), True)[:200])
    print("  conclusion lines:", len(res["conclusion"].split("\n")))
