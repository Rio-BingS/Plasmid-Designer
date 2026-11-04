"""自定义同源臂 Gibson（custom_arms 模式）测试

用户按同一方向粘贴两条重组臂，工具把整条插入分子按两种取向试装配：
恰一取向有效；定位失败/歧义/臂过短/区间重叠等用户输入问题显式报错。
方向判定（cassette_hint）、起始密码子去除/CDS 扩展全套复用。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.construct_assembly import plan_custom_arms  # noqa: E402
from core.seq_utils import revcomp  # noqa: E402

# 合成载体：A*20 + 臂5区(21-37) + C*30(替换区 38-67) + 臂3区(68-85) + T*20
BB = "A" * 20 + "GGTGGTGGTACGTACTG" + "C" * 30 + "TTATCCACAGGACTCAGT" + "T" * 20
ARM5 = "GGTGGTGGTACGTACTG"
ARM3 = "TTATCCACAGGACTCAGT"


# ==================== plan_custom_arms 单元 ====================

def test_forward_placement():
    plan = plan_custom_arms(BB, ARM5, ARM3)
    assert not plan.insert_reverse
    assert plan.left_cut == 37 and plan.right_cut == 67   # 0-based
    assert plan.head == "" and plan.tail == ""            # 无缝，无位点残留
    # 构建体几何：backbone[:37] + gene + backbone[67:]
    assert BB[:37].endswith(ARM5) and BB[67:].startswith(ARM3)


def test_reverse_placement_with_same_direction_paste():
    """负链基因：用户同向粘贴臂的反向互补序列 → insert_reverse"""
    plan = plan_custom_arms(BB, revcomp(ARM3), revcomp(ARM5))
    assert plan.insert_reverse
    assert plan.left_cut == 37 and plan.right_cut == 67


def test_ambiguous_both_orientations_error():
    """臂回文对称（arm5 == arm3）→ 两种取向都成立 → 歧义报错"""
    pal = "ACGTACGTACGTACG" * 3  # 45bp，自身反向互补需回文——用同一序列同区匹配
    bb = "A" * 20 + pal + "C" * 30 + revcomp(pal) + "T" * 20
    # arm5=pal 在载体上正链(20)与负链(68)各命中一次 → 先触发定位不唯一
    with pytest.raises(ValueError, match="匹配到 2 处"):
        plan_custom_arms(bb, pal, revcomp(pal))


def test_no_match_error_names_the_arm():
    with pytest.raises(ValueError, match="臂①（原 5′ 臂栏）在载体上无精确匹配"):
        plan_custom_arms(BB, "ACGCGTACGTACGTA", ARM3)


def test_multiple_match_error():
    bb = "A" * 20 + ARM5 + "C" * 30 + ARM5 + "T" * 20
    with pytest.raises(ValueError, match="匹配到 2 处"):
        plan_custom_arms(bb, ARM5, ARM3)


def test_short_arm_rejected():
    with pytest.raises(ValueError, match="组装效率低"):
        plan_custom_arms(BB, "GGTGGT", ARM3)


def test_arms_overlap_rejected():
    """臂3 与臂5 匹配在同一位置：无法围出插入区间"""
    with pytest.raises(ValueError, match="无法围出插入区间"):
        plan_custom_arms(BB, ARM5, ARM5)


# ==================== 端到端 run_design ====================

AA = "M" + "EKT" * 20


def _arms_request(**over):
    payload = {
        "sequence": AA,
        "sequence_type": "amino_acid",
        "vector_id": "synth",
        "cloning_method": "gibson",
        "gibson_linearization": "custom_arms",
        "homology_arm_5": ARM5,
        "homology_arm_3": ARM3,
    }
    payload.update(over)
    from app.routes.models import DesignRequest
    return DesignRequest(**payload)


@pytest.fixture()
def synth_library(monkeypatch):
    from core.vector_library import MCS, Vector
    from app import design_service

    vector = Vector(
        id="synth", name="synth", sequence=BB,
        mcs=MCS(name="MCS", start=38, end=67, sites=[]),
        elements=[],
    )

    class _Lib:
        def get_vector(self, vector_id):
            return vector

    monkeypatch.setattr(design_service, "get_vector_library", lambda: _Lib())


def test_custom_arms_end_to_end(synth_library):
    result = run_design_fn("ca_e2e", _arms_request(sequence_name="GFP"))
    assert result.status.value == "completed", result.errors
    # 几何：替换区被换成基因（无缝，臂是载体自身碱基）
    assert result.insert_start == 38 and result.insert_end == 37 + len(
        result.optimized_sequence)
    construct = result.construct_sequence
    assert construct[:37] == BB[:37] and construct[37 + len(result.optimized_sequence):] == BB[67:]
    # 质粒命名默认 = 序列名
    assert result.plasmid_name == "GFP"
    # 引物：正向 = 臂5（overhang）+ 基因前段退火区（sequence）
    assert result.primers[0].overhang == ARM5
    assert result.primers[1].overhang == revcomp(ARM3)


def test_custom_arms_start_codon_trim(monkeypatch):
    """5′ 臂以 ATG 结尾（用户把起始密码子放进臂里）→ 默认去除插入序列开头 ATG"""
    from core.vector_library import MCS, Vector
    from app import design_service

    arm5_atg = "GGTGGTGGTACGCATATG"   # 末尾 ATG = 位点提供的起始密码子
    bb = "A" * 20 + arm5_atg + "C" * 30 + ARM3 + "T" * 20
    vector = Vector(id="synth", name="synth", sequence=bb,
                    mcs=MCS(name="MCS", start=39, end=67, sites=[]), elements=[])

    class _Lib:
        def get_vector(self, vector_id):
            return vector

    monkeypatch.setattr(design_service, "get_vector_library", lambda: _Lib())
    result = run_design_fn("ca_trim", _arms_request(homology_arm_5=arm5_atg))
    assert result.status.value == "completed", result.errors
    assert any(w.startswith("已自动去除冗余起始密码子") for w in result.warnings)
    assert not result.cloned_sequence.upper().startswith("ATG")
    feat = next(f for f in result.construct_features
                if f["type"] == "CDS" and "designed insert" in f.get("description", "").lower())
    assert result.construct_sequence[feat["start"] - 1:feat["start"] + 2] == "ATG"


def test_custom_arms_direction_follows_promoter_on_pet28a():
    """pET-28a 表达盒在负链：臂只给位置，装配链向按启动子自动判成负链
    （旧模型按粘贴链向拼成正链、只告警不纠正——现在直接拼对）"""
    from app.design_service import get_vector_library
    bb = get_vector_library().get_vector("pET-28a").sequence
    arm5 = bb[140:158]   # 紧贴 XhoI(158) 左侧
    arm3 = bb[238:256]   # 紧贴 NdeI(238) 右侧
    result = run_design_fn("ca_dir", _arms_request(
        vector_id="pET-28a", homology_arm_5=arm5, homology_arm_3=arm3))
    assert result.status.value == "completed", result.errors
    assert any("CDS 方向与启动子一致" in w and "负链" in w
               for w in result.warnings)
    # 构建体 CDS 落在负链（与启动子同向）
    cds = next(f for f in result.construct_features
               if f["type"] == "CDS"
               and "designed insert" in (f.get("description") or "").lower())
    assert cds["strand"] == "-"


def run_design_fn(design_id, request):
    from app.design_service import run_design
    return run_design(design_id, request)


_BB_HOLDER = {"seq": BB}


def _set_bb(seq):
    """更换合成载体序列（同 monkeypatch 库配合）"""
    _BB_HOLDER["seq"] = seq
    from core.vector_library import MCS, Vector
    from app import design_service

    vector = Vector(
        id="synth", name="synth", sequence=seq,
        mcs=MCS(name="MCS", start=38, end=67, sites=[]),
        elements=[],
    )
    design_service.get_vector_library = lambda: type(
        "L", (), {"get_vector": lambda s, v: vector})()
