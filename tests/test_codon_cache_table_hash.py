"""密码子优化缓存键含密码子表内容摘要（审查 10 M6）

此前缓存键只有 (CODON_OPT_ALGO, 序列, 物种, GC 区间, 排除酶)：改了
data/codon_tables/*.yaml 却忘了递增 CODON_OPT_ALGO 时，Redis（跨重启保留）
在 24 h TTL 内继续返回旧表算出的序列。现在键里带上该物种所用表文件的
内容摘要（进程内按 mtime/大小缓存），改表即自动失效。
"""

import os
import shutil
from pathlib import Path

import pytest

from app.cache import cache
from app.config import settings
from app.routes.models import SequenceType
from core import codon_optimizer as co

TABLES = Path(__file__).resolve().parents[1] / "data" / "codon_tables"
_AA = "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAPILSRVGDGTQDNLSGAEKAVQVKVKALPDAQFEVVHSLAKW"


@pytest.fixture
def tables(tmp_path, monkeypatch):
    d = tmp_path / "codon_tables"
    shutil.copytree(TABLES, d)
    monkeypatch.setattr(settings, "CODON_TABLES_DIR", str(d))
    cache.backend.clear_pattern("codon_opt:*")
    yield d
    cache.backend.clear_pattern("codon_opt:*")


def _edit(path: Path, old: str, new: str):
    st = path.stat()
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    # 粗粒度文件系统上同一时刻写入 mtime 可能不变；强制推进，模拟真实编辑
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


def _first_codon_line(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        head = line.split(":")[0].strip()
        if len(head) == 3 and head.isalpha() and head.isupper():
            return line
    raise AssertionError("表里没有密码子行")


def test_editing_table_changes_cache_key(tables):
    k1 = cache._codon_opt_key(_AA, "ecoli", 40, 60, [])
    assert cache._codon_opt_key(_AA, "ecoli", 40, 60, []) == k1  # 未改表：稳定
    ecoli = co.resolve_codon_table_file("ecoli")
    line = _first_codon_line(ecoli)
    _edit(ecoli, line, line + "  # edited")
    assert cache._codon_opt_key(_AA, "ecoli", 40, 60, []) != k1


def test_stale_result_not_served_after_table_edit(tables):
    from app.design_service import process_sequence

    cache.cache_codon_optimization(
        sequence=_AA, species="ecoli", gc_min=40, gc_max=60, exclude_enzymes=[],
        result={"dna_sequence": "STALE", "cai": 0.0, "gc_content": 0.0, "warnings": []},
    )
    dna, *_ = process_sequence(_AA, SequenceType.AMINO_ACID, True, "ecoli", 40, 60)
    assert dna == "STALE"  # 同一张表：照常命中

    ecoli = co.resolve_codon_table_file("ecoli")
    line = _first_codon_line(ecoli)
    _edit(ecoli, line, line + "  # 新数据")
    dna, *_ = process_sequence(_AA, SequenceType.AMINO_ACID, True, "ecoli", 40, 60)
    assert dna != "STALE"


def test_other_species_unaffected_by_edit(tables):
    k_human = cache._codon_opt_key(_AA, "human", 40, 60, [])
    ecoli = co.resolve_codon_table_file("ecoli")
    line = _first_codon_line(ecoli)
    _edit(ecoli, line, line + "  # edited")
    assert cache._codon_opt_key(_AA, "human", 40, 60, []) == k_human
    assert co.codon_table_fingerprint("ecoli") != co.codon_table_fingerprint("human")


def test_fingerprint_cached_per_mtime(tables, monkeypatch):
    co.codon_table_fingerprint("ecoli")
    reads = []
    real = Path.read_bytes

    def spy(self):
        reads.append(self)
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    for _ in range(5):
        co.codon_table_fingerprint("ecoli")
    assert reads == []  # 表未变：不重复读盘
    ecoli = co.resolve_codon_table_file("ecoli")
    line = _first_codon_line(ecoli)
    _edit(ecoli, line, line + "  # edited")
    co.codon_table_fingerprint("ecoli")
    assert len(reads) == 1
    # 同一路径只保留最新摘要，反复改表不累积
    paths = [k[0] for k in co._TABLE_DIGESTS]
    assert len(paths) == len(set(paths))


def test_fingerprint_matches_table_the_optimizer_loads(tables):
    for species in ("ecoli", "human", "yeast", "cho"):
        path = co.resolve_codon_table_file(species)
        assert path is not None
        assert co.CodonOptimizer(species=species).codon_table_used == path.stem
    assert co.codon_table_fingerprint("martian") == "none"
