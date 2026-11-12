"""S6 判定：Verdict 的唯一调用点，结论头行与正文的唯一拼装处

- `judge(...)`：全系统唯一调用 build_verdict 的地方（analyze、范围重算、
  人工复核、decompose 后重建都经这里）。
- `compose_conclusion(headline, body_lines)`：头行 + 正文拼装（analyze 与
  重算同一函数，三出口同源）。
- `rejudge(record, ...)`：从存档结果重算 Verdict 并重新拼装结论。正文与
  Verdict 无关（只有头行由 verdict.headline 生成），所以正文原样保留、头行
  换新；没有 read 的记录本无头行，不吞正文首行（旧「只替换首行」的缺陷）。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from core.sanger.verdict import build_verdict


def judge(result: Dict, ref_length: int, scope=None,
          synonymous_action: str = "ok",
          review_decisions: Optional[List[Dict]] = None) -> Dict:
    return build_verdict(result, ref_length, scope,
                         synonymous_action=synonymous_action,
                         review_decisions=review_decisions)


def compose_conclusion(headline: str, body_lines: List[str]) -> str:
    return "\n".join([headline] + list(body_lines))


def conclusion_body(record: Dict) -> List[str]:
    """存档结论去掉头行后的正文行。analyze 只在有 read 时加头行。"""
    conc = record.get("conclusion") or ""
    lines = conc.split("\n") if conc else []
    return lines[1:] if (record.get("reads") and lines) else lines


def rejudge(record: Dict, ref_length: int, scope=None,
            synonymous_action: str = "ok",
            review_decisions: Optional[List[Dict]] = None) -> Tuple[Dict, str]:
    """返回 (verdict, conclusion)，不改 record"""
    verdict = judge(record, ref_length, scope, synonymous_action=synonymous_action,
                    review_decisions=review_decisions)
    head = verdict.get("headline") or ""
    conclusion = record.get("conclusion") or ""
    if head:
        conclusion = compose_conclusion(head, conclusion_body(record))
    return verdict, conclusion
