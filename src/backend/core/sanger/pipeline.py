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

    consensus_chars: List[str] = []
    diffs: List[Dict] = []  # 共识与参考的差异位（供前端高亮，人工核对最终构建体）
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
            alt = best_key[1:]
            cons_index = len(consensus_chars)
            consensus_chars.extend(list(alt))
            # 插入只新增碱基，紧跟其后的参考碱基必须保留，
            # 否则共识序列会悄悄吞掉插入位点右侧的参考碱基
            consensus_chars.append(reference[pos - 1].upper())
            diffs.append({
                "ref_pos": pos, "ref_base": "-", "cons_base": alt, "cons_index": cons_index,
            })
        elif best_key == "-":
            diffs.append({
                "ref_pos": pos, "ref_base": reference[pos - 1].upper(), "cons_base": "-",
            })
        else:
            consensus_chars.append(best_key)
            if best_key != reference[pos - 1].upper():
                diffs.append({
                    "ref_pos": pos, "ref_base": reference[pos - 1].upper(),
                    "cons_base": best_key, "cons_index": len(consensus_chars) - 1,
                })

    covered_ranges = merge_coverage(
        [(i + 1, i + 1) for i, c in enumerate(covered) if c], L
    )
    coverage = sum(1 for c in covered if c) / L if L else 0.0
    return {
        "sequence": "".join(consensus_chars),
        "covered_ranges": covered_ranges,
        "coverage_percent": round(coverage * 100, 2),
        "diffs": diffs,
    }


