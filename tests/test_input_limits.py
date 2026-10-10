"""输入上限回归（终审 C-03 / C-06）。

C-03：设计序列上限 100_000 不是防护而是 CPU DoS 入口——滑窗精修是
O(n²)，实测 10k aa 19.6s 且无超时不可取消。现按序列类型收紧（氨基酸
5000 / DNA 20000，可用环境变量覆盖），超限应是 422 校验错误而非让请求
进入计算层。

C-06：分析类接口此前完全不限长、窗口/步长下限为 1——实测 1Mb 序列 +
window=1/step=1 得到 108MB 响应。现统一 max_length 与窗口下限。
"""

import pytest

from app.routes.models import (
    BatchDesignRequest, CloningMethod, DesignRequest, SequenceType,
    MAX_INPUT_AA, MAX_INPUT_DNA,
)
from app.analysis_routes import (
    GCAnalysisRequest, ORFRequest, SequenceAnalysisRequest,
    MAX_ANALYSIS_SEQ_BP,
)


def _design(sequence: str, seq_type: SequenceType = SequenceType.AMINO_ACID):
    return DesignRequest(
        sequence=sequence,
        sequence_type=seq_type,
        cloning_method=CloningMethod.RESTRICTION,
        enzyme_5="EcoRI",
        enzyme_3="HindIII",
    )


def test_design_rejects_overlong_amino_acid():
    """氨基酸超 5000 残基 → 422 校验错误（不再进计算层）"""
    with pytest.raises(ValueError, match="序列过长"):
        _design("M" * (MAX_INPUT_AA + 1))


def test_design_rejects_overlong_dna():
    """DNA 超 20000 碱基 → 校验错误"""
    with pytest.raises(ValueError, match="序列过长"):
        _design("ATG" * (MAX_INPUT_DNA // 3 + 1), SequenceType.DNA)


def test_design_accepts_within_limit():
    """上限之内的请求不受影响（不误伤正常长度）"""
    ok = _design("M" * MAX_INPUT_AA)
    assert len(ok.sequence) == MAX_INPUT_AA


def test_dna_limit_is_larger_than_aa():
    """DNA 上限按碱基数计，应显著大于氨基酸上限（同一蛋白的核酸形式更长）"""
    assert MAX_INPUT_DNA > MAX_INPUT_AA


def test_batch_design_rejects_overlong_and_total():
    with pytest.raises(ValueError, match="序列过长"):
        BatchDesignRequest(
            sequences=["M" * (MAX_INPUT_AA + 1)],
            sequence_type=SequenceType.AMINO_ACID,
            cloning_method=CloningMethod.RESTRICTION,
            enzyme_5="EcoRI",
            enzyme_3="HindIII",
        )
    # 单条合规但总量超限（总量按「条数 × 上限 / 2」留余量）
    with pytest.raises(ValueError, match="总长度"):
        BatchDesignRequest(
            sequences=["M" * MAX_INPUT_AA] * 3,
            sequence_type=SequenceType.AMINO_ACID,
            cloning_method=CloningMethod.RESTRICTION,
            enzyme_5="EcoRI",
            enzyme_3="HindIII",
        )


def test_analysis_requests_reject_overlong_sequence():
    """C-06：分析类请求统一 max_length（此前 1Mb 序列可直达，响应 108MB）"""
    too_long = "A" * (MAX_ANALYSIS_SEQ_BP + 1)
    for model in (SequenceAnalysisRequest, ORFRequest, GCAnalysisRequest):
        with pytest.raises(Exception):
            model(sequence=too_long)


def test_gc_window_size_has_lower_bound():
    """C-06：window_size=1 无统计意义且会放大输出（实测响应 108MB）"""
    with pytest.raises(Exception):
        GCAnalysisRequest(sequence="ACGT" * 10, window_size=1, step_size=1)
    ok = GCAnalysisRequest(sequence="ACGT" * 10, window_size=10, step_size=5)
    assert ok.window_size >= 10


def test_gc_step_size_lower_bound_and_region_cap():
    """gc-analysis 输出放大：200 kb + window 10 + step 1 = 20 万区间 / 13 MB 响应"""
    from app.analysis_routes import MAX_GC_REGIONS
    seq = "ATGC" * 50_000
    with pytest.raises(Exception):
        GCAnalysisRequest(sequence=seq, window_size=10, step_size=1)
    with pytest.raises(Exception):
        GCAnalysisRequest(sequence="ACGT" * 100, window_size=100, step_size=9)
    # 步长合规但区间数仍超上限
    with pytest.raises(Exception):
        GCAnalysisRequest(sequence=seq, window_size=10, step_size=5)
    ok = GCAnalysisRequest(sequence=seq, window_size=100, step_size=50)  # 前端默认
    assert (len(seq) - 100) // 50 + 1 <= MAX_GC_REGIONS
    assert ok.step_size == 50


def test_gc_analysis_endpoint_rejects_amplifying_params():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.post("/api/analysis/gc-analysis",
               json={"sequence": "ATGC" * 50_000, "window_size": 10, "step_size": 1})
    assert r.status_code == 422
    r = c.post("/api/analysis/gc-analysis", json={"sequence": "ATGC" * 500})
    assert r.status_code == 200


def test_orf_min_length_has_lower_bound():
    """min_length=1 会产出无意义的 ORF 洪流"""
    with pytest.raises(Exception):
        ORFRequest(sequence="ATG" * 20, min_length=1)
    assert ORFRequest(sequence="ATG" * 20, min_length=30).min_length >= 30
