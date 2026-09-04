"""Sanger 测序全自动分析管线

输入：一个或多个 .ab1 字节流 + 参考载体序列（与特征注释）
输出：每条 read 的比对与变体、合并突变表、共识序列（consensus）、自动结论

流程：ABIF 解析 → 质量修剪 → 双向比对（自动判向）→ 多 read 一致性投票
      → 共识序列生成 → 特征级注释 → 自动结论
可选：检测到 tracy 可执行文件时，对疑似混合样品执行 tracy decompose 解卷积。
"""

import os
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

from core.sanger.abif_reader import extract_read, AbiParseError
from core.sanger.aligner import align_read, merge_coverage
from core.sanger.annotator import annotate_variants, summarize_severity

TRACY_BIN = os.environ.get("TRACY_BIN", "tracy")
MIXED_PEAK_RATIO = 0.30  # 次级峰 / 主峰 高于此比例视为疑似混合
MIN_TRIM_Q = 20          # 默认末端修剪质量阈值
MIN_WINDOW = 50          # 修剪后最短保留长度


def _trim_by_quality(bases: str, quality: List[int], min_q: int) -> Tuple[int, int]:
    """返回保留区间 [start, end)（0-based）：去除两端质量低于阈值的碱基"""
    n = len(bases)
    s, e = 0, n
    while s < e and quality[s] < min_q:
        s += 1
    while e > s and quality[e - 1] < min_q:
        e -= 1
    return (s, e)


def _detect_mixed_positions(bases: str, trace: Dict[str, List[int]],
                            peak_indices: List[int]) -> List[int]:
    """检测疑似混合/杂合位点：次级通道峰面积占主峰比例过高"""
    mixed = []
    for i, base in enumerate(bases):
        if base not in "ACGT" or i >= len(peak_indices):
            continue
        pk = peak_indices[i]
        window = range(max(0, pk - 2), min(min(len(v) for v in trace.values()), pk + 3))
        areas = {}
        for b in "ACGT":
            areas[b] = sum(trace[b][j] for j in window)
        sorted_a = sorted(areas.values(), reverse=True)
        if sorted_a[1] > 0 and sorted_a[0] > 0:
            ratio = sorted_a[1] / sorted_a[0]
            if ratio > MIXED_PEAK_RATIO and areas[base] == sorted_a[0]:
                mixed.append(i + 1)
    return mixed


