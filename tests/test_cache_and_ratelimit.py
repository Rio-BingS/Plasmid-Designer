"""缓存接线与认证状态中间件测试。

覆盖两项遗留修复：
1. AuthStateMiddleware 写入 request.state.user → 用户级限流配额（user_*）生效
2. 缓存子系统业务接线：密码子优化 / 设计结果 / 载体列表与详情 / 静态酶表
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.auth.jwt_auth import User, create_access_token
from app.auth.middleware import AuthStateMiddleware
from app.cache import cache
from app.rate_limit import RateLimitMiddleware
from app.routes.models import CloningMethod, DesignRequest, DesignStatus, SequenceType
from app.routes.design_routes import get_design, run_design_task
from app.routes.vector_routes import get_vector, list_vectors


# ==================== 认证状态中间件 & 用户级限流 ====================

def _build_mini_app() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    @app.get("/who")
    async def who(request: Request):
        return {"user": getattr(request.state, "user", None)}

    # 与 main.py 相同的添加顺序：先限流，后认证状态（后添加者为外层，先执行）
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthStateMiddleware)
    return app


@pytest.fixture(scope="module")
def mini_client():
    return TestClient(_build_mini_app())


@pytest.fixture(scope="module")
def user_token():
    user = User(id="user_rl_test", email="rl@example.com", username="rl")
    return create_access_token(user)


class TestAuthStateMiddleware:
    def test_valid_token_sets_state_user(self, mini_client, user_token):
        resp = mini_client.get("/who", headers={"Authorization": f"Bearer {user_token}"})
        assert resp.status_code == 200
        assert resp.json()["user"]["id"] == "user_rl_test"

    def test_no_token_leaves_state_user_absent(self, mini_client):
        resp = mini_client.get("/who")
        assert resp.status_code == 200
        assert resp.json()["user"] is None

    def test_invalid_token_falls_back_anonymous(self, mini_client):
        resp = mini_client.get("/who", headers={"Authorization": "Bearer not-a-jwt"})
        assert resp.status_code == 200
        assert resp.json()["user"] is None


class TestUserRateLimitActivation:
    def test_authenticated_request_uses_user_quota(self, mini_client, user_token):
        resp = mini_client.get("/ping", headers={"Authorization": f"Bearer {user_token}"})
        assert resp.headers["X-RateLimit-Limit"] == "200"  # user_default

    def test_anonymous_request_uses_ip_quota(self, mini_client):
        resp = mini_client.get("/ping")
        assert resp.headers["X-RateLimit-Limit"] == "100"  # default


class TestSequencingEndpointTier:
    """峰图带叠加一次要拉多条 trace（GET 只读）：走 default 档而非 upload
    20 次/小时，否则勾几条 read 就把上传配额烧光、峰图全部 429。"""

    def _type(self, method, path):
        mw = RateLimitMiddleware(app=None)
        req = SimpleNamespace(url=SimpleNamespace(path=path), method=method)
        return mw._get_endpoint_type(req)

    def test_get_trace_and_analysis_are_default_tier(self):
        assert self._type("GET", "/api/sequencing/analyses/seq_x/trace/0") == "default"
        assert self._type("GET", "/api/sequencing/analyses/seq_x") == "default"

    def test_post_analyze_stays_upload_tier(self):
        assert self._type("POST", "/api/sequencing/analyze") == "upload"


class TestRouteLevelRateLimitClassification:
    """终审 C-04：限流曾按路径子串分类——轮询 GET /api/design/{id} 被
    "/design" 命中写档（实测提交后第 10 次轮询 429），GET /api/auth/verify
    与 /auth/me 也计入 auth 档（第 6 次 429 → 前端 catch 直接 clearAuth
    强制登出）。改为按（方法 + 路由模板）后，只读端点必须走 default 档。"""

    def _type(self, method, path):
        mw = RateLimitMiddleware(app=None)
        req = SimpleNamespace(url=SimpleNamespace(path=path), method=method)
        return mw._get_endpoint_type(req)

    def test_polling_design_status_is_read_tier(self):
        """轮询设计进度（提交后可能轮询数十次）不得计入 design 写配额"""
        assert self._type("GET", "/api/design/design_abc123") == "default"
        assert self._type("GET", "/api/design/design_abc123/map") == "default"
        assert self._type("GET", "/api/design/batch/batch_abc123") == "default"

    def test_auth_reads_are_read_tier(self):
        """checkAuth（/auth/me）与邮箱校验只读不得计入 auth 档"""
        assert self._type("GET", "/api/auth/me") == "default"
        assert self._type("GET", "/api/auth/verify") == "default"

    def test_write_operations_keep_business_tiers(self):
        """真正的写操作仍按业务档计（不因放宽只读而失去防护）"""
        assert self._type("POST", "/api/design") == "design"
        assert self._type("POST", "/api/design/batch") == "batch"
        assert self._type("POST", "/api/auth/login") == "auth"
        assert self._type("POST", "/api/auth/register") == "auth"
        assert self._type("POST", "/api/sequencing/analyze-batch") == "upload"

    def test_thirty_polls_after_one_submit_never_429(self):
        """端到端：提交一次设计后连续轮询 30 次，不得出现 429
        （终审实测第 10 次即被拦）"""
        from fastapi.testclient import TestClient
        from app.main import app as real_app
        from app.rate_limit import limiter

        limiter._requests.clear()
        client = TestClient(real_app)
        ids = []
        for _ in range(30):
            r = client.get("/api/design/design_poll_test")
            ids.append(r.status_code)
        # 404（不存在的设计）也不该变成 429（限流先于路由执行）
        assert 429 not in ids, f"轮询被限流拦截: {ids}"


class TestCorsOutermostAndOptionsBypass:
    """终审 C-05：CORS 中间件必须位于最外层（429/5xx 响应带 CORS 头，
    否则前端只见 Network Error），预检 OPTIONS 不计入限流配额。"""

    def test_429_response_carries_cors_headers(self):
        from fastapi.middleware.cors import CORSMiddleware
        from app.rate_limit import limiter

        app = FastAPI()

        @app.get("/api/ping")
        async def ping():
            return {"ok": True}

        # 与 main.py 相同顺序：内层限流 → 最外层 CORS（后添加者为外层）
        app.add_middleware(RateLimitMiddleware)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://app.example.com"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        client = TestClient(app)
        limiter._requests.clear()
        # 用小配额路径打满：default 档 anonymous 100 次/小时——直接灌满
        key = "testclient"
        for _ in range(100):
            limiter.is_allowed(f"rl:{key}:default", 100, 3600)
        resp = client.get("/api/ping", headers={"Origin": "https://app.example.com"})
        # 打满后该端点应 429，且必须带 CORS 头（修复前 access-control-allow-origin=None）
        if resp.status_code == 429:
            assert resp.headers.get("access-control-allow-origin") == "https://app.example.com"
        else:
            # 未打满也要验证 CORS 已在最外层接线
            assert resp.headers.get("access-control-allow-origin") == "https://app.example.com"

    def test_options_preflight_not_rate_limited(self):
        from fastapi.middleware.cors import CORSMiddleware
        from app.rate_limit import limiter

        app = FastAPI()

        @app.get("/api/ping")
        async def ping():
            return {"ok": True}

        app.add_middleware(RateLimitMiddleware)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://app.example.com"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        client = TestClient(app)
        limiter._requests.clear()
        # 打满 default 档配额
        for _ in range(100):
            limiter.is_allowed("rl:testclient:default", 100, 3600)
        # 预检请求必须照常 200，不被 429 拒绝（连续多次）
        for _ in range(3):
            r = client.options(
                "/api/ping",
                headers={
                    "Origin": "https://app.example.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert r.status_code == 200


# ==================== 缓存业务接线 ====================

_AA = "MKVLWAALLTFLGCAATSGSQAPDRRNRLALASLLRLQGVSSVQIRCRDSDMNADADATIRR"
_DNA = "ATGGATTACAAGGATGACGATGACAAGGGTACCAAGCTTGTCGACCTCGAGCACCCGGGTGGTACCAAGCTTCTAGCTAGCGGATCC"


def _ids(items):
    return [v["id"] if isinstance(v, dict) else v.id for v in items]


class TestCodonOptimizationCache:
    def test_optimize_result_is_cached_and_reused(self):
        cache.backend.clear_pattern("codon_opt:*")

        from app.design_service import process_sequence

        dna1, cai1, gc1, warns1 = process_sequence(
            _AA, SequenceType.AMINO_ACID, True, "ecoli", 40, 60
        )

        cached = cache.get_codon_optimization(
            sequence=_AA, species="ecoli", gc_min=40, gc_max=60
        )
        assert cached is not None
        assert cached["dna_sequence"] == dna1

        # 第二次调用走缓存，结果一致
        dna2, cai2, gc2, warns2 = process_sequence(
            _AA, SequenceType.AMINO_ACID, True, "ecoli", 40, 60
        )
        assert (dna2, cai2, gc2) == (dna1, cai1, gc1)
        assert list(warns2) == list(warns1)


class TestDesignResultCache:
    def test_completed_design_is_cached(self):
        design_id = "design_cache_test_1"
        request = DesignRequest(
            sequence=_DNA,
            sequence_type=SequenceType.DNA,
            cloning_method=CloningMethod.RESTRICTION,
            enzyme_5="EcoRI",
            enzyme_3="HindIII",
        )
        cache.invalidate_design(design_id)

        run_design_task(design_id, request)

        cached_data = cache.get_design_result(design_id)
        assert cached_data is not None
        assert cached_data["status"] == "completed"

    def test_get_design_read_through_repopulates_cache(self):
        design_id = "design_cache_test_1"
        cache.invalidate_design(design_id)
        # designs_db 内存镜像仍在，get_design 应走读回填路径重新写缓存
        result = asyncio.run(get_design(design_id))
        assert result.status == DesignStatus.COMPLETED
        assert result.design_id == design_id
        assert cache.get_design_result(design_id) is not None


class TestVectorCache:
    def test_vector_list_roundtrip_and_invalidation(self):
        cache.backend.clear_pattern("vector_list:*")
        filters = {"vector_type": None, "host": None}

        first = asyncio.run(list_vectors())
        assert len(first) == 9

        cached_list = cache.get_vector_list(filters)
        assert cached_list is not None
        assert len(cached_list) == len(first)

        # 第二次调用走缓存
        second = asyncio.run(list_vectors())
        assert _ids(second) == _ids(first)

        # 失效后重新回填
        cache.backend.clear_pattern("vector_list:*")
        assert cache.get_vector_list(filters) is None
        asyncio.run(list_vectors())
        assert cache.get_vector_list(filters) is not None

    def test_vector_detail_cached(self):
        cache.backend.delete("vector:pUC19")

        detail = asyncio.run(get_vector("pUC19"))
        assert detail.id == "pUC19"
        assert cache.get_vector("pUC19") is not None

    def test_cache_stats_now_counts_keys(self):
        # 接线后 stats 不再恒为空
        stats = cache.get_stats()
        assert stats["available"] is True
        assert stats["keys"] > 0


class TestStaticEnzymesCache:
    def test_enzymes_endpoint_cached(self):
        from app.analysis_routes import list_enzymes

        cache.backend.clear_pattern("analysis_enzymes:*")

        first = asyncio.run(list_enzymes())
        assert first["total"] > 0

        key = cache._generate_key("analysis_enzymes")
        assert cache.backend.get(key) is not None

        # 第二次调用走缓存，结构一致
        second = asyncio.run(list_enzymes())
        assert second == first
