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


# 载体 YAML 曾两次出现坐标错误：SnapGene 来源的特征起点写成 0-based（整体左移 1 位），
# pUC19 的特征沿用了 M77789 的另一套编号（与 L09137 序列错位 ~160 bp、方向相反）。
# 两者都会让 CDS 不再是完整 ORF、图谱翻译读码框错位。守门：所有载体的蛋白编码特征
# （CDS / resistance，排除融合标签与酶切位点短肽）必须是读码框内的完整 ORF
_ALL_VECTORS = tuple(sorted(p.stem for p in VECTORS_DIR.glob("*.yaml")))
_STARTS = {"ATG", "GTG", "TTG"}


def _vector_id(lib, stem):
    v = lib.get_vector(stem)
    if v is None:  # 文件名与 id 大小写不同（pCDNA3.1.yaml → pcDNA3.1）
        v = next(x for x in lib.list_vectors() if x.id.lower() == stem.lower())
    return v


def _feature_dna(seq, e):
    from core.seq_utils import revcomp
    sub = seq[e.start - 1:e.end]
    return revcomp(sub) if e.strand == "-" else sub


@pytest.mark.parametrize("stem", _ALL_VECTORS)
def test_vector_cds_are_complete_orfs(loaded, stem):
    from core.seq_utils import translate
    lib, _ = loaded
    v = _vector_id(lib, stem)
    checked = 0
    for e in v.elements:
        if e.element_type.value not in ("CDS", "resistance") or "site" in e.name:
            continue
        dna = _feature_dna(v.sequence.upper(), e)
        prot = translate(dna, stop_at_stop=False)
        assert len(dna) % 3 == 0, f"{v.id}: {e.name} 长度 {len(dna)} 不是 3 的倍数"
        assert dna[:3] in _STARTS and prot.endswith("*") and "*" not in prot[:-1], \
            f"{v.id}: {e.name} 不是完整 ORF（坐标错位？）"
        checked += 1
    assert checked >= 1, f"{v.id}: 没有可校验的蛋白编码特征"


@pytest.mark.parametrize("stem", _ALL_VECTORS)
def test_vector_mcs_contains_listed_sites(loaded, stem):
    """mcs 列出的每个位点：position 处确为该识别序列，且整段落在 mcs 区间内
    （pET 的 NcoI/NdeI 在 SnapGene MCS 特征之外但在 mcs 区间内，故不要求落在 MCS 特征里）"""
    lib, _ = loaded
    v = _vector_id(lib, stem)
    if not (v.mcs and v.mcs.sites):
        pytest.skip("无 mcs 定义")
    seq = v.sequence.upper()
    for s in v.mcs.sites:
        rec = s.recognition_seq.upper()
        pos = s.cut_position_5  # YAML position：识别序列首碱基（1-based）
        assert seq[pos - 1:pos - 1 + len(rec)] == rec, \
            f"{v.id}: {s.enzyme_name} 不在 {pos}（序列中位置 {seq.find(rec) + 1}）"
        end = pos + len(rec) - 1
        assert v.mcs.start <= pos and end <= v.mcs.end, \
            f"{v.id}: {s.enzyme_name} {pos}-{end} 不在 mcs {v.mcs.start}-{v.mcs.end}"


def test_puc19_reference_annotation(loaded):
    """pUC19（L09137 编号）：lacZα complement(146..469)、AmpR complement(1626..2486)、
    MCS 396..452（EcoRI..HindIII），lac promoter/operator 在 lacZα 上游（负链）"""
    lib, _ = loaded
    v = lib.get_vector("pUC19")
    want = {"lacZ-alpha": (146, 469, "-"), "AmpR (bla)": (1626, 2486, "-"),
            "MCS": (396, 452, "-"), "lac operator": (489, 505, "-"),
            "lac promoter": (513, 543, "-"), "ori": (867, 1455, "-")}
    for name, (a, b, st) in want.items():
        e = v.get_element(name)
        assert e is not None, name
        assert (e.start, e.end, e.strand) == (a, b, st), name
    assert _feature_dna(v.sequence, v.get_element("lac promoter")) == \
        "TTTACACTTTATGCTTCCGGCTCGTATGTTG"


@pytest.mark.parametrize("vector_id", ("pcDNA3.1", "pET-28a", "pYES2"))
def test_snapgene_vector_t7_promoter_exact(loaded, vector_id):
    lib, _ = loaded
    v = lib.get_vector(vector_id)
    e = v.get_element("T7 promoter")
    assert _feature_dna(v.sequence, e) == "TAATACGACTCACTATAGG"
    assert e.end - e.start + 1 == 19
