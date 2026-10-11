"""批量测序 read 数上限：服务器默认 / 分组（访问层级）/ 个人覆盖

与功能开关同一模型（见 app/features.py）：
- 「分组」即访问层级 anonymous / user / admin，站点设置里每个层级各有
  「每组最多 read 数」「整批最多 read 数」两个值，未设置（None）时用
  服务器默认（环境变量 SEQUENCING_BATCH_MAX_READS_PER_GROUP /
  SEQUENCING_BATCH_MAX_TOTAL_READS，默认 24 / 240）；
- 用户表上的两个可空列是个人覆盖，NULL = 跟随所在层级；
- 实际生效值 = 个人覆盖 → 层级设置 → 服务器默认，最后再按绝对安全上限
  （SEQUENCING_BATCH_READS_PER_GROUP_HARD_MAX / SEQUENCING_BATCH_READS_HARD_MAX，
  默认 384 / 5000）截断：管理员手误填一个超大值也不至于完全拆掉保护。
"""

import os
from typing import Dict, Optional, Tuple

TIERS: Tuple[str, ...] = ("anonymous", "user", "admin")
TIER_LABELS: Dict[str, str] = {"anonymous": "匿名访客", "user": "普通用户", "admin": "管理员"}
PER_GROUP = "max_reads_per_group"
TOTAL = "max_total_reads"
FIELDS: Tuple[str, ...] = (PER_GROUP, TOTAL)
FIELD_LABELS: Dict[str, str] = {PER_GROUP: "每组最多 read 数", TOTAL: "整批最多 read 数"}
# 用户表列名（与 FIELDS 一一对应）
USER_COLUMNS: Dict[str, str] = {
    PER_GROUP: "batch_max_reads_per_group",
    TOTAL: "batch_max_total_reads",
}


def _env_int(name: str, default: int) -> int:
    return max(1, int(os.environ.get(name, str(default))))


# 服务器默认（管理员未给某层级设值时生效）
DEFAULT_READS_PER_GROUP = _env_int("SEQUENCING_BATCH_MAX_READS_PER_GROUP", 24)
DEFAULT_TOTAL_READS = _env_int("SEQUENCING_BATCH_MAX_TOTAL_READS", 240)
# 绝对安全上限：管理员填写的值须不超过它，生效值也一律按它截断
HARD_MAX_READS_PER_GROUP = _env_int("SEQUENCING_BATCH_READS_PER_GROUP_HARD_MAX", 384)
HARD_MAX_TOTAL_READS = _env_int("SEQUENCING_BATCH_READS_HARD_MAX", 5000)


def hard_max(field: str) -> int:
    return HARD_MAX_READS_PER_GROUP if field == PER_GROUP else HARD_MAX_TOTAL_READS


def empty_tier_limits() -> Dict[str, Dict[str, Optional[int]]]:
    return {t: {f: None for f in FIELDS} for t in TIERS}


def _positive_int(v) -> Optional[int]:
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        return None
    return v


def normalize_tier_limits(raw) -> Dict[str, Dict[str, Optional[int]]]:
    """清洗存储的层级上限（JSON 解析结果）：未知层级/字段丢弃，非正整数视为未设置"""
    out = empty_tier_limits()
    if not isinstance(raw, dict):
        return out
    for t in TIERS:
        vals = raw.get(t)
        if isinstance(vals, dict):
            for f in FIELDS:
                out[t][f] = _positive_int(vals.get(f))
    return out


def validate_limit(value, field: str, who: str) -> Optional[int]:
    """校验管理员提交的单个上限：None = 清除（跟随上一级）；否则须为
    1..安全上限 的整数。不合法抛 ValueError（中文提示，路由转 422）"""
    if value is None:
        return None
    label = f"{who}「{FIELD_LABELS[field]}」"
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label}须为正整数，留空表示跟随上一级设置")
    if value < 1:
        raise ValueError(f"{label}须为正整数（≥1），留空表示跟随上一级设置")
    cap = hard_max(field)
    if value > cap:
        raise ValueError(f"{label}不能超过服务器安全上限 {cap} 条"
                         "（如确需更高，请调整服务器环境变量）")
    return value


def _tier_for(user) -> str:
    from app.features import tier_for
    return tier_for(user)


def effective_limits(site: dict, user,
                     defaults: Optional[Tuple[int, int]] = None) -> Dict[str, Dict]:
    """当前请求者的生效上限：{field: {"value": int, "source": str}}

    source ∈ user / tier / default / hard_max，用于 413 提示说明是哪一级的限制。
    defaults 缺省用环境变量默认（批量路由传入自己的模块级值，便于测试打桩）。"""
    per_group_default, total_default = defaults or (DEFAULT_READS_PER_GROUP, DEFAULT_TOTAL_READS)
    tier = _tier_for(user)
    tier_vals = normalize_tier_limits(site.get("batch_read_limits")).get(tier, {})
    out: Dict[str, Dict] = {}
    for f, dflt in ((PER_GROUP, per_group_default), (TOTAL, total_default)):
        override = _positive_int(getattr(user, USER_COLUMNS[f], None)) if user is not None else None
        if override is not None:
            value, source = override, "user"
        elif tier_vals.get(f) is not None:
            value, source = tier_vals[f], "tier"
        else:
            value, source = dflt, "default"
        if value > hard_max(f):
            value, source = hard_max(f), "hard_max"
        out[f] = {"value": value, "source": source, "tier": tier}
    return out


def source_label(info: Dict) -> str:
    """413 提示里的「上限来源」说明"""
    src = info.get("source")
    if src == "user":
        return "本账号的单独设置"
    if src == "tier":
        return f"「{TIER_LABELS.get(info.get('tier'), info.get('tier'))}」分组设置"
    if src == "hard_max":
        return "服务器安全上限"
    return "服务器默认配置"
