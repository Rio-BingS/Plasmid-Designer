"""批量测序分析的归组与逐组分析（与 FastAPI 请求上下文无关的纯流程）

网页批量端点（app/routes/sequencing_routes._run_batch）与离线脚本
（scripts/batch_sequencing_report.py）共用本模块：归组 → 逐组跑 Sanger
管线 → 一句话结论 → 对外 payload，保证两端结论、整理包内容不会各自漂移。

请求相关的部分（上传读取、数量上限的 HTTP 报错、分析记录登记与持久化、
访问令牌）留在路由里，经 register 回调与参数注入。

文件条目约定（两端一致）：{"name", "ext", "stem", "bytes"}，离线脚本额外带
"path"（原始文件路径，供整理清单追溯）。
"""

import time
import uuid
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from core.sanger.batch import excel_conclusion, match_files, norm_stem
from core.sanger.pipeline import analyze
from core.sanger.reference_parser import parse_reference_topology

# register(label, ref_seq, features, res) -> (analysis_id, record)
Register = Callable[[str, str, List[Dict], Dict], Tuple[str, Dict]]


def reference_circular(topology: Optional[bool]) -> bool:
    """参考拓扑 → 是否按环状比对：文件明确标 linear 才按线性；标 circular
    或未标明（FASTA）按环状——测序验证的参考绝大多数是质粒，且环状比对
    对不跨原点的 read 结果与线性完全一致，误判为环状的代价最低。"""
    return topology is not False


def group_batch_uploads(
    reads: List[Dict], refs: List[Dict], rows: Optional[List[Dict]]
):
    """把 reads/refs 归组到各质粒

    reads/refs 每项：{"name", "ext", "stem", "bytes"}。
    带信息表（rows 非 None）时用 core.sanger.match_files（引物列→reads、
    质粒名称→图谱）；无信息表时按包含关系归组——每个图谱自成一个质粒，
    测序文件名主干包含且仅包含一个图谱主干时归入该质粒。

    返回 (ordered_names, per_plasmid, unmatched)：
    per_plasmid: 质粒名 -> {"reference": {"file","how"}|None, "reads": [{"file","how"}]}
    """
    if rows is not None:
        per_plasmid, unmatched = match_files(rows, reads + refs)
        ordered = [r["name"] for r in rows]
        ordered += [n for n in per_plasmid if n not in set(ordered)]
        return ordered, per_plasmid, unmatched

    per_plasmid: Dict[str, Dict] = {}
    unmatched: List[Dict] = []
    for r in refs:
        raw_stem = r["name"].rsplit(".", 1)[0] if "." in r["name"] else r["name"]
        if norm_stem(raw_stem) in {norm_stem(k) for k in per_plasmid}:
            unmatched.append({"file": r, "reason": "已存在同名图谱，本文件未采用"})
            continue
        per_plasmid[raw_stem] = {
            "reference": {"file": r, "how": "文件名即质粒名"},
            "reads": [],
        }
    for f in reads:
        cands = {k: norm_stem(k) for k in per_plasmid if norm_stem(k) and norm_stem(k) in f["stem"]}
        if not cands:
            unmatched.append({"file": f, "reason": "文件名不包含任何图谱名"})
            continue
        # 图谱名互为前缀时（MX 与 MX2）取最长包含（最具体者），仍有并列才放弃
        best_len = max(len(v) for v in cands.values())
        best = sorted(k for k, v in cands.items() if len(v) == best_len)
        if len(best) > 1:
            unmatched.append({"file": f,
                              "reason": f"文件名同时包含多个图谱名（{'、'.join(best)[:80]}），无法唯一归组"})
        else:
            per_plasmid[best[0]]["reads"].append({"file": f, "how": "文件名包含图谱名"})
    return list(per_plasmid.keys()), per_plasmid, unmatched


