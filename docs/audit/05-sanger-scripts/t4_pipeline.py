import sys, random, traceback
sys.path.insert(0, "src/backend"); sys.path.insert(0, "tests")
from abif_utils import make_ab1
import core.sanger.pipeline as P
from core.seq_utils import revcomp
random.seed(4)
ref="".join(random.choice("ACGT") for _ in range(900))
def mut(s,i,b): return s[:i]+b+s[i+1:]
def ab1(seq,q=45): return make_ab1(seq,[q]*len(seq),samples_per_base=4,fwo=None)
alt = "A" if ref[400]!="A" else "C"
readA = mut(ref[100:700],300,alt)   # ref pos 401
readB = ref[150:750]
print("== a. 同一文件重复上传 ==")
res=P.analyze([("x.ab1",ab1(readA)),("x.ab1",ab1(readA))],ref)
for v in res["variants"]: print(v["ref_pos"],v["type"],"support",v["support_reads"],"conf",v["confidence"])
print("== b. 两 read 冲突（A 报替换，B 同位为参考碱基 Q45）==")
res=P.analyze([("A.ab1",ab1(readA)),("B.ab1",ab1(revcomp(readB)))],ref)
for v in res["variants"]: print(v["ref_pos"],v["type"],v["ref_base"],">",v["alt_base"],"support",v["support_reads"],"conf",v["confidence"], "cross_conflict", v.get("cross_read_conflict"))
cons=res["consensus"]["sequence"]; print("consensus@401:",cons[400],"ref:",ref[400],"diffs:",res["consensus"]["diffs"])
print("conclusion head:",res["conclusion"].splitlines()[0])
print("== c. 起始密码子突变为终止 → 空蛋白 ==")
ref2=ref[:200]+"ATGAAACCCGGGTTTAAACCCGGGTTTTAA"+ref[230:]
feats=[{"name":"orf","type":"CDS","start":201,"end":230,"strand":"+"}]
rd=mut(ref2[100:700],100,"T"); rd=mut(rd,101,"A"); rd=mut(rd,102,"G")
try:
    res=P.analyze([("c.ab1",ab1(rd)),("c2.ab1",ab1(rd))],ref2,feats)
    print([ (c["name"],c["verdict"][:80]) for c in res["cds_reports"]])
except Exception as e:
    traceback.print_exc(limit=3)
print("== d. tracy basecall 返回全低质量 ==")
P._try_tracy_basecall=lambda blob: ("ACGT"*30,[5]*120)
try:
    P.analyze([("d.ab1",ab1(readA))],ref); print("ok")
except Exception as e:
    print("CRASH", type(e).__name__, e)
