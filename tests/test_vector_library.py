"""
载体库模块测试
"""

import pytest
import sys
sys.path.insert(0, '/root/.openclaw/workspace/plasmid-designer-v2/src/backend')

from core.vector_library import (
    VectorLibrary, Vector, VectorElement, MCS, CloningSite,
    ElementType, VectorCompatibilityChecker
)


def test_vector_creation():
    """测试载体创建"""
    vector = Vector(
        id="test_vector",
        name="Test Vector",
        sequence="ATGC" * 100,
        source="Test",
        vector_type="expression",
        host=["E.coli"],
        antibiotic_resistance=["kanamycin"]
    )
    
    assert vector.id == "test_vector"
    assert vector.length == 400
    assert "kanamycin" in vector.antibiotic_resistance


def test_vector_element():
    """测试载体元件"""
    element = VectorElement(
        name="T7_promoter",
        element_type=ElementType.PROMOTER,
        start=1,
        end=50,
        sequence="ATGC" * 12 + "AT"
    )
    
    assert element.name == "T7_promoter"
    assert element.element_type == ElementType.PROMOTER
    assert element.length == 50


def test_mcs_creation():
    """测试MCS创建"""
    mcs = MCS(
        name="MCS",
        start=100,
        end=150,
        reading_frame=1,
        sites=[
            CloningSite(
                enzyme_name="BamHI",
                recognition_seq="GGATCC",
                cut_position_5=110,
                cut_position_3=116,
                overhang="GATC"
            )
        ]
    )
    
    assert mcs.name == "MCS"
    assert len(mcs.sites) == 1
    assert "BamHI" in mcs.get_unique_enzymes()


def test_vector_library():
    """测试载体库管理"""
    library = VectorLibrary()
    
    vector = Vector(
        id="pTest",
        name="Test Plasmid",
        sequence="ATGC" * 100,
        vector_type="cloning"
    )
    
    library.add_vector(vector)
    
    # 测试获取
    retrieved = library.get_vector("pTest")
    assert retrieved is not None
    assert retrieved.name == "Test Plasmid"
    
    # 测试列表
    vectors = library.list_vectors()
    assert len(vectors) == 1
    
    # 测试搜索
    results = library.search_vectors("Test")
    assert len(results) == 1


def test_compatibility_checker():
    """测试兼容性检查"""
    # 创建测试载体
    mcs = MCS(
        name="MCS",
        start=100,
        end=150,
        sites=[
            CloningSite(enzyme_name="EcoRI", recognition_seq="GAATTC", 
                       cut_position_5=110, cut_position_3=116, overhang="AATT"),
            CloningSite(enzyme_name="BamHI", recognition_seq="GGATCC",
                       cut_position_5=120, cut_position_3=126, overhang="GATC"),
            CloningSite(enzyme_name="XhoI", recognition_seq="CTCGAG",
                       cut_position_5=130, cut_position_3=136, overhang="TCGA")
        ]
    )
    
    vector = Vector(
        id="test",
        name="test",
        sequence="ATGC" * 100,
        mcs=mcs
    )
    
    # 测试不含酶切位点的序列
    clean_insert = "ATGCATGCATGC" * 10
    result = VectorCompatibilityChecker.check_enzyme_sites(clean_insert, vector, ["EcoRI", "XhoI"])
    
    assert result['compatible'] == True
    assert len(result['conflicts']) == 0
    
    # 测试含酶切位点的序列
    bad_insert = "GAATTC" + "ATGC" * 10  # 含EcoRI位点
    result = VectorCompatibilityChecker.check_enzyme_sites(bad_insert, vector, ["EcoRI", "XhoI"])
    
    assert result['compatible'] == False
    assert len(result['conflicts']) > 0


def test_element_type_filter():
    """测试元件类型过滤"""
    elements = [
        VectorElement("prom1", ElementType.PROMOTER, 1, 50, "ATGC"),
        VectorElement("term1", ElementType.TERMINATOR, 100, 150, "GCTA"),
        VectorElement("prom2", ElementType.PROMOTER, 200, 250, "ATGC"),
    ]
    
    vector = Vector(
        id="test",
        name="test",
        sequence="ATGC" * 100,
        elements=elements
    )
    
    promoters = vector.get_elements_by_type(ElementType.PROMOTER)
    assert len(promoters) == 2
    
    terminators = vector.get_elements_by_type(ElementType.TERMINATOR)
    assert len(terminators) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
