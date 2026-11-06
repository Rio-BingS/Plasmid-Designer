import sys, random, time, struct, collections, logging
logging.disable(logging.WARNING)
sys.path.insert(0, "src/backend"); sys.path.insert(0, "tests")
from abif_utils import make_ab1
from core.sanger import abif_reader as ar
import core.sanger.pipeline as P
random.seed(5)
seq="".join(random.choice("ACGT") for _ in range(700))
good=make_ab1(seq,[40]*700,samples_per_base=12,fwo="GATC")
print("good size",len(good))
def run(fn, data):
    t=time.time()
    try: r=fn(data); return "OK", time.time()-t, r
    except Exception as e: return type(e).__name__, time.time()-t, None
for name,fn in (("biopython",ar._extract_read_biopython),("internal",ar._extract_read_internal)):
    c=collections.Counter(); worst=0; okbad=0
    for k in range(400):
        d=bytearray(good)
        if k<150: d=d[:random.randint(0,len(d))]
        else:
            for _ in range(random.randint(1,20)):
                i=random.randrange(len(d)); d[i]=random.randrange(256)
        st,dt,r=run(fn,bytes(d)); c[st]+=1; worst=max(worst,dt)
        if st=="OK" and r and (len(r["quality"])!=len(r["bases"]) or len(r["peak_indices"])!=len(r["bases"])): okbad+=1
    print(name, dict(c), "worst %.3fs"%worst, "解析成功但 bases/quality/peaks 长度不一致:", okbad)
# 恶意：目录项 elem_count 巨大 / data_size 巨大
hdr=bytearray(good)
# 找 PBAS 目录项改 data_size 为 0xFFFFFFFF
i=hdr.find(b"PBAS"); struct.pack_into(">I",hdr,i+16,0xFFFFFFF0)
for name,fn in (("biopython",ar._extract_read_biopython),("internal",ar._extract_read_internal)):
    print("huge data_size", name, run(fn,bytes(hdr))[:2])
hdr=bytearray(good); struct.pack_into(">I",hdr,18,0x7FFFFFFF)   # 目录项个数巨大
for name,fn in (("biopython",ar._extract_read_biopython),("internal",ar._extract_read_internal)):
    print("huge dir count", name, run(fn,bytes(hdr))[:2])
# PLOC 指向 trace 之外 / 负值
seq2=seq
d=make_ab1(seq2,[40]*700,samples_per_base=12,fwo="GATC")
d=bytearray(d); i=d.find(b"PLOC"); off=struct.unpack_from(">I",d,i+20)[0]
struct.pack_into(">I",d,off+4*350,10**9); struct.pack_into(">I",d,off+4*351,0)
r=ar.extract_read(bytes(d)); print("PLOC 越界值", r["peak_indices"][349:353])
try:
    res=P.analyze([("p.ab1",bytes(d))],"".join(random.choice("ACGT") for _ in range(50))+seq2); print("analyze ok, errors",res["errors"])
except Exception as e: print("analyze CRASH",type(e).__name__,e)
# 质量缺失（无 PCON）
d=make_ab1(seq,[],samples_per_base=12,fwo="GATC")
r=ar.extract_read(d); print("无PCON: quality len", len(r["quality"]))
res=P.analyze([("q.ab1",d)],seq); print("无PCON analyze errors:",res["errors"])
# 内部解析器 SPNM pString(type 18)
