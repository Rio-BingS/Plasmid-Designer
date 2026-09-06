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


def _read_grade(trimmed: str, trimmed_q: List[int]) -> Tuple[str, float]:
    """Read 质量评级（ClinQC/Mutation Surveyor 类工具的 QC 口径）

    A：Q20 比例 ≥90%、修剪后 ≥400bp、无 N
    C：Q20 比例 <70% 或修剪后 <100bp
    B：其余
    返回 (等级, Q20 比例)
    """
    q20 = sum(1 for q in trimmed_q if q >= 20)
    q20_ratio = round(q20 / len(trimmed_q), 3) if trimmed_q else 0.0
    n_count = trimmed.upper().count("N")
    if q20_ratio >= 0.9 and len(trimmed) >= 400 and n_count == 0:
        return "A", q20_ratio
    if q20_ratio < 0.7 or len(trimmed) < 100:
        return "C", q20_ratio
    return "B", q20_ratio


def _variant_confidence(v: Dict, mixed_positions: set) -> str:
    """变异置信度分级（Mutation Surveyor 置信评分的简化口径）

    - 变异位点落在该 read 的混合峰列表 → 低（疑似杂合/混合，须人工看峰）
    - 多 read 支持且平均 Q≥25 → 高
    - 单 read：Q≥40 高；25–39 中；<25 低
    """
    if v.get("read_pos") and v["read_pos"] in mixed_positions:
        return "low"
    q = v.get("read_q") or 0
    support = v.get("support_reads") or 1
    if q < 25:
        return "medium" if support >= 2 else "low"
    if q >= 40:
        return "high"
    return "high" if support >= 2 else "medium"


