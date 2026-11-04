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
import re
from typing import Dict, List, Optional, Tuple

from core.sanger.annotator import coding_sequence, group_feature_segments
from core.sanger.batch import _match_ref_name, norm_stem
from core.seq_utils import revcomp

# 可作分析目标的特征类型：与批量页 feature_alias_names 同口径（启动子、
# 复制起点之类的通用元件名拿去匹配只会制造歧义）
CDS_TYPES = ("cds", "gene")

# 提取区段两端保留的参考侧翼 bp（比对锚定上下文）。真实 MX 数据实证：
# 贴着 CDS 首端的插入在零上下文参考下会被软 clipping 吞掉（见
# resolve_reference docstring）——侧翼不是可选项，是正确性前提
FLANK_BP = 150


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
    所有命中指向同一候选才采用；指向不同候选时判歧义不猜。read 名命中
    与否只影响自动识别，不影响该 read 是否参与分析（见模块 docstring）。"""
    names = {c["name"] for c in candidates}
    by_name = {c["name"]: c for c in candidates}
    ref_hits = _stem_hits(norm_stem(ref_stem), names) if ref_stem else {}
    if len(ref_hits) == 1:
        name, how = next(iter(ref_hits.items()))
        return by_name[name], f"参考文件名匹配（{how}）"
    read_hits: Dict[str, str] = {}
    for raw in read_names or ():
        stem = norm_stem(raw)
        if not stem:
            continue
        for name, how in _stem_hits(stem, names).items():
            read_hits.setdefault(name, f"{raw}: {how}")
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


def resolve_reference(ref_seq: str, features: List[Dict], ref_stem: str,
                      read_names: List[str],
                      explicit_name: Optional[str] = None) -> Tuple[str, List[Dict]]:
    """目的基因模式的参考替换：确定目标 CDS → 提取编码区段 + 侧翼 → 伪特征。

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

    定位失败抛 CdsResolveError（candidates 附可选清单）。"""
    candidates = cds_candidates(features)
    if not candidates:
        raise CdsResolveError("参考文件内没有可作分析目标的 CDS/gene 特征", [])
    if explicit_name:
        key = norm_stem(explicit_name)
        cand = next((c for c in candidates if norm_stem(c["name"]) == key), None)
        if cand is None:
            raise CdsResolveError(
                f"参考文件内没有名为「{explicit_name}」的 CDS/gene 特征", candidates)
        how = "手动选择"
    else:
        cand, how = resolve_auto(candidates, ref_stem, read_names)
        if cand is None:
            raise CdsResolveError(how, candidates)

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
