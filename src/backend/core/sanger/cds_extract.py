"""目的基因模式（单样品可选）：参考图谱内按文件名定位并提取目的 CDS。

参考是完整质粒图谱时，按「参考文件名 + read 文件名」识别样品对应的目的
CDS，以「该 CDS ± 侧翼」的线性区段（而非整个质粒）为比对参考——报告聚焦
基因段，伪 CDS 特征区间即目标 CDS，蛋白级结论由管线 cds_reports 自动产出
（管线零改动）。所有 read（含载体通用引物 read，如 M13F/T7-Term）照常参与
比对——引物名与 CDS 名无关，重点是覆盖并验证这段 CDS。

**为什么带侧翼**：零上下文的 CDS-only 参考会把贴着 CDS 首尾的插入/缺失
吞进软 clipping（真实 MX 数据实证，蛋白判定翻转），FLANK_BP 侧翼是正确性
前提，见 resolve_reference。

匹配口径与批量归组完全同源（batch._match_ref_name）：全名精确 > 忽略分隔符
精确 > 与名称某段一致 > 双向包含（短侧 ≥4 字符，防 "MX" 命中 "MX2"）；
候选不唯一一律不猜——抛 CdsResolveError 交前端下拉手动选择。

提取/拼接用 annotator 的 group_feature_segments + coding_sequence 同款实现，
与管线 cds_reports 的蛋白重建口径天然一致。
"""

import re
from typing import Dict, List, Optional, Tuple

from core.sanger.annotator import coding_sequence, group_feature_segments
from core.sanger.batch import _match_ref_name, norm_stem
from core.sanger.scope import (
    JUNCTION_HALF_WIDTH, Scope, SOURCE_CDS_TARGET,
)
from core.seq_utils import revcomp

# 可作分析目标的特征类型：与批量页 feature_alias_names 同口径（启动子、
# 复制起点之类的通用元件名拿去匹配只会制造歧义）
CDS_TYPES = ("cds", "gene")

# 提取区段两端保留的参考侧翼 bp（比对锚定上下文）。真实 MX 数据实证：
# 贴着 CDS 首端的插入在零上下文参考下会被软 clipping 吞掉（见
# resolve_reference docstring）——侧翼不是可选项，是正确性前提
FLANK_BP = 150

# 裁剪路径（W8，仅质粒超 MAX_REF_BP 时启用）的侧翼下限与 read 余量：
# 侧翼 = max(1500, 最长 read + 200)。app 层 MAX_REF_BP（200_000）是请求
# 上限的权威；这里给缺省值，调用方（路由）显式传入路由常量保持同源
MAX_REF_BP_DEFAULT = 200_000
CROP_FLANK_FLOOR = 1500
CROP_READ_MARGIN = 200


class CdsResolveError(ValueError):
    """目的 CDS 定位失败：参考内无候选 / 显式名称不存在 / 自动匹配歧义。

    candidates 带去重后的可选清单（端点转 400 并把清单回显给前端下拉）。"""

    def __init__(self, message: str, candidates: Optional[List[Dict]] = None):
        super().__init__(message)
        self.candidates = candidates or []


def cds_candidates(features: List[Dict]) -> List[Dict]:
    """可作分析目标的 CDS/gene 特征清单（嵌套去重，多段特征合并）。

    多段 CDS（reference_parser 对 join 的逐段展开记录）按 feature_id 归组为
    一个候选；嵌套去重与 pipeline._build_cds_reports 同款——外沿被更大候选
    完全包含的小特征（6xHis 标签、嵌套报告基因 luciferase）不单独列出。

    返回按参考位置排序的列表，每项：
    {"name", "start", "end"（外沿，1-based 闭区间）, "strand", "length"
     （各段编码碱基之和）, "parts"（提取用的原始分段）}"""
    feats = [f for f in (features or [])
             if str(f.get("type") or "").strip().lower() in CDS_TYPES]
    cands: List[Dict] = []
    for parts in group_feature_segments(feats):
        start = min(int(p["start"]) for p in parts)
        end = max(int(p["end"]) for p in parts)
        cands.append({
            "name": str(parts[0].get("name") or "CDS"),
            "start": start,
            "end": end,
            "strand": parts[0].get("strand") or "+",
            "length": sum(int(p["end"]) - int(p["start"]) + 1 for p in parts),
            "parts": parts,
        })
    # 嵌套去重：外沿被更大候选完全包含的剔除（与管线同款判定：严格更长才算包）
    nested = set()
    for a in cands:
        for b in cands:
            if a is b:
                continue
            if (b["start"] <= a["start"] and a["end"] <= b["end"]
                    and (b["end"] - b["start"]) > (a["end"] - a["start"])):
                nested.add(id(a))
                break
    return sorted((c for c in cands if id(c) not in nested),
                  key=lambda c: (c["start"], c["end"]))


