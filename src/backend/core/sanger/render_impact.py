# -*- coding: utf-8 -*-
"""10e 呈现：范围外「附带发现」（第二层）与「未评估」（第三层）文案。

只读 verdict["impact"]（verdict._impact_layers 的结构化结果）。
- 醒目提醒：范围外确证 P1/P2 → 一行 ⚠（正文常驻，不改状态）；
- 第二层：每个对象一行 ↳（按 P1→P2→P3、确证在前、位置排序；可折叠）；
- 第三层：一行「注意：」列出未完整覆盖、无确证异常的元件（可折叠）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from core.sanger.elements import CATEGORY_LABEL, SOURCE_LABEL
from core.sanger.impact import RULE_TEXT

_TYPE = {"substitution": "替换", "insertion": "插入", "deletion": "缺失"}


def _target_text(it: Dict) -> str:
    t = it["target"]
    if t["kind"] == "enzyme_site":
        return f"设计酶 {t['name']} 位点"
    if t["kind"] == "backbone":
        return "骨架（无注释）"
    cat = CATEGORY_LABEL.get(t.get("category"), "元件")
    src = SOURCE_LABEL.get(it.get("source"), "")
    return f"{t['name']}（{cat}{'，' + src if src and src != '标注' else ''}）"


def _sites_text(it: Dict) -> str:
    parts = []
    for c in it["consequences"][:3]:
        cq = c.get("consequence") or _TYPE.get(c.get("type"), c.get("type") or "")
        st = "确证" if c.get("status") == "confirmed" else "未决"
        parts.append(f"位置 {c['ref_pos']} {cq}（{st}）")
    more = f" 等 {len(it['consequences'])} 处" if len(it["consequences"]) > 3 else ""
    return "；".join(parts) + more


def impact_lines(verdict: Optional[Dict]) -> List[str]:
    imp = (verdict or {}).get("impact")
    if not imp:
        return []
    by_id = {it["item_id"]: it for it in imp["items"]}
    lines: List[str] = []
    adv = [by_id[i] for i in imp["advisories"] if i in by_id]
    if adv:
        names = "、".join(_target_text(it) for it in adv[:3]) + (" 等" if len(adv) > 3 else "")
        lines.append(f"⚠ 范围外发现 {len(adv)} 处需关注的确证异常：{names}"
                     "——不影响本次判定，建议单独核对")
    for iid in imp["shown"]:
        it = by_id.get(iid)
        if not it:
            continue
        cov = "，部分覆盖" if it.get("coverage") == "partial" else ""
        cap = "，来源未确证封顶" if it.get("cap_applied") else ""
        lines.append(f"  ↳ 附带发现 {it['level']} · {_target_text(it)}："
                     f"{RULE_TEXT.get(it['rule'], it['rule'])}{cov}{cap}——{_sites_text(it)}")
    tail = []
    if imp.get("pending_more"):
        tail.append(f"另有 {imp['pending_more']} 处范围外未决（P3，可展开复核队列）")
    if imp.get("full_ok"):
        tail.append(f"另有 {imp['full_ok']} 个元件完整覆盖，未见异常")
    ua = imp.get("unassessed") or []
    if ua:
        tail.append("范围外未完整覆盖、未评估：" + "、".join(u["name"] for u in ua[:6])
                    + (f" 等 {len(ua)} 个" if len(ua) > 6 else ""))
    if tail:
        lines.append("注意：" + "；".join(tail))
    return lines


_PREFIXES = ("⚠ 范围外发现 ", "  ↳ 附带发现 ", "注意：另有 ", "注意：范围外未完整覆盖")


def with_impact_lines(verdict: Optional[Dict], body_lines: list) -> list:
    """去掉正文里旧的影响分级行，按本次 verdict 追加在末尾（rejudge 同源）"""
    new = impact_lines(verdict)
    flat = "\n".join(body_lines).split("\n") if body_lines else []
    had = any(ln.startswith(_PREFIXES) for ln in flat)
    if not new and not had:
        return list(body_lines)
    kept = [ln for ln in flat if not ln.startswith(_PREFIXES)]
    return kept + new
