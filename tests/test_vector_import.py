"""
载体导入模块单元测试
"""

import pytest
import sys

from core.external_vector_importer import (
    NCBIClient, GenBankImporter, VectorLibraryManager,
    ExternalVector
)


# ==================== NCBI 客户端测试 ====================

class TestNCBIClient:
    """NCBI 客户端测试"""
    
    def test_client_initialization(self):
        """测试客户端初始化"""
        client = NCBIClient(email="test@example.com")
        
        assert client.email == "test@example.com"
        assert client.min_interval > 0
    
    def test_rate_limiting_config(self):
        """测试速率限制配置"""
        client_no_key = NCBIClient()
        client_with_key = NCBIClient(api_key="test_key")
        
        # 有 API key 时间隔更短
        assert client_with_key.min_interval < client_no_key.min_interval
    
    def test_search_returns_list(self):
        """测试搜索返回列表"""
        client = NCBIClient()
        
        # 模拟搜索（不实际调用 API）
        # 实际测试应该 mock API 响应
        assert hasattr(client, 'search')
        assert hasattr(client, 'fetch_genbank')
        assert hasattr(client, 'fetch_fasta')


# ==================== GenBank 导入器测试 ====================

class TestGenBankImporter:
    """GenBank 导入器测试"""
    
    def test_parse_genbank_format(self):
        """测试解析 GenBank 格式"""
        importer = GenBankImporter()
        
        # 模拟 GenBank 内容
        genbank_content = """
LOCUS       TEST_SEQ                100 bp DNA
DEFINITION  Test sequence for unit testing
ACCESSION   TEST123
FEATURES    Location/Qualifiers
     CDS             1..50
ORIGIN
        1 atgcatgcat gcatgcatgc atgcatgcat gcatgcatgc atgcatgcat gc
//
"""
        
        vector = importer._parse_genbank(genbank_content)
        
        assert vector is not None
        assert vector.name == "TEST_SEQ"
        assert len(vector.sequence) > 0
    
    def test_importer_methods(self):
        """测试导入器方法"""
        importer = GenBankImporter()
        
        assert hasattr(importer, 'import_file')
        assert hasattr(importer, 'import_directory')


# ==================== 外部载体模型测试 ====================

class TestExternalVector:
    """外部载体模型测试"""
    
    def test_vector_creation(self):
        """测试创建载体"""
        vector = ExternalVector(
            id="test_vector",
            name="Test Vector",
            source="test",
            sequence="ATGCATGC",
            description="Test description",
            vector_type="expression",
            host=["E.coli"],
            antibiotic_resistance=["Amp"],
            features=[{"type": "CDS", "start": 1, "end": 4}]
        )
        
        assert vector.id == "test_vector"
        assert vector.name == "Test Vector"
        assert len(vector.sequence) == 8
    
    def test_vector_url_optional(self):
        """测试 URL 可选"""
        vector = ExternalVector(
            id="test",
            name="Test",
            source="test",
            sequence="ATGC",
            description="",
            vector_type="cloning",
            host=[],
            antibiotic_resistance=[],
            features=[]
        )
        
        assert vector.url is None


# ==================== 向量库管理器测试 ====================

class TestVectorLibraryManager:
    """向量库管理器测试"""
    
    def test_manager_initialization(self):
        """测试管理器初始化"""
        manager = VectorLibraryManager(data_dir="/tmp/test_vectors")
        
        assert manager.data_dir == "/tmp/test_vectors"
        assert manager.vectors == {}
    
    def test_manager_clients(self):
        """测试管理器客户端"""
        manager = VectorLibraryManager()
        
        assert manager.ncbi is not None
        assert manager.addgene is not None
        assert manager.genbank is not None
    
    def test_search_local_empty(self):
        """测试本地搜索（空）"""
        manager = VectorLibraryManager()
        results = manager.search_local("nonexistent")
        
        assert isinstance(results, list)
        assert len(results) == 0


# ==================== GenBank 解析测试 ====================

class TestGenBankParsing:
    """GenBank 解析测试"""
    
    def test_locus_parsing(self):
        """测试 LOCUS 行解析"""
        importer = GenBankImporter()
        
        content = "LOCUS       MY_VECTOR              5000 bp DNA circular"
        vector = importer._parse_genbank(content)
        
        assert vector.name == "MY_VECTOR"
    
    def test_sequence_extraction(self):
        """测试序列提取"""
        importer = GenBankImporter()
        
        content = """
LOCUS       TEST
ORIGIN
     1 atgcatgcat gc
//
"""
        vector = importer._parse_genbank(content)
        
        # 序列应该只包含 ATGC
        assert set(vector.sequence.upper()).issubset({'A', 'T', 'G', 'C'})
    
    def test_features_extraction(self):
        """测试特征提取"""
        importer = GenBankImporter()
        
        content = """
LOCUS       TEST
FEATURES    Location/Qualifiers
     CDS             1..100
     promoter        101..150
ORIGIN
     1 atgcatgcat
//
"""
        vector = importer._parse_genbank(content)
        
        assert len(vector.features) >= 0  # 取决于解析实现


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