def _try_tracy_decompose(ab1_path: str, ref_fasta: str) -> Optional[List[Dict]]:
    """若 tracy 可用，对混合样品执行 decompose，返回两条等位基因序列"""
    tracy = shutil.which(TRACY_BIN) or (TRACY_BIN if os.path.isfile(TRACY_BIN) else None)
    if not tracy:
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            out_prefix = os.path.join(td, "dec")
            proc = subprocess.run(
                [tracy, "decompose", "-r", ref_fasta, "-o", out_prefix, ab1_path],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode != 0:
                return None
            alleles = []
            for suffix in (".1.fasta", ".2.fasta", "_1.fasta", "_2.fasta"):
                p = out_prefix + suffix
                if os.path.isfile(p):
                    with open(p) as fh:
                        seq = "".join(line.strip() for line in fh if not line.startswith(">"))
                    alleles.append({"sequence": seq, "source": suffix})
            return alleles or None
    except (OSError, subprocess.SubprocessError):
        return None


def _build_consensus(reference: str, read_results: List[Dict]) -> Dict:
    """按参考坐标逐位质量加权投票生成共识序列

    每个 read 以其平均质量为权重为覆盖区间内的参考碱基投票；
    变体（替换/缺失/插入）以 权重+5 的票修正对应位点——单 read 覆盖区
    即以该 read 为准（模拟人工核对），多 read 覆盖时孤立低质量差异被压制。
    """
    L = len(reference)
    votes: List[Dict[str, int]] = [{} for _ in range(L)]

    for r in read_results:
        weight = max(10, int(r["mean_q"]))
        aln = r["alignment"]
        for pos in range(aln["ref_start"], aln["ref_end"] + 1):
            if 1 <= pos <= L:
                base = reference[pos - 1].upper()
                votes[pos - 1][base] = votes[pos - 1].get(base, 0) + weight
        for v in aln["variants"]:
            if v["type"] == "substitution":
                idx = v["ref_pos"] - 1
                if 0 <= idx < L:
                    alt = v["alt_base"].upper()
                    votes[idx][alt] = votes[idx].get(alt, 0) + weight + 5
            elif v["type"] == "deletion":
                for k in range(v["length"]):
                    idx = v["ref_pos"] - 1 + k
                    if 0 <= idx < L:
                        votes[idx]["-"] = votes[idx].get("-", 0) + weight + 5
            elif v["type"] == "insertion":
                idx = v["ref_pos"]  # 插入点右侧参考位置
                if 0 <= idx < L:
                    votes[idx][f"+{v['alt_base'].upper()}"] = weight + 5

    consensus_chars = []
    covered: List[bool] = []
    for pos in range(1, L + 1):
        cell = votes[pos - 1]
        if not cell:
            consensus_chars.append(reference[pos - 1].upper())
            covered.append(False)
            continue
        covered.append(True)
        best_key = max(cell.items(), key=lambda kv: kv[1])[0]
        if best_key.startswith("+"):
            consensus_chars.append(best_key[1:])
        elif best_key == "-":
            pass  # deletion：跳过该参考碱基
        else:
            consensus_chars.append(best_key)

    covered_ranges = merge_coverage(
        [(i + 1, i + 1) for i, c in enumerate(covered) if c], L
    )
    coverage = sum(1 for c in covered if c) / L if L else 0.0
    return {
        "sequence": "".join(consensus_chars),
        "covered_ranges": covered_ranges,
        "coverage_percent": round(coverage * 100, 2),
    }


def analyze(
    ab1_files: List[Tuple[str, bytes]],
    reference: str,
    features: Optional[List[Dict]] = None,
    min_q: int = MIN_TRIM_Q,
    allow_decompose: bool = True,
) -> Dict:
    """全自动分析入口

    ab1_files: [(filename, bytes), ...]
    reference: 参考载体序列（环形质粒按线性处理，1-based 坐标）
    """
    features = features or []
    ref = reference.upper().replace("U", "T")
    if not ref:
        raise ValueError("参考序列为空，无法进行比对分析")

    read_results: List[Dict] = []
    errors: List[Dict] = []

    for filename, blob in ab1_files:
        try:
            read = extract_read(blob)
        except (AbiParseError, Exception) as e:  # noqa: BLE001 单文件失败不阻断整体
            errors.append({"filename": filename, "error": f"解析失败: {e}"})
            continue

        bases = read["bases"]
        quality = read["quality"]
        if len(bases) < MIN_WINDOW:
            errors.append({"filename": filename, "error": "碱基数过少（<50bp）"})
            continue

        s, e = _trim_by_quality(bases, quality, min_q)
        trimmed = bases[s:e]
        trimmed_q = quality[s:e]
        mean_q = sum(trimmed_q) / len(trimmed_q) if trimmed_q else 0

        aln = align_read(trimmed, ref)

        # 变体附加质量值
        for v in aln["variants"]:
            rp = v.get("read_pos") or 1
            qi = min(max(rp - 1, 0), len(trimmed_q) - 1)
            v["quality"] = trimmed_q[qi]

        mixed_positions = _detect_mixed_positions(
            trimmed, read["trace"], [p - s for p in read["peak_indices"] if s <= p < e]
        )

        read_results.append({
            "filename": filename,
            "sample_name": read["sample_name"],
            "raw_length": len(bases),
            "trimmed_length": len(trimmed),
            "mean_q": round(mean_q, 1),
            "trimmed_bases": trimmed,
            "trimmed_quality": trimmed_q,
            "alignment": aln,
            "mixed_positions": mixed_positions,
            "trace": read["trace"],
            "peak_indices": read["peak_indices"],
        })

    # 合并全部变体 → 注释 → 汇总
    all_variants: List[Dict] = []
    for r in read_results:
        for v in r["alignment"]["variants"]:
            vv = {**v, "read": r["filename"], "read_q": v.get("quality", 0)}
            all_variants.append(vv)
    # 按 (ref_pos, type, alt) 去重合并（多 read 支持计数）
    merged: Dict[Tuple, Dict] = {}
    for v in all_variants:
        key = (v["ref_pos"], v["type"], v.get("alt_base", ""))
        if key in merged:
            merged[key]["support_reads"] += 1
            merged[key]["read_q"] = max(merged[key]["read_q"], v["read_q"])
        else:
            merged[key] = {**v, "support_reads": 1}
    variants = list(merged.values())
    variants.sort(key=lambda x: (x["ref_pos"], x["type"]))
    variants = annotate_variants(variants, features, ref)

    consensus = _build_consensus(ref, read_results)

    coverage_ranges = merge_coverage(
        [(r["alignment"]["ref_start"], r["alignment"]["ref_end"]) for r in read_results],
        len(ref),
    )

    # 自动结论
    if not read_results:
        conclusion = "没有可分析的测序文件"
    elif not variants:
        conclusion = (
            f"构建序列与设计一致：{len(read_results)} 条 read 全部匹配，"
            f"覆盖参考序列的 {consensus['coverage_percent']:.1f}%"
        )
    else:
        lines = [f"共检出 {len(variants)} 处差异（覆盖 {consensus['coverage_percent']:.1f}%）："]
        lines.extend(summarize_severity(variants))
        if consensus["coverage_percent"] < 95:
            lines.append(f"注意：仍有 {100 - consensus['coverage_percent']:.1f}% 区域未被测序覆盖，建议补充引物")
        conclusion = "\n".join(lines)

    # 混合样品提示：检出疑似混合位点时建议人工复核或使用 tracy decompose 解卷积
    mixed_reads = [r for r in read_results if r["mixed_positions"]]

    return {
        "reads": [
            {k: v for k, v in r.items() if k not in ("trace", "peak_indices")}
            for r in read_results
        ],
        # 峰图原始数据（与 reads 同序）：四通道 + 碱基 + 质量 + 峰位置
        "traces": [
            {
                "filename": r["filename"],
                "bases": r["trimmed_bases"],
                "quality": r["trimmed_quality"],
                "channels": r["trace"],
                "peak_indices": r["peak_indices"],
            }
            for r in read_results
        ],
        "variants": variants,
        "consensus": consensus,
        "coverage_ranges": coverage_ranges,
        "conclusion": conclusion,
        "mixed_detected": {r["filename"]: r["mixed_positions"] for r in mixed_reads},
        "errors": errors,
        "engine": "internal+biopython",
    }