def _build_cds_reports(
    ref: str, features: List[Dict], variants: List[Dict], consensus: Dict
) -> List[Dict]:
    """CDS 级别测序结论：覆盖完整性 + 共识重建 CDS 的翻译产物与参考逐位比对

    回答“整段 CDS 测序结果有没有问题”：
    - 覆盖：CDS 区间与共识覆盖区间求交，未覆盖部分按参考填充、不参与判定；
    - 蛋白：从共识差异重建 CDS 序列（按链方向翻译），与参考翻译逐位比对，
      给出一致/不一致、移码数、无义提前终止位置与氨基酸替换清单。
    """
    from Bio.Seq import Seq

    diff_by_pos: Dict[int, Dict] = {}
    for d in consensus.get("diffs", []):
        diff_by_pos[d["ref_pos"]] = d  # 每个参考位置至多一条共识差异
    covered_ranges = consensus.get("covered_ranges", [])

    def _span_coverage(start: int, end: int) -> float:
        cov = 0
        for s, e in covered_ranges:
            lo, hi = max(s, start), min(e, end)
            if hi >= lo:
                cov += hi - lo + 1
        length = end - start + 1
        return round(cov / length * 100, 1) if length else 0.0

    def _rebuild(start: int, end: int) -> str:
        """参考区间 [start, end] 的共识序列（替换/插入/缺失已应用，参考方向）"""
        out: List[str] = []
        for pos in range(start, end + 1):
            d = diff_by_pos.get(pos)
            if d is None:
                out.append(ref[pos - 1])
            elif d["cons_base"] == "-":
                continue  # 缺失：该参考碱基被删
            elif d["ref_base"] == "-":
                out.extend(d["cons_base"])  # 插入锚定在 pos 之前
                out.append(ref[pos - 1])
            else:
                out.append(d["cons_base"])  # 替换
        return "".join(out)

    def _translate(seq: str, strand: str) -> str:
        s = str(Seq(seq).reverse_complement()) if strand == "-" else seq
        s = s[:len(s) - len(s) % 3]  # 移码可能留下不足整密码子的尾部
        prot = str(Seq(s).translate(table=11))
        return prot[:-1] if prot.endswith("*") else prot  # 去掉末尾终止密码子

    reports: List[Dict] = []
    for f in features or []:
        if f.get("type") != "CDS":
            continue
        start, end = int(f["start"]), int(f["end"])
        if start < 1 or end > len(ref) or start > end:
            continue
        strand = f.get("strand") or "+"
        cov_pct = _span_coverage(start, end)
        base = {
            "name": f.get("name") or "CDS",
            "start": start,
            "end": end,
            "strand": strand,
            "covered_percent": cov_pct,
            "coverage_status": "full" if cov_pct >= 99 else ("uncovered" if cov_pct <= 0 else "partial"),
        }
        if cov_pct <= 0:
            reports.append({
                **base,
                "ref_protein_length": None,
                "alt_protein_length": None,
                "protein_identical": None,
                "premature_stop_aa": None,
                "frameshift_count": 0,
                "aa_changes": [],
                "verdict": "未被测序覆盖，无法判定，建议补充覆盖该区域的引物",
            })
            continue

        ref_prot = _translate(ref[start - 1:end], strand)
        alt_prot_raw = _translate(_rebuild(start, end), strand)
        stop_idx = alt_prot_raw.find("*")  # 内部终止（-1 为无）
        alt_prot = alt_prot_raw[:stop_idx] if stop_idx >= 0 else alt_prot_raw

        in_cds = [v for v in variants if start <= v["ref_pos"] <= end]
        frameshifts = [v for v in in_cds if v.get("frameshift")]
        protein_identical = ref_prot == alt_prot_raw

        aa_diffs: List[str] = []
        if not protein_identical and stop_idx < 0 and len(ref_prot) == len(alt_prot):
            aa_diffs = [
                f"{a}{i + 1}{b}"
                for i, (a, b) in enumerate(zip(ref_prot, alt_prot)) if a != b
            ]

        if protein_identical:
            parts = [f"翻译产物与参考一致（{len(ref_prot)} aa）"]
        else:
            parts = []
            if stop_idx >= 0:
                parts.append(f"无义突变使翻译提前终止于第 {stop_idx + 1} 位氨基酸")
            if len(alt_prot) != len(ref_prot):
                parts.append(f"翻译产物长度改变（{len(ref_prot)} → {len(alt_prot)} aa）")
            if frameshifts:
                parts.append(f"移码 {len(frameshifts)} 处")
            if aa_diffs:
                preview = "、".join(aa_diffs[:5]) + ("等" if len(aa_diffs) > 5 else "")
                parts.append(f"氨基酸替换 {len(aa_diffs)} 处（{preview}）")
            if not parts:
                parts.append("存在氨基酸差异")
            parts.insert(0, "翻译产物与参考不一致")
        verdict = "；".join(parts)
        verdict = (
            f"CDS 覆盖 {cov_pct}%（未覆盖部分按参考填充、未验证），已测区域{verdict}"
            if cov_pct < 99
            else f"CDS 完整覆盖，{verdict}"
        )
        reports.append({
            **base,
            "coverage_status": "full" if cov_pct >= 99 else "partial",
            "ref_protein_length": len(ref_prot),
            "alt_protein_length": len(alt_prot),
            "protein_identical": protein_identical,
            "premature_stop_aa": stop_idx + 1 if stop_idx >= 0 else None,
            "frameshift_count": len(frameshifts),
            "aa_changes": aa_diffs[:10],
            "verdict": verdict,
        })
    return reports


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
        # 阈值高于全部碱基 Q 值时修剪结果为空：记为错误条目，
        # 不能送进 PairwiseAligner（空序列会抛 ValueError → 500）
        if len(trimmed) < MIN_WINDOW:
            errors.append({
                "filename": filename,
                "error": f"Q≥{min_q} 的碱基不足 {MIN_WINDOW}bp（修剪后仅 {len(trimmed)}bp），无法比对",
            })
            continue
        mean_q = sum(trimmed_q) / len(trimmed_q) if trimmed_q else 0

        aln = align_read(trimmed, ref, trimmed_q)

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

    cds_reports = _build_cds_reports(ref, features, variants, consensus)

    # 自动结论（编码区结论放最前，直接回答“整段 CDS 有没有问题”）
    cds_lines = [
        f"【{cr['name']} CDS】{cr['verdict']}"
        for cr in cds_reports if cr["coverage_status"] != "uncovered"
    ]
    if not read_results:
        conclusion = "没有可分析的测序文件"
    elif not variants:
        conclusion = (
            f"构建序列与设计一致：{len(read_results)} 条 read 全部匹配，"
            f"覆盖参考序列的 {consensus['coverage_percent']:.1f}%"
        )
        conclusion += "\n" + "\n".join(cds_lines) if cds_lines else ""
    else:
        lines = [f"共检出 {len(variants)} 处差异（覆盖 {consensus['coverage_percent']:.1f}%）："]
        lines.extend(summarize_severity(variants))
        lines.extend(cds_lines)
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
        "cds_reports": cds_reports,
        "conclusion": conclusion,
        "mixed_detected": {r["filename"]: r["mixed_positions"] for r in mixed_reads},
        "errors": errors,
        "engine": "internal+biopython",
    }
