"""data/vectors 演示数据完整性守门测试。

保证载体库演示数据始终准确可用：
- 每个 YAML 必须载入成功（未知类型降级不报错）
- 序列非空、仅含 ACGTN、长度合理
- 特征坐标在序列范围内、类型在词表内、数量充足
- 有 data_provenance 血统记录（可追溯到来源）
- mcs 位点识别序列能命中序列（若有 mcs 定义）
"""

from pathlib import Path

import pytest

from core.vector_library import VectorLibrary, ElementType

VECTORS_DIR = Path(__file__).resolve().parent.parent / "data" / "vectors"
VALID_TYPES = {t.value for t in ElementType}


@pytest.fixture(scope="module")
def loaded():
    lib = VectorLibrary()
    count = lib.load_from_directory(str(VECTORS_DIR))
    return lib, count


def test_all_yaml_files_load(loaded):
    lib, count = loaded
    files = list(VECTORS_DIR.glob("*.yaml"))
    assert files, "data/vectors 下没有 YAML 文件"
    assert count == len(files), f"{len(files) - count} 个 YAML 载入失败"


def test_every_vector_has_real_sequence(loaded):
    lib, _ = loaded
    for v in lib.list_vectors():
        assert len(v.sequence) >= 1000, f"{v.id}: 序列缺失或过短（{len(v.sequence)} bp）"
        bad = set(v.sequence.upper()) - set("ACGTN")
        assert not bad, f"{v.id}: 非法碱基 {bad}"


def test_features_within_bounds_and_typed(loaded):
    lib, _ = loaded
    for v in lib.list_vectors():
        assert len(v.elements) >= 3, f"{v.id}: 特征过少（{len(v.elements)}）"
        for e in v.elements:
            assert e.element_type.value in VALID_TYPES
            assert 1 <= e.start <= e.end <= len(v.sequence), \
                f"{v.id}: {e.name} 坐标越界 {e.start}-{e.end}（序列 {len(v.sequence)} bp）"


def test_provenance_recorded(loaded):
    """每个有序列的载体必须记录数据来源血统"""
    import yaml
    for path in VECTORS_DIR.glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not data.get("sequence"):
            continue
        prov = data.get("data_provenance")
        assert prov, f"{path.name}: 缺少 data_provenance"
        assert prov.get("source"), f"{path.name}: data_provenance.source 为空"
        assert prov.get("url") or prov.get("accession"), \
            f"{path.name}: data_provenance 缺少 url/accession"


def test_mcs_sites_present_in_sequence(loaded):
    """mcs 定义的酶识别序列必须真实存在于载体序列中（保证克隆设计可用）"""
    lib, _ = loaded
    for v in lib.list_vectors():
        if not (v.mcs and v.mcs.sites):
            continue
        seq = v.sequence.upper()
        hits = [s.enzyme_name for s in v.mcs.sites
                if s.recognition_seq and s.recognition_seq.upper() in seq]
        assert hits, f"{v.id}: mcs 酶切位点无一命中载体序列"


def test_flagship_vector_cross_check(loaded):
    """旗舰载体长度与权威记录交叉核对（数据被误改时第一时间报警）"""
    lib, _ = loaded
    expected = {
        "pET-28a": 5369,   # SnapGene 官方 / Addgene vector DB 一致
        "pUC19": 2686,     # NCBI L09137
        "pGEX-6P-1": 4984, # NCBI U78872
    }
    for vid, length in expected.items():
        v = lib.get_vector(vid)
        assert v is not None, f"缺少旗舰载体 {vid}"
        assert len(v.sequence) == length, \
            f"{vid}: 序列长度 {len(v.sequence)} != 权威记录 {length}（数据疑似被改动）"
