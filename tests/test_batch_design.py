"""
批量设计模块单元测试
"""

import pytest
from datetime import datetime

# 路径注入由 conftest.py 统一完成
from app.routes.models import BatchDesignRequest, BatchDesignStatus, BatchProgressResponse
from app.database.models import BatchJobDB, BatchDesignDB, DesignDB


# ==================== 批量设计请求测试 ====================

class TestBatchDesignRequest:
    """批量设计请求测试"""
    
    def test_valid_batch_request(self):
        """测试有效的批量请求"""
        from pydantic import ValidationError
        
        # 有效请求
        data = {
            "sequences": ["ATGC", "GCTA"],
            "vector_id": "pET-28a"
        }
        request = BatchDesignRequest(**data)
        
        assert len(request.sequences) == 2
        assert request.vector_id == "pET-28a"
    
    def test_empty_sequences_invalid(self):
        """测试空序列列表无效"""
        from pydantic import ValidationError
        
        with pytest.raises(ValidationError):
            BatchDesignRequest(sequences=[])
    
    def test_max_sequences_limit(self):
        """测试最大序列数量限制"""
        from pydantic import ValidationError
        
        # 超过 100 个序列应该失败
        sequences = ["ATGC"] * 101
        
        with pytest.raises(ValidationError):
            BatchDesignRequest(sequences=sequences)
    
    def test_sequence_names_optional(self):
        """测试序列名称可选"""
        data = {
            "sequences": ["ATGC", "GCTA"],
            "sequence_names": ["seq1", "seq2"]
        }
        request = BatchDesignRequest(**data)
        
        assert request.sequence_names == ["seq1", "seq2"]


# ==================== 批量任务状态测试 ====================

class TestBatchDesignStatus:
    """批量任务状态测试"""
    
    def test_batch_status_creation(self):
        """测试创建批量状态"""
        status = BatchDesignStatus(
            batch_id="batch_test123",
            total=10,
            completed=0,
            failed=0,
            status="pending",
            results=[],
            errors=[]
        )
        
        assert status.batch_id == "batch_test123"
        assert status.total == 10
        assert status.status == "pending"
    
    def test_batch_status_progress(self):
        """测试批量状态进度"""
        status = BatchDesignStatus(
            batch_id="batch_test123",
            total=10,
            completed=7,
            failed=1,
            status="running",
            results=["design_1", "design_2"],
            errors=[{"index": 0, "error": "test"}]
        )
        
        assert status.completed == 7
        assert status.failed == 1
        assert len(status.results) == 2


# ==================== 数据库模型测试 ====================

class TestBatchJobDB:
    """批量任务数据库模型测试"""
    
    def test_batch_job_creation(self):
        """测试创建批量任务记录"""
        batch = BatchJobDB(
            id="batch_test123",
            total=5,
            completed=0,
            failed=0,
            status="pending"
        )
        
        assert batch.id == "batch_test123"
        assert batch.total == 5
        assert batch.status == "pending"
    
    def test_batch_job_timestamps(self):
        """测试批量任务时间戳"""
        from datetime import datetime
        batch = BatchJobDB(
            id="batch_test123",
            total=5,
            created_at=datetime.utcnow()
        )
        
        assert batch.created_at is not None
        assert batch.completed_at is None
    
    def test_batch_design_association(self):
        """测试批量-设计关联"""
        assoc = BatchDesignDB(
            batch_id="batch_1",
            design_id="design_1",
            sequence_name="test_seq"
        )
        
        assert assoc.batch_id == "batch_1"
        assert assoc.design_id == "design_1"


# ==================== 进度响应测试 ====================

class TestBatchProgressResponse:
    """批量进度响应测试"""
    
    def test_progress_response(self):
        """测试进度响应"""
        response = BatchProgressResponse(
            batch_id="batch_test",
            total=10,
            completed=5,
            failed=1,
            pending=4,
            status="running",
            progress_percent=60.0,
            results=[{"design_id": "d1"}],
            errors=[]
        )
        
        assert response.progress_percent == 60.0
        assert response.pending == 4
    
    def test_completed_response(self):
        """测试完成响应"""
        response = BatchProgressResponse(
            batch_id="batch_test",
            total=10,
            completed=10,
            failed=0,
            pending=0,
            status="completed",
            progress_percent=100.0,
            results=[],
            errors=[]
        )
        
        assert response.status == "completed"
        assert response.progress_percent == 100.0


# ==================== 辅助函数测试 ====================

class TestBatchHelpers:
    """批量设计辅助函数测试"""
    
    def test_progress_calculation(self):
        """测试进度百分比计算"""
        total = 10
        completed = 7
        failed = 1
        
        progress = ((completed + failed) / total) * 100
        
        assert progress == 80.0
    
    def test_sequence_chunking(self):
        """测试序列分块"""
        sequences = ["A", "B", "C", "D", "E"]
        chunk_size = 2
        
        chunks = [
            sequences[i:i + chunk_size]
            for i in range(0, len(sequences), chunk_size)
        ]
        
        assert len(chunks) == 3
        assert chunks[0] == ["A", "B"]
        assert chunks[-1] == ["E"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