def plasmid_groups(ordered: List[str], per_plasmid: Dict[str, Dict]) -> List[Dict]:
    """质粒模式归组 → 统一的 groups 结构（与克隆模式 match_clone_files 同形）"""
    return [{"plasmid": p, "clone": "", "reference": per_plasmid[p]["reference"],
             "reads": per_plasmid[p]["reads"]} for p in ordered]


def _parse_group_reference(ref_file: Dict, max_ref_bp: Optional[int]):
    """解析一个分组的参考图谱 → (ref_seq, features, circular)；失败返回异常对象"""
    try:
        ref_seq, features, topology = parse_reference_topology(
            ref_file["file"]["name"], ref_file["file"]["bytes"])
        if len(ref_seq) < 50:
            raise ValueError(f"参考序列过短（{len(ref_seq)} bp），无法比对")
        # 终审 C-01：长度上限（批量一次可能解析多个大图谱，
        # 无上限时逐个跑局部比对会拖垮整批）
        if max_ref_bp is not None and len(ref_seq) > max_ref_bp:
            raise ValueError(
                f"参考序列过长（{len(ref_seq)} bp > {max_ref_bp} bp）："
                "请截取待验证区段后重新分析")
        return (ref_seq, features, reference_circular(topology))
    except Exception as e:  # noqa: BLE001 失败也缓存，同质粒后续克隆不再重复解析
        return e


def parse_group_references(groups: List[Dict],
                           max_ref_bp: Optional[int] = None) -> Dict[str, object]:
    """预先解析各分组将参与比对的参考图谱，返回 {质粒名: 解析结果或异常}。

    只解析「有图谱且有 read」的分组（与 run_batch_groups 的惰性解析范围
    一致）；结果原样传给 run_batch_groups(ref_cache=...)，不会重复解析。"""
    cache: Dict[str, object] = {}
    for g in groups:
        if g["reference"] and g["reads"] and g["plasmid"] not in cache:
            cache[g["plasmid"]] = _parse_group_reference(g["reference"], max_ref_bp)
    return cache


def estimate_batch_cost(groups: List[Dict], ref_cache: Dict[str, object]) -> int:
    """批次计算量估算：Σ(参考长度 bp × 该组 read 数)，单位 bp·read。

    局部比对的耗时与内存都随「参考长度 × read 数」线性增长（read 长度
    约 1 kb 量级，视为常数）；解析失败或无图谱的分组不跑比对，不计入。"""
    total = 0
    for g in groups:
        entry = ref_cache.get(g["plasmid"]) if g["reference"] and g["reads"] else None
        if isinstance(entry, tuple):
            total += len(entry[0]) * len(g["reads"])
    return total


