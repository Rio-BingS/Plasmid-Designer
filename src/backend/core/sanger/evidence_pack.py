"""证据包（goal T22；A 5.4）

对每个 blocking 项与 review_trace/resequence 变体，收集全部覆盖 read 在
该位 ±10 峰的四通道小图（纯 Python 生成 SVG，无前端渲染依赖）+ 调用/Q/
峰比/p_real 表与判定理由，整理为：

- 结构化 JSON（前端证据面板：点击变体/阻断项时多 read 堆叠对齐显示）；
- Markdown 报告（小图内联 + 表格，单样品下载，脱离系统后仍可核对）。

数据源是**存储的分析记录**（sequencing_store record）：reads（含比对与
evidence_by_read）、variants、verdict、_trace_data（四通道原始数据）。
不重跑分析、不改判定。
"""

from typing import Dict, List, Optional

from core.sanger.readmap import ReadRefMap

# 每个小图的峰窗（变体位两侧各 10 峰）与画布尺寸
HALF_WINDOW = 10
SVG_WIDTH = 720
SVG_HEIGHT = 130
# 通道配色（ab1 惯例：A 绿 / C 蓝 / G 黑 / T 红）
CHANNEL_COLORS = {"A": "#00A000", "C": "#0030D0", "G": "#303030", "T": "#D00000"}
# 证据包位点上限（异常多位点时防文档爆炸；按 ref_pos 排序取前 N）
MAX_SITES = 60

# 出图动作（T15 四档）：review_trace/resequence 的变体必须给峰图
_PACK_ACTIONS = {"review_trace", "resequence"}


def _trim_peaks(trace_entry: Dict) -> List[Optional[int]]:
    """原始 peak_indices 按修剪偏移切成逐碱基峰位（与 pipeline 同式）"""
    raw = trace_entry.get("peak_indices") or []
    s = int(trace_entry.get("trim_start") or 0)
    return [raw[s + k] if 0 <= s + k < len(raw) else None
            for k in range(len(trace_entry.get("bases") or ""))]


def render_site_svg(trace_entry: Dict, read_pos: int,
                    half: int = HALF_WINDOW,
                    width: int = SVG_WIDTH,
                    height: int = SVG_HEIGHT) -> Optional[str]:
    """单 read 在某 read 位 ±half 峰的四通道小图（SVG 字符串）。

    trace_entry：_trace_data 条目 {bases, channels, peak_indices, trim_start}；
    read_pos 为修剪后 read 坐标（1-based，与 evidence_by_read 同坐标系）。
    read 位越界/无峰 → None。
    """
    bases = (trace_entry.get("bases") or "").upper()
    peaks = _trim_peaks(trace_entry)
    if not bases or not peaks or not (1 <= read_pos <= len(bases)):
        return None
    c = read_pos - 1
    if peaks[c] is None:
        # 中心碱基无峰位：向两侧找最近有峰的碱基
        for d in range(1, half + 1):
            if c - d >= 0 and peaks[c - d] is not None:
                c = c - d
                break
            if c + d < len(peaks) and peaks[c + d] is not None:
                c = c + d
                break
        else:
            return None
    lo, hi = max(0, c - half), min(len(peaks) - 1, c + half)
    pts = [p for p in peaks[lo:hi + 1] if p is not None]
    if len(pts) < 2:
        return None
    n = min(len(v) for v in (trace_entry.get("channels") or {}).values()) \
        if trace_entry.get("channels") else 0
    if n <= 0:
        return None
    x0, x1 = pts[0], max(pts) + 1
    vmax = max((max((ch[p] for p in pts if 0 <= p < n), default=0)
                for ch in trace_entry["channels"].values()), default=0)
    if vmax <= 0:
        return None

    def X(p: int) -> float:
        return 40 + (p - x0) / max(1, x1 - x0) * (width - 60)

    def Y(v: float) -> float:
        return (height - 28) - v / vmax * (height - 48)

    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">',
        f'<rect width="{width}" height="{height}" fill="white"/>',
        # 中心位点高亮带
        f'<rect x="{X(peaks[c]) - 8:.1f}" y="8" width="16" height="{height - 20}" '
        f'fill="#D0342C" opacity="0.12"/>',
    ]
    for ch, color in CHANNEL_COLORS.items():
        data = trace_entry["channels"].get(ch)
        if not data:
            continue
        pts_svg = " ".join(f"{X(p):.1f},{Y(data[p]):.1f}"
                           for p in pts if 0 <= p < n)
        parts.append(f'<polyline fill="none" stroke="{color}" '
                     f'stroke-width="1.4" points="{pts_svg}"/>')
    # 峰下碱基标注（中心位加粗）
    for k in range(lo, hi + 1):
        p = peaks[k]
        if p is None or not (0 <= p < n):
            continue
        bold = ' font-weight="bold"' if k == c else ""
        parts.append(f'<text x="{X(p):.1f}" y="{height - 10}" '
                     f'font-size="11" text-anchor="middle"{bold} '
                     f'fill="#333">{bases[k]}</text>')
    parts.append("</svg>")
    return "".join(parts)


