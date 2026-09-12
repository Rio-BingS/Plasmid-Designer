"""A2 基线校正的真实 ab1 校准探针（docs/poly-final-actionable-list.md A2 验收）

对每组真实数据逐 read 报告：
- 峰高保持：校正前后每峰复合峰高的中位比值（应接近 1，强峰不被吃掉）；
- 本底追踪：头/中/尾三段本底通道中位数的前后对比（后段本底应被压低）；
- poly 平台安全：read 内 ≥20bp 同碱基调用段的校正后峰高中位数占校正前
  比例（合并平台若被当本底扣平，此值会趋近 0——护栏必须兜住）；
- 耗时。

用法：
  python scripts/poly_signal_probe.py \
      --data-dir "../plasmid-designer V1.01/sequencing results" \
      --data-dir "../plasmid-designer V1.01/sequencing results/1/1/wrong/M/1"
"""

import argparse
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "src" / "backend"
if (BACKEND / "core").is_dir():
    sys.path.insert(0, str(BACKEND))

READ_EXTS = {".ab1", ".ab"}
REF_EXTS = {".dna", ".gb", ".gbk", ".genbank", ".fasta", ".fa", ".fna"}


def _median(xs):
    s = sorted(xs)
    return s[len(s) // 2] if s else 0.0


def probe_read(name, blob):
    from core.sanger.abif_reader import extract_read
    from core.sanger.pipeline import _trim_by_quality
    from core.sanger.signal import baseline_correct, peak_heights, composite_signal

    t0 = time.perf_counter()
    read = extract_read(blob)
    if len(read["bases"]) < 50:
        return None
    s, e = _trim_by_quality(read["bases"], read["quality"], 20)
    peaks = read["peak_indices"][s:e]
    trace_raw = read["trace"]
    trace_cor = baseline_correct(trace_raw)
    dt = time.perf_counter() - t0

    h_raw = peak_heights(trace_raw, peaks)
    h_cor = peak_heights(trace_cor, peaks)
    pairs = [(a, b) for a, b in zip(h_raw, h_cor) if a > 50]
    if not pairs:
        return None
    # 峰高保持：分位段报告（低峰更敏感）
    pairs.sort()
    low = _median([b / a for a, b in pairs[: len(pairs) // 4]])
    mid = _median([b / a for a, b in pairs[len(pairs) // 3: 2 * len(pairs) // 3]])
    high = _median([b / a for a, b in pairs[-len(pairs) // 4:]])

    comp_raw = composite_signal(trace_raw)
    comp_cor = composite_signal(trace_cor)
    n = len(comp_cor)

    def bg(comp, lo, hi):
        seg = sorted(comp[lo:hi])
        return seg[max(0, int(0.1 * len(seg)))]

    bgs = {
        "head_raw": bg(comp_raw, 0, n // 4), "head_cor": bg(comp_cor, 0, n // 4),
        "mid_raw": bg(comp_raw, n // 3, 2 * n // 3), "mid_cor": bg(comp_cor, n // 3, 2 * n // 3),
        "tail_raw": bg(comp_raw, 3 * n // 4, n), "tail_cor": bg(comp_cor, 3 * n // 4, n),
    }

    # poly 平台安全：read 内 ≥20bp 同碱基调用段（参考 polyA 在 read 上的映射段）
    trimmed = read["bases"][s:e]
    plateau = None
    i = 0
    best = (0, None)
    while i < len(trimmed):
        j = i
        while j + 1 < len(trimmed) and trimmed[j + 1] == trimmed[i]:
            j += 1
        if j - i + 1 > best[0] and trimmed[i] in "ACGT" and i > 5 and j < len(trimmed) - 5:
            best = (j - i + 1, (i, j))
        i = j + 1
    if best[1] and best[0] >= 15:
        i0, i1 = best[1]
        seg_r = [h_raw[k] for k in range(i0, i1 + 1) if h_raw[k] > 0]
        seg_c = [h_cor[k] for k in range(i0, i1 + 1) if h_cor[k] > 0]
        if seg_r and seg_c:
            plateau = {"len": best[0], "base": trimmed[i0],
                       "raw_med": _median(seg_r), "cor_med": _median(seg_c),
                       "ratio": round(_median(seg_c) / _median(seg_r), 3)}
    return {"low": round(low, 3), "mid": round(mid, 3), "high": round(high, 3),
            "bg": bgs, "plateau": plateau, "sec": round(dt, 3),
            "bases": len(trimmed)}


def main():
    ap = argparse.ArgumentParser(description="A2 基线校正真实数据校准探针")
    ap.add_argument("--data-dir", action="append", required=True)
    args = ap.parse_args()

    for d in args.data_dir:
        p = Path(d)
        files = sorted(x for x in p.iterdir() if x.is_file())
        refs = [x for x in files if x.suffix.lower() in REF_EXTS]
        reads = [x for x in files if x.suffix.lower() in READ_EXTS]
        print(f"\n=== {p.name}（参考 {refs[0].name if refs else '?'}，{len(reads)} reads）===")
        for rp in reads:
            r = probe_read(rp.name, rp.read_bytes())
            if r is None:
                print(f"  {rp.name}: 跳过")
                continue
            bg = r["bg"]
            print(f"  {rp.name} ({r['bases']}bp, {r['sec']}s)")
            print(f"    峰高保持(校正/原始): 低峰 {r['low']}  中峰 {r['mid']}  高峰 {r['high']}")
            print(f"    本底(10分位): 头 {bg['head_raw']}→{bg['head_cor']}  "
                  f"中 {bg['mid_raw']}→{bg['mid_cor']}  尾 {bg['tail_raw']}→{bg['tail_cor']}")
            pl = r["plateau"]
            if pl:
                print(f"    最长同碱基段: {pl['base']}×{pl['len']}  "
                      f"峰高中位 {pl['raw_med']}→{pl['cor_med']} (比值 {pl['ratio']})")


if __name__ == "__main__":
    main()
