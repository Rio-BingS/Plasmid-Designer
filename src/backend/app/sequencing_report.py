"""批量测序分析「整理包」构建（网页端复刻离线脚本的整理与报告产物）

离线批处理（scripts/batch_sequencing_report.py）在本地输出目录里产出：
按质粒归档的文件副本（只复制不覆盖）、每组 测序分析报告.md、分析结果.json、
整理清单.csv（原始路径 + MD5 可追溯）与结论回填的信息表。网页端批量分析
拿不到本地路径（浏览器一次上传、服务器不落盘），改为分析完成后按上传
原始字节在内存里打包 ZIP，经 GET /api/sequencing/batches/{batch_id}/report
下载，缓存时效清理见 sequencing_routes._sweep_expired。

归档结构（2026-09 起按交付整理习惯调整，与离线脚本的平铺结构分道）：
同一质粒一个文件夹（信息表里 '17648'/'MBYSTC'/'17648 MBYSTC' 等不同写法
按表内别名合并，口径与 core.sanger.batch 一致），其下固定 正确/ 错误/ 两个
子文件夹按各组一句话结论分置文件与报告（合格→正确、不合格→错误，分析
失败/缺图谱等无法判定的归 无法判定/），参考图谱放质粒文件夹根。

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
from typing import Dict, List, Optional, Tuple

# 表内别名判定的分隔符不敏感形式与 core.sanger.batch 共用一份实现，保证
# 归档合并与图谱共享对「同一质粒的不同写法」口径完全一致
from core.sanger.batch import _squash

ZIP_ROOT = "测序整理"


def _safe_component(name: str) -> str:
    """zip 条目路径段安全化：去掉路径分隔符/Windows 非法字符，限长"""
    t = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(name)).strip(" ._")
    return t[:80] or "unnamed"


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _conclusion_bucket(conclusion: str) -> str:
    """按一句话结论把组归入质粒文件夹下的子文件夹：合格→正确，不合格→错误；
    其余（分析失败、缺参考图谱、没匹配到 reads 等）无法自动判定 → 无法判定"""
    if conclusion.startswith("合格"):
        return "正确"
    if conclusion.startswith("不合格"):
        return "错误"
    return "无法判定"


def _plasmid_folders(plasmids: List[str]) -> Dict[str, str]:
    """归档文件夹名：表内别名写法并入同一质粒文件夹——X（分隔符不敏感）恰为
    另一名称 Y 的某一空白分隔段（'17648' ⊂ '17648 MBYSTC'）且宿主唯一时视为
    同一质粒并入 Y；宿主多于一个（'17648' 同时是 17648 A/17648 B 的段）或
    无宿主（'17648' 与 'MBYSTC' 并存但表中无全名行）时维持自身，与
    core.sanger.batch 的表内别名共享同一「候选不唯一不猜」口径。
    返回 {原始名: 文件夹名}。"""
    uniq = sorted({p for p in plasmids if p})
    rep: Dict[str, str] = {}
    for n in uniq:                     # 仅大小写/分隔符不同的写法共用一个文件夹
        rep.setdefault(_squash(n), n)
    reps = sorted(set(rep.values()))
    segs = {n: {_squash(t) for t in re.split(r"\s+", n.strip()) if t} for n in reps}
    host: Dict[str, Optional[str]] = {}
    for n in reps:
        cands = [m for m in reps if m != n and _squash(n) in segs[m]]
        host[n] = cands[0] if len(cands) == 1 else None
    final: Dict[str, str] = {}
    for n in reps:                     # 传递并入最终宿主（环兜底限步数）
        cur, hops = n, 0
        while host.get(cur) and hops <= len(reps):
            cur = host[cur]
            hops += 1
        final[n] = cur
    return {p: final[rep[_squash(p)]] if p else "sample" for p in plasmids}


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
    # 双峰（疑似混合）检测：次级峰 >30% 的位点计为双峰位点（已剔除 poly 滑移
    # 伪影与饱和峰拖影），read 级分级——widespread 提示混有第二种质粒
    flagged = []
    if record and record["reads"]:
        for r in record["reads"]:
            p = r.get("mixed_profile") or {}
            if p.get("count"):
                flagged.append((r, p))
    if flagged:
        lines += [
            "## 双峰（疑似混合）检测", "",
            "次级峰面积占主峰比例 >30% 的位点计为双峰位点（poly 下游滑移伪影与"
            "饱和峰拖影已剔除）。多个分散双峰位点提示样品可能混有第二种质粒"
            "（两个单克隆的混合培养物），此时主峰序列只代表多数克隆。", "",
            "| read | 判定 | 双峰位点 | 次峰占比(中位) | 估计次要克隆占比 | 位点（前10个） |",
            "|---|---|---|---|---|---|",
        ]
        for r, p in flagged:
            cls = p.get("class")
            label = ("**疑似混合样品**" if cls == "widespread"
                     else "个别双峰位点" if cls == "scattered" else "—")
            pct = (f"{round((p.get('median_ratio') or 0) * 100)}%"
                   if p.get("median_ratio") is not None else "—")
            frac = p.get("minor_fraction")
            frac_txt = f"约 {round(frac * 100)}%" if frac else "—"
            span = p.get("span")
            span_txt = f"（跨 {span[0]}–{span[1]}）" if span else ""
            pos_txt = "、".join(str(x) for x in p["positions"][:10]) + span_txt
            if p.get("pullup_excluded"):
                pos_txt += f"；另有 {p['pullup_excluded']} 处饱和峰拖影已剔除"
            lines.append(f"| {r['filename']} | {label} | {p['count']} | {pct} | {frac_txt} | {pos_txt} |")
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

    归档布局：同一质粒（含表内别名写法合并，见 _plasmid_folders）一个文件夹，
    参考图谱在文件夹根；各组文件与报告按结论分入 正确/ 错误/（无法判定的入
    无法判定/），正确/错误 两个子文件夹固定保留（空置也给出，结构可预期）。
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

    items = payload["items"]
    folders = _plasmid_folders([g["plasmid"] or "" for g in groups])

    # 预分桶：图谱与 read 皆空的组（未找到任何东西）无可归档，不产文件夹
    buckets: Dict[Tuple[str, str], List[int]] = {}
    for i, (g, item) in enumerate(zip(groups, items)):
        if not (g["reference"] or g["reads"]):
            continue
        folder = _safe_component(folders.get(g["plasmid"] or "") or "sample")
        buckets.setdefault(
            (folder, _conclusion_bucket(item.get("conclusion") or "")), []).append(i)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 每个质粒文件夹固定给出 正确/错误 子文件夹（空置也保留）；无法判定/
        # 只在确有内容时出现
        for folder in sorted({folder for folder, _b in buckets}):
            for d in ("正确", "错误"):
                zf.writestr(zipfile.ZipInfo(f"{ZIP_ROOT}/{folder}/{d}/"), b"")

        ref_archived: Dict[str, set] = {}   # 质粒文件夹 → 已归档图谱 (文件名, MD5)
        for (folder, bucket), idxs in buckets.items():
            # 同桶多组（克隆模式多克隆 / 表内别名写法并档）时报告与 json 带组名
            # 后缀区分；单组保持干净名
            multi = len(idxs) > 1
            for i in idxs:
                g, item = groups[i], items[i]
                plasmid, clone = g["plasmid"], g["clone"]
                if g["reference"]:
                    e = g["reference"]["file"]
                    data = e.get("bytes") or b""
                    seen = ref_archived.setdefault(folder, set())
                    key = (e.get("name"), _md5(data))
                    if key not in seen:     # 别名组/同质粒克隆共享同一图谱：只归档一份
                        seen.add(key)
                        arc = _arc(folder, _safe_component(e.get("name") or "reference"))
                        zf.writestr(arc, data)
                        manifest.append({
                            "质粒": plasmid or "", "克隆": clone or "",
                            "文件": e.get("name") or arc.rsplit("/", 1)[-1],
                            "类型": "参考图谱", "结论": "",
                            "匹配方式": g["reference"]["how"], "归档路径": arc,
                            "大小KB": round(len(data) / 1024, 1), "MD5": key[1],
                        })
                copied = []
                if g["reference"]:
                    copied.append({"name": g["reference"]["file"].get("name") or "reference",
                                   "kind": "参考图谱", "how": g["reference"]["how"]})
                for r in g["reads"]:
                    e = r["file"]
                    data = e.get("bytes") or b""
                    arc = _arc(f"{folder}/{bucket}", _safe_component(e.get("name") or "read"))
                    zf.writestr(arc, data)
                    copied.append({"name": e.get("name") or "read",
                                   "kind": "测序 read", "how": r["how"]})
                    manifest.append({
                        "质粒": plasmid or "", "克隆": clone or "",
                        "文件": e.get("name") or arc.rsplit("/", 1)[-1],
                        "类型": "测序 read", "结论": item.get("conclusion") or "",
                        "匹配方式": r["how"], "归档路径": arc,
                        "大小KB": round(len(data) / 1024, 1), "MD5": _md5(data),
                    })
                record = records.get(item["analysis_id"]) if item.get("analysis_id") else None
                gname = item.get("clone") or plasmid or "sample"
                rname = (f"测序分析报告-{_safe_component(gname)}.md" if multi
                         else "测序分析报告.md")
                zf.writestr(_arc(f"{folder}/{bucket}", rname),
                            _group_report_md(item, record, copied))
                if record:
                    dump = {k: v for k, v in record.items()
                            if k not in ("_trace_data", "_created_ts", "reference")}
                    jname = (f"分析结果-{_safe_component(gname)}.json" if multi
                             else "分析结果.json")
                    zf.writestr(_arc(f"{folder}/{bucket}", jname),
                                json.dumps(dump, ensure_ascii=False, indent=1))

        for u in unmatched:
            entry = u["file"]
            data = entry.get("bytes") or b""
            arc = _arc("未匹配文件", _safe_component(entry.get("name") or "file"))
            zf.writestr(arc, data)
            manifest.append({
                "质粒": "", "克隆": "", "文件": entry.get("name") or arc.rsplit("/", 1)[-1],
                "类型": "未匹配", "结论": "", "匹配方式": u["reason"], "归档路径": arc,
                "大小KB": round(len(data) / 1024, 1), "MD5": _md5(data),
            })

        # 整理清单（可追溯：归档路径 + MD5 + 该组结论）
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=["质粒", "克隆", "文件", "类型", "结论",
                                            "匹配方式", "归档路径", "大小KB", "MD5"])
        w.writeheader()
        w.writerows(manifest)
        zf.writestr(f"{ZIP_ROOT}/整理清单.csv", "\ufeff" + out.getvalue())

        # 批次总览：各组结论 + 未匹配清单
        lines = ["批量测序分析整理包",
                 f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
                 f"分组模式：{'克隆模式（每行一个克隆独立分析）' if payload.get('clone_mode') else '质粒模式'}",
                 "归档结构：每个质粒一个文件夹（表内不同写法已合并），参考图谱在其根；"
                 "其下 正确/ 错误/ 按各组一句话结论分置（合格→正确、不合格→错误，"
                 "分析失败或缺图谱等无法判定的归 无法判定/）",
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