def _stem_hits(stem: str, names: set) -> Dict[str, str]:
    """一个文件主干对候选名集合的命中：全名多级匹配 + 组成 token 多级匹配。

    token 级兜底覆盖「主干包含 CDS 名的某个组成段」的真实形态——
    S99675-17647-seqF1 里的 17647 ↔ CDS「17647 MUTPK」：全串双向包含比不上
    （两边 squash 都比对方长），按分隔符拆 token 后 17647 与名称段一致才命中。
    多 token 命中不同候选时返回多项，由调用方判歧义。"""
    out: Dict[str, str] = {}
    name, how = _match_ref_name(stem, names, label="CDS")
    if name:
        out[name] = how
    for tok in re.split(r"[\s._\-]+", stem):
        if len(tok) < 2 or tok == stem:
            continue
        name, how = _match_ref_name(tok, names, label="CDS")
        if name:
            out.setdefault(name, f"{how}（{tok}）")
    return out


def resolve_auto(candidates: List[Dict], ref_stem: str,
                 read_names: List[str]) -> Tuple[Optional[Dict], str]:
    """按文件名自动识别目标 CDS。返回 (候选 或 None, 说明)。

    来源优先级：参考文件名 > read 文件名。参考名命中唯一候选直接采用
    （MX.dna → CDS「MX」）；参考名未命中时 read 文件名兜底（
    S99675-17647-seqF1 ⊇ CDS「17647 MUTPK」的组成段 17647）。
    W9（CM P2-G）：参考名命中与 read 名命中**指向不同候选**时判歧义不猜
    （GFP_backbone × mCherry_F 探针——旧实现参考名唯一即静默采用）；
    read 命中是参考命中的子集或反之（同一候选收敛）不冲突。read 名命中
    与否只影响自动识别，不影响该 read 是否参与分析（见模块 docstring）。"""
    names = {c["name"] for c in candidates}
    by_name = {c["name"]: c for c in candidates}
    ref_hits = _stem_hits(norm_stem(ref_stem), names) if ref_stem else {}
    read_hits: Dict[str, str] = {}
    for raw in read_names or ():
        stem = norm_stem(raw)
        if not stem:
            continue
        for name, how in _stem_hits(stem, names).items():
            read_hits.setdefault(name, f"{raw}: {how}")
    if ref_hits:
        all_hits = {k: f"参考文件名 {how}" for k, how in ref_hits.items()}
        all_hits.update(read_hits)
        if len(all_hits) > 1 and read_hits and not (
                set(read_hits) <= set(ref_hits)
                or set(ref_hits) <= set(read_hits)):
            return None, ("参考文件名与 read 文件名指向不同 CDS（"
                          + "、".join(sorted(all_hits))[:80]
                          + "），判歧义——请显式选择目标 CDS")
        if len(ref_hits) == 1:
            name, how = next(iter(ref_hits.items()))
            return by_name[name], f"参考文件名匹配（{how}）"
    all_hits = dict(read_hits)
    if ref_hits:
        # 参考名未唯一（含歧义）时并入 union：read 若收敛到其中之一仍可用
        all_hits.update({k: f"参考文件名 {how}" for k, how in ref_hits.items()})
    if len(all_hits) == 1:
        name, detail = next(iter(all_hits.items()))
        return by_name[name], f"read 文件名匹配（{detail}）" if read_hits else \
            f"参考文件名匹配（{ref_hits[name]}）"
    if len(all_hits) > 1:
        return None, ("文件名同时指向多个 CDS（"
                      + "、".join(sorted(all_hits))[:80] + "），无法唯一确定")
    return None, "文件名未能唯一识别目的 CDS"


