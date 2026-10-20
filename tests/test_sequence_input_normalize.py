"""粘贴序列的字符归一（全角/异体星号/各类空白）

用户报障：「设计页面输入氨基酸序列中的 * 无法被识别」。根因不是后端不认 '*'
（CODON_TABLE 里本来就有终止密码子），而是中文输入法下 Shift+8 打出的是全角
＊（U+FF0A），连同﹡（U+FE61）、∗（U+2217）等异体星号一起被当成「无效的氨基酸
代码」。凡是从 Word/Excel/网页复制来的序列还常带全角字母、全角空格、NBSP 与
零宽字符。

修法：在最外层（请求模型 / process_sequence）统一做字符等价归一，非法字符才留
到校验阶段报「哪个字符、第几位」——不在归一阶段静默丢弃。
"""

import pytest

from app.design_service import process_sequence, run_design
from app.routes.models import (
    BatchDesignRequest, DesignRequest, DesignStatus, SequenceType,
)
from core.codon_optimizer import CodonOptimizer, translate_dna
from core.seq_input import find_invalid_chars, normalize_dna_text, normalize_sequence_text
from core.sequence_validator import SequenceValidator


# ==================== 归一函数 ====================

@pytest.mark.parametrize("raw", [
    "MKV*",           # 半角（正常输入）
    "MKV\uff0a",      # 全角＊（中文输入法 Shift+8）
    "MKV\ufe61",      # 小型星号﹡
    "MKV\u2217",      # 数学星号∗
    "MKV\u066d",      # 阿拉伯星号٭
    "ＭＫＶ\uff0a",   # 全角字母 + 全角星号
    "MKV\u00a0*",     # 不换行空格
    "MKV\u3000*",     # 全角空格
    "MKV\n*\n",       # 换行
    "MKV\t*",         # 制表符
    "MKV\u200b*",     # 零宽空格
])
def test_asterisk_variants_fold_to_plain_star(raw):
    assert normalize_sequence_text(raw) == "MKV*"


def test_dna_normalization_folds_rna_u():
    # augc→ATGC、u→T、全角/NBSP 空白去掉
    assert normalize_dna_text("augc u\xa0a") == "ATGCTA"


def test_protein_u_is_not_turned_into_t():
    """U（硒代半胱氨酸）不是 T（苏氨酸）：蛋白输入不能做 U→T"""
    assert normalize_sequence_text("MKVU") == "MKVU"


def test_invalid_chars_reported_with_position():
    assert find_invalid_chars("MKV#", "ACDEFGHIKLMNPQRSTVWY*") == [("#", 4)]


# ==================== 请求模型 ====================

def test_design_request_normalizes_fullwidth_star():
    req = DesignRequest(sequence="MKV\uff0a", sequence_type=SequenceType.AMINO_ACID,
                        vector_id="pET-28a", cloning_method="gibson")
    assert req.sequence == "MKV*"


def test_batch_request_normalizes_each_sequence():
    req = BatchDesignRequest(sequences=["MKV\uff0a", "ＭＡ\uff0a"],
                             sequence_type=SequenceType.AMINO_ACID,
                             vector_id="pET-28a", cloning_method="gibson")
    assert req.sequences == ["MKV*", "MA*"]


# ==================== 设计流水线端到端 ====================

def test_design_pipeline_accepts_fullwidth_star():
    dna, cai, gc, warnings = process_sequence(
        "MKVLW\uff0a", SequenceType.AMINO_ACID, True, "ecoli", 40, 60)
    assert translate_dna(dna) == "MKVLW*"
    assert not [w for w in warnings if "无效" in w]


def test_design_result_completes_with_stop_symbol():
    req = DesignRequest(sequence="MKVLW\uff0a", sequence_type=SequenceType.AMINO_ACID,
                        sequence_name="stop_demo", vector_id="pET-28a",
                        cloning_method="gibson")
    res = run_design("design_stop_symbol", req)
    assert res.status == DesignStatus.COMPLETED, res.errors
    assert res.optimized_sequence.endswith(("TAA", "TAG", "TGA"))


def test_internal_stop_is_kept_and_flagged():
    req = DesignRequest(sequence="MKV\uff0aW", sequence_type=SequenceType.AMINO_ACID,
                        sequence_name="internal_stop", vector_id="pET-28a",
                        cloning_method="gibson")
    res = run_design("design_internal_stop", req)
    assert res.status == DesignStatus.COMPLETED, res.errors
    assert any("Internal stop" in w for w in res.warnings)


# ==================== 错误信息 ====================

def test_invalid_amino_acid_error_names_char_and_position():
    with pytest.raises(ValueError) as exc:
        CodonOptimizer(species="ecoli").optimize("MKV#W")
    msg = str(exc.value)
    assert "#" in msg and "第 4 位" in msg
    assert "ACDEFGHIKLMNPQRSTVWY" in msg


def test_optimizer_accepts_fullwidth_star_directly():
    res = CodonOptimizer(species="ecoli").optimize("MKV\uff0a")
    assert translate_dna(res.dna_sequence) == "MKV*"


# ==================== 校验器 ====================

def test_aa_validator_accepts_stop_symbol():
    result = SequenceValidator().validate("MKV*", sequence_type="amino_acid",
                                          check_orf=False, check_gc=False)
    assert result.is_valid, result.errors


def test_aa_validator_still_rejects_ambiguous_codes():
    """X/B/Z 没有确定密码子，保持既有契约报错（只有 * 是新增的合法输入）"""
    result = SequenceValidator().validate("MKVX", sequence_type="amino_acid",
                                          check_orf=False, check_gc=False)
    assert not result.is_valid
    assert any("X" in e for e in result.errors)


def test_aa_validator_rejects_unknown_symbol():
    result = SequenceValidator().validate("MKV#", sequence_type="amino_acid",
                                          check_orf=False, check_gc=False)
    assert not result.is_valid
    assert any("#" in e for e in result.errors)
