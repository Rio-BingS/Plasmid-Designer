"""单 read 压缩区降级要放过多 read 互证；N 不压过干净读出的碱基

对应两处口径修复：
- _merged_zone_artifact：mixed/poly 合并压缩只削弱单条 read 的证据，
  support_reads >= 2 的变异不能被降为 low（否则真实 indel 从共识/CDS 消失）
- _pick_base_vote：N/IUPAC 是"没读清"，不能压过另一条 read 干净读出的碱基
"""
import random

from core.sanger.aligner import align_read
from core.sanger.pipeline import (
    _build_consensus,
    _merged_zone_artifact,
    _pick_base_vote,
)

random.seed(11)
REF = "".join(random.choice("ACGT") for _ in range(400))


def _rr(read: str, q: int = 40):
    return {"mean_q": q, "alignment": align_read(read, REF, [q] * len(read))}


# ==================== 压缩区降级 ====================

def _v(**kw):
    v = {"confidence": "medium", "read_pos": 150, "support_reads": 1}
    v.update(kw)
    return v


def test_merged_zone_ignores_multi_read_support():
    """多 read 互证的变异不受单 read 压缩区降级影响"""
    src = {"poly_merged_zones": [{"positions": {148, 149, 150, 151}}]}
    assert _merged_zone_artifact(_v(support_reads=2), src) is False
    assert _merged_zone_artifact(_v(support_reads=3), src) is False


def test_merged_zone_downgrades_single_read_in_zone():
    src = {"poly_merged_zones": [{"positions": {148, 149, 150, 151}}]}
    assert _merged_zone_artifact(_v(support_reads=1), src) is True
    assert _merged_zone_artifact(_v(support_reads=1, read_pos=10), src) is False


def test_merged_zone_skips_low_and_missing_source():
    src = {"poly_merged_zones": [{"positions": {150}}]}
    assert _merged_zone_artifact(_v(confidence="low"), src) is False
    assert _merged_zone_artifact(_v(), None) is False
    assert _merged_zone_artifact(_v(), {}) is False


def test_merged_zone_handles_missing_positions_key():
    assert _merged_zone_artifact(_v(), {"poly_merged_zones": [{}]}) is False


# ==================== N 不压过明确碱基 ====================

def test_pick_base_vote_prefers_definite_base():
    assert _pick_base_vote({"N": 30.0, "A": 20.0}) == "A"
    assert _pick_base_vote({"A": 20.0, "N": 30.0}) == "A"   # 与插入顺序无关


def test_pick_base_vote_keeps_n_when_nothing_else():
    assert _pick_base_vote({"N": 12.0}) == "N"
    assert _pick_base_vote({"R": 5.0, "Y": 3.0}) == "R"


def test_pick_base_vote_treats_indel_as_definite():
    assert _pick_base_vote({"+A": 10.0, "N": 30.0}) == "+A"
    assert _pick_base_vote({"-": 5.0, "N": 9.0}) == "-"


def test_consensus_prefers_clean_base_over_n():
    """一条 read 读 N、另一条干净读出参考碱基 → 共识取该碱基"""
    bad = list(REF[100:300])
    bad[50] = "N"
    cons = _build_consensus(REF, [_rr("".join(bad)), _rr(REF[100:300])])
    assert cons["sequence"][150] == REF[150]
    assert all(d["cons_base"] != "N" for d in cons["diffs"])


def test_consensus_keeps_n_from_single_read():
    """单 read 的 N 仍如实保留（不被参考碱基悄悄填上）"""
    bad = list(REF[100:300])
    bad[50] = "N"
    cons = _build_consensus(REF, [_rr("".join(bad))])
    assert cons["sequence"][150] == "N"
