"""测序重型比对并发闸回归锁"""

import asyncio
import threading

import pytest
from fastapi import HTTPException

from app.routes import sequencing_routes as sr


def test_heavy_work_is_bounded_and_times_out(monkeypatch):
    monkeypatch.setattr(sr, "MAX_CONCURRENT_ALIGNMENTS", 1)
    monkeypatch.setattr(sr, "HEAVY_QUEUE_TIMEOUT", 0.2)
    gate = threading.Event()
    running = []

    def slow():
        running.append(1)
        gate.wait(5)
        return "done"

    async def main():
        first = asyncio.create_task(sr._run_heavy(slow))
        for _ in range(500):              # 等第一个任务真正进入线程池（负载下也稳定）
            if running:
                break
            await asyncio.sleep(0.01)
        assert running == [1]
        with pytest.raises(HTTPException) as e:
            await sr._run_heavy(slow)
        assert e.value.status_code == 503
        assert len(running) == 1          # 第二个任务从未进入线程池
        gate.set()
        assert await first == "done"
        # 闸已释放：后续任务可正常执行
        assert await sr._run_heavy(lambda: 42) == 42

    asyncio.run(main())


def test_analyze_endpoint_routes_through_gate(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from tests.abif_utils import make_ab1

    calls = []
    real = sr._run_heavy

    async def spy(fn, *a, **kw):
        calls.append(fn.__name__)
        return await real(fn, *a, **kw)

    monkeypatch.setattr(sr, "_run_heavy", spy)
    ref = ">ref\n" + "AAG" * 40
    r = TestClient(app).post("/api/sequencing/analyze",
                             files={"reference": ("ref.fasta", ref, "text/plain"),
                                    "reads": ("r1.ab1", make_ab1("AAG" * 40, [40] * 120),
                                              "application/octet-stream")})
    assert r.status_code == 200, r.text
    assert calls == ["_run_full_analysis"]
