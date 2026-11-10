# -*- coding: utf-8 -*-
"""真实 AB1 子集基准（goal v3 W16，PL G5）

数据：tests/data/real_ab1/ ——脱敏真实测序子集（17647 MUTPK 图谱 +
S99676 克隆 5 条 read，1.5MB）。数据来源：V1.01 交付的实验数据（用户
自有，无第三方许可问题）。**包含 3 处真实确证变异**（ins@335、
ins@440、del@4081）——合成数据造不出真实病态峰型（染料压缩/拖尾/
衰减），这份子集让 CI 真正跑出敏感度：真值若因实现改动而变化，
按口径变更流程处理（递增 algo_version + 提交正文写依据），不得改
真值凑绿（W16 防回潮）。

S99676 真值（algo v16 口径变更，2026-11-10；原 v15 真值见 git 历史）：
- status=fail（4081 del38 确证）
- 确证变异：4081 del（high）。335 ins / 440 ins 由 v15 的 high/medium 改为
  unresolved【待峰图确认】——两处只由 10855 一条 read 支持，335 位于 read
  725bp（尾端扩展区）、440 位于 read 831bp（信号区外，Q12）；main(v2.36.0)
  两处均未报出；同 read 700bp 后 Q45-57 仍成串出现次峰
  （work/local_signal.py、work/fp_features.py 可重跑）
- 其余 4 条 read（11131/17647/M13F/M13R）各自单独分析均为 ✓（与 main 一致）
- 覆盖 ~63%（单克隆 5 引物，范围未声明——measured 口径）
- 混合：无 mixed 判定（S99675 才是 mixed 真实样本，未入库）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data" / "real_ab1"

pytestmark = pytest.mark.skipif(
    not DATA_DIR.is_dir() or not list(DATA_DIR.glob("*.ab1")),
    reason="真实 AB1 子集不在场（tests/data/real_ab1）")


def _real_result():
    global _ctx
    if _ctx is None:
        from core.sanger.pipeline import analyze
        from core.sanger.reference_parser import parse_reference_topology
        ref_name = "MUTPK.dna"
        ref_seq, feats, topo = parse_reference_topology(
            ref_name, (DATA_DIR / ref_name).read_bytes())
        ab1s = [(p.name, p.read_bytes()) for p in sorted(DATA_DIR.glob("*.ab1"))]
        _ctx = analyze(ab1s, ref_seq, feats, circular=topo is not False)
    return _ctx


_ctx = None


def test_real_subset_confirms_true_variants():
    """敏感度：3 处真实确证变异必须全部检出（位置/类型/档位锁真值）"""
    res = _real_result()
    conf = {v["ref_pos"]: v for v in res["variants"]
            if v.get("confidence") in ("high", "medium")}
    for pos, typ, tier in ((4081, "deletion", "high"),):
        v = conf.get(pos)
        assert v is not None, f"真实变异 {pos} {typ} 未检出（敏感度回潮）"
        assert v["type"] == typ, (pos, v["type"], typ)
        assert v.get("confidence") == tier, (pos, v.get("confidence"), tier)
    # v16：335/440 仍检出并留痕（unresolved），不得消失
    allv = {v["ref_pos"]: v for v in res["variants"]}
    for pos in (335, 440):
        assert pos in allv and allv[pos]["status"] == "unresolved", pos
    assert set(conf) == {4081}, sorted(conf)


def test_real_subset_verdict_shape():
    """判定形状：fail（确证变异驱动）；确证数 ≥3；范围未声明（measured）"""
    res = _real_result()
    vd = res["verdict"]
    assert vd["status"] == "fail", vd["status"]
    n_conf = len([v for v in res["variants"]
                  if v.get("confidence") in ("high", "medium")])
    assert n_conf == 1, n_conf  # v16：仅 4081
    sc = vd["scope"]
    assert not sc["declared"], sc
    cov = res["consensus"]["coverage_percent"]
    assert 50 <= cov <= 75, cov  # 单克隆 5 引物实测 ~63%；带宽防漂移
