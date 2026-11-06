import sys, time, random, tracemalloc
sys.path.insert(0, "src/backend"); sys.path.insert(0, "tests")
from abif_utils import make_ab1
from core.sanger import abif_reader as ar
from core.sanger.signal import _fill_invalid, baseline_correct
from core.sanger.aligner import align_read
random.seed(1)

print("== 1. _fill_invalid 对齐 ==")
n=1000; centers=list(range(0,n,50))+[n-1]
vals=[float(c) for c in centers]   # 本底 = x 线性
out=_fill_invalid(centers, vals, 0.0, n)
errs=[out[x]-x for x in range(n)]
print("len", len(out), "x=500 baseline", out[500], "x=999", out[999], "max err", max(abs(e) for e in errs))

print("== 2. 局部比对 软剪切 偏移 ==")
ref="".join(random.choice("ACGT") for _ in range(600))
junk="".join(random.choice("ACGT") for _ in range(30))
read=junk+ref[100:400]
a=align_read(read, ref, [40]*len(read))
print("dir",a["direction"],"ref_start",a["ref_start"],"aligned_read_len",a["aligned_read_len"],"read len",len(read))
# 引入一个替换：ref 位置 200 (1-based) ，read 中索引 30+99
r2=list(read); i=30+99; r2[i]= "A" if r2[i]!="A" else "C"; r2="".join(r2)
a=align_read(r2, ref, [40]*len(r2))
print("variant", [(v["ref_pos"],v["read_pos"]) for v in a["variants"]], "true read_pos", i+1)
from core.sanger.pipeline import _read_ref_maps
r={"alignment":a,"trimmed_bases":r2}
r2ref,ref2call=_read_ref_maps(r)
print("read2ref[%d] ->"%(i+1), r2ref.get(i+1), " (应为 200)")
print("ref2call[200] read_pos", ref2call[200]["read_pos"], "(应为", i+1, ")")

print("== 3. 重复序列 len(alignments) ==")
for L in (200, 1000):
    t=time.time()
    try:
        a=align_read("A"*L, "A"*(L*3))
        print(L,"ok",round(time.time()-t,2),"s", a["ref_start"])
    except Exception as e:
        print(L,"EXC",type(e).__name__,e, round(time.time()-t,2))
t=time.time()
try:
    a=align_read("AT"*300, "AT"*1500); print("AT ok", round(time.time()-t,2))
except Exception as e: print("AT EXC",type(e).__name__,e)

print("== 4. 大参考 内存/时间 ==")
for RL in (10000, 100000, 300000):
    ref="".join(random.choice("ACGT") for _ in range(RL))
    read=ref[RL//2:RL//2+900]
    tracemalloc.start(); t=time.time()
    a=align_read(read, ref)
    cur,peak=tracemalloc.get_traced_memory(); tracemalloc.stop()
    print(RL, "time", round(time.time()-t,2), "s  py-peak MB", round(peak/1e6,1))
