"""
克隆策略模块测试
"""

import pytest
import sys
sys.path.insert(0, '/root/.openclaw/workspace/plasmid-designer-v2/src/backend')

from core.clone_strategy import (
    CloningMethod, CloningStrategy, CloningStep,
    GibsonAssemblyStrategy, GoldenGateStrategy, RestrictionCloningStrategy,
    generate_cloning_strategy
)


def test_gibson_strategy():
    """测试Gibson Assembly策略生成"""
    generator = GibsonAssemblyStrategy()
    
    insert = "ATG" + "A" * 100 + "TAA"
    vector = "G" * 1000
    
    strategy = generator.generate(
        insert_seq=insert,
        insert_name="test_insert",
        vector_seq=vector,
        vector_name="test_vector",
        insert_position=500,
        homology_arm=20
    )
    
    assert strategy.method == CloningMethod.GIBSON
    assert len(strategy.steps) >= 5
    assert strategy.insert_name == "test_insert"
    assert "Gibson" in strategy.to_protocol()


def test_golden_gate_strategy():
    """测试Golden Gate策略生成"""
    generator = GoldenGateStrategy()
    
    insert = "ATG" + "GCT" * 50 + "TAA"
    vector = "N" * 5000
    
    strategy = generator.generate(
        insert_seq=insert,
        insert_name="gg_insert",
        vector_seq=vector,
        vector_name="gg_vector",
        enzyme="BsaI",
        overhang_5="AATG",
        overhang_3="GCTT"
    )
    
    assert strategy.method == CloningMethod.GOLDEN_GATE
    assert "BsaI" in strategy.enzymes
    assert len(strategy.steps) >= 3
    assert "Golden Gate" in strategy.to_protocol()


def test_restriction_strategy():
    """测试限制性酶切克隆策略生成"""
    generator = RestrictionCloningStrategy()
    
    insert = "ATG" + "A" * 100 + "TAA"
    vector = "N" * 5000
    
    strategy = generator.generate(
        insert_seq=insert,
        insert_name="res_insert",
        vector_seq=vector,
        vector_name="res_vector",
        enzyme_5="EcoRI",
        enzyme_3="XhoI",
        dephosphorylate=True
    )
    
    assert strategy.method == CloningMethod.RESTRICTION
    assert "EcoRI" in strategy.enzymes
    assert "XhoI" in strategy.enzymes
    assert len(strategy.warnings) > 0
    # 协议默认输出中文；英文标题含方法名 Restriction
    assert "限制性酶切" in strategy.to_protocol()
    assert "Restriction" in strategy.to_protocol(language="en")


def test_cloning_step():
    """测试克隆步骤"""
    step = CloningStep(
        step_number=1,
        action="PCR amplify",
        description="Amplify insert",
        reagents=["DNA", "Primers", "Polymerase"],
        conditions={"Temperature": "98°C", "Time": "30s"},
        duration="1 hour",
        notes="Use high-fidelity polymerase"
    )
    
    assert step.step_number == 1
    assert len(step.reagents) == 3
    assert step.action == "PCR amplify"


def test_strategy_to_protocol():
    """测试策略转协议"""
    # 注意：使用关键字参数。第 4 个位置参数是 description_zh，
    # 按旧签名用位置传列表会导致中文协议渲染崩溃。
    steps = [
        CloningStep(step_number=1, action="PCR", description="Amplify", duration="1h"),
        CloningStep(step_number=2, action="Digest", description="Cut vector",
                    reagents=["Enzyme"], conditions={"Temp": "37°C"}, duration="2h"),
    ]
    
    strategy = CloningStrategy(
        method=CloningMethod.GIBSON,
        insert_name="test",
        vector_name="pTest",
        steps=steps,
        primers=[{"name": "F", "sequence": "ATGC"}],
        enzymes=["EcoRI"],
        expected_product_size=1000
    )
    
    protocol = strategy.to_protocol()
    
    assert "Gibson" in protocol
    assert "test" in protocol
    assert "PCR" in protocol
    assert "Digest" in protocol


def test_generate_cloning_strategy_gibson():
    """测试策略生成入口 - Gibson"""
    strategy = generate_cloning_strategy(
        method=CloningMethod.GIBSON,
        insert_seq="ATG" + "A" * 50 + "TAA",
        insert_name="test",
        vector_seq="N" * 1000,
        vector_name="pTest",
        insert_position=500
    )
    
    assert strategy.method == CloningMethod.GIBSON


def test_generate_cloning_strategy_golden_gate():
    """测试策略生成入口 - Golden Gate"""
    strategy = generate_cloning_strategy(
        method=CloningMethod.GOLDEN_GATE,
        insert_seq="ATG" + "A" * 50 + "TAA",
        insert_name="test",
        vector_seq="N" * 1000,
        vector_name="pTest",
        enzyme="BsmBI",
        overhang_5="AAAA",
        overhang_3="TTTT"
    )
    
    assert strategy.method == CloningMethod.GOLDEN_GATE
    assert "BsmBI" in strategy.enzymes


def test_generate_cloning_strategy_restriction():
    """测试策略生成入口 - Restriction"""
    strategy = generate_cloning_strategy(
        method=CloningMethod.RESTRICTION,
        insert_seq="ATG" + "A" * 50 + "TAA",
        insert_name="test",
        vector_seq="N" * 1000,
        vector_name="pTest",
        enzyme_5="BamHI",
        enzyme_3="HindIII"
    )
    
    assert strategy.method == CloningMethod.RESTRICTION
    assert "BamHI" in strategy.enzymes


def test_restriction_protocol_has_no_unfilled_placeholder():
    """回归：PCR 条件曾输出未填充的字面量 {Tm-5}°C 占位符"""
    strategy = generate_cloning_strategy(
        method=CloningMethod.RESTRICTION,
        insert_seq="ATG" + "A" * 50 + "TAA",
        insert_name="test",
        vector_seq="N" * 1000,
        vector_name="pTest",
        enzyme_5="EcoRI",
        enzyme_3="XhoI",
    )
    protocol = strategy.to_protocol(language="zh") + strategy.to_protocol(language="en")
    assert "{Tm-5}" not in protocol
    # 退火温度应给出可读的通用参考
    assert "Tm-5" in protocol or "退火" in protocol or "anneal" in protocol.lower()


def test_restriction_dephosphorylate_flag_respected():
    """回归：dephosphorylate=False 时不应再出现去磷酸化步骤"""
    def gen(flag):
        return RestrictionCloningStrategy().generate(
            insert_seq="ATG" + "A" * 30 + "TAA",
            insert_name="t", vector_seq="N" * 500, vector_name="pT",
            enzyme_5="EcoRI", enzyme_3="XhoI", dephosphorylate=flag,
        )
    on = gen(True)
    off = gen(False)
    on_conds = " ".join(str(c) for s in on.steps for c in s.conditions.values())
    off_conds = " ".join(str(c) for s in off.steps for c in s.conditions.values())
    assert "CIP" in on_conds or "去磷酸" in "".join(s.description_zh for s in on.steps)
    assert "CIP" not in off_conds


def test_golden_gate_warning_names_actual_enzyme():
    """回归：警告曾硬编码 BsaI/BsmBI，与实际所选酶无关"""
    strategy = generate_cloning_strategy(
        method=CloningMethod.GOLDEN_GATE,
        insert_seq="ATG" + "A" * 50 + "TAA",
        insert_name="test",
        vector_seq="N" * 1000,
        vector_name="pTest",
        enzyme="BsmBI",
    )
    warnings_text = " ".join(strategy.warnings + strategy.warnings_zh)
    assert "BsmBI" in warnings_text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
