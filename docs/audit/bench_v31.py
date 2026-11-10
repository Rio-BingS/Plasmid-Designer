"""v3.1 性能对比：同机同参数跑 b39b46a 与 fix/sanger-scope-v3.1 的 analyze()

用法（在仓库根目录，两个 checkout 各跑一次）：
    python docs/audit/bench_v31.py

只测 `core.sanger.pipeline.analyze` 的纯计算耗时（不含 tracy 外部进程、不含
数据库），每例跑 REPS 次取中位数。用例：
  - synth    合成 900bp 单 read + 2 处替换
  - long     合成 1600bp 单 read（峰形计算随长度近似线性）
  - real_mx  真实 MX 3 条 read + 7039bp 参考（数据不在场时跳过）
"""
import os
import sys
import time
from pathlib import Path

R = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(R), str(R / "src" / "backend"), str(R / "tests")]

import random  # noqa: E402

from abif_utils import make_trace_ab1  # noqa: E402
from core.sanger.pipeline import analyze  # noqa: E402

REPS = 7

# 真实数据（V1.01 交付，不入库；不在场时跳过）。基线用 worktree 跑时
# 仓库根不在项目目录下，用 PD_REAL_DIR 指过去
REAL_DIR = Path(os.environ.get("PD_REAL_DIR")
                or R.parent / "plasmid-designer V1.01" / "sequencing results")


def real_mx_case():
    """MX 的真实 3 条 read + 参考；数据不在场返回 None"""
    if not REAL_DIR.is_dir():
        return None
    mx_ref = REAL_DIR / "MX.dna"
    reads = sorted(REAL_DIR.glob("SZ15674*.ab1"))
    if not mx_ref.is_file() or len(reads) < 3:
        return None
    from core.sanger.reference_parser import parse_reference_topology

    ref_seq, features, topo = parse_reference_topology(mx_ref.name, mx_ref.read_bytes())
    files = [(p.name, p.read_bytes()) for p in reads[:3]]
    return files, ref_seq, features, (topo == "circular")



def rs(seed, n):
    r = random.Random(seed)
    return "".join(r.choice("ACGT") for _ in range(n))


def mk(ref, s, e, subs=(), **kw):
    mol = list(ref[s - 1:e])
    for p, a in subs:
        mol[p - s] = a
    return make_trace_ab1("".join(mol), **kw)


def bench(tag, ab1, ref, feats=None, circ=True):
    files = ab1 if isinstance(ab1, list) else [(tag, ab1)]
    feats = feats or []
    analyze(files, ref, feats, circular=circ)     # 预热（首次会 import/编译正则）
    ts = []
    for _ in range(REPS):
        t0 = time.perf_counter()
        analyze(files, ref, feats, circular=circ)
        ts.append((time.perf_counter() - t0) * 1000)
    ts.sort()
    print(f"  {tag:8s} median {ts[len(ts) // 2]:7.1f} ms   "
          f"min {ts[0]:7.1f}   max {ts[-1]:7.1f}   n={REPS}")
    return ts[len(ts) // 2]


def main():
    ref900 = rs("bench", 900)
    ref1600 = rs("bench2", 1600)
    cases = [
        ("synth", mk(ref900, 1, 900, subs=[(300, "A"), (600, "G")], q=45), ref900, None, True),
        ("long", mk(ref1600, 1, 1600, subs=[(800, "T")], q=45), ref1600, None, True),
    ]
    med = {}
    for tag, ab1, ref, feats, circ in cases:
        med[tag] = bench(tag, ab1, ref, feats, circ)

    mx = real_mx_case()
    if mx is None:
        print("  real_mx  跳过（V1.01 真实数据不在场）")
    else:
        files, ref_seq, feats, circ = mx
        med["real_mx"] = bench("real_mx", files, ref_seq, feats, circ)
    print(f"TOTAL_MEDIAN {sum(med.values()):.1f} ms")


if __name__ == "__main__":
    main()
