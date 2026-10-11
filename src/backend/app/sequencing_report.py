"""批量测序分析「整理包」构建（网页端与离线脚本共用）

整理包内容：按质粒归档的文件副本、每组 测序分析报告.md、分析结果.json、
整理清单.csv（归档路径 + MD5 可追溯）、批次总览.txt 与结论回填的信息表。
条目由 batch_archive_entries 统一生成，两端只是落地方式不同：

- 网页端批量分析拿不到本地路径（浏览器一次上传、服务器不落盘），按上传
  原始字节在内存里打包 ZIP（build_batch_zip），经
  GET /api/sequencing/batches/{batch_id}/report 下载，缓存时效清理见
  sequencing_routes._sweep_expired；
- 离线脚本（scripts/batch_sequencing_report.py）把同一份条目写到本地输出
  目录（原始文件只读不动，整理清单另记原始路径）。

归档结构（2026-09 起按交付整理习惯调整）：同一质粒一个文件夹（信息表里
'17648'/'MBYSTC'/'17648 MBYSTC' 等不同写法按表内别名合并，口径与
core.sanger.batch 一致），其下固定 正确/ 错误/ 两个子文件夹按各组一句话
结论分置文件与报告（合格→正确、不合格→错误，分析失败/缺图谱等无法判定
的归 无法判定/），参考图谱放质粒文件夹根。

本模块只做与路由无关的纯构建：输入（归组、分析记录、信息表）均为已成形
数据，输出归档条目 / ZIP 字节与回填后的信息表字节。
"""

import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Union

# 表内别名判定的分隔符不敏感形式与 core.sanger.batch 共用一份实现，保证
# 归档合并与图谱共享对「同一质粒的不同写法」口径完全一致
from core.sanger.batch import _squash
# 报告里 poly 结构的名称与峰图判读短语与网页卡共用同一实现（同一事件在
# 网页、结论文本、整理包报告里说法一致，避免各写各的）
from core.sanger.pipeline import (END_MARGIN, _peak_verdict_phrase,
                                  _pos_ranges_str, _run_label,
                                  alignment_span_text)

ZIP_ROOT = "测序整理"


_COMPONENT_MAX = 80
# 视为扩展名的后缀上限（含点）：超过的当成名字本身一起截断
_EXT_MAX = 16


def _fit_component(t: str, limit: int = _COMPONENT_MAX) -> str:
    """把已安全化的名字压到 limit 字符内：截断主名、保留扩展名
    （'…long.fasta' 截断后仍以 .fasta 结尾，下游软件可按扩展名打开）。
    未超长的名字原样返回。"""
    if len(t) <= limit:
        return t
    stem, dot, ext = t.rpartition(".")
    if dot and stem and 0 < len(ext) + 1 <= _EXT_MAX and len(ext) + 1 < limit:
        keep = stem[:limit - len(ext) - 1].rstrip(" .")
        if keep:
            return f"{keep}.{ext}"
    return t[:limit].rstrip(" .")


def _safe_component(name: str) -> str:
    """zip 条目路径段安全化：去掉路径分隔符/Windows 非法字符，限长 80 字符
    （超长时截断主名、保留扩展名；未超长的名字与此前完全相同）"""
    t = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(name)).strip(" ._")
    return _fit_component(t) or "unnamed"


def _unique_components(names: List[str]) -> Dict[str, str]:
    """一组互不相同的原始名 → 互不冲突的路径段 {原始名: 路径段}

    不同的名字安全化/截断后可能撞成同一段（'a/b' 与 'a:b' 都成 'a_b'，前
    80 字符相同的长名）；按忽略大小写比较（Windows/macOS 文件系统不区分），
    撞名时安全化前后不变的那个保留原段，其余按原始名排序依次加 ~2、~3…
    （仍不超过 80 字符）。结果只取决于这组名字本身，与出现顺序无关。"""
    by_key: Dict[str, List[str]] = {}
    for n in sorted(set(names)):
        by_key.setdefault(_safe_component(n).casefold(), []).append(n)
    taken = set(by_key)
    out: Dict[str, str] = {}
    for key, group in by_key.items():
        # 安全化后不变的名字优先保留原段（不因别的名字出现而改名）
        group.sort(key=lambda n: (_safe_component(n) != n, n))
        out[group[0]] = _safe_component(group[0])
        k = 2
        for n in group[1:]:
            base = _safe_component(n)
            while True:
                suffix = f"~{k}"
                cand = _fit_component(base, _COMPONENT_MAX - len(suffix)) + suffix
                k += 1
                if cand.casefold() not in taken:
                    break
            taken.add(cand.casefold())
            out[n] = cand
    return out


