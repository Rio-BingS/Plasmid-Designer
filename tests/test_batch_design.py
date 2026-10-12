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
        from app.timeutil import utcnow_naive
        batch = BatchJobDB(
            id="batch_test123",
            total=5,
            created_at=utcnow_naive()
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


# ==================== Golden Gate 酶校验回归 ====================

class TestBatchGoldenGateEnzyme:
    """回归：批量 Golden Gate 必须显式指定 Type IIS 酶，与单条设计同一口径

    bug：批量设计页从不发送 enzyme，而 Golden Gate 缺酶时请求 422，
    整批提交直接失败。服务端刻意不回落 BsaI（静默套酶会产出错误引物）。
    """

    def test_golden_gate_without_enzyme_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Golden Gate 需指定 Type IIS 酶"):
            BatchDesignRequest(sequences=["ATGAAA"], cloning_method="golden_gate")

    def test_golden_gate_unsupported_enzyme_rejected(self):
        from pydantic import ValidationError

        # EcoRI 不是 Type IIS，不能用于 Golden Gate
        with pytest.raises(ValidationError, match="不支持的 Golden Gate 酶"):
            BatchDesignRequest(
                sequences=["ATGAAA"], cloning_method="golden_gate", enzyme="EcoRI"
            )

    def test_golden_gate_with_enzyme_accepted(self):
        request = BatchDesignRequest(
            sequences=["ATGAAA"], cloning_method="golden_gate", enzyme="BsaI"
        )
        assert request.enzyme == "BsaI"

    def test_no_server_side_default_enzyme(self):
        """批量 API 不替调用方兜底默认酶：未指定方法时 enzyme 保持 None"""
        request = BatchDesignRequest(sequences=["ATGAAA"])
        assert request.enzyme is None

    def test_batch_endpoint_422_lists_candidate_enzymes(self):
        """端点层：缺酶/错酶的 Golden Gate 批量请求返回 422，且不创建任务"""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.routes import batch_routes

        client = TestClient(app)
        body = {
            "sequences": ["MKVLWAALLTFLGCAATSG"],
            "sequence_type": "amino_acid",
            "cloning_method": "golden_gate",
        }
        before = len(batch_routes.batch_jobs)

        resp = client.post("/api/design/batch", json=body)
        assert resp.status_code == 422
        assert "Golden Gate" in str(resp.json()["detail"])

        resp = client.post("/api/design/batch", json={**body, "enzyme": "EcoRI"})
        assert resp.status_code == 422
        assert "BsaI" in str(resp.json()["detail"])

        assert len(batch_routes.batch_jobs) == before

    def test_batch_task_forwards_enzyme_to_each_row(self, monkeypatch):
        """后台任务逐条构造 DesignRequest 时把批量的 enzyme 原样带下去"""
        from app.routes import batch_routes
        from app.routes.models import DesignResult, DesignStatus

        seen = []

        def fake_run_design(design_id, single):
            seen.append((single.cloning_method.value, single.enzyme))
            return DesignResult(
                design_id=design_id, status=DesignStatus.COMPLETED,
                input_sequence=single.sequence, optimized_sequence="ATG",
                construct_sequence="ATG", vector_id=single.vector_id,
                cloning_method=single.cloning_method.value, created_at=datetime.now(),
            )

        monkeypatch.setattr(batch_routes, "run_design", fake_run_design)
        monkeypatch.setattr(batch_routes, "_persist", lambda result: None)

        request = BatchDesignRequest(
            sequences=["ATGAAA", "ATGCCC"], cloning_method="golden_gate", enzyme="BsmBI"
        )
        job = BatchDesignStatus(batch_id="batch_ggfwd0001", total=2, completed=0,
                                failed=0, status="pending")
        batch_routes._persist_batch(job)
        batch_routes.run_batch_design_task(job.batch_id, request, ["a", "b"])

        assert seen == [("golden_gate", "BsmBI"), ("golden_gate", "BsmBI")]
        done = batch_routes._load_batch(job.batch_id)
        assert done.completed == 2 and done.failed == 0

    def test_batch_row_error_message_is_readable(self):
        """逐条失败原因不带 pydantic 样板，直接给业务消息"""
        from pydantic import ValidationError
        from app.routes.batch_routes import _format_row_error
        from app.routes.models import DesignRequest

        try:
            DesignRequest(sequence="ATGAAA", cloning_method="golden_gate")
        except ValidationError as exc:
            text = _format_row_error(exc)
        assert "Golden Gate 需指定 Type IIS 酶" in text
        assert "validation error" not in text
        assert "value_error" not in text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
