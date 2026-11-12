"""关键元件名称匹配须按独立词（verdict.CRITICAL_NAME_RE）

旧正则为纯子串匹配：alpha/Shank/chaperone 含「ha」、this/whisker 含「his」，
会被误判为亲和标签而进入关键元件表。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/backend"))

from core.sanger.verdict import CRITICAL_NAME_RE, _critical_features  # noqa: E402

HIT = ["6xHis", "His6", "His-tag", "HisTag", "8x His", "HA", "HA-tag", "3xHA",
       "3xFLAG", "FLAG tag", "c-Myc", "Myc-tag", "Strep-tag II", "StrepII",
       "GST", "MBP", "Kozak", "loxP", "loxP site", "attB1", "attR2", "FRT",
       "AmpR", "AmpR promoter", "KanR", "CmR", "NeoR/KanR", "HIS3"]
MISS = ["alpha", "lacZ alpha", "Shank3", "chaperone", "this", "whisker",
        "GSTP1x", "Streptomyces", "mycelium", "frtz", "attC", "Hsp70",
        "MBPase", "EGFP", "lacI"]


@pytest.mark.parametrize("name", HIT)
def test_tag_names_hit(name):
    assert CRITICAL_NAME_RE.search(name), name


@pytest.mark.parametrize("name", MISS)
def test_substrings_do_not_hit(name):
    assert not CRITICAL_NAME_RE.search(name), name


def test_critical_features_uses_word_match():
    feats = [{"type": "CDS", "name": "lacZ alpha"},
             {"type": "CDS", "name": "6xHis"},
             {"type": "promoter", "name": "anything"}]
    names = [f["name"] for f in _critical_features(feats)]
    assert names == ["6xHis", "anything"]