def _md_cell(value) -> str:
    """Markdown 表格单元格转义：| 会把行切成多列、换行会截断表格行
    （文件名、特征名等来自用户数据）；不含这两类字符的内容原样不变"""
    t = str(value)
    t = t.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return t.replace("|", "\\|")


def _md_row(*cells) -> str:
    return "| " + " | ".join(_md_cell(c) for c in cells) + " |"


# CSV 公式注入防护（OWASP）：以这些字符开头的单元格在 Excel/WPS 里会被当作
# 公式或 DDE 执行，写出时前缀 ' 当作文本
_CSV_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_needs_guard(t: str) -> bool:
    # 本身以 ' 开头、去掉后又需防护的（如原名就是 '=x）也加一层，保证能
    # 被 csv_unguard 无歧义还原
    return bool(t) and (t[0] in _CSV_FORMULA_START
                        or (t[0] == "'" and _csv_needs_guard(t[1:])))


def csv_guard(value):
    """整理清单 CSV 单元格防公式注入；非字符串（数值）原样返回"""
    if isinstance(value, str) and _csv_needs_guard(value):
        return "'" + value
    return value


def csv_unguard(value: str) -> str:
    """csv_guard 的逆运算（离线脚本重跑时读回上次清单的归档路径用）"""
    if value.startswith("'") and _csv_needs_guard(value[1:]):
        return value[1:]
    return value


def csv_guard_row(row: Dict) -> Dict:
    return {k: csv_guard(v) for k, v in row.items()}


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


# 报告抬头里「在哪跑的」措辞：网页端与离线脚本共用同一报告构建，只有这一处不同
_ORIGIN_WORDING = {
    "web": ("网页批量分析", "服务器"),
    "offline": ("本地批处理", "本机"),
}


