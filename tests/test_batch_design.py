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


# ==================== v2.27.0：内联载体 / 合并订购表 / ZIP 命名 / 进度字段 ====================

class TestBatchInlineVector:
    """批量请求支持内联一次性载体（与单条 DesignRequest.vector 同语义）"""

    def test_inline_vector_accepted(self):
        from app.schemas import DesignVectorInput

        vec = DesignVectorInput(
            id="pdir", name="pDir-1", sequence="ACGT" * 100,
            topology="circular")
        request = BatchDesignRequest(
            sequences=["ATGAAA", "ATGCCC"], enzyme_5="NdeI", enzyme_3="XhoI",
            cloning_method="restriction", vector=vec)
        assert request.vector is not None
        assert request.vector.name == "pDir-1"

    def test_inline_vector_forwarded_to_each_row(self, monkeypatch):
        """后台任务逐条构造 DesignRequest 时内联载体原样带下去"""
        from app.schemas import DesignVectorInput
        from app.routes import batch_routes
        from app.routes.models import DesignResult, DesignStatus

        seen = []

        def fake_run_design(design_id, single):
            seen.append(single.vector)
            return DesignResult(
                design_id=design_id, status=DesignStatus.COMPLETED,
                input_sequence=single.sequence, optimized_sequence="ATG",
                construct_sequence="ATG", vector_id=single.vector_id,
                cloning_method=single.cloning_method.value, created_at=datetime.now(),
            )

        monkeypatch.setattr(batch_routes, "run_design", fake_run_design)
        monkeypatch.setattr(batch_routes, "_persist", lambda result: None)

        vec = DesignVectorInput(
            id="pdir", name="pDir-1", sequence="ACGT" * 100,
            topology="circular")
        request = BatchDesignRequest(
            sequences=["ATGAAA", "ATGCCC"], enzyme_5="NdeI", enzyme_3="XhoI",
            cloning_method="restriction", vector=vec)
        job = BatchDesignStatus(batch_id="batch_vecfwd001", total=2, completed=0,
                                failed=0, status="pending")
        batch_routes._persist_batch(job)
        batch_routes.run_batch_design_task(job.batch_id, request, ["a", "b"])

        assert len(seen) == 2
        assert all(v is not None and v.name == "pDir-1" for v in seen)


class TestBatchPrimerTsv:
    """合并引物订购表：列序、类型标注、按基因分组"""

    def _primer(self, name, target_start=None, tm=55.0, gc=50.0):
        from app.schemas import PrimerInfo
        return PrimerInfo(
            name=name, sequence="ACGT", full_sequence="ACGTACGTAC",
            tm=tm, gc_content=gc, length=10, target_start=target_start)

    def test_header_and_column_order(self):
        from app.routes.batch_routes import generate_batch_primer_tsv
        text = generate_batch_primer_tsv([("geneA", self._primer("geneA_S01", 0))])
        header = text.splitlines()[0]
        assert header.split("\t") == [
            "#", "基因名", "引物名", "类型", "序列(5'→3')", "长度", "Tm", "GC%", "备注"]
        row = text.splitlines()[1].split("\t")
        assert row[0] == "geneA_S01" and row[1] == "geneA"   # 首列=引物名（行标识）
        assert row[4] == "ACGTACGTAC"                        # 订购口径 = full_sequence

    def test_type_labels(self):
        from app.routes.batch_routes import generate_batch_primer_tsv, _oligo_type
        assert _oligo_type(self._primer("g_S01", 0)) == "Sense"
        assert _oligo_type(self._primer("g_AS02", 10)) == "Antisense"
        assert _oligo_type(self._primer("g_F")) == "Forward"
        assert _oligo_type(self._primer("g_R")) == "Reverse"
        text = generate_batch_primer_tsv([
            ("g1", self._primer("g1_F")), ("g1", self._primer("g1_R")),
            ("g2", self._primer("g2_S01", 0)),
        ])
        types = [line.split("\t")[3] for line in text.splitlines()[1:]]
        assert types == ["Forward", "Reverse", "Sense"]

    def test_grouped_by_gene_in_batch_order(self):
        from app.routes.batch_routes import generate_batch_primer_tsv
        text = generate_batch_primer_tsv([
            ("geneA", self._primer("geneA_S01", 0)),
            ("geneB", self._primer("geneB_S01", 0)),
            ("geneB", self._primer("geneB_AS02", 5)),
        ])
        genes = [line.split("\t")[1] for line in text.splitlines()[1:]]
        assert genes == ["geneA", "geneB", "geneB"]


class TestZipEntryName:
    """ZIP 内文件名 = 序列名（清洗），重名缀 design_id 末 4 位"""

    def test_sanitize_and_dedupe(self):
        from app.routes.batch_routes import _zip_entry_name
        used = set()
        assert _zip_entry_name("A01-1", "design_aaaabbbb", ".gb", used) == "A01-1.gb"
        # 重名 → 缀 design_id 末 4 位
        assert _zip_entry_name("A01-1", "design_ccccdddd", ".gb", used) == "A01-1_dddd.gb"
        # 非法字符清洗；清洗后为空 → 用 design_id 末 4 位
        assert _zip_entry_name("a/b:c", "design_11112222", ".gb", used) == "a_b_c.gb"
        assert _zip_entry_name("***", "design_33334444", ".gb", used) == "4444.gb"


