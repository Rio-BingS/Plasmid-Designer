"""测序分析对外摘要（goal v3 W16 自 sequencing_routes.py 拆分：routes
超行数预算回拨）。纯 dict 构造——剔除参考序列与 trace 原始数据，白名单
式透传（防回潮约束 2：新增返回字段在此显式登记）。"""

from typing import Dict

from core.sanger import review


def summarize(record: Dict) -> Dict:
    """对外摘要：剔除参考序列与 trace 原始数据"""
    reads = []
    for i, r in enumerate(record["reads"]):
        reads.append({
            "index": i,
            "filename": r["filename"],
            "sample_name": r.get("sample_name", ""),
            "raw_length": r["raw_length"],
            "trimmed_length": r["trimmed_length"],
            "mean_q": r["mean_q"],
            "grade": r.get("grade"),
            "q20_ratio": r.get("q20_ratio"),
            # read 可信区（位置先验 + Q 值与峰型逐条判定）；旧记录缺省
            **({"reliable_zone": {k: r["reliable_zone"].get(k) for k in (
                "start", "end", "raw_start", "raw_end", "front_reason", "tail_reason")}}
               if r.get("reliable_zone") else {}),
            "direction": r["alignment"]["direction"],
            "ref_start": r["alignment"]["ref_start"],
            # 环状参考跨原点的 read：ref_end 为展开坐标（> reference_length，
            # 按 ((p-1) % L) + 1 折回），wraps_origin/ref_segments 给出折回
            # 后的两段；线性/不跨原点时两字段缺省（向后兼容）
            "ref_end": r["alignment"]["ref_end"],
            **({"wraps_origin": True,
                "ref_segments": r["alignment"].get("ref_segments")}
               if r["alignment"].get("wraps_origin") else {}),
            # 终审 A-20：对齐块在原始电泳 read 内的 1-based 起止（软剪切
            # 偏移），前端 origIdx 换算与 read2ref 同口径
            "query_start": r["alignment"].get("query_start"),
            "query_end": r["alignment"].get("query_end"),
            "identity": r["alignment"]["identity"],
            "mixed_positions": r.get("mixed_positions", []),
            "mixed_detail": r.get("mixed_detail", []),
            # 软检出带双峰（goal v3 W4）：地板下、af≥0.115 的展示带，
            # verdict 侧在声明范围×功能区出 pending 复核项
            "mixed_soft": r.get("mixed_soft", []),
            "mixed_class": (r.get("mixed_profile") or {}).get("class"),
            "mixed_count": (r.get("mixed_profile") or {}).get("count", 0),
            # 逐列对齐视图（read 与参考的原始证据，供人工核对）
            "alignment_view": r["alignment"].get("aligned"),
        })
    return {
        "analysis_id": record["analysis_id"], "sample_name": record.get("sample_name", ""),
        "created_at": record["created_at"], "engine": record["engine"],
        "conclusion": record["conclusion"],
        "conclusion_groups": record.get("conclusion_groups"),  # R6 折叠组；旧记录 None→前端回退
        "reads": reads,
        "variants": record["variants"],
        "consensus": record["consensus"],
        "coverage_ranges": record["coverage_ranges"],
        "coverage_gaps": record.get("coverage_gaps", []),
        # 参考是否按环状比对（旧记录无此字段，按 False 呈现）
        "circular": bool(record.get("circular", False)),
        "cds_reports": record.get("cds_reports", []),
        "homopolymers": record.get("homopolymers", []),
        "mixed_detected": record.get("mixed_detected", {}), "mixed_profiles": record.get("mixed_profiles", {}),
        "decomposed_alleles": record.get("decomposed_alleles", {}),
        # 结构化判定与未决证据（goal T2；B P1-8）；旧记录缺省 None/[]
        "verdict": record.get("verdict"), "algo_version": record.get("algo_version"),
        "resolved_regions": record.get("resolved_regions", []), "unresolved_sites": record.get("unresolved_sites", []), "mixed_variants": record.get("mixed_variants", []),
        "mixed_corroboration": record.get("mixed_corroboration"), "errors": record["errors"], "warnings": record.get("warnings", []),  # T24 解析层告警；旧记录缺省 []
        # 复核队列与人工确认留痕（goal v3 W14）；旧记录无键时由 core 惰性派生
        "review_queue": review.queue_for_record(record),
        "review_decisions": record.get("review_decisions", []),
        "reference_length": len(record["reference"]), "features": record["features"],
    }