def run_batch_groups(
    groups: List[Dict], min_q: int, register: Register,
    max_ref_bp: Optional[int] = None,
    ref_cache: Optional[Dict[str, object]] = None,
    soft_timeout: Optional[float] = None,
) -> Tuple[List[Dict], Dict[str, Dict]]:
    """逐组跑管线并给出一句话结论，返回 (items, records_by_id)

    每组恰好产出一个 item，顺序与 groups 一致（整理包打包依赖此对应）。
    成功分析的组经 register 登记并取回 (analysis_id, record)；record 即整理包
    报告与 分析结果.json 的数据来源。单组失败不阻断批次。

    ref_cache：parse_group_references 的结果（已解析的图谱直接复用）。
    soft_timeout：批次墙钟软时限（秒）。只在开始比对下一组之前检查——
    工作线程无法被安全中断，正在比对的组总会跑完；超时后剩余分组不再
    比对，结论记为「分析失败：…超过时限…」，已完成的分组照常返回。
    """
    records_by_id: Dict[str, Dict] = {}
    items: List[Dict] = []
    # 同一质粒多个克隆共用一张图谱：解析结果（或异常）按质粒缓存
    ref_cache = {} if ref_cache is None else ref_cache
    deadline = (time.monotonic() + soft_timeout) if soft_timeout else None
    for g in groups:
        plasmid, clone = g["plasmid"], g["clone"]
        ref_file = g["reference"]
        n_reads = len(g["reads"])
        label = f"{clone} {plasmid}".strip() or plasmid or "sample"
        item = {
            "plasmid": plasmid,
            "clone": clone or None,
            "status": "analyzed",
            "conclusion": "",
            "analysis_id": None,
            "reference_name": ref_file["file"]["name"] if ref_file else None,
            "reference_length": None,
            "read_count": n_reads,
            "variant_count": 0,
            "pending_count": 0,
            "coverage_percent": None,
        }
        if ref_file is None and n_reads == 0:
            item["status"] = "not_found"
            item["conclusion"] = (f"未找到克隆 {clone} 的图谱或测序文件" if clone
                                  else "未在文件夹中找到该质粒的测序文件或图谱")
            items.append(item)
            continue

        res = None
        if ref_file and n_reads:
            if deadline is not None and time.monotonic() > deadline:
                item["status"] = "failed"
                item["conclusion"] = (
                    f"分析失败：本批次运行超过 {int(soft_timeout)} 秒的时限，"
                    "该组未比对；请拆分为更小的批次后重新分析")
                items.append(item)
                continue
            ref_entry = ref_cache.get(plasmid)
            if ref_entry is None:
                ref_entry = _parse_group_reference(ref_file, max_ref_bp)
                ref_cache[plasmid] = ref_entry
            if isinstance(ref_entry, Exception):
                item["status"] = "failed"
                item["conclusion"] = f"分析失败：{ref_entry}"
                items.append(item)
                continue
            ref_seq, features, circular = ref_entry
            try:
                ab1s = [(r["file"]["name"], r["file"]["bytes"]) for r in g["reads"]]
                res = analyze(ab1s, ref_seq, features, min_q=min_q, circular=circular)
                aid, rec = register(label, ref_seq, features, res)
                item["analysis_id"] = aid
                # 终审 C-07：打包用的记录快照随批次产出，避免事后回查全局
                # dict（并发淘汰时会 KeyError）
                records_by_id[aid] = rec
                item["reference_length"] = len(ref_seq)
                confirmed = [v for v in res["variants"] if v.get("confidence") != "low"]
                item["variant_count"] = len(confirmed)
                item["pending_count"] = len(res["variants"]) - len(confirmed)
                item["coverage_percent"] = res["consensus"]["coverage_percent"]
            except Exception as e:  # noqa: BLE001 单个质粒失败不阻断批次
                item["status"] = "failed"
                item["conclusion"] = f"分析失败：{e}"
                items.append(item)
                continue

        if ref_file and n_reads == 0:
            item["status"] = "no_reads"
            item["conclusion"] = (f"已找到参考图谱，但未匹配到克隆 {clone} 的测序文件（.ab1）"
                                  if clone else
                                  "已找到参考图谱，但未匹配到它的测序文件（.ab1）")
        elif ref_file is None:
            item["status"] = "no_reference"
            item["conclusion"] = excel_conclusion(label, None, n_reads, False)
        else:
            item["conclusion"] = excel_conclusion(label, res, n_reads, True)
        items.append(item)
    return items, records_by_id


def batch_payload(items: List[Dict], unmatched: List[Dict], min_q: int,
                  excel_mode: bool, clone_mode: bool, ignored: List[str]) -> Dict:
    """批量结果的对外 JSON（网页端点响应体，也是整理包 批次总览.txt 的数据源）"""
    return {
        "batch_id": f"seqbatch_{uuid.uuid4().hex[:12]}",
        "created_at": datetime.now().isoformat(),
        "min_q": min_q,
        "excel_mode": excel_mode,
        "clone_mode": clone_mode,
        "items": items,
        "unmatched": [
            {"filename": u["file"].get("name") or str(u["file"]["path"].name),
             "reason": u["reason"]}
            for u in unmatched
        ],
        "ignored_files": ignored,
    }
