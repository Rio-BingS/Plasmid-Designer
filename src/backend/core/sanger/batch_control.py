"""批内同引物克隆互为对照（2026-11-11）

问题：单条 read 上的孤立双峰（AF 0.15-0.3）在真实单克隆里很常见，单 read
无法区分「上下文噪声」与「低频亚群」。Sanger 背景次峰主要由序列上下文和
在 read 中的位置决定——同一质粒、同一引物的不同克隆在同一参考位点会重复
出现同一个次峰。DEMO7 实测（work/pair_control.py）：同引物两克隆次峰比
r 的跨克隆 Spearman 0.76-0.85；S00024-40007 被判「疑似混合」所依据的
2915 C/T，在另一克隆同位点同次碱基 r=0.13，主峰未降。

做法（只用分析结果里已有的 traces 与 read↔参考映射，不重跑比对）：
1. 对照 read：同一批次、同一参考序列、**不同克隆**、同方向、read 原点
   （未修剪第 1 个碱基外推到参考的位置）相差 ≤ START_TOL 的 read；与候选 read 原始信号完全相同的（改名复制件）不算
   独立对照。
2. 候选位点：read 的 mixed_detail 中 AF ≥ MINOR_AF_MIN 的位点（与跨引物
   互检同一入口），映射到参考坐标。
3. 度量：峰顶 ±2 采样点各通道最大值；r = 最高非判读通道 / 判读通道；
   n = 判读通道峰高 / 同 read 前后 8 峰判读通道峰高中位数（主峰相对高度）。
   对照在该位点须 Q ≥ CTRL_MIN_Q。
4. 判「批内背景」须同时满足（每个有效对照分别判，过半对照满足才算）：
   - 对照次峰为同一碱基（参考链口径）；
   - r_对照 ≥ max(R_CTRL_MIN, R_CTRL_FRAC × r_候选)——对照也有可观次峰；
   - n_候选 / n_对照 ≥ N_RATIO_MIN——候选主峰没有比对照明显变矮（真实
     亚群会把信号从主等位挪到次等位，主峰按 1-AF 下降）。
   例外（不降级）：任一对照 r ≥ R_SHARED_HIGH（对照自身 AF≥0.23）→ 记
   shared_high——多克隆共有的高丰度双峰可能是母本/参考问题，保持原判。

本模块只产出证据；判读侧（crosscheck._corroborate_mixed）把批内背景位点
当作「另一条证据读出干净」同类处理：不再进未决位点、计入 mixed_sample
降级、mixed_variant 改 info。互检否决、多引物同报（multi）一律不受影响
——批内对照只能把「无法互检」的孤立双峰降级，不能削弱任何已有阻断证据
之外的判断。"""

import hashlib
from typing import Dict, List, Optional

from core.sanger.mixed_call import MINOR_AF_MIN
from core.sanger.readmap import ReadRefMap

START_TOL = 15
CTRL_MIN_Q = 20
R_CTRL_MIN = 0.08
R_CTRL_FRAC = 0.4
N_RATIO_MIN = 0.85
R_SHARED_HIGH = 0.30
_NEIGH = 8
_COMP = str.maketrans("ACGT", "TGCA")


def _trace_sig(tr: Dict) -> str:
    ch = tr.get("channels") or {}
    h = hashlib.sha1()
    for b in sorted(ch):
        h.update(b.encode())
        h.update(str(list(ch[b][:4000])).encode())
    return h.hexdigest()


