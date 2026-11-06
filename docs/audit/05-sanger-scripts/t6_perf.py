import sys, random, time, logging, cProfile, pstats, io
logging.disable(logging.WARNING)
sys.path.insert(0, "src/backend"); sys.path.insert(0, "tests")
from abif_utils import make_ab1
from core.sanger import abif_reader as ar
import core.sanger.pipeline as P
from core.seq_utils import revcomp
random.seed(6)
ref="".join(random.choice("ACGT") for _ in range(6000))
reads=[]
for k in range(6):
    s=ref[k*800:k*800+1000]
    if k%2: s=revcomp(s)
    reads.append((f"r{k}.ab1", make_ab1(s,[40]*len(s),samples_per_base=12)))
t=time.time(); r=ar.extract_read(reads[0][1]); print("extract_read(无FWO,内容检测) %.2fs"%(time.time()-t))
from core.sanger.signal import baseline_correct
t=time.time(); baseline_correct(r["trace"]); print("baseline_correct %.2fs"%(time.time()-t))
pr=cProfile.Profile(); t=time.time(); pr.enable()
res=P.analyze(reads,ref)
pr.disable(); print("analyze 6 reads × 1000bp, ref 6kb: %.2fs"%(time.time()-t))
s=io.StringIO(); pstats.Stats(pr,stream=s).sort_stats("cumulative").print_stats(14); print(s.getvalue()[:2500])
