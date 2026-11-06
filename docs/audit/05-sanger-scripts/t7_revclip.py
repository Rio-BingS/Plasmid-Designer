import sys, random
sys.path.insert(0, "src/backend")
from core.sanger.aligner import align_read
from core.sanger.pipeline import _read_ref_maps, _aligned_run_window
from core.seq_utils import revcomp
random.seed(7)
ref="".join(random.choice("ACGT") for _ in range(800))
# 反向 read：原始电泳方向 = revcomp(ref[200:600]) + 3' 末端 2 个错误碱基
core=revcomp(ref[200:600])
for tail in ("", "GG"[:1], "TT", "ACGTA"):
    read=core+tail
    a=align_read(read, ref)
    r2ref,_=_read_ref_maps({"alignment":a,"trimmed_bases":read})
    # 原始 read 第 1 个碱基 (1-based) 对应 ref 600
    print(f"3'端多 {len(tail)} 个非参考碱基: dir={a['direction']} aligned_read_len={a['aligned_read_len']} read2ref[1]={r2ref.get(1)} (应为600)")