class _ReadView:
    """单条 read 的度量视图（trimmed 坐标，1-based）。"""

    def __init__(self, clone: str, ref_key: str, r: Dict, tr: Dict):
        self.clone = clone
        self.ref_key = ref_key
        self.name = r.get("filename")
        self.r = r
        self.rmap = ReadRefMap.from_alignment(r)
        aln = r.get("alignment") or {}
        self.direction = aln.get("direction", "+")
        self.ref_start = int(aln.get("ref_start") or 0)
        # read 原点（未修剪第 1 个碱基）在参考上的外推位置：同一引物的 read
        # 原点相同，而修剪起点随各 read 质量不同（DEMO7 M13F 两克隆修剪后
        # 起点差 40bp 以上），不能直接比较比对起点
        _off = int(tr.get("trim_start") or 0) + int(aln.get("query_start") or 1) - 1
        self.origin = (self.ref_start - _off if self.direction == "+"
                       else int(aln.get("ref_end") or 0) + _off)
        self.bases = (r.get("trimmed_bases") or "").upper()
        self.q = r.get("trimmed_quality") or []
        self.peaks = r.get("trimmed_peaks") or []
        self.ch = tr.get("channels") or {}
        self.sig = _trace_sig(tr)
        self._prim: Optional[List[float]] = None

    def _heights(self, i: int):
        """0 基 i → (判读碱基, {碱基: 峰高}) 或 None"""
        if not (0 <= i < len(self.bases)) or i >= len(self.peaks):
            return None
        p = self.peaks[i]
        if p is None or not all(b in self.ch for b in "ACGT"):
            return None
        n = min(len(v) for v in self.ch.values())
        if not (0 <= p < n):
            return None
        lo, hi = max(0, p - 2), min(n, p + 3)
        return self.bases[i], {b: float(max(self.ch[b][lo:hi])) for b in "ACGT"}

    def _primaries(self) -> List[float]:
        if self._prim is None:
            out = []
            for i in range(len(self.bases)):
                h = self._heights(i)
                out.append(h[1].get(h[0], 0.0) if h and h[0] in "ACGT" else 0.0)
            self._prim = out
        return self._prim

    def metrics(self, read_pos: int) -> Optional[Dict]:
        i = read_pos - 1
        h = self._heights(i)
        if not h or h[0] not in "ACGT":
            return None
        called, hs = h
        prim = hs[called]
        if prim <= 0:
            return None
        sec_b = max((b for b in "ACGT" if b != called), key=lambda b: hs[b])
        prims = [x for x in self._primaries()[max(0, i - _NEIGH): i + _NEIGH + 1] if x > 0]
        prims.sort()
        med = prims[len(prims) // 2] if prims else prim
        sec_ref = sec_b.translate(_COMP) if self.direction == "-" else sec_b
        return {"r": round(hs[sec_b] / prim, 3),
                "n": round(prim / med, 3) if med else None,
                "sec": sec_ref,
                "q": int(self.q[i]) if i < len(self.q) else 0}


def _views(entries: List[Dict]) -> List[_ReadView]:
    out = []
    for ent in entries:
        res = ent.get("result") or {}
        traces = {t.get("filename"): t for t in res.get("traces") or []}
        for r in res.get("reads") or []:
            tr = traces.get(r.get("filename"))
            if tr and r.get("alignment"):
                out.append(_ReadView(ent["clone"], ent["ref_key"], r, tr))
    return out


def ref_key(seq: str) -> str:
    return hashlib.sha1(seq.upper().encode()).hexdigest()[:16]


def _same_primer(a: _ReadView, b: _ReadView, L: int) -> bool:
    if a.clone == b.clone or a.ref_key != b.ref_key or a.direction != b.direction:
        return False
    if a.sig == b.sig:
        return False  # 同一份原始信号（改名复制件）不是独立对照
    d = abs(a.origin - b.origin)
    if L:
        d = min(d, L - d)
    return d <= START_TOL


def compute(entries: List[Dict]) -> Dict[str, Dict]:
    """entries: [{clone, ref_key, ref_len, result}]，同一批次全部样品。

    返回 {clone: {"by_read": {filename: {ref_pos: site}}, "sites": [site...]}}，
    site = {ref_pos, read, r, n, sec, verdict, controls:[...]}；verdict ∈
    batch_background / clone_specific / shared_high / no_control。
    只返回至少有一条对照 read 的样品。"""
    views = _views(entries)
    L_by_key = {e["ref_key"]: int(e.get("ref_len") or 0) for e in entries}
    out: Dict[str, Dict] = {}
    for v in views:
        ctrls, _sigs = [], set()
        for c in views:
            # 同一份原始信号的多个改名件只算一个对照
            if _same_primer(v, c, L_by_key.get(v.ref_key, 0)) and c.sig not in _sigs:
                ctrls.append(c)
                _sigs.add(c.sig)
        if not ctrls:
            continue
        sites = []
        for e in v.r.get("mixed_detail") or []:
            ratio = float(e.get("ratio") or 0)
            if ratio / (1 + ratio) < MINOR_AF_MIN:
                continue
            rp = v.rmap.read_to_ref(e.get("pos"))
            if not rp:
                continue
            m = v.metrics(e["pos"])
            if not m:
                continue
            cinfo = []
            for c in ctrls:
                cp = c.rmap.ref_to_read(int(rp))
                cm = c.metrics(cp) if cp else None
                if not cm or cm["q"] < CTRL_MIN_Q:
                    continue
                ok = (cm["sec"] == m["sec"]
                      and cm["r"] >= max(R_CTRL_MIN, R_CTRL_FRAC * m["r"])
                      and cm["n"] and m["n"] is not None
                      and m["n"] / cm["n"] >= N_RATIO_MIN)
                cinfo.append({"clone": c.clone, "read": c.name, "r": cm["r"],
                              "n": cm["n"], "q": cm["q"], "sec": cm["sec"],
                              "supports_background": bool(ok)})
            if not cinfo:
                verdict = "no_control"
            elif any(ci["r"] >= R_SHARED_HIGH and ci["sec"] == m["sec"] for ci in cinfo):
                verdict = "shared_high"
            elif sum(ci["supports_background"] for ci in cinfo) * 2 > len(cinfo):
                verdict = "batch_background"
            else:
                verdict = "clone_specific"
            sites.append({"ref_pos": int(rp), "read": v.name, "read_pos": e["pos"],
                          "r": m["r"], "n": m["n"], "sec": m["sec"],
                          "verdict": verdict, "controls": cinfo})
        slot = out.setdefault(v.clone, {"by_read": {}, "sites": []})
        slot["by_read"][v.name] = {s["ref_pos"]: s for s in sites}
        slot["sites"].extend(sites)
    return out


def background_positions(evidence: Optional[Dict]) -> Dict[str, set]:
    """{filename: {ref_pos 判为批内背景}}，供 _corroborate_mixed 使用。"""
    if not evidence:
        return {}
    return {fn: {rp for rp, s in sites.items() if s["verdict"] == "batch_background"}
            for fn, sites in (evidence.get("by_read") or {}).items()}
