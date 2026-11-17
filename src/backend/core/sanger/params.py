"""Sanger 管线参数，按阶段集中（步骤 6）。数值与 v17 完全一致；pipeline
以原名 re-export，旧导入路径不变。可信区参数见 reliable_zone.ZoneParams，
打分参数见 scoring.load_params。

S1 read 阶段（修剪 / 比对 / 端部）
"""
MIN_TRIM_Q = 20            # 默认末端修剪质量阈值
MIN_WINDOW = 50            # 修剪后最短保留长度
PARTIAL_ALIGN_RATIO = 0.8  # 比对上的 read 长度低于修剪后长度的该比例即视为「部分比对」
# poly 下游骤降判定的 read 末端豁免宽度（END_MARGIN×2）。read 首尾不可信区
# 已改为逐条 read 判定（reliable_zone：位置先验 + Q 值与峰型），不再固定扣 20bp
END_MARGIN = 20

# S1 双峰 / 染料拖影
MIXED_PEAK_RATIO = 0.30     # 次级峰 / 主峰 高于此比例视为疑似混合
MIXED_WIDESPREAD_MIN = 5    # 双峰位点 ≥ 此数 → read 级判「疑似混合样品」
MIXED_SCATTERED_MIN = 2     # 2-4 个 → 「个别双峰位点」提示（不升级结论）
PULLUP_HEIGHT_FACTOR = 3.0  # 主峰高 ≥ 全 read 峰高中位 × 此倍数视为饱和峰
PULLUP_MAX_RATIO = 0.5      # 饱和峰位次级峰占比 ≤ 此值且与主峰同期 → 按染料拖影（pull-up）抑制

# S3 置信 / 退化
# 退化区段占该 read 实测覆盖的比例阈值（goal v3.1 §3.2；与 review 的
# read 级折叠共用同一常量语义）
DEGRADED_READ_SHARE = 0.30

# S6 判定：poly 大量缺失报警（二期 10d，用户 2026-11-17 21:39–21:49 拍板）
# 结构级 poly（tier=poly、同聚物）确证缺失 ≥ COUNT 个碱基 **或** 缺失占
# 该 poly 参考长度 ≥ PCT% 即报警（不能判 ✓）。两条各带启用标志，取消一个
# 只看另一个，都取消则不报警。默认开启。覆盖优先级（低→高）：本表 <
# 环境变量 SANGER_POLY_LARGE_DEL_{COUNT,PCT,COUNT_ENABLED,PCT_ENABLED} <
# 单次请求参数（分析/重新判定的 poly_thresholds）。不进 S1–S3 缓存键。
POLY_LARGE_DEL_COUNT = 5
POLY_LARGE_DEL_PCT = 15.0          # 百分比（分母 = 该 poly 参考长度）
POLY_LARGE_DEL_COUNT_ENABLED = True
POLY_LARGE_DEL_PCT_ENABLED = True

STAGE_PARAMS = {
    "S1_read": ("MIN_TRIM_Q", "MIN_WINDOW", "PARTIAL_ALIGN_RATIO", "END_MARGIN"),
    "S1_mixed": ("MIXED_PEAK_RATIO", "MIXED_WIDESPREAD_MIN", "MIXED_SCATTERED_MIN",
                 "PULLUP_HEIGHT_FACTOR", "PULLUP_MAX_RATIO"),
    "S3_confidence": ("DEGRADED_READ_SHARE",),
    # S6 只影响判定与呈现（换阈值只需重跑 S6–S7），不进缓存键
    "S6_verdict": ("POLY_LARGE_DEL_COUNT", "POLY_LARGE_DEL_PCT",
                   "POLY_LARGE_DEL_COUNT_ENABLED", "POLY_LARGE_DEL_PCT_ENABLED"),
}


def resolve_poly_thresholds(override=None) -> dict:
    """大量缺失阈值（S6）：默认值 < 环境变量 < 单次请求参数。

    返回 {"count": int, "pct": float, "count_enabled": bool, "pct_enabled": bool}。
    override 可以是 dict（键同返回值）或 None；非法值忽略、回落上一级。"""
    import os

    def _b(v, d):
        if v is None:
            return d
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    def _num(v, d, typ):
        try:
            x = typ(v)
            return x if x >= 0 else d
        except (TypeError, ValueError):
            return d

    out = {
        "count": POLY_LARGE_DEL_COUNT, "pct": POLY_LARGE_DEL_PCT,
        "count_enabled": POLY_LARGE_DEL_COUNT_ENABLED,
        "pct_enabled": POLY_LARGE_DEL_PCT_ENABLED,
    }
    env = os.environ
    out["count"] = _num(env.get("SANGER_POLY_LARGE_DEL_COUNT"), out["count"], int)
    out["pct"] = _num(env.get("SANGER_POLY_LARGE_DEL_PCT"), out["pct"], float)
    out["count_enabled"] = _b(env.get("SANGER_POLY_LARGE_DEL_COUNT_ENABLED"),
                              out["count_enabled"])
    out["pct_enabled"] = _b(env.get("SANGER_POLY_LARGE_DEL_PCT_ENABLED"),
                            out["pct_enabled"])
    if isinstance(override, dict):
        if override.get("count") is not None:
            out["count"] = _num(override["count"], out["count"], int)
        if override.get("pct") is not None:
            out["pct"] = _num(override["pct"], out["pct"], float)
        out["count_enabled"] = _b(override.get("count_enabled"), out["count_enabled"])
        out["pct_enabled"] = _b(override.get("pct_enabled"), out["pct_enabled"])
    return out


def poly_large_deletion(missing: int, ref_len, th: dict) -> bool:
    """确证缺失是否达到「大量缺失」（两条为「或」；都停用则恒 False）"""
    if not missing or missing <= 0:
        return False
    hit_n = th["count_enabled"] and missing >= th["count"]
    hit_p = (th["pct_enabled"] and ref_len
             and missing * 100.0 / float(ref_len) >= th["pct"])
    return bool(hit_n or hit_p)