def collect_sites(record: Dict) -> List[Dict]:
    """出图位点：verdict.blocking（有参考坐标的）+ review_trace/resequence
    变体。按 (ref_pos, kind) 去重，ref_pos 升序，超上限截断。"""
    sites: List[Dict] = []
    seen: set = set()

    def add(ref_pos, kind, label, detail):
        key = (ref_pos, kind)
        if not ref_pos or key in seen:
            return
        seen.add(key)
        sites.append({"ref_pos": int(ref_pos), "kind": kind,
                      "label": label, "detail": detail or ""})

    verdict = record.get("verdict") or {}
    for b in verdict.get("blocking") or []:
        if b.get("ref_start"):
            add(b["ref_start"], b.get("kind") or "blocking",
                (b.get("detail") or "")[:80], b.get("detail"))
    for v in record.get("variants") or []:
        if v.get("action") in _PACK_ACTIONS and v.get("ref_pos"):
            add(v["ref_pos"], f"variant_{v['action']}",
                f"{v.get('ref_pos')} {v.get('type')}（{v.get('action')}）",
                v.get("detail") or v.get("low_reason") or "")
    sites.sort(key=lambda s: s["ref_pos"])
    return sites[:MAX_SITES]


def build_pack_data(record: Dict) -> Dict:
    """结构化证据包（前端面板 format=json 消费）。

    {sample, verdict_status, algo_version, sites: [{ref_pos, kind, label,
    detail, reads: [{read, svg, call, q, mutant_pct, p_real, artifact,
    ref_pos}]}]}——svg 为 None 表示该 read 无峰数据（仅表格行）。"""
    traces = {t.get("filename"): t
              for t in (record.get("_trace_data") or {}).values()
              if isinstance(t, dict) and t.get("filename")}
    var_at: Dict[int, List[Dict]] = {}
    for v in record.get("variants") or []:
        var_at.setdefault(v.get("ref_pos"), []).append(v)
    sites: List[Dict] = []
    for site in collect_sites(record):
        pos = site["ref_pos"]
        rows: List[Dict] = []
        for r in record.get("reads") or []:
            fname = r.get("filename")
            rmap = ReadRefMap.from_alignment(r)
            rp = rmap.ref_to_read(pos)
            if rp is None:
                continue
            te = traces.get(fname) or {}
            call = (te.get("bases") or (r.get("trimmed_bases") or ""))[rp - 1:rp]
            ev = None
            p_real = None
            for v in var_at.get(pos, []):
                match = next((e for e in (v.get("evidence_by_read") or [])
                              if e.get("read") == fname), None)
                if match is not None:
                    ev = match
                    p_real = v.get("p_real")
                    break
            rows.append({
                "read": fname,
                "read_pos": rp,
                "call": call or "",
                "q": (ev or {}).get("q"),
                "mutant_pct": (ev or {}).get("mutant_pct"),
                "p_real": p_real,
                "artifact": (ev or {}).get("artifact"),
                "svg": render_site_svg(te, rp) if te else None,
            })
        sites.append({**site, "reads": rows})
    return {
        "sample": record.get("sample_name") or "",
        "verdict_status": (record.get("verdict") or {}).get("status"),
        "algo_version": record.get("algo_version"),
        "sites": sites,
    }


_ACTION_LABELS = {
    "review_trace": "建议核对峰图",
    "resequence": "建议复测",
}


def render_markdown(pack: Dict) -> str:
    """证据包 Markdown（小图内联 + 证据表，单样品下载）"""
    lines: List[str] = [
        f"# Sanger 证据包：{pack.get('sample') or ''}",
        "",
        f"- 判定状态：**{pack.get('verdict_status') or '?'}**"
        f"（算法版本 {pack.get('algo_version') or '?'}）",
        f"- 位点数：{len(pack.get('sites') or [])}"
        "（blocking 项 + 建议核对/复测的变体）",
        "",
    ]
    for i, site in enumerate(pack.get("sites") or [], 1):
        lines.append(f"## {i}. 位点 {site['ref_pos']}（{site['kind']}）")
        lines.append("")
        if site.get("detail"):
            lines.append(f"{site['detail']}")
            lines.append("")
        if site.get("label") and site["label"] != site.get("detail"):
            lines.append(f"> {site['label']}")
            lines.append("")
        lines.append("| read | read 位 | 调用 | Q | 峰强比 | P(真实) | 伪迹 |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in site.get("reads") or []:
            lines.append(
                f"| {r['read']} | {r.get('read_pos') or '-'} | "
                f"{r.get('call') or '-'} | {r.get('q') if r.get('q') is not None else '-'} | "
                f"{r.get('mutant_pct') if r.get('mutant_pct') is not None else '-'} | "
                f"{r.get('p_real') if r.get('p_real') is not None else '-'} | "
                f"{r.get('artifact') or '-'} |")
        lines.append("")
        for r in site.get("reads") or []:
            if r.get("svg"):
                lines.append(f"**{r['read']}**（位点 ±{HALF_WINDOW} 峰）")
                lines.append("")
                lines.append(r["svg"])
                lines.append("")
    lines.append("---")
    lines.append("小图为该 read 在位点两侧各 "
                 f"{HALF_WINDOW} 峰的四通道信号（A 绿 / C 蓝 / G 黑 / T 红），"
                 "红色竖带为位点位置。生成自 Plasmid Designer 证据包（T22）。")
    return "\n".join(lines) + "\n"