def resolve_explicit(features: List[Dict], name: str) -> Dict:
    """显式选择目标 CDS（goal v3 W9，CM P1-F/P2-G）：在**未去重**的
    CDS/gene 分组中查找——允许选中嵌套特征（6xHis 标签、融合蛋白里
    嵌套的报告基因，cds_candidates 的嵌套去重不适用于显式选择）；
    重名（两处同名的 CDS/gene，如双拷贝）报错不静默取第一个。

    返回候选 dict（与 cds_candidates 同形）；找不到抛 CdsResolveError
    （candidates 附去重后的可选清单供前端下拉）。"""
    feats = [f for f in (features or [])
             if str(f.get("type") or "").strip().lower() in CDS_TYPES]
    key = norm_stem(name)
    hits: List[List[Dict]] = []
    for parts in group_feature_segments(feats):
        if norm_stem(str(parts[0].get("name") or "CDS")) == key:
            hits.append(parts)
    if not hits:
        raise CdsResolveError(
            f"参考文件内没有名为「{name}」的 CDS/gene 特征",
            cds_candidates(features))
    if len(hits) > 1:
        spans = "、".join(
            f"{p[0].get('name') or 'CDS'}"
            f"（{min(int(q['start']) for q in p)}-{max(int(q['end']) for q in p)}）"
            for p in hits)
        raise CdsResolveError(
            f"参考内有 {len(hits)} 个同名「{name}」特征（{spans}）"
            "——请改用「验证范围」按坐标声明", cds_candidates(features))
    parts = hits[0]
    return {
        "name": str(parts[0].get("name") or "CDS"),
        "start": min(int(p["start"]) for p in parts),
        "end": max(int(p["end"]) for p in parts),
        "strand": parts[0].get("strand") or "+",
        "length": sum(int(p["end"]) - int(p["start"]) + 1 for p in parts),
        "parts": parts,
    }


def _resolve_candidate(features: List[Dict], ref_stem: str,
                       read_names: List[str],
                       explicit_name: Optional[str] = None
                       ) -> Tuple[Dict, str]:
    """确定目标 CDS（resolve_reference / prepare_scope_mode 共用）。

    explicit_name（前端下拉显式选择）优先于自动匹配。定位失败抛
    CdsResolveError（candidates 附可选清单）。"""
    candidates = cds_candidates(features)
    if not candidates:
        raise CdsResolveError("参考文件内没有可作分析目标的 CDS/gene 特征", [])
    if explicit_name:
        # W9（CM P1-F）：显式选择在未去重候选中查找（嵌套可选、重名报错）
        return resolve_explicit(features, explicit_name), "手动选择"
    cand, how = resolve_auto(candidates, ref_stem, read_names)
    if cand is None:
        raise CdsResolveError(how, candidates)
    return cand, how


