"""平台功能清单与访问层级

功能开关（FEATURE_REGISTRY）决定「未登录访客 / 普通登录用户」两组人群分别
能看到与使用哪些功能：前端据此隐藏导航与拦截路由，后端据此在 API 层拒绝
（require_feature 依赖）。管理员不受任何开关限制。

键名是稳定契约（存进数据库 site_settings 表的 JSON 数组），只能新增不能改名。
"""

from typing import Dict, List, Optional

# key -> 展示名（管理面板/前端共用）；order 即导航顺序
FEATURE_REGISTRY: Dict[str, Dict[str, str]] = {
    "design": {"label": "引物/序列设计"},
    "batch": {"label": "批量设计"},
    "vectors": {"label": "载体库"},
    "sequencing": {"label": "测序分析"},
    "analysis": {"label": "序列工具"},
    "codon": {"label": "密码子表"},
}

# 默认开关 = 全部开放，保持既有行为不变；管理员在后台自行收紧
ALL_FEATURES: List[str] = list(FEATURE_REGISTRY.keys())
DEFAULT_ANONYMOUS_FEATURES: List[str] = list(ALL_FEATURES)
DEFAULT_USER_FEATURES: List[str] = list(ALL_FEATURES)


def valid_features(values: Optional[List]) -> List[str]:
    """清洗功能清单：去重、按注册表顺序排序、丢弃未知键"""
    if not values:
        return []
    seen, out = set(), []
    for v in values:
        if v in FEATURE_REGISTRY and v not in seen:
            seen.add(v)
            out.append(v)
    return [f for f in ALL_FEATURES if f in seen]


def tier_for(user) -> str:
    """用户访问层级：admin > user > anonymous（user 为 None 即匿名）"""
    if user is None:
        return "anonymous"
    return "admin" if getattr(user, "is_admin", False) else "user"


def features_for_tier(site_settings: dict, tier: str) -> List[str]:
    """某层级可用的功能清单；管理员永远全量"""
    if tier == "admin":
        return list(ALL_FEATURES)
    key = "user_features" if tier == "user" else "anonymous_features"
    return list(site_settings.get(key) or [])


def is_feature_allowed(site_settings: dict, tier: str, feature: str) -> bool:
    return tier == "admin" or feature in features_for_tier(site_settings, tier)
