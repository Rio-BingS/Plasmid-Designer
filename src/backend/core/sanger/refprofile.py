"""S0a 参考画像（纯函数）：分析前只由参考序列与注释决定的事实，集中一处算。

- ref：归一后的参考（大写、U→T）
- length / circular：长度与拓扑
- poly_runs：参考上的重复结构扫描（find_repeat_runs，B3：period 1-3 + 观察级）
- scope：经 resolve_scope 归一的声明范围（S0b 唯一入口；未声明为 None）

下游只读本画像，不再各自对参考做同类扫描。不读 read，不改入参。
"""
from typing import Dict, List, Optional

from core.sanger.homopolymer import find_repeat_runs
from core.sanger.scope import resolve_scope



def normalize_reference(reference: str) -> str:
    return (reference or "").upper().replace("U", "T")


def build_reference_profile(reference: str, features: Optional[List[Dict]] = None,
                            circular: bool = False, scope=None, *,
                            enzymes=None, design_comment: Optional[str] = None) -> Dict:
    ref = normalize_reference(reference)
    features = features or []
    scope_obj, scope_err = resolve_scope(scope, features, len(ref))
    from core.sanger.elements import complete_elements
    from core.sanger.design_enzymes import build_design_enzymes
    els = complete_elements(ref, features, circular)
    rngs = list(getattr(scope_obj, "ranges", None) or []) if scope_obj is not None else []
    design = build_design_enzymes(ref, features, circular, rngs or None,
                                  user_enzymes=enzymes, comment=design_comment)
    return {
        "elements": els,
        "design_enzymes": design,
        "ref": ref,
        "length": len(ref),
        "circular": bool(circular),
        "poly_runs": find_repeat_runs(ref),
        "scope": scope_obj,
        "scope_error": scope_err,
    }
