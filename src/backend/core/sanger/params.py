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

STAGE_PARAMS = {
    "S1_read": ("MIN_TRIM_Q", "MIN_WINDOW", "PARTIAL_ALIGN_RATIO", "END_MARGIN"),
    "S1_mixed": ("MIXED_PEAK_RATIO", "MIXED_WIDESPREAD_MIN", "MIXED_SCATTERED_MIN",
                 "PULLUP_HEIGHT_FACTOR", "PULLUP_MAX_RATIO"),
    "S3_confidence": ("DEGRADED_READ_SHARE",),
}
