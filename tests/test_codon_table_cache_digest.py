"""密码子表清单缓存按目录内容指纹分键：改表后不能继续返回旧清单"""
import pytest

from app.cache import cache
from core import codon_optimizer as co


def test_digest_changes_when_table_added(tmp_path, monkeypatch):
    (tmp_path / "Ecoli_K12.yaml").write_text("x", encoding="utf-8")
    monkeypatch.setattr(co, "_codon_tables_dir", lambda: tmp_path)
    first = co.codon_tables_digest()
    (tmp_path / "Human.yaml").write_text("y", encoding="utf-8")
    second = co.codon_tables_digest()
    assert len(first) == 16 and first != second


def test_digest_stable_without_changes(tmp_path, monkeypatch):
    (tmp_path / "A.yaml").write_text("x", encoding="utf-8")
    monkeypatch.setattr(co, "_codon_tables_dir", lambda: tmp_path)
    assert co.codon_tables_digest() == co.codon_tables_digest()


def test_digest_none_when_dir_missing(monkeypatch):
    monkeypatch.setattr(co, "_codon_tables_dir", lambda: None)
    assert co.codon_tables_digest() == "none"


def test_cache_is_keyed_by_digest():
    tables = [{"id": "ecoli", "name": "ecoli", "file": "Ecoli_K12.yaml"}]
    cache.cache_codon_tables(tables, "digest-a")
    assert cache.get_codon_tables("digest-a") == tables
    assert cache.get_codon_tables("digest-b") is None
