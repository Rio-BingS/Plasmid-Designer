"""整理包命名与写出内容的安全性（网页端整理包与离线脚本共用 sequencing_report）

覆盖：超长名字截断保留扩展名、不同质粒安全化后撞名时加 ~N 区分（短名不变）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.sequencing_report import (  # noqa: E402
    BatchArchive,
    _safe_component,
    _unique_components,
    batch_archive_entries,
)


# ---------------------------------------------------------------- L2 命名


def test_safe_component_short_names_unchanged():
    # 未超长的名字与此前实现逐字一致（网页整理包里的既有名字不变）
    assert _safe_component("T1.ab1") == "T1.ab1"
    assert _safe_component("../../ESCAPE") == "ESCAPE"
    assert _safe_component("..") == "unnamed"
    assert _safe_component("a/b") == "a_b"
    assert _safe_component("x" * 80) == "x" * 80


def test_safe_component_truncation_keeps_extension():
    name = "L" * 120 + "1.fasta"
    out = _safe_component(name)
    assert len(out) <= 80
    assert out.endswith(".fasta")
    assert out.startswith("L" * 70)
    # 截断不留 Windows 不接受的结尾空格/点
    assert _safe_component("A" * 74 + "  " + "B" * 30 + ".ab1") == "A" * 74 + ".ab1"


def test_safe_component_long_name_without_extension():
    out = _safe_component("N" * 200)
    assert out == "N" * 80
    # 「扩展名」过长（不像扩展名）时按原样截断
    out = _safe_component("a." + "b" * 100)
    assert len(out) == 80 and out.startswith("a.")


def test_unique_components_disambiguates_deterministically():
    names = ["a/b", "a:b", "a_b"]
    m = _unique_components(names)
    # 安全化前后不变的 'a_b' 保留原名，其余按原始名排序加 ~2、~3
    assert m["a_b"] == "a_b"
    assert sorted([m["a/b"], m["a:b"]]) == ["a_b~2", "a_b~3"]
    assert len(set(m.values())) == 3
    # 与输入顺序无关
    assert _unique_components(list(reversed(names))) == m


def test_unique_components_long_names_stay_within_limit():
    a, b = "L" * 100 + "A", "L" * 100 + "B"
    m = _unique_components([a, b])
    assert m[a] == "L" * 80
    assert m[b] == "L" * 78 + "~2"
    assert all(len(v) <= 80 for v in m.values())


def test_unique_components_case_insensitive_and_short_untouched():
    m = _unique_components(["P1", "P2", "x/y", "X_Y"])
    assert m["P1"] == "P1" and m["P2"] == "P2"
    assert {m["x/y"], m["X_Y"]} == {"X_Y", "x_y~2"}


def _group(plasmid, ref_name=None, read_names=()):
    ref = ({"file": {"name": ref_name, "bytes": b">r\nACGT\n"}, "how": "质粒名"}
           if ref_name else None)
    reads = [{"file": {"name": n, "bytes": n.encode()}, "how": "引物列"}
             for n in read_names]
    return {"plasmid": plasmid, "clone": None, "reference": ref, "reads": reads}


def _item(plasmid):
    return {"plasmid": plasmid, "clone": None, "conclusion": "无法判定：测试",
            "analysis_id": None, "status": "skipped", "read_count": 1,
            "coverage_percent": None, "variant_count": 0, "reference_name": None}


def _entries(groups):
    items = [_item(g["plasmid"]) for g in groups]
    payload = {"items": items, "unmatched": [], "clone_mode": False}
    return [a for a, _d in batch_archive_entries(payload, groups, [], None, None,
                                                 None, {})]


def test_archive_separates_plasmids_that_sanitize_to_same_folder():
    groups = [_group("a/b", read_names=["r1.ab1"]),
              _group("a:b", read_names=["r2.ab1"])]
    arcs = _entries(groups)
    assert "测序整理/a_b/无法判定/r1.ab1" in arcs
    assert "测序整理/a_b~2/无法判定/r2.ab1" in arcs
    # 两份报告各在自己的文件夹，不再出现 测序分析报告 (2).md
    assert "测序整理/a_b/无法判定/测序分析报告.md" in arcs
    assert "测序整理/a_b~2/无法判定/测序分析报告.md" in arcs
    assert not any("(2)" in a for a in arcs)


def test_archive_long_plasmid_names_keep_reference_extension():
    p1, p2 = "L" * 100 + "1", "L" * 100 + "2"
    groups = [_group(p1, ref_name=p1 + ".fasta", read_names=["a.ab1"]),
              _group(p2, ref_name=p2 + ".fasta", read_names=["b.ab1"])]
    arcs = _entries(groups)
    refs = [a for a in arcs if a.endswith(".fasta")]
    assert len(refs) == 2
    folders = {a.split("/")[1] for a in refs}
    assert folders == {"L" * 80, "L" * 78 + "~2"}
    assert all(len(seg) <= 80 for a in arcs for seg in a.split("/"))


def test_archive_short_names_unchanged():
    groups = [_group("TST", ref_name="TST.fasta", read_names=["T1.ab1"])]
    arcs = _entries(groups)
    assert "测序整理/TST/TST.fasta" in arcs
    assert "测序整理/TST/无法判定/T1.ab1" in arcs
    assert BatchArchive(["TST"]).folder_of(groups[0]) == "TST"
