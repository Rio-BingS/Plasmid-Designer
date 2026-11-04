"""存储层往返契约测试（终审 D-01 / G-05）。

D-01 的三个缺陷：
1. DBDesignStore._db_to_dict 丢 user_id → 落库的完成态设计属主变 None，
   被属主校验当成「匿名公开」记录；
2. construct_features 回读为 None → DesignResult.model_validate 抛
   ValidationError，被 _load 的 except: pass 吞成 404（失败态设计在
   database 模式下永久读不回来）；
3. clone_protocol 字段从未落库（DesignDB 无此列）。

本文件用「Memory / DB 两种实现共用同一契约」的方式锁住往返：
保存 → 读取 → DesignResult.model_validate 必须成功，且属主/克隆方案/
构建体特征三类字段必须原样回来。
"""

import pytest

from app.routes.models import DesignResult, DesignStatus
from app.storage.memory_store import MemoryDesignStore
from app.storage.db_store import DBDesignStore
from app.storage import STORAGE_MODE


def _design_payload(design_id: str = "design_contract_1") -> dict:
    return {
        "design_id": design_id,
        "status": DesignStatus.COMPLETED.value,
        "input_sequence": "MKV",
        "sequence_type": "amino_acid",
        "sequence_name": "contract",
        "vector_id": "pET-28a",
        "vector_name": "pET-28a",
        "cloning_method": "restriction",
        "optimized_sequence": "ATGAAGGTG",
        "cai": 0.82,
        "gc_content": 55.5,
        "final_length": 210,
        "validation_passed": True,
        "construct_sequence": "ATGAAGGTG" * 3,
        "construct_features": [
            {"name": "insert", "type": "CDS", "start": 1, "end": 9, "strand": "+"},
        ],
        "insert_start": 1,
        "insert_end": 9,
        "clone_protocol": "1. 双酶切 37℃ 1h\n2. 连接 16℃ 过夜",
        "user_id": "user_contract_1",
        "primers": [],
        "warnings": [],
        "errors": [],
        "created_at": "2026-10-09T00:00:00",
        "completed_at": "2026-10-09T00:00:01",
    }


@pytest.mark.parametrize("store", [
    pytest.param(MemoryDesignStore(), id="memory"),
    pytest.param(DBDesignStore(), id="database"),
])
def test_design_roundtrip_preserves_owner_and_protocol(store):
    """终审 D-01：属主与克隆方案必须往返（DB 模式曾两者都丢）"""
    data = _design_payload()
    store.save(data["design_id"], data)
    got = store.get(data["design_id"])
    assert got is not None, f"{type(store).__name__} 读取为空（往返失败）"

    # 关键：回读数据必须能被 DesignResult 接受（失败态曾在这里抛错被吞）
    result = DesignResult.model_validate(got)
    assert result.user_id == "user_contract_1", (
        f"{type(store).__name__} 丢 user_id → 属主变匿名公开记录")
    assert result.clone_protocol == data["clone_protocol"], (
        f"{type(store).__name__} 丢 clone_protocol")
    assert isinstance(result.construct_features, list), (
        f"{type(store).__name__} 的 construct_features 不是列表（曾回读为 None）")
    assert result.construct_features[0]["name"] == "insert"


@pytest.mark.parametrize("store", [
    pytest.param(MemoryDesignStore(), id="memory"),
    pytest.param(DBDesignStore(), id="database"),
])
def test_design_roundtrip_with_empty_features_still_loads(store):
    """无构建体特征（失败态/早期态）也必须能回载——曾因 None 被吞成 404。"""
    data = _design_payload("design_contract_2")
    data["construct_features"] = []
    data["status"] = DesignStatus.FAILED.value
    data["clone_protocol"] = None
    store.save(data["design_id"], data)
    got = store.get(data["design_id"])
    assert got is not None
    result = DesignResult.model_validate(got)
    assert result.construct_features == []
    assert result.status == DesignStatus.FAILED


def test_db_store_is_active_mode_by_default():
    """记录当前默认存储模式：契约测试必须覆盖生产默认路径（database）"""
    assert STORAGE_MODE == "database", (
        f"默认存储模式变为 {STORAGE_MODE}，契约测试的覆盖重点需同步调整")
