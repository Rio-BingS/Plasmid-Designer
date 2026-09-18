"""批量测序分析「整理包」构建（网页端复刻离线脚本的整理与报告产物）

离线批处理（scripts/batch_sequencing_report.py）在本地输出目录里产出：
按质粒归档的文件副本（只复制不覆盖）、每组 测序分析报告.md、分析结果.json、
整理清单.csv（原始路径 + MD5 可追溯）与结论回填的信息表。网页端批量分析
拿不到本地路径（浏览器一次上传、服务器不落盘），改为分析完成后按上传
原始字节在内存里打包同构 ZIP，经 GET /api/sequencing/batches/{batch_id}/report
下载，缓存时效清理见 sequencing_routes._sweep_expired。

本模块只做与路由无关的纯构建：输入（归组、分析记录、信息表）均为已成形
数据，输出 ZIP 字节与回填后的信息表字节。
"""

import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime
from typing import Dict, List, Optional

ZIP_ROOT = "测序整理"


def _safe_component(name: str) -> str:
    """zip 条目路径段安全化：去掉路径分隔符/Windows 非法字符，限长"""
    t = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(name)).strip(" ._")
    return t[:80] or "unnamed"


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _fmt_change(v) -> str:
    t = v["type"]
    if t == "substitution":
        return f"{v.get('ref_base', '?')}>{v.get('alt_base', '?')}"
    if t == "deletion":
        return f"Δ{v.get('length', 1)}bp"
    if t == "insertion":
        return f"+{v.get('alt_base', '?')}"
    return t


def _fmt_evidence(v) -> str:
    ev = v.get("peak_evidence") or {}
    parts = []
    if ev.get("mutant_pct") is not None:
        parts.append(f"峰强比 {ev['mutant_pct']:.0f}%")
    if ev.get("snr") is not None:
        parts.append(f"信噪比 {ev['snr']:.1f}")
    if ev.get("insertion_peak_ratio") is not None:
        parts.append(f"插入峰强度 {ev['insertion_peak_ratio'] * 100:.0f}%")
    if v.get("corroborated_by_basecall"):
        parts.append("重basecall印证")
    return "、".join(parts) or "—"


