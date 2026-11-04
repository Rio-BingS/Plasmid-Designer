"""图谱引物：密集场景数据模型与布局回归。

验收要求（模块 A / Step 1 / Step 5）：
  - 引物对象自带 `strand / tm / gc / start / end`，渲染层不用再算；
  - 密集引物（40 条挤在 600..1400）在环形布局里**不重叠**——
    这是前端 layoutCircularLabels 的属性，用 Python 侧无法直接覆盖，
    因此这里守住「后端如实下发字段 + 数量正确」，重叠由前端测试
    （primer_dense_layout.test.ts）用同一份生成规则验证。

样本生成规则与前端测试保持一致（见 tests/primer_dense_layout.test.ts），
两边用同一组起点/链向即可复现同一密集图。
"""

from core.plasmid_map import build_document

BASES = "ATGAAACGTCTGGAAATCGCTGCTGAACTGCGTAAAGGCGCTGCTACCGTTCTGAAAGAACTGGCTAAA"


def _seq(n: int = 600) -> str:
    s = (BASES * ((n // len(BASES)) + 1))[:n]
    return s


def _dense_primers(n: int = 40, start: int = 30, step: int = 12, span: int = 20):
    """n 条引物，起点逐条 +step、长度 span，正反向交替（与前端测试一致）"""
    out = []
    for i in range(n):
        s = start + i * step
        e = s + span - 1
        rev = i % 2 == 1
        name = f"D_{i + 1:02d}" + ("_AS" if rev else "")
        out.append({"name": name, "start": s, "end": e, "strand": -1 if rev else 1})
    return out


def test_dense_primers_keep_all_fields():
    """40 条密集引物：全部保留，且每条都带 strand/tm/gc/start/end"""
    seq = _seq(600)
    doc = build_document(name="dense", sequence=seq, topology="linear", source_format="raw",
                         features=[], primers=_dense_primers(40, start=30, step=10, span=20),
                         annotate=False)
    prims = doc["primers"]
    assert len(prims) == 40
    for p in prims:
        assert p["strand"] in (1, -1)
        assert 1 <= p["start"] <= p["end"] <= len(seq)
        # Step 1：Tm / GC 由后端算好随文档下发（渲染层零计算）
        assert isinstance(p["tm"], float) and p["tm"] >= 0
        assert isinstance(p["gc"], float) and 0 <= p["gc"] <= 100
        assert p["sequence"]
    # 正反向各 20 条（交替规则）
    assert sum(1 for p in prims if p["strand"] == 1) == 20
    assert sum(1 for p in prims if p["strand"] == -1) == 20


def test_dense_primer_tm_gc_match_sequence():
    """Tm/GC 必须由退火区序列算出（与全库唯一实现一致），不是占位值"""
    from core.primer_base import calculate_tm
    from core.seq_utils import gc_percent

    seq = _seq(600)
    doc = build_document(name="dense", sequence=seq, topology="linear", source_format="raw",
                         features=[], primers=_dense_primers(5, start=30, step=40, span=20),
                         annotate=False)
    for p in doc["primers"]:
        assert p["tm"] == round(calculate_tm(p["sequence"]), 1)
        assert p["gc"] == round(gc_percent(p["sequence"]), 1)


def test_dense_primers_widths_are_thin():
    """密集但短（20 bp）的引物不应触发「引物过长」等告警"""
    seq = _seq(600)
    doc = build_document(name="dense", sequence=seq, topology="linear", source_format="raw",
                         features=[], primers=_dense_primers(40, start=30, step=10, span=20),
                         annotate=False)
    assert not any("引物超过" in w for w in doc["meta"]["warnings"])