def prepare_scope_mode(ref_seq: str, features: List[Dict], ref_stem: str,
                       read_names: List[str],
                       explicit_name: Optional[str] = None,
                       circular: bool = True,
                       max_ref_bp: int = MAX_REF_BP_DEFAULT,
                       read_lens: Optional[List[int]] = None,
                       ) -> Tuple[str, List[Dict], Scope, bool]:
    """目的基因模式 W8 目标路径（CM P0-A/P0-B/P1-C/P1-F）：不再裁参考。

    全质粒按真实拓扑比对，scope = 目标 CDS + 接合区（source=cds_target），
    全部注释原样保留。返回 (ref_seq, features, scope, circular)：

    - len(ref_seq) ≤ max_ref_bp：参考、features、拓扑（circular 入参）
      原样返回；
      scope.ranges = 目标 CDS 各段（跨原点 join 按折回形态自然成两段），
      scope.junctions = 各段两端 ±30bp（属需测范围但不进头行区间——
      cds_target 路径与「全质粒 + scope」的结论才能逐字同源，CM5）；
    - 超限才裁剪（goal W8）：侧翼 = max(1500, 最长 read + 200)；窗口 =
      「CDS 首段 5′ 侧翼起点 → 末段 3′ 侧翼终点」的环状跨原点切片——
      跨原点 CDS 的窗口天然穿过原点（等价于「旋转使 CDS 连续」），近原点
      CDS 的两侧侧翼也完整（P1-C）；features 平移进窗口坐标（窗口内的
      段保留、目标 CDS 加 analysis_target 标记——蛋白报告与结论资格门
      随窗口坐标生效），scope 同步平移，circular=False。窗口外溢出段由
      pipeline 的 full_ref 重定位兜底（能连续定位到窗口外不算异常）。
    """
    cand, how = _resolve_candidate(features, ref_stem, read_names,
                                   explicit_name)
    name = cand["name"]
    parts = cand["parts"]
    L = len(ref_seq)
    ranges = [(int(p["start"]), int(p["end"])) for p in parts]
    junctions: List[Tuple[int, int]] = []
    for s, e in ranges:
        for x in (s, e):
            a, b = x - JUNCTION_HALF_WIDTH, x + JUNCTION_HALF_WIDTH
            # 窗口越原点用 start > end 折回形态（normalized 拆两段）——
            # 接合区跟随真实拓扑（跨原点 CDS 的接缝判定不丢）
            if a < 1:
                a += L
            if b > L:
                b -= L
            junctions.append((a, b))
    reason = (f"目的基因模式：目标 CDS「{name}」（{how}），"
              "范围 = CDS + 接合区（两端 ±30bp）")

    if L <= max_ref_bp:
        scope = Scope(ranges=ranges, junctions=junctions,
                      source=SOURCE_CDS_TARGET, elements=[name],
                      reason=reason)
        return ref_seq, features, scope.normalized(L), bool(circular)

    # —— 裁剪路径（goal W8：只有质粒超 MAX_REF_BP 才裁）——
    flank = CROP_FLANK_FLOOR
    if read_lens:
        flank = max(flank, max(read_lens) + CROP_READ_MARGIN)
    win_start = ranges[0][0] - flank
    win_end = ranges[-1][1] + flank
    if win_end - win_start + 1 >= L:      # CDS+侧翼覆盖整条质粒：整条即窗口
        off, span = 1, L
    else:
        span = ((win_end - win_start) % L) + 1   # 跨原点 diff<0 → 环状切片
        off = ((win_start - 1) % L) + 1          # 窗口起点折回 [1, L]
    win = "".join(ref_seq[(off - 1 + i) % L] for i in range(span))

    # 平移映射：原始坐标 p → 窗口局部 i = ((p - off) % L) + 1（p 在窗口弧
    # 上时唯一）。特征段映射后跨窗口折回点的拆两段，完整落在窗口内的保留
    def _to_window(s: int, e: int) -> List[Tuple[int, int]]:
        ls = (s - off) % L
        le = (e - off) % L
        if ls <= le:
            ivs = [(ls + 1, le + 1)]
        else:                             # 段跨窗口折回点：拆两段
            ivs = [(ls + 1, span), (1, le + 1)]
        return [(a, b) for a, b in ivs if 1 <= a <= b <= span]

    feats_out: List[Dict] = []
    for feat in features:
        s, e = int(feat.get("start") or 0), int(feat.get("end") or 0)
        if s < 1 or e < s:
            continue
        locs = _to_window(s, e)
        if not locs:
            continue
        is_target = feat.get("name") == name and (s, e) in ranges
        for k, (a, b) in enumerate(locs):
            f2 = dict(feat)
            f2["start"], f2["end"] = a, b
            if len(locs) > 1:
                # 跨窗口折回点拆出的多段：补分组键让管线重聚（原本无
                # feature_id 的普通特征拆段后给合成键）
                if feat.get("feature_id") is None:
                    f2["feature_id"] = f"__w{feat.get('name') or ''}_{s}_{e}"
                f2["segment_index"] = k
                f2["segment_count"] = len(locs)
            if is_target:
                f2["analysis_target"] = True
            feats_out.append(f2)
    scope = Scope(
        ranges=[r for s, e in ranges for r in _to_window(s, e)],
        junctions=[], source=SOURCE_CDS_TARGET, elements=[name],
        reason=reason + "；质粒超长度上限，已裁剪取窗（窗口坐标）")
    # 窗口内接合区：从平移后的 CDS 段重新生成（窗口已含 CDS ± 侧翼，
    # 接合区必完整落在窗口内）
    for s, e in scope.ranges:
        scope.junctions.extend(
            [(max(1, s - JUNCTION_HALF_WIDTH), s + JUNCTION_HALF_WIDTH),
             (e - JUNCTION_HALF_WIDTH, e + JUNCTION_HALF_WIDTH)])
    scope.normalized(span)
    return win, feats_out, scope, False