def _coverage_gaps(covered_ranges: List[Tuple[int, int]], length: int,
                   min_len: int = 100, cap: int = 10) -> List[Dict]:
    """覆盖缺口（未覆盖区间）按长度降序，供补测建议"""
    gaps: List[Dict] = []
    prev = 0
    for s, e in covered_ranges:
        if s - prev - 1 >= min_len:
            gaps.append({"start": prev + 1, "end": s - 1, "length": s - prev - 1})
        prev = max(prev, e)
    if length - prev >= min_len:
        gaps.append({"start": prev + 1, "end": length, "length": length - prev})
    gaps.sort(key=lambda g: -g["length"])
    return gaps[:cap]


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

    ALT_START_CODONS = {"GTG", "TTG", "ATT", "CTG", "ATC", "ATA"}  # 细菌替代起始

    def _translate(seq: str, strand: str) -> str:
        s = str(Seq(seq).reverse_complement()) if strand == "-" else seq
        s = s[:len(s) - len(s) % 3]  # 移码可能留下不足整密码子的尾部
        prot = str(Seq(s).translate(table=11))
        if prot and s[:3] in ALT_START_CODONS:
            prot = "M" + prot[1:]  # 细菌替代起始密码子按惯例显示为 M
        return prot[:-1] if prot.endswith("*") else prot  # 去掉末尾终止密码子

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
                # 插入位于 pos 与 pos-1 之间：仅当插入点严格落在 CDS 内部才影响该 CDS；
                # 紧贴起点之前（pos == start）的插入不改变 CDS 自身序列
                if start < pos <= end:
                    out.extend(d["cons_base"])
                out.append(ref[pos - 1])
            else:
                out.append(d["cons_base"])  # 替换
        return "".join(out)

    def _protein_alignment(ref_prot: str, alt_prot: str) -> Dict:
        """蛋白层面全局比对（BLOSUM62，blastp 同款 gap 罚分；HGVS 要求的对比方式）

        返回一致残基数、首个分歧参考位置（0-based，含 gap 事件）、
        错义替换清单（仅对齐块内）与残基水平的插入/缺失总数。
        """
        from Bio import Align

        aligner = Align.PairwiseAligner()
        try:
            from Bio.Align import substitution_matrices
            aligner.substitution_matrix = substitution_matrices.load("BLOSUM62")
        except Exception:
            aligner.match_score = 2
            aligner.mismatch_score = -1
        aligner.open_gap_score = -11
        aligner.extend_gap_score = -1
        best = aligner.align(ref_prot, alt_prot)[0]

        blocks = best.aligned
        identical = 0
        aligned_ref = 0
        aligned_alt = 0
        first_diff: Optional[int] = None
        prev_ref_end = 0
        subs: List[str] = []
        for (rs, re_), (qs, qe_) in zip(blocks[0], blocks[1]):
            if rs > prev_ref_end and first_diff is None:
                first_diff = prev_ref_end  # 比对 gap：参考缺失或 alt 插入残基
            for k in range(re_ - rs):
                a, b = ref_prot[rs + k], alt_prot[qs + k]
                if a == b:
                    identical += 1
                else:
                    if first_diff is None:
                        first_diff = rs + k
                    subs.append(f"{a}{rs + k + 1}{b}")
            aligned_ref += re_ - rs
            aligned_alt += qe_ - qs
            prev_ref_end = re_
        if first_diff is None and aligned_ref < len(ref_prot):
            first_diff = aligned_ref  # 尾部参考残基在 alt 中缺失
        return {
            "identical": identical,
            "aligned_ref": aligned_ref,
            "aligned_alt": aligned_alt,
            "first_diff": first_diff if first_diff is not None else len(ref_prot),
            "subs": subs,
            "gap_residues": (len(ref_prot) - aligned_ref) + (len(alt_prot) - aligned_alt),
        }

    reports: List[Dict] = []
    for f in features or []:
        if f.get("type") != "CDS":
            continue
        start, end = int(f["start"]), int(f["end"])
        if start < 1 or end > len(ref) or start > end:
            continue
        strand = f.get("strand") or "+"
        cov_pct = _span_coverage(start, end)
        frame_note = (
            "" if (end - start + 1) % 3 == 0
            else f"（注意：该特征长度 {end - start + 1} bp 不是 3 的倍数，翻译按参考阅读框截断）"
        )
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
                "consequences": [],
                "synonymous_count": 0,
                "verdict": "未被测序覆盖，无法判定，建议补充覆盖该区域的引物",
            })
            continue

        ref_prot = _translate(ref[start - 1:end], strand)
        alt_nt_full = _rebuild(start, end)
        alt_prot_raw = _translate(alt_nt_full, strand)
        stop_idx = alt_prot_raw.find("*")  # 内部终止（-1 为无）
        alt_prot = alt_prot_raw[:stop_idx] if stop_idx >= 0 else alt_prot_raw

        # 变体是否影响该 CDS：替换/缺失按碱基区间与 CDS 相交判定；
        # 插入发生在 ref_pos 与 ref_pos+1 之间，插入点严格落在 CDS 内部
        #（anchor ∈ [start, end-1]）才影响该 CDS，紧贴边界的插入不改变 CDS 自身序列
        def _affects(v: Dict) -> bool:
            vlen = int(v.get("length") or 1)
            if v.get("type") == "insertion":
                return start <= v["ref_pos"] < end
            if v.get("type") == "deletion":
                return not (v["ref_pos"] + vlen - 1 < start or v["ref_pos"] > end)
            return start <= v["ref_pos"] <= end

        in_cds = [v for v in variants if _affects(v)]
        indels = [v for v in in_cds if v.get("type") in ("insertion", "deletion")]
        # 移码自判定：影响该 CDS 的 indel 长度非 3 的倍数即为移码
        #（不依赖全局注释——注释器只看变体自身所在特征，会漏掉边界插入）
        frameshifts = [v for v in indels if int(v.get("length") or 1) % 3 != 0]
        inframe_ins = sum(1 for v in indels if v["type"] == "insertion" and int(v.get("length") or 1) % 3 == 0)
        inframe_del = sum(1 for v in indels if v["type"] == "deletion" and int(v.get("length") or 1) % 3 == 0)
        protein_identical = ref_prot == alt_prot_raw

        # 起始/终止密码子状态（参考方向核酸层面判定，参照 VEP/snpEff 的 start_lost/stop_lost）
        from Bio.SeqUtils import seq3

        def _orient(s: str) -> str:
            return str(Seq(s).reverse_complement()) if strand == "-" else s

        start_codons = {"ATG"} | ALT_START_CODONS
        stop_codons = {"TAA", "TAG", "TGA"}
        ref_o = _orient(ref[start - 1:end])
        alt_o = _orient(alt_nt_full)
        alt_o_codons = alt_o[:len(alt_o) - len(alt_o) % 3]
        start_lost = ref_o[:3] in start_codons and alt_o[:3] not in start_codons
        # 移码下整个下游阅读框已改变，参考终止密码子无从谈起，不报 stop_lost
        stop_lost = (
            not frameshifts
            and len(ref_o) >= 3 and ref_o[-3:] in stop_codons
            and (len(alt_o_codons) < 3 or alt_o_codons[-3:] not in stop_codons)
        )

        # CDS 区间内的 DNA 替换数（用于同义突变统计）
        dna_subs = sum(
            1 for pos, d in diff_by_pos.items()
            if start <= pos <= end and d["ref_base"] != "-" and d["cons_base"] != "-"
        )

        aln = _protein_alignment(ref_prot, alt_prot)
        first_diff = aln["first_diff"]  # 0-based；一致前缀长度
        aa_diffs = [] if frameshifts else aln["subs"]  # 移码区的“替换”是移码噪声，不列
        # 同义计数仅在无移码/无内部终止时可靠（错义数来自蛋白比对）
        synonymous = max(0, dna_subs - len(aa_diffs)) if not frameshifts and stop_idx < 0 else 0

        # Sequence Ontology 标准后果词表（与 VEP/snpEff/bcftools csq 对齐，按影响从高到低）
        consequences: List[str] = []
        if stop_idx >= 0:
            consequences.append("stop_gained")
        if stop_lost:
            consequences.append("stop_lost")
        if start_lost:
            consequences.append("start_lost")
        if frameshifts:
            consequences.append("frameshift_variant")
        if inframe_ins:
            consequences.append("inframe_insertion")
        if inframe_del:
            consequences.append("inframe_deletion")
        if aa_diffs:
            consequences.append("missense_variant")
        if synonymous:
            consequences.append("synonymous_variant")

        if protein_identical:
            parts = [f"翻译产物与参考一致（{len(ref_prot)} aa，蛋白层面完全比对）"]
            if synonymous:
                parts[0] += f"；存在 {synonymous} 处同义突变（DNA 变、蛋白不变）"
        else:
            parts = ["翻译产物与参考不一致"]
            prefix = f"前 {first_diff} aa 与参考一致" if first_diff > 0 else "自第 1 aa 起即存在差异"
            details: List[str] = []
            if stop_idx >= 0:
                details.append(
                    f"无义突变使翻译提前终止于第 {stop_idx + 1} aa（产物 {len(alt_prot)} aa，参考 {len(ref_prot)} aa）"
                )
            if start_lost:
                details.append(f"起始密码子改变（{ref_o[:3]} → {alt_o[:3]}）")
            if stop_lost:
                details.append("终止密码子丢失，翻译将读穿至下游")
            if frameshifts:
                # HGVS：fs 位点 1 号计新阅读框，终止给出 fsTerN；3 字符氨基酸码
                if first_diff < len(ref_prot) and first_diff < len(alt_prot):
                    ter = stop_idx + 1 - first_diff if stop_idx >= 0 else None
                    fs = (
                        f"p.{seq3(ref_prot[first_diff])}{first_diff + 1}{seq3(alt_prot[first_diff])}fs"
                        + (f"Ter{ter}" if ter and ter > 0 else "")
                    )
                else:
                    fs = ""
                details.append(
                    f"{len(frameshifts)} 处移码使自第 {first_diff + 1} aa 起阅读框改变"
                    + (f"（{fs}）" if fs else "")
                    + "，其后产物不可与参考逐位比对"
                )
            else:
                if inframe_ins or inframe_del:
                    n_bp = sum(int(v.get("length") or 1) for v in in_cds
                               if v.get("type") in ("insertion", "deletion")
                               and int(v.get("length") or 1) % 3 == 0)
                    details.append(f"框内插入/缺失 {n_bp} bp（阅读框保持）")
                if len(alt_prot) != len(ref_prot):
                    details.append(f"翻译产物长度改变（{len(ref_prot)} → {len(alt_prot)} aa）")
                if aln["gap_residues"]:
                    details.append(f"存在 {aln['gap_residues']} 个残基的插入/缺失")
                if aa_diffs:
                    preview = "、".join(aa_diffs[:5]) + ("等" if len(aa_diffs) > 5 else "")
                    details.append(f"错义替换 {len(aa_diffs)} 处（{preview}）")
                if synonymous:
                    details.append(f"另有 {synonymous} 处同义突变")
            parts.append(prefix + ("；" + "；".join(details) if details else ""))
        verdict = "；".join(parts)
        verdict = (
            f"CDS 覆盖 {cov_pct}%（未覆盖部分按参考填充、未验证），已测区域{verdict}"
            if cov_pct < 99
            else f"CDS 完整覆盖，{verdict}"
        )
        verdict += frame_note
        reports.append({
            **base,
            "coverage_status": "full" if cov_pct >= 99 else "partial",
            "ref_protein_length": len(ref_prot),
            "alt_protein_length": len(alt_prot),
            "protein_identical": protein_identical,
            "premature_stop_aa": stop_idx + 1 if stop_idx >= 0 else None,
            "frameshift_count": len(frameshifts),
            "aa_changes": aa_diffs[:10],
            "consequences": consequences,
            "synonymous_count": synonymous,
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
        grade, q20_ratio = _read_grade(trimmed, trimmed_q)

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
            "grade": grade,
            "q20_ratio": q20_ratio,
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

    # 变异置信度（Mutation Surveyor 式评估的简化口径）
    mixed_by_read = {r["filename"]: set(r["mixed_positions"]) for r in read_results}
    for v in variants:
        v["confidence"] = _variant_confidence(v, mixed_by_read.get(v.get("read", ""), set()))

    consensus = _build_consensus(ref, read_results)

    coverage_ranges = merge_coverage(
        [(r["alignment"]["ref_start"], r["alignment"]["ref_end"]) for r in read_results],
        len(ref),
    )
    coverage_gaps = _coverage_gaps(coverage_ranges, len(ref))

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
            gap_hint = ""
            if coverage_gaps:
                g = coverage_gaps[0]
                gap_hint = (
                    f"；共 {len(coverage_gaps)} 段未覆盖缺口（最大 {g['start']}-{g['end']}，{g['length']}bp），"
                    "建议从已测区边缘设计引物补测"
                )
            lines.append(f"注意：仍有 {100 - consensus['coverage_percent']:.1f}% 区域未被测序覆盖，建议补充引物{gap_hint}")
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
        "coverage_gaps": coverage_gaps,
        "cds_reports": cds_reports,
        "conclusion": conclusion,
        "mixed_detected": {r["filename"]: r["mixed_positions"] for r in mixed_reads},
        "errors": errors,
        "engine": "internal+biopython",
    }