class TestBatchDownloadEndpoints:
    """端点层：合并订购表与 ZIP 内容/命名（走真实 run_design 的 completed 任务）"""

    def _submit_and_run(self, client, names=True):
        """提交 2 条序列的批量任务。TestClient 同步执行 BackgroundTasks，
        响应返回时后台任务已跑完"""
        body = {
            "sequences": ["MGEKTKTEKTKTEKTKTEKT", "MGDYKDDDDKGGGG"],
            "sequence_type": "amino_acid",
            "cloning_method": "restriction",
            "enzyme_5": "NdeI", "enzyme_3": "XhoI",
            "optimize_codons": False,
        }
        if names:
            body["sequence_names"] = ["geneAA", "geneBB"]
        resp = client.post("/api/design/batch", json=body)
        assert resp.status_code == 200, resp.text
        batch_id = resp.json()["batch_id"]
        from app.routes import batch_routes
        job = batch_routes._load_batch(batch_id)
        assert job.status == "completed" and job.completed == 2, job.errors
        return batch_id, job

    def test_progress_includes_sequence_name_and_counts(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        batch_id, _ = self._submit_and_run(client)
        resp = client.get(f"/api/design/batch/{batch_id}")
        assert resp.status_code == 200
        rows = resp.json()["results"]
        assert len(rows) == 2
        assert {r["sequence_name"] for r in rows} == {"geneAA", "geneBB"}
        assert all(r["primer_count"] > 0 for r in rows)
        assert all(isinstance(r["warnings_count"], int) for r in rows)

    def test_primers_tsv_endpoint(self):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        batch_id, _ = self._submit_and_run(client)
        resp = client.get(f"/api/design/batch/{batch_id}/download/primers")
        assert resp.status_code == 200
        assert "tab-separated-values" in resp.headers["content-type"]
        lines = resp.text.strip().splitlines()
        assert lines[0].split("\t") == [
            "#", "基因名", "引物名", "类型", "序列(5'→3')", "长度", "Tm", "GC%", "备注"]
        genes = {line.split("\t")[1] for line in lines[1:]}
        assert genes == {"geneAA", "geneBB"}

    def test_zip_uses_sequence_names_and_contains_all_primers(self):
        import io
        import zipfile as zfmod
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        batch_id, _ = self._submit_and_run(client)
        resp = client.get(f"/api/design/batch/{batch_id}/download")
        assert resp.status_code == 200
        with zfmod.ZipFile(io.BytesIO(resp.content)) as zf:
            names = zf.namelist()
        assert "all_primers.tsv" in names
        assert "geneAA.gb" in names and "geneAA_primers.tsv" in names
        assert "geneBB.gb" in names and "geneBB_primers.tsv" in names
        assert not any(n.startswith("design_") for n in names)

    def test_zip_duplicate_names_get_design_suffix(self):
        """两条同名序列：文件名缀各自 design_id 末 4 位，不互相覆盖"""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        body = {
            "sequences": ["MGEKTKTEKTKTEKTKTEKT", "MGEKTKTEKTKTEKTKTEKT"],
            "sequence_type": "amino_acid",
            "cloning_method": "restriction",
            "enzyme_5": "NdeI", "enzyme_3": "XhoI",
            "optimize_codons": False,
            "sequence_names": ["same", "same"],
        }
        resp = client.post("/api/design/batch", json=body)
        assert resp.status_code == 200
        batch_id = resp.json()["batch_id"]
        resp = client.get(f"/api/design/batch/{batch_id}/download")
        import io
        import zipfile as zfmod
        with zfmod.ZipFile(io.BytesIO(resp.content)) as zf:
            gb_names = sorted(n for n in zf.namelist() if n.endswith(".gb"))
        # 重名去重：两条文件名不同，且都是同一 base（其中一条带 _dddd 后缀）
        assert len(gb_names) == 2
        b1, b2 = (n[:-3] for n in gb_names)
        assert b1 != b2 and (b1.split("_")[0] == b2.split("_")[0])


# ==================== v2.29.0：行级参数覆盖（row_params） ====================

class TestRowParams:
    """行级参数覆盖：契约校验、后台任务合并、行级报错不炸整批"""

    def _fake_run(self, seen):
        from app.routes.models import DesignResult, DesignStatus

        def fake(design_id, single):
            seen.append(single)
            return DesignResult(
                design_id=design_id, status=DesignStatus.COMPLETED,
                input_sequence=single.sequence, optimized_sequence="ATG",
                construct_sequence="ATG", vector_id=single.vector_id,
                cloning_method=single.cloning_method.value, created_at=datetime.now(),
            )
        return fake

    def test_row_params_count_mismatch_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="row_params 数量必须与 sequences 一致"):
            BatchDesignRequest(
                sequences=["ATGAAA", "ATGCCC"], enzyme_5="NdeI", enzyme_3="XhoI",
                cloning_method="restriction",
                row_params=[{"cloning_method": "gibson"}])   # 1 条 vs 2 序列

    def test_row_params_accept_enums_and_none(self):
        request = BatchDesignRequest(
            sequences=["ATGAAA", "ATGCCC"], enzyme_5="NdeI", enzyme_3="XhoI",
            cloning_method="restriction",
            row_params=[{"cloning_method": "gibson", "gibson_linearization": "custom_arms"},
                        None])
        assert request.row_params[0].cloning_method.value == "gibson"
        assert request.row_params[1] is None

    def test_row_params_merged_into_each_design_request(self, monkeypatch):
        """行级非 None 覆盖批级；None 行完全继承批级"""
        from app.routes import batch_routes
        from app.routes.models import DesignResult, DesignStatus

        seen = []
        monkeypatch.setattr(batch_routes, "run_design", self._fake_run(seen))
        monkeypatch.setattr(batch_routes, "_persist", lambda result: None)

        request = BatchDesignRequest(
            sequences=["ATGAAA", "ATGCCC"], enzyme_5="NdeI", enzyme_3="XhoI",
            cloning_method="restriction",
            row_params=[
                {"cloning_method": "gibson", "gibson_linearization": "single_site",
                 "enzyme_5": "BamHI", "enzyme_3": "EcoRI"},
                None,
            ])
        job = BatchDesignStatus(batch_id="batch_rowparam01", total=2, completed=0,
                                failed=0, status="pending")
        batch_routes._persist_batch(job)
        batch_routes.run_batch_design_task(job.batch_id, request, ["a", "b"])

        # 第 1 行：行级覆盖生效
        assert seen[0].cloning_method.value == "gibson"
        assert seen[0].gibson_linearization == "single_site"
        assert seen[0].enzyme_5 == "BamHI" and seen[0].enzyme_3 == "EcoRI"
        # 第 2 行：完全继承批级
        assert seen[1].cloning_method.value == "restriction"
        assert seen[1].enzyme_5 == "NdeI" and seen[1].enzyme_3 == "XhoI"

    def test_row_vector_resolved_from_library_name(self, monkeypatch):
        """行级载体列（库内载体名）→ 解析成 vector_id"""
        from unittest.mock import patch
        from app.routes import batch_routes
        from app.routes.models import DesignResult, DesignStatus

        class _FakeVec:
            id = "pET-21a"
            name = "pET-21a(+)"

        class _FakeLib:
            def get_vector(self, vid):
                return None          # 按名填的载体不是 ID：ID 查落空
            def list_vectors(self):
                return [_FakeVec()]  # name 精确匹配 → pET-21a

        seen = []
        monkeypatch.setattr(batch_routes, "run_design", self._fake_run(seen))
        monkeypatch.setattr(batch_routes, "_persist", lambda result: None)

        request = BatchDesignRequest(
            sequences=["ATGAAA"], enzyme_5="NdeI", enzyme_3="XhoI",
            cloning_method="restriction",
            row_params=[{"vector": "pET-21a(+)"}])
        job = BatchDesignStatus(batch_id="batch_rowvec001", total=1, completed=0,
                                failed=0, status="pending")
        batch_routes._persist_batch(job)
        with patch("app.vector_library_cache.get_vector_library",
                   return_value=_FakeLib()):
            batch_routes.run_batch_design_task(job.batch_id, request, ["a"])
        assert seen[0].vector_id == "pET-21a"      # 名字 → 解析成库内 id

    def test_row_vector_not_in_library_fails_row_only(self, monkeypatch):
        """行级载体不在库：该行 errors，其余行正常完成"""
        from unittest.mock import patch
        from app.routes import batch_routes
        from app.routes.models import DesignResult, DesignStatus

        class _FakeLib:
            def get_vector(self, vid):
                return None
            def list_vectors(self):
                return []

        seen = []
        monkeypatch.setattr(batch_routes, "run_design", self._fake_run(seen))
        monkeypatch.setattr(batch_routes, "_persist", lambda result: None)

        request = BatchDesignRequest(
            sequences=["ATGAAA", "ATGCCC"], enzyme_5="NdeI", enzyme_3="XhoI",
            cloning_method="restriction",
            row_params=[{"vector": "不存在的载体"}, None])
        job = BatchDesignStatus(batch_id="batch_rowvec002", total=2, completed=0,
                                failed=0, status="pending")
        batch_routes._persist_batch(job)
        with patch("app.vector_library_cache.get_vector_library",
                   return_value=_FakeLib()):
            batch_routes.run_batch_design_task(job.batch_id, request, ["a", "b"])
        done = batch_routes._load_batch(job.batch_id)
        assert done.completed == 1 and done.failed == 1
        assert "不在载体库" in done.errors[0]["error"]

    def test_row_invalid_enum_rejected_at_submit(self):
        """行级非法枚举在批量请求构造时即 422（提交时拦截）"""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            BatchDesignRequest(
                sequences=["ATGAAA"], enzyme_5="NdeI", enzyme_3="XhoI",
                cloning_method="restriction",
                row_params=[{"cloning_method": "not_a_method"}])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