def resolve_reference(ref_seq: str, features: List[Dict], ref_stem: str,
                      read_names: List[str],
                      explicit_name: Optional[str] = None) -> Tuple[str, List[Dict]]:
    """目的基因模式的参考替换（v2 旧路径，W8 起仅测试与回退对照使用）：
    确定目标 CDS → 提取编码区段 + 侧翼 → 伪特征。

    explicit_name（前端下拉显式选择）优先于自动匹配。返回 (ext_seq, feats)：

    - 单段 CDS：取「CDS ± FLANK_BP 参考侧翼」的线性区段（负链 CDS 整段反向
      互补成编码方向）。**侧翼不可省**——真实 MX 数据实证：贴着 CDS 首端的
      插入（参考 5076，CDS 第 2 位）在零上下文的 CDS-only 参考下被 leading
      软 clipping 吞掉（蛋白判定由「不一致」翻成「一致」，验证结论直接错），
      侧翼给比对以锚定上下文后正常检出。伪 CDS 特征区间即目标 CDS
      （特征名沿用原名），5′/3′ 侧翼以 misc_feature 标注——报告坐标 =
      CDS 内坐标 + 前置侧翼偏移，落在侧翼的变异（载体-基因接合区）额外可见。
    - 多段 CDS（join）：按编码方向拼接为纯编码序列、不加侧翼（段间接缝无
      参考上下文可加），坐标即 CDS 内坐标——接缝处边界效应留待真实多段
      数据出现后再议。

    W8 起生产路径改走 prepare_scope_mode（全质粒 + scope，不裁参考）；
    本函数保留为裁参考口径的对照实现（P0-A/P0-B/P1-C 的行为问题在
    REVIEW_cds_mode 有完整记录）。

    定位失败抛 CdsResolveError（candidates 附可选清单）。"""
    cand, how = _resolve_candidate(features, ref_stem, read_names, explicit_name)

    parts = cand["parts"]
    if len(parts) > 1:
        cds = coding_sequence(ref_seq, parts)
        return cds, [{
            "name": cand["name"], "type": "CDS", "start": 1, "end": len(cds),
            "strand": "+",
            "analysis_target": True,
            "description": (f"目的基因模式：以「{cand['name']}」的多段编码序列"
                            f"（join，{len(parts)} 段）为参考（{how}），"
                            "坐标即 CDS 内坐标"),
        }]

    seg = parts[0]
    s, e = int(seg["start"]), int(seg["end"])
    span_start = max(1, s - FLANK_BP)
    span_end = min(len(ref_seq), e + FLANK_BP)
    ext = ref_seq[span_start - 1:span_end]
    if cand["strand"] == "-":
        ext = revcomp(ext)
        cds_start = span_end - e + 1
        cds_end = span_end - s + 1
    else:
        cds_start = s - span_start + 1
        cds_end = e - span_start + 1

    feats: List[Dict] = []
    if cds_start > 1:
        feats.append({
            "name": "5′侧翼", "type": "misc_feature",
            "start": 1, "end": cds_start - 1, "strand": "+",
            "description": f"参考侧翼比对上下文（{cds_start - 1} bp），非分析目标",
        })
    feats.append({
        "name": cand["name"], "type": "CDS",
        "start": cds_start, "end": cds_end, "strand": "+",
        # 管线结论覆盖资格门（pipeline._conclusion_summary）与本前端
        # utils/cdsMode.findCdsTarget 共用的机器标记
        "analysis_target": True,
        "description": (f"目的基因模式：分析目标为「{cand['name']}」"
                        f"（参考内 {s}-{e}，{how}）。两端各保留 {FLANK_BP}bp "
                        "参考侧翼作比对上下文，本特征区间即目标 CDS"),
    })
    if cds_end < len(ext):
        feats.append({
            "name": "3′侧翼", "type": "misc_feature",
            "start": cds_end + 1, "end": len(ext), "strand": "+",
            "description": f"参考侧翼比对上下文（{len(ext) - cds_end} bp），非分析目标",
        })
    return ext, feats
