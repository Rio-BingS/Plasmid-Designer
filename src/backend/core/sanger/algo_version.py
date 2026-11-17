"""二期开关 f：SANGER_ALGO_VERSION 组合化（用户 2026-11-17 拍板默认开启；
SANGER_ALGO_VERSION_COMPOSITE=0 回单一版本号）

规格 §6：算法版本改为各阶段版本 + 口径开关的组合，并随记录落库。
SANGER_ALGO_VERSION_COMPOSITE=1 开启后，result["algo_version"] 写成
`v17+r1.s4-2.p4-1`，若有口径开关偏离部署默认，再追加 `+pmin=1` 等；
同时多写 result["algo_versions"]（结构化分量）。默认关：仍写 "v17"，
不新增字段。本模块不 import pipeline（基础版本号由调用方传入）。
"""

from __future__ import annotations

import os
from typing import Dict

from core.sanger.polycall import POLYCALL_VERSION
from core.sanger.refprofile import REFPROFILE_VERSION
from core.sanger.sitecall import SITECALL_VERSION

# 口径开关：环境变量 → (短名, 部署默认值)
SWITCHES = {
    "SANGER_READ_MIXED_CALL": ("rmix", "1"),
    "SANGER_POLY_MISSING_MIN": ("pmin", "1"),
    "SANGER_MIXED_LINES_READCALL": ("mrc", "1"),
    "SANGER_LOW_AF_SCOPE_ONLY": ("laf", "1"),
    "SANGER_REPEAT_FROM_POLYCALL": ("rpc", "1"),
}


def enabled() -> bool:
    return os.environ.get("SANGER_ALGO_VERSION_COMPOSITE", "1") == "1"


def parts(base: str) -> Dict:
    sw = {}
    for env, (short, default) in SWITCHES.items():
        val = os.environ.get(env, default)
        if val != default:
            sw[short] = val
    return {"base": base, "refprofile": REFPROFILE_VERSION,
            "sitecall": SITECALL_VERSION, "polycall": POLYCALL_VERSION,
            "switches": sw}


def compose(base: str) -> str:
    """组合版本串；开关关闭时原样返回 base（v17）"""
    if not enabled():
        return base
    p = parts(base)
    s = f"{base}+{p['refprofile']}.{p['sitecall']}.{p['polycall']}"
    if p["switches"]:
        s += "+" + ",".join(f"{k}={v}" for k, v in sorted(p["switches"].items()))
    return s


def extra_fields(base: str) -> Dict:
    """开关开时随结果落库的结构化分量；关时不加字段"""
    return {"algo_versions": parts(base)} if enabled() else {}
