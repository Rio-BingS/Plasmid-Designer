"""tracy decompose 解卷积输出解析与等位判读（goal T10；A 3.6）

tracy decompose 的产物是 outprefix.json（-f json）与 outprefix.align1/
.align2（官方文档 gear-genomics.com/docs/tracy/cli），不产出 .1.fasta
一类后缀——旧实现只找 fasta 后缀，「混合样品自动解卷积」对用户不可见
地恒空。

解析顺序：JSON 优先（形状宽容：alleles[] / allele1+allele2 /
primary+secondary，序列校验 ACGTN-）→ 退回 outprefix.align1/.align2
（FASTA）。两条等位各自走与主流程同一套比对 + CDS 判读
（annotate_decomposed_allele），直接回答「主/次克隆各自是否正确」；
任一等位与设计不符由 verdict 记 allele_mismatch（pending）。

生产镜像内置 tracy；本机无 tracy 时测试以桩模拟产物（fixtures 按
tracy v0.9.1 文档格式存 tests/fixtures/tracy/decompose_*）。
"""

import json
import os
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional


def try_tracy_decompose(ab1_path: str, ref_fasta: str) -> Optional[List[Dict]]:
    """若 tracy 可用，对混合样品执行 decompose，返回两条等位基因序列。

    产物解析见模块 docstring；无 tracy / 运行失败 / 无可解析产物一律
    返回 None（调用方降级为不解卷积，终审 C-09 同款隔离）。"""
    from core.sanger.pipeline import TRACY_BIN  # 延迟导入避免环回

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
            alleles = _parse_decompose_output(out_prefix)
            return alleles or None
    except (OSError, subprocess.SubprocessError):
        return None


def _read_fasta_seq(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return "".join(line.strip() for line in fh if not line.startswith(">")).upper()


def _alleles_from_json(data) -> List[Dict]:
    """decompose JSON 的等位序列（形状按 tracy 版本有差异，做形状宽容）"""
    cands: List = []
    if isinstance(data, list):
        cands = data
    elif isinstance(data, dict):
        if isinstance(data.get("alleles"), list):
            cands = data["alleles"]
        else:
            for k in ("allele1", "allele2", "primary", "secondary"):
                if isinstance(data.get(k), str):
                    cands.append({"sequence": data[k]})
    out: List[Dict] = []
    for e in cands:
        seq = e if isinstance(e, str) else \
            (e.get("sequence") or e.get("seq") or "") if isinstance(e, dict) else ""
        seq = "".join(str(seq).split()).upper()
        if seq and set(seq) <= set("ACGTN-"):
            out.append({"sequence": seq, "source": "json"})
    return out


def _parse_decompose_output(out_prefix: str) -> List[Dict]:
    """产物解析：JSON 优先，退回 .align1/.align2（FASTA）"""
    jp = out_prefix + ".json"
    if os.path.isfile(jp):
        try:
            with open(jp, encoding="utf-8") as fh:
                als = _alleles_from_json(json.load(fh))
            if als:
                return als
        except (ValueError, OSError):
            pass  # JSON 缺失/结构不识别 → 退回 align 文件
    out: List[Dict] = []
    for suffix in (".align1", ".align2"):
        p = out_prefix + suffix
        if os.path.isfile(p):
            seq = _read_fasta_seq(p)
            if seq:
                out.append({"sequence": seq, "source": suffix})
    return out


def annotate_decomposed_allele(al: Dict, ref: str, features: List[Dict]) -> None:
    """解卷积等位走与主流程同一套比对 + CDS 判读（goal T10；A 3.6）。

    就地写入 cds_reports / variants / identity；判读失败降级为空报告，
    不拖垮整份分析（终审 C-09 同款隔离）。等位无跨 read 互检，蛋白判定
    按单 read 口径。"""
    from core.sanger.aligner import align_read, merge_coverage
    from core.sanger.pipeline import _cds_reports_safe
    from core.sanger import indel_norm

    try:
        aln = align_read(al.get("sequence") or "", ref)
        # 等位序列是线性化产物，indel 归一化按线性口径（环状折回不适用）
        vs = [indel_norm.normalize_left(ref, v)
              for v in (aln.get("variants") or [])]
        covered = merge_coverage([(int(aln["ref_start"]), int(aln["ref_end"]))],
                                 len(ref))
        cov = sum(e - s + 1 for s, e in covered) / max(1, len(ref)) * 100
        consensus = {"covered_ranges": covered,
                     "coverage_percent": round(cov, 1),
                     "verified_ranges": covered, "trusted_ranges": covered,
                     "sequence": al.get("sequence") or ""}
        al["cds_reports"] = _cds_reports_safe(ref, features, vs, consensus)
        al["variants"] = [{"ref_pos": v["ref_pos"], "type": v.get("type"),
                           "length": int(v.get("length") or 1),
                           "alt_base": v.get("alt_base")} for v in vs]
        al["identity"] = aln.get("identity")
    except Exception:  # noqa: BLE001
        al["cds_reports"] = []


def decompose_and_annotate(ab1_path: str, ref_fasta: str, ref_seq: str,
                           features: List[Dict]) -> Optional[List[Dict]]:
    """decompose + 每等位判读（routes 单点入口；goal T10）"""
    als = try_tracy_decompose(ab1_path, ref_fasta)
    if not als:
        return None
    for al in als:
        annotate_decomposed_allele(al, ref_seq, features)
    return als