def _group_report_md(item: Dict, record: Optional[Dict], copied: List[Dict]) -> str:
    """单分组 测序分析报告.md（与离线脚本 write_report_md 同构，数据来自
    内存分析记录而非本地 res：reads/variants/cds_reports 键一致）"""
    plasmid = item.get("plasmid") or "sample"
    label = f"{item['clone']} {plasmid}".strip() if item.get("clone") else plasmid
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    ref_name = item.get("reference_name")
    if record:
        engine_note = ""
        if record["engine"] == "internal+biopython":
            engine_note = "；服务器未安装 tracy，跳过重 basecall 交叉印证"
        head = [
            f"# {label} 测序分析报告", "",
            f"- 生成时间：{now}",
            f"- 分析引擎：{record['engine']}（平台 Sanger 全自动管线，网页批量分析{engine_note}）",
            f"- 参考图谱：{ref_name}（{len(record['reference'])} bp，"
            f"{len(record['features'])} 个特征）",
            "",
        ]
    else:
        head = [
            f"# {label} 测序分析报告", "",
            f"- 生成时间：{now}",
            "- 未运行序列分析",
            f"- 参考图谱：{ref_name or '缺失'}",
            "",
        ]
    lines = head + ["## 结论", "", f"> **{item['conclusion']}**", ""]
    if record and record["reads"]:
        lines += ["各编码区（CDS）结论：", ""]
        for c in record["cds_reports"]:
            mark = {"full": "✅", "partial": "🟡", "uncovered": "⚪"}.get(c["coverage_status"], "")
            lines.append(f"- {mark} **{c['name']}**（{c['covered_percent']}% 覆盖）：{c['verdict']}")
        lines.append("")
    if copied:
        lines += ["## 文件整理（原始文件未改动，此处为副本）", "",
                  "| 文件 | 类型 | 匹配方式 |", "|---|---|---|"]
        for e in copied:
            lines.append(f"| {e['name']} | {e['kind']} | {e['how']} |")
        lines.append("")
    if record and record["reads"]:
        lines += ["## 测序 read 概况", "",
                  "| 文件 | QC 等级 | 平均 Q | 有效长度 | 比对区段（参考坐标） |",
                  "|---|---|---|---|---|"]
        for r in record["reads"]:
            a = r["alignment"]
            lines.append(f"| {r['filename']} | {r['grade']} | {r['mean_q']} | {r['trimmed_length']} "
                         f"| {a['ref_start']}–{a['ref_end']} |")
        lines.append("")
    if record and record["variants"]:
        lines += ["## 变异明细", "",
                  "| 位置 | 类型 | 变化 | 置信度 | 测序Q | 支持read数 | 峰级证据 | 所在特征 | 氨基酸变化 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for v in record["variants"]:
            feats = "、".join(f["name"] for f in v.get("features", [])) or "—"
            aa = v.get("aa_change") or ("同义" if v.get("synonymous")
                                        else "移码" if v.get("frameshift") else "—")
            conf = {"high": "高", "medium": "中", "low": "低"}.get(v.get("confidence"), v.get("confidence"))
            lines.append(f"| {v['ref_pos']} | {v['type']} | {_fmt_change(v)} | {conf} "
                         f"| {v.get('read_q', '')} | {v.get('support_reads', 1)} "
                         f"| {_fmt_evidence(v)} | {feats} | {aa} |")
        lines += ["", "置信度说明：**高**=峰级证据与 Q 值充分支持；**中**=证据一般，建议关注；"
                  "**低**=疑似测序噪声，未计入结论。", ""]
    if record and record["errors"]:
        lines += ["## 分析中的错误", ""]
        lines += [f"- {e['filename']}: {e['error']}" for e in record["errors"]]
        lines.append("")
    return "\n".join(lines)


def backfill_excel(wb, cols: Optional[Dict], rows: Optional[List[Dict]],
                   items: List[Dict], clone_mode: bool) -> Optional[bytes]:
    """把各组结论回填信息表「测序结果」列（与离线脚本同规则：已有内容不覆盖，
    换行追加），返回回填后的 xlsx 字节；无可回填内容时返回 None。

    克隆模式按（质粒名, 克隆号）对应行——离线脚本不支持克隆模式，这是网页端
    的扩展；质粒模式按质粒名对应（多行同名质粒回填同一结论）。
    """
    if wb is None or cols is None or rows is None:
        return None
    concls: Dict = {}
    for it in items:
        concl = it.get("conclusion") or ""
        if not concl:
            continue
        if clone_mode and it.get("clone"):
            concls[(it["plasmid"], it["clone"])] = concl
        else:
            concls.setdefault(it["plasmid"], concl)
    filled = 0
    ws = wb.worksheets[0]
    for row in rows:
        key = (row["name"], row["clone"]) if clone_mode else row["name"]
        concl = concls.get(key)
        if not concl:
            continue
        cell = ws.cell(row=row["row"], column=cols["result"])
        old = str(cell.value or "").strip()
        cell.value = concl if not old or old == concl else f"{old}\n{concl}"
        filled += 1
    if not filled:
        return None
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_batch_zip(
    payload: Dict,
    groups: List[Dict],
    unmatched: List[Dict],
    excel_name: Optional[str],
    excel_original: Optional[bytes],
    excel_filled: Optional[bytes],
    records: Dict[str, Dict],
) -> bytes:
    """构建整理包 ZIP（在内存中完成，调用方负责缓存与时效）

    payload：analyze-batch 的对外 JSON（batch_id/created_at/clone_mode/items/
    unmatched/ignored_files）；groups：_run_batch 的归组（条目含原始字节）；
    unmatched：未匹配原始条目 [{"file","reason"}]；records：analysis_id →
    内存分析记录（供报告与 分析结果.json 取数）。groups 与 payload["items"]
    由 _run_batch 保证一一对应（每组恰好产出一个 item，顺序一致）。
    """
    manifest: List[Dict] = []
    used_arcs: set = set()          # 全局防 zip 条目冲突
    used_in_dir: Dict[str, set] = {}   # 同一文件夹内防同名（跨文件夹允许重名）

    def _arc(arcdir: str, base: str) -> str:
        seen = used_in_dir.setdefault(arcdir, set())
        name = base
        n = 2
        while name in seen:
            stem, dot, suffix = base.rpartition(".")
            name = (f"{stem} ({n}).{suffix}" if dot else f"{base} ({n})")
            n += 1
        seen.add(name)
        arc = f"{ZIP_ROOT}/{arcdir}/{name}" if arcdir else f"{ZIP_ROOT}/{name}"
        while arc in used_arcs:      # 理论到不了（目录内已唯一），兜底防冲突
            arc += "_"
        used_arcs.add(arc)
        return arc

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for g, item in zip(groups, payload["items"]):
            plasmid, clone = g["plasmid"], g["clone"]
            parts = [_safe_component(plasmid or "sample")]
            if payload.get("clone_mode") and clone:
                parts.append(_safe_component(clone))
            arcdir = "/".join(parts)
            copied = []
            if g["reference"]:
                copied.append({"name": g["reference"]["file"].get("name") or "reference",
                               "kind": "参考图谱", "how": g["reference"]["how"]})
            for r in g["reads"]:
                copied.append({"name": r["file"].get("name") or "read",
                               "kind": "测序 read", "how": r["how"]})
            if not copied:
                continue
            for e in ([g["reference"]] if g["reference"] else []) + g["reads"]:
                entry = e["file"]
                data = entry.get("bytes") or b""
                arc = _arc(arcdir, _safe_component(entry.get("name") or "file"))
                zf.writestr(arc, data)
                manifest.append({
                    "质粒": plasmid or "", "克隆": clone or "",
                    "文件": entry.get("name") or arc.rsplit("/", 1)[-1],
                    "类型": "参考图谱" if e is g["reference"] else "测序 read",
                    "匹配方式": e["how"], "归档路径": arc,
                    "大小KB": round(len(data) / 1024, 1), "MD5": _md5(data),
                })
            record = records.get(item["analysis_id"]) if item.get("analysis_id") else None
            zf.writestr(_arc(arcdir, "测序分析报告.md"), _group_report_md(item, record, copied))
            if record:
                dump = {k: v for k, v in record.items()
                        if k not in ("_trace_data", "_created_ts", "reference")}
                zf.writestr(_arc(arcdir, "分析结果.json"),
                            json.dumps(dump, ensure_ascii=False, indent=1))

        for u in unmatched:
            entry = u["file"]
            data = entry.get("bytes") or b""
            arc = _arc("未匹配文件", _safe_component(entry.get("name") or "file"))
            zf.writestr(arc, data)
            manifest.append({
                "质粒": "", "克隆": "", "文件": entry.get("name") or arc.rsplit("/", 1)[-1],
                "类型": "未匹配", "匹配方式": u["reason"], "归档路径": arc,
                "大小KB": round(len(data) / 1024, 1), "MD5": _md5(data),
            })

        # 整理清单（可追溯：归档路径 + MD5）
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=["质粒", "克隆", "文件", "类型", "匹配方式",
                                            "归档路径", "大小KB", "MD5"])
        w.writeheader()
        w.writerows(manifest)
        zf.writestr(f"{ZIP_ROOT}/整理清单.csv", "\ufeff" + out.getvalue())

        # 批次总览：各组结论 + 未匹配清单
        lines = ["批量测序分析整理包",
                 f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
                 f"分组模式：{'克隆模式（每行一个克隆独立分析）' if payload.get('clone_mode') else '质粒模式'}",
                 "", f"各组结论（{len(payload['items'])} 组）："]
        for it in payload["items"]:
            label = f"{it['plasmid']}（克隆 {it['clone']}）" if it.get("clone") else it["plasmid"]
            cov = (f"覆盖 {it['coverage_percent']}%" if it.get("coverage_percent") is not None
                   else "覆盖 —")
            lines.append(f"  [{it['status']}] {label} — reads={it['read_count']}，{cov}，"
                         f"确证差异 {it['variant_count']} — {it['conclusion']}")
        if payload["unmatched"]:
            lines += ["", f"未匹配文件（{len(payload['unmatched'])} 个，归档在 未匹配文件/）："]
            lines += [f"  {u['filename']} — {u['reason']}" for u in payload["unmatched"]]
        if payload.get("ignored_files"):
            lines += ["", f"已忽略无关文件：{'、'.join(payload['ignored_files'])}"]
        zf.writestr(f"{ZIP_ROOT}/批次总览.txt", "\n".join(lines))

        # 信息表：结论回填版 + 原始备份（无可回填结论时保留原表）
        if excel_name and excel_original:
            base = _safe_component(excel_name)
            if excel_filled:
                zf.writestr(f"{ZIP_ROOT}/{base}", excel_filled)
                zf.writestr(f"{ZIP_ROOT}/原始备份_{base}", excel_original)
            else:
                zf.writestr(f"{ZIP_ROOT}/{base}", excel_original)
    return buf.getvalue()
