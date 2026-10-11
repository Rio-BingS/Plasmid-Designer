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


# ---------------------------------------------------------------- 审查 02 M5
# 重型内存/CPU 阶段（上传读入、Excel 解析、记录落库、峰图回灌解压、整理包
# 打包）必须在并发闸内执行，同步部分不得在事件循环线程上跑


def _in_gate() -> bool:
    """并发上限打桩为 1 时，任一闸被占即说明调用发生在槽内"""
    return any(s.locked() for s in list(sr._HEAVY_SEMAPHORES.values()))


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


@pytest.fixture
def probe(monkeypatch):
    """把目标函数包一层，记录 (名字, 是否在闸内, 是否在事件循环线程)"""
    monkeypatch.setattr(sr, "MAX_CONCURRENT_ALIGNMENTS", 1)
    sr._HEAVY_SEMAPHORES.clear()
    seen = []

    def wrap(obj, name):
        real = getattr(obj, name)
        if asyncio.iscoroutinefunction(real):
            async def spy(*a, **kw):
                seen.append((name, _in_gate(), True))
                return await real(*a, **kw)
        else:
            def spy(*a, **kw):
                seen.append((name, _in_gate(), _on_event_loop()))
                return real(*a, **kw)
        monkeypatch.setattr(obj, name, spy)

    yield wrap, seen
    sr._HEAVY_SEMAPHORES.clear()


def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_single_analysis_register_and_persist_inside_gate(probe):
    from app import sequencing_store
    from tests.abif_utils import make_ab1

    wrap, seen = probe
    wrap(sr, "_read_ab1_files")
    wrap(sr, "_run_full_analysis")
    wrap(sequencing_store, "persist_record")
    ref = ">ref\n" + "AAG" * 40
    r = _client().post("/api/sequencing/analyze",
                       files={"reference": ("ref.fasta", ref, "text/plain"),
                              "reads": ("r1.ab1", make_ab1("AAG" * 40, [40] * 120),
                                        "application/octet-stream")})
    assert r.status_code == 200, r.text
    assert r.json()["analysis_id"].startswith("seq_")
    assert r.json().get("access_token")          # 匿名响应形状不变
    names = [n for n, _, _ in seen]
    assert names == ["_read_ab1_files", "_run_full_analysis", "persist_record"]
    assert all(in_gate for _, in_gate, _ in seen), seen
    # 比对与落库（json+zlib+DB 提交）不在事件循环线程
    assert not any(on_loop for n, _, on_loop in seen if n != "_read_ab1_files"), seen


def _xlsx_bytes(rows):
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["质粒名称", "测序引物", "测序结果"])
    for name, primers in rows:
        ws.append([name, ";".join(primers), None])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_batch_read_excel_and_pack_inside_gate(probe):
    from tests.abif_utils import make_ab1

    wrap, seen = probe
    wrap(sr, "_read_limited")
    wrap(sr, "load_excel")
    wrap(sr, "build_batch_zip")
    ref = "ATGAAACGT" * 12 + "TAA"
    files = [
        ("files", ("MX.fasta", f">MX\n{ref}\n".encode(), "application/octet-stream")),
        ("files", ("MX-F.ab1", make_ab1(ref, [40] * len(ref)), "application/octet-stream")),
        ("excel", ("测序.xlsx", _xlsx_bytes([("MX", ["MX-F"])]),
                   "application/octet-stream")),
    ]
    r = _client().post("/api/sequencing/analyze-batch", files=files, data={"min_q": "20"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report_ready"] is True and body["access_token"]
    assert body["items"][0]["status"] == "analyzed"
    names = {n for n, _, _ in seen}
    assert names == {"_read_limited", "load_excel", "build_batch_zip"}
    assert all(in_gate for _, in_gate, _ in seen), seen
    assert not any(on_loop for n, _, on_loop in seen if n != "_read_limited"), seen


def test_batch_bad_excel_still_400_inside_gate(probe):
    wrap, seen = probe
    wrap(sr, "load_excel")
    files = [
        ("files", ("MX.fasta", b">MX\n" + b"ACGT" * 30 + b"\n", "application/octet-stream")),
        ("excel", ("测序.xlsx", b"not a zip", "application/octet-stream")),
    ]
    r = _client().post("/api/sequencing/analyze-batch", files=files)
    assert r.status_code == 400
    assert "信息表" in r.json()["detail"]
    assert seen and all(g and not loop for _, g, loop in seen), seen
    assert not any(s.locked() for s in sr._HEAVY_SEMAPHORES.values())  # 异常后闸已释放


def test_rehydrate_decompress_inside_gate(probe):
    from app import sequencing_store
    if not sequencing_store.db_enabled():
        pytest.skip("回灌只在 database 模式下发生")
    wrap, seen = probe
    res = {"engine": "internal", "conclusion": "ok", "reads": [], "variants": [],
           "consensus": {"sequence": "ACGT" * 20, "coverage_percent": 100.0},
           "coverage_ranges": [], "errors": [],
           "traces": [{"A": list(range(200)), "C": [], "G": [], "T": []}]}
    aid, tok, _ = sr._register_analysis("s", "ACGT" * 20, [], res)
    with sr._STORE_LOCK:
        sr._ANALYSES.pop(aid, None)               # 模拟内存过期 → 走 DB 回灌
    wrap(sequencing_store, "load_record_meta")
    wrap(sequencing_store, "load_trace")
    r = _client().get(f"/api/sequencing/analyses/{aid}/trace/0",
                      headers={"X-Access-Token": tok})
    assert r.status_code == 200, r.text
    assert r.json()["A"][:3] == [0, 1, 2]
    assert [n for n, _, _ in seen] == ["load_record_meta", "load_trace"]
    assert all(g and not loop for _, g, loop in seen), seen
    # 已回灌：再次读取走缓存，不再占槽解压
    seen.clear()
    r = _client().get(f"/api/sequencing/analyses/{aid}", headers={"X-Access-Token": tok})
    assert r.status_code == 200 and seen == []