def _group_report_md(item: Dict, record: Optional[Dict], copied: List[Dict],
                     origin: str = "web") -> str:
    """单分组 测序分析报告.md（网页整理包与离线脚本共用；数据来自分析记录：
    reads/variants/cds_reports/homopolymers 等键与管线输出一致）

    origin："web"（网页批量分析）或 "offline"（离线脚本），只影响抬头措辞。"""
    plasmid = item.get("plasmid") or "sample"
    label = f"{item['clone']} {plasmid}".strip() if item.get("clone") else plasmid
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    ref_name = item.get("reference_name")
    if record:
        run_where, host = _ORIGIN_WORDING.get(origin, _ORIGIN_WORDING["web"])
        engine_note = ""
        if record["engine"] == "internal+biopython":
            engine_note = f"；{host}未安装 tracy，跳过重 basecall 交叉印证"
        head = [
            f"# {label} 测序分析报告", "",
            f"- 生成时间：{now}",
            f"- 分析引擎：{record['engine']}（平台 Sanger 全自动管线，{run_where}{engine_note}）",
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
    # 变体分组：重复区 indel（落在 poly/重复结构内）以「重复数变化」呈现，
    # 单独列于 poly 章节，不与常规变异混排——普通突变是"序列真的变了"，
    # 重复区 indel 往往是"这个结构到底有多少个单元说不清"，性质不同
    record_variants = (record or {}).get("variants") or []

    def _is_repeat_indel(v: Dict) -> bool:
        return v.get("type") in ("insertion", "deletion") and bool(v.get("homopolymer"))

    repeat_indels = [v for v in record_variants if _is_repeat_indel(v)]
    regular_variants = [v for v in record_variants if not _is_repeat_indel(v)]
    # poly 同聚物 / 重复结构判读（与网页 poly 卡同口径）：poly 级结构 +
    # 带变体的观察级结构；峰图矛盾的结构附逐 read 调用/峰数证据
    hp_entries = [e for e in ((record or {}).get("homopolymers") or [])
                  if e.get("tier") == "poly" or e.get("variant")]
    if hp_entries or repeat_indels:
        lines += [
            "## poly 同聚物 / 重复结构判读", "",
            "重复区的 indel 以「重复数变化」呈现（多了/少了几个单元）；峰图可分辨峰"
            "是独立于碱基调用的证据，与调用不一致时以峰图为准并建议人工核对。", "",
        ]
        if hp_entries:
            lines += ["| 结构 | 区间 | 参考重复数 | 峰图判读 | 计数可靠性 |",
                      "|---|---|---|---|---|"]
            for e in hp_entries:
                rel_txt = ("可靠" if e.get("count_reliable", True)
                           else "**不可靠**（峰图与调用不一致，建议核对峰图）")
                lines.append(_md_row(_run_label(e), f"{e['start']}–{e['end']}",
                                     e['ref_repeat_count'], _peak_verdict_phrase(e),
                                     rel_txt))
            lines.append("")
        for e in hp_entries:
            if e.get("count_reliable", True) or not e.get("read_counts"):
                continue
            lines.append(f"- **{_run_label(e)} {e['start']}-{e['end']}**（峰图矛盾）逐 read 证据：")
            for x in e["read_counts"]:
                d = "反向" if x["direction"] == "-" else "正向"
                n = x["peak_count"] if x["peak_count"] is not None else "?"
                cov = (f"，覆盖段 {x['covered_span'][0]}–{x['covered_span'][1]}"
                       if x.get("coverage") == "partial" and x.get("covered_span") else "")
                lines.append(f"  - {x['filename']}（{d}{cov}）：调用 {x['called_count']}，可分辨峰 {n}")
            lines.append("")
        if repeat_indels:
            lines += ["重复区 indel（重复数变化）：", "",
                      "| 位置 | 类型 | 变化 | 所在结构 | 重复数（参考→测得） | 置信度 |",
                      "|---|---|---|---|---|---|"]
            for v in repeat_indels:
                hp = v["homopolymer"]
                kind = "插入" if v["type"] == "insertion" else "缺失"
                conf = {"high": "高", "medium": "中", "low": "低"}.get(
                    v.get("confidence"), v.get("confidence"))
                lines.append(_md_row(
                    v['ref_pos'], kind, _fmt_change(v),
                    f"{_run_label(hp)} {hp['start']}-{hp['end']}",
                    f"{hp['ref_repeat_count']}→{hp['observed_repeat_count']}", conf))
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
            "| read | 判定 | 双峰位点 | 次峰占比(中位) | 估计次要克隆占比 | 位点范围（read 坐标） |",
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
            pos_txt = _pos_ranges_str(p["positions"], max_ranges=10)
            if p.get("pullup_excluded"):
                pos_txt += f"；另有 {p['pullup_excluded']} 处饱和峰拖影已剔除"
            lines.append(_md_row(r['filename'], label, p['count'], pct, frac_txt, pos_txt))
        lines.append("")
    if copied:
        lines += ["## 文件整理（原始文件未改动，此处为副本）", "",
                  "| 文件 | 类型 | 匹配方式 |", "|---|---|---|"]
        for e in copied:
            lines.append(_md_row(e['name'], e['kind'], e['how']))
        lines.append("")
    if record and record["reads"]:
        lines += ["## 测序 read 概况", "",
                  "| 文件 | QC 等级 | 平均 Q | 有效长度 | 比对区段（参考坐标） | 末端不可信区（参考坐标） |",
                  "|---|---|---|---|---|---|"]
        for r in record["reads"]:
            a = r["alignment"]
            s, e = int(a["ref_start"]), int(a["ref_end"])
            # 环状跨原点 read：展开坐标（e 可 > L）折回到参考坐标再展示，
            # 跨原点的区段拆成两段（线性即原样「s–e」）
            def span(x: int, y: int) -> str:
                return alignment_span_text({**a, "ref_start": x, "ref_end": y})
            if e - s + 1 <= 2 * END_MARGIN:
                zones = f"{span(s, e)}（整段）"
            else:
                zones = f"{span(s, s + END_MARGIN - 1)}、{span(e - END_MARGIN + 1, e)}"
            lines.append(_md_row(r['filename'], r['grade'], r['mean_q'],
                                 r['trimmed_length'], alignment_span_text(a), zones))
        lines += ["", f"末端不可信区 = 每条 read 首尾约 {END_MARGIN}bp 的信号爬升/下降段"
                  "（折算为参考坐标，与覆盖方向无关）；该区间碱基判读可信度低，"
                  "其中的差异已按低置信处理，如需确认末端请换引物从对侧覆盖。", ""]
    if regular_variants:
        lines += ["## 变异明细", "",
                  "| 位置 | 类型 | 变化 | 置信度 | 测序Q | 支持read数 | 峰级证据 | 所在特征 | 氨基酸变化 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for v in regular_variants:
            feats = "、".join(f["name"] for f in v.get("features", [])) or "—"
            aa = v.get("aa_change") or ("同义" if v.get("synonymous")
                                        else "移码" if v.get("frameshift") else "—")
            conf = {"high": "高", "medium": "中", "low": "低"}.get(v.get("confidence"), v.get("confidence"))
            lines.append(_md_row(v['ref_pos'], v['type'], _fmt_change(v), conf,
                                 v.get('read_q', ''), v.get('support_reads', 1),
                                 _fmt_evidence(v), feats, aa))
        lines += ["", "置信度说明：**高**=峰级证据与 Q 值充分支持；**中**=证据一般，建议关注；"
                  "**低**=疑似测序噪声，未计入结论。", ""]
    if record and record["errors"]:
        lines += ["## 分析中的错误", ""]
        lines += [f"- {e['filename']}: {e['error']}" for e in record["errors"]]
        lines.append("")
    return "\n".join(lines)


# 本工具写进「测序结果」列的一句话结论的固定开头（core.sanger.batch.excel_conclusion
# 与 app.sequencing_batch 的全部结论措辞，含旧版本用过的「疑似混合样品：」）。
# 重跑回填时以此识别上次写入的行并替换，其余行视为用户自己写的内容保留
_TOOL_CONCLUSION_RE = re.compile(
    r"^(?:(?:合格|不合格|疑似混合(?:样品)?|无法判定|分析失败)："
    r"|分析失败$"
    r"|已整理 \d+ 个测序文件，但缺少同名参考图谱"
    r"|未找到克隆 .+ 的图谱或测序文件$"
    r"|未在文件夹中找到该质粒的测序文件或图谱$"
    r"|已找到参考图谱，但未匹配到)")


def _is_tool_conclusion(line: str) -> bool:
    return bool(_TOOL_CONCLUSION_RE.match(line.strip()))


def merge_result_cell(old, concl: str) -> str:
    """「测序结果」单元格的新内容：去掉此前由本工具写入的结论行（任何版本，
    包括旧版重跑叠加出的多行），保留用户自己写的行，新结论放在最后一行"""
    concl = " ".join(str(concl).splitlines()).strip()   # 结论恒为单行，便于下次识别
    lines = str(old or "").splitlines()
    user = "\n".join(ln for ln in lines if not _is_tool_conclusion(ln)).strip()
    return f"{user}\n{concl}" if user else concl


def backfill_excel(wb, cols: Optional[Dict], rows: Optional[List[Dict]],
                   items: List[Dict], clone_mode: bool) -> Optional[bytes]:
    """把各组结论回填信息表「测序结果」列（网页端与离线脚本共用：用户已写
    的内容保留，结论换行写在最后；此前本工具写入的结论被替换而不是叠加，
    见 merge_result_cell），返回回填后的 xlsx 字节；无可回填内容时返回 None。

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
        cell.value = merge_result_cell(cell.value, concl)
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

    条目内容与顺序由 batch_archive_entries 决定（离线脚本落盘用同一份），
    这里只负责写进 ZIP：目录条目以 ZipInfo 写入，文件以 DEFLATE 压缩。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc, data in batch_archive_entries(
                payload, groups, unmatched, excel_name, excel_original,
                excel_filled, records):
            if data is None:
                zf.writestr(zipfile.ZipInfo(arc), b"")
            else:
                zf.writestr(arc, data)
    return buf.getvalue()


MANIFEST_FIELDS = ["质粒", "克隆", "文件", "类型", "结论", "匹配方式", "归档路径",
                   "大小KB", "MD5"]
SOURCE_PATH_FIELD = "原始路径"


class BatchArchive:
    """整理包条目的增量构建器（batch_archive_entries 与离线脚本逐质粒落盘共用）

    同名去重（_arc）、图谱只归档一份、报告与 json 的命名都只取决于同一质粒
    文件夹内的组，因此逐个质粒文件夹构建与整批一次构建得到的条目完全相同；
    整理清单的行按「所在结论桶的首组序号」排序，与整批构建的行序一致。
    """

    def __init__(self, plasmids: List[str], origin: str = "web",
                 source_paths: bool = False, root: str = ZIP_ROOT):
        self.prefix = f"{root}/" if root else ""
        self.origin = origin
        self.source_paths = source_paths
        self.fields = MANIFEST_FIELDS + ([SOURCE_PATH_FIELD] if source_paths else [])
        self._folders = _plasmid_folders(plasmids)
        # 不同质粒文件夹安全化后撞名时加 ~N 区分，不再合并进同一文件夹
        self._folder_dirs = _unique_components(list(self._folders.values()))
        self._used_arcs: set = set()          # 全局防 zip 条目冲突
        self._used_in_dir: Dict[str, set] = {}   # 同一文件夹内防同名（跨文件夹允许重名）
        self._ref_archived: Dict[str, set] = {}  # 质粒文件夹 → 已归档图谱 (文件名, MD5)
        self._manifest: List[Tuple[Tuple[float, int], Dict]] = []
        self._seq = 0

    def _arc(self, arcdir: str, base: str) -> str:
        seen = self._used_in_dir.setdefault(arcdir, set())
        name = base
        n = 2
        while name in seen:
            stem, dot, suffix = base.rpartition(".")
            name = (f"{stem} ({n}).{suffix}" if dot else f"{base} ({n})")
            n += 1
        seen.add(name)
        arc = f"{self.prefix}{arcdir}/{name}" if arcdir else f"{self.prefix}{name}"
        while arc in self._used_arcs:      # 理论到不了（目录内已唯一），兜底防冲突
            arc += "_"
        self._used_arcs.add(arc)
        return arc

    def _row(self, order: float, row: Dict, entry: Dict) -> Dict:
        if self.source_paths:
            row[SOURCE_PATH_FIELD] = str(entry.get("path") or "")
        self._seq += 1
        self._manifest.append(((order, self._seq), row))
        return row

    def folder_of(self, group: Dict) -> Optional[str]:
        """组所在的质粒文件夹；图谱与 read 皆空的组无可归档，返回 None"""
        if not (group["reference"] or group["reads"]):
            return None
        name = self._folders.get(group["plasmid"] or "") or "sample"
        return self._folder_dirs.get(name) or _safe_component(name)

    def folder_dirs(self, folder: str) -> List[Tuple[str, None]]:
        """质粒文件夹固定给出的 正确/ 错误/ 子文件夹（空置也保留）"""
        return [(f"{self.prefix}{folder}/{d}/", None) for d in ("正确", "错误")]

    def bucket_entries(self, folder: str, bucket: str,
                       members: List[Tuple[Dict, Dict, Optional[Dict]]],
                       order: int) -> Tuple[List, List[Dict]]:
        """一个（质粒文件夹, 结论桶）的条目与整理清单行

        members 为该桶内各组 (group, item, record)，按组序；order 为桶内首组
        在整批中的序号（决定清单行序）。返回 (entries, rows)。"""
        entries: List[Tuple[str, Optional[Union[bytes, str]]]] = []
        rows: List[Dict] = []
        # 同桶多组（克隆模式多克隆 / 表内别名写法并档）时报告与 json 带组名
        # 后缀区分；单组保持干净名
        multi = len(members) > 1
        for g, item, record in members:
            plasmid, clone = g["plasmid"], g["clone"]
            if g["reference"]:
                e = g["reference"]["file"]
                data = e.get("bytes") or b""
                seen = self._ref_archived.setdefault(folder, set())
                key = (e.get("name"), _md5(data))
                if key not in seen:     # 别名组/同质粒克隆共享同一图谱：只归档一份
                    seen.add(key)
                    arc = self._arc(folder, _safe_component(e.get("name") or "reference"))
                    entries.append((arc, data))
                    rows.append(self._row(order, {
                        "质粒": plasmid or "", "克隆": clone or "",
                        "文件": e.get("name") or arc.rsplit("/", 1)[-1],
                        "类型": "参考图谱", "结论": "",
                        "匹配方式": g["reference"]["how"], "归档路径": arc,
                        "大小KB": round(len(data) / 1024, 1), "MD5": key[1],
                    }, e))
            copied = []
            if g["reference"]:
                copied.append({"name": g["reference"]["file"].get("name") or "reference",
                               "kind": "参考图谱", "how": g["reference"]["how"]})
            for r in g["reads"]:
                e = r["file"]
                data = e.get("bytes") or b""
                arc = self._arc(f"{folder}/{bucket}",
                                _safe_component(e.get("name") or "read"))
                entries.append((arc, data))
                copied.append({"name": e.get("name") or "read",
                               "kind": "测序 read", "how": r["how"]})
                rows.append(self._row(order, {
                    "质粒": plasmid or "", "克隆": clone or "",
                    "文件": e.get("name") or arc.rsplit("/", 1)[-1],
                    "类型": "测序 read", "结论": item.get("conclusion") or "",
                    "匹配方式": r["how"], "归档路径": arc,
                    "大小KB": round(len(data) / 1024, 1), "MD5": _md5(data),
                }, e))
            gname = item.get("clone") or plasmid or "sample"
            rname = (f"测序分析报告-{_safe_component(gname)}.md" if multi
                     else "测序分析报告.md")
            entries.append((self._arc(f"{folder}/{bucket}", rname),
                            _group_report_md(item, record, copied, self.origin)))
            if record:
                dump = {k: v for k, v in record.items()
                        if k not in ("_trace_data", "_created_ts", "reference")}
                jname = (f"分析结果-{_safe_component(gname)}.json" if multi
                         else "分析结果.json")
                entries.append((self._arc(f"{folder}/{bucket}", jname),
                                json.dumps(dump, ensure_ascii=False, indent=1)))
        return entries, rows

    def unmatched_entry(self, u: Dict) -> Tuple[Tuple[str, bytes], Dict]:
        """一个未匹配文件的条目与清单行（归入 未匹配文件/，清单排在最后）"""
        entry = u["file"]
        data = entry.get("bytes") or b""
        arc = self._arc("未匹配文件", _safe_component(entry.get("name") or "file"))
        row = self._row(float("inf"), {
            "质粒": "", "克隆": "", "文件": entry.get("name") or arc.rsplit("/", 1)[-1],
            "类型": "未匹配", "结论": "", "匹配方式": u["reason"], "归档路径": arc,
            "大小KB": round(len(data) / 1024, 1), "MD5": _md5(data),
        }, entry)
        return (arc, data), row

    def manifest_csv(self, rows: Optional[List[Dict]] = None, header: bool = True) -> str:
        """整理清单 CSV 文本（可追溯：归档路径 + MD5 + 该组结论）

        rows 缺省为已构建的全部行（按整批行序）；header=False 只给数据行，
        供离线脚本逐质粒追加。"""
        if rows is None:
            rows = [r for _k, r in sorted(self._manifest, key=lambda kr: kr[0])]
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=self.fields)
        if header:
            w.writeheader()
        w.writerows([csv_guard_row(r) for r in rows])
        return ("\ufeff" if header else "") + out.getvalue()

    def manifest_entry(self) -> Tuple[str, str]:
        return (f"{self.prefix}整理清单.csv", self.manifest_csv())

    def overview_entry(self, payload: Dict) -> Tuple[str, str]:
        """批次总览：各组结论 + 未匹配清单"""
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
        return (f"{self.prefix}批次总览.txt", "\n".join(lines))

    def excel_entries(self, excel_name: Optional[str], excel_original: Optional[bytes],
                      excel_filled: Optional[bytes]) -> List[Tuple[str, bytes]]:
        """信息表：结论回填版 + 原始备份（无可回填结论时保留原表）"""
        if not (excel_name and excel_original):
            return []
        base = _safe_component(excel_name)
        if excel_filled:
            return [(f"{self.prefix}{base}", excel_filled),
                    (f"{self.prefix}原始备份_{base}", excel_original)]
        return [(f"{self.prefix}{base}", excel_original)]


def archive_buckets(archive: BatchArchive, groups: List[Dict],
                    items: List[Dict]) -> Dict[Tuple[str, str], List[int]]:
    """预分桶 {(质粒文件夹, 结论桶): [组序号]}，键按首组序号排列"""
    buckets: Dict[Tuple[str, str], List[int]] = {}
    for i, (g, item) in enumerate(zip(groups, items)):
        folder = archive.folder_of(g)
        if folder is None:
            continue
        buckets.setdefault(
            (folder, _conclusion_bucket(item.get("conclusion") or "")), []).append(i)
    return buckets


def batch_archive_entries(
    payload: Dict,
    groups: List[Dict],
    unmatched: List[Dict],
    excel_name: Optional[str],
    excel_original: Optional[bytes],
    excel_filled: Optional[bytes],
    records: Dict[str, Dict],
    origin: str = "web",
    source_paths: bool = False,
    root: str = ZIP_ROOT,
) -> List[Tuple[str, Optional[Union[bytes, str]]]]:
    """整理包的全部条目 [(归档路径, 内容)]，网页 ZIP 与离线脚本落盘共用

    归档路径以 root（默认 ZIP_ROOT；离线脚本传 "" 直接相对输出目录）开头、
    "/" 分隔；内容为 None 表示目录条目（路径以 "/" 结尾），str 按 UTF-8
    写出。origin 见 _group_report_md；
    source_paths=True 时整理清单追加「原始路径」列（取文件条目的 "path"，
    离线脚本用于追溯本地原件；网页端无本地路径，保持不加）。

    payload：analyze-batch 的对外 JSON（batch_id/created_at/clone_mode/items/
    unmatched/ignored_files）；groups：_run_batch 的归组（条目含原始字节）；
    unmatched：未匹配原始条目 [{"file","reason"}]；records：analysis_id →
    内存分析记录（供报告与 分析结果.json 取数）。groups 与 payload["items"]
    由 _run_batch 保证一一对应（每组恰好产出一个 item，顺序一致）。

    归档布局：同一质粒（含表内别名写法合并，见 _plasmid_folders）一个文件夹，
    参考图谱在文件夹根；各组文件与报告按结论分入 正确/ 错误/（无法判定的入
    无法判定/），正确/错误 两个子文件夹固定保留（空置也给出，结构可预期）。
    离线脚本按质粒文件夹逐个构建（BatchArchive），条目内容与这里相同。
    """
    items = payload["items"]
    archive = BatchArchive([g["plasmid"] or "" for g in groups], origin,
                           source_paths, root)
    buckets = archive_buckets(archive, groups, items)

    entries: List[Tuple[str, Optional[Union[bytes, str]]]] = []
    # 每个质粒文件夹固定给出 正确/错误 子文件夹（空置也保留）；无法判定/
    # 只在确有内容时出现
    for folder in sorted({folder for folder, _b in buckets}):
        entries += archive.folder_dirs(folder)
    for (folder, bucket), idxs in buckets.items():
        members = [(groups[i], items[i],
                    records.get(items[i]["analysis_id"]) if items[i].get("analysis_id")
                    else None) for i in idxs]
        part, _rows = archive.bucket_entries(folder, bucket, members, idxs[0])
        entries += part
    for u in unmatched:
        entries.append(archive.unmatched_entry(u)[0])
    entries.append(archive.manifest_entry())
    entries.append(archive.overview_entry(payload))
    entries += archive.excel_entries(excel_name, excel_original, excel_filled)
    return entries
