"""密码子表数据守门（终审 A-04）。

历史事故：Human.yaml/CHO.yaml 的 His 行与终止密码子行是从大肠杆菌表复制的
（人源 Kazusa 实际 CAT 0.42/CAC 0.58，表里写成 0.57/0.43——正是 E. coli 的
值；主终止子 TAA/TGA 占比也随复制颠倒），Yeast Pro 最优密码子错误，
optimal_codons 段与频率行自相矛盾。本文件锁住三类错误：
1. 同一家族行在任意两物种间不得完全相同（防复制粘贴）；
2. 每个表必须带 Kazusa 数据源留痕（taxonomy_id + data_provenance + 抓取日期）；
3. 每个同义家族内 fraction 求和为 1（±容差），保证归一化语义。
"""

import math
from pathlib import Path

import pytest

TABLES_DIR = Path(__file__).resolve().parents[1] / "data" / "codon_tables"

# 同义密码子家族（标准遗传密码；终止密码子单独一组）
FAMILIES = {
    "F": ["TTT", "TTC"],
    "L": ["TTA", "TTG", "CTT", "CTC", "CTA", "CTG"],
    "I": ["ATT", "ATC", "ATA"],
    "M": ["ATG"],
    "V": ["GTT", "GTC", "GTA", "GTG"],
    "S": ["TCT", "TCC", "TCA", "TCG", "AGT", "AGC"],
    "P": ["CCT", "CCC", "CCA", "CCG"],
    "T": ["ACT", "ACC", "ACA", "ACG"],
    "A": ["GCT", "GCC", "GCA", "GCG"],
    "Y": ["TAT", "TAC"],
    "Stop": ["TAA", "TAG", "TGA"],
    "W": ["TGG"],
    "H": ["CAT", "CAC"],
    "Q": ["CAA", "CAG"],
    "N": ["AAT", "AAC"],
    "K": ["AAA", "AAG"],
    "D": ["GAT", "GAC"],
    "E": ["GAA", "GAG"],
    "C": ["TGT", "TGC"],
    "R": ["CGT", "CGC", "CGA", "CGG", "AGA", "AGG"],
    "G": ["GGT", "GGC", "GGA", "GGG"],
}


def _load(name: str) -> dict:
    import yaml

    return yaml.safe_load((TABLES_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


def _per_thousand(name: str) -> dict:
    """从行尾注释读取每千次数（fraction 可能在两表间偶然相同，
    per_thousand 同时相同才是复制粘贴）"""
    import re

    out = {}
    for line in (TABLES_DIR / f"{name}.yaml").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([ACGT]{3}): [0-9.]+\s*#\s*per_thousand=([0-9.]+)", line)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


# 每个表必须声明的 NCBI taxonomy id。CHO 细胞亲本是中国仓鼠
# Cricetulus griseus (10029)，不是叙利亚仓鼠 Mesocricetus auratus (10036)
EXPECTED_TAXIDS = {
    "Human": (9606, "Homo sapiens"),
    "CHO": (10029, "Cricetulus griseus"),
    "Yeast": (4932, "Saccharomyces cerevisiae"),
    "Ecoli_K12": (316407, "Escherichia coli W3110"),
}


@pytest.mark.parametrize("name", ["Human", "CHO", "Yeast", "Ecoli_K12"])
def test_table_has_kazusa_provenance(name):
    """每张表必须带物种分类号与数据源留痕（无来源的数字不可信）。"""
    data = _load(name)
    assert isinstance(data.get("taxonomy_id"), int), f"{name} 缺 taxonomy_id"
    provenance = data.get("data_provenance", "")
    assert "kazusa" in provenance.lower(), f"{name} 缺 Kazusa 数据源留痕: {provenance!r}"


@pytest.mark.parametrize("name", ["Human", "CHO", "Yeast", "Ecoli_K12"])
def test_table_declares_expected_taxon(name):
    """物种分类号必须是该表真正对应的物种，且数据源标注同一个 id。

    回归：CHO 表曾被重建成 taxid 10036（叙利亚仓鼠 Mesocricetus
    auratus）的数据——数值自洽、守卫测试全过，但物种是错的。
    """
    data = _load(name)
    taxid, species_hint = EXPECTED_TAXIDS[name]
    assert data["taxonomy_id"] == taxid, (
        f"{name} 应为 taxid {taxid}（{species_hint}），实际 {data['taxonomy_id']}")
    assert f"kazusa:{taxid}" in str(data.get("data_provenance", "")), (
        f"{name} 数据源留痕与 taxonomy_id 不一致: {data.get('data_provenance')!r}")


def test_cho_values_come_from_chinese_hamster():
    """CHO 表数值须取自 Kazusa 10029（中国仓鼠），抽查与 10036 区分度最大的行。

    Kazusa 10029: TTT 0.47/19.6、CAC 0.56/12.9、AAA 0.39/24.6
    Kazusa 10036: TTT 0.41/17.4、CAC 0.60/15.1、AAA 0.36/20.0
    """
    cho, pt = _load("CHO"), _per_thousand("CHO")
    for codon, frac, per_k in (("TTT", 0.47, 19.6), ("CAC", 0.56, 12.9),
                               ("AAA", 0.39, 24.6), ("GAT", 0.47, 24.6)):
        assert cho[codon] == frac, f"CHO {codon} 应为 10029 的 {frac}，实际 {cho[codon]}"
        assert pt[codon] == per_k, f"CHO {codon} per_thousand 应为 {per_k}，实际 {pt.get(codon)}"


@pytest.mark.parametrize("name", ["Human", "CHO", "Yeast", "Ecoli_K12"])
def test_families_sum_to_one(name):
    """同义家族内 fraction 求和 = 1（±0.03 容差，两位小数舍入）。"""
    data = _load(name)
    for aa, codons in FAMILIES.items():
        if aa == "M" or aa == "W":
            continue  # 单密码子家族无归一化语义
        vals = [data.get(c) for c in codons]
        assert all(isinstance(v, (int, float)) for v in vals), f"{name} {aa} 家族缺值: {vals}"
        s = sum(vals)
        assert math.isclose(s, 1.0, abs_tol=0.03), f"{name} {aa} 家族求和 {s:.3f} != 1"


def test_human_rows_differ_from_ecoli():
    """终审 A-04 回归锁：Human 的 His 行与终止密码子行曾是 E. coli 数据
    （CAC 0.58 写成 0.43；TAA/TGA 颠倒）——两表这些家族行必须不同。"""
    human, ecoli = _load("Human"), _load("Ecoli_K12")
    for aa in ("H", "Stop"):
        row_h = tuple(human[c] for c in FAMILIES[aa])
        row_e = tuple(ecoli[c] for c in FAMILIES[aa])
        assert row_h != row_e, f"Human 与 E. coli 的 {aa} 行完全相同（复制粘贴嫌疑）: {row_h}"


def test_cho_rows_differ_from_ecoli():
    """CHO 与 E. coli 的 His/终止家族行不得完全相同（曾是复制值）。"""
    cho, ecoli = _load("CHO"), _load("Ecoli_K12")
    for aa in ("H", "Stop"):
        row_c = tuple(cho[c] for c in FAMILIES[aa])
        row_e = tuple(ecoli[c] for c in FAMILIES[aa])
        assert row_c != row_e, f"CHO 与 E. coli 的 {aa} 行完全相同: {row_c}"


def test_human_his_matches_kazusa_ratio():
    """人源 His 家族 CAC 必须是主流码子（Kazusa CAT 0.42/CAC 0.58）。"""
    human = _load("Human")
    assert human["CAT"] < human["CAC"], (
        f"Human His 应 CAC > CAT（Kazusa 0.58/0.42），实际 {human['CAT']}/{human['CAC']}"
    )


def test_yeast_pro_optimal_is_cca():
    """酵母 Pro 家族最优密码子是 CCA（Kazusa per-thousand: CCA 18.3 最高）。"""
    yeast = _load("Yeast")
    pro = {c: yeast[c] for c in FAMILIES["P"]}
    assert max(pro, key=pro.get) == "CCA", f"Yeast Pro 最优应为 CCA，实际 {pro}"


def test_no_two_tables_share_identical_family_row():
    """任意两表同一家族行不得完全相同（防整行复制——表间同家族恰好
    同值的概率极低，完全相同几乎必然是复制）。"""
    names = ["Human", "CHO", "Yeast", "Ecoli_K12"]
    tables = {n: _load(n) for n in names}
    pts = {n: _per_thousand(n) for n in names}
    for aa, codons in FAMILIES.items():
        if aa in ("M", "W"):
            continue
        rows = {}
        for n in names:
            # 同时比较占比与每千次数：不同物种的占比可能恰好相同
            # （人与中国仓鼠的 Val 家族即为真实巧合），per_thousand 也
            # 逐位相同才判复制
            rows[n] = (tuple(round(float(tables[n][c]), 2) for c in codons),
                       tuple(pts[n].get(c) for c in codons))
        seen = {}
        for n, row in rows.items():
            if row in seen:
                # E. coli 与 CHO/Human 同为哺乳动物式偏好可能整体接近，
                # 但六密码子家族逐位两位小数完全相同仍判复制
                pytest.fail(f"{seen[row]} 与 {n} 的 {aa} 家族行完全相同"
                            f"（占比与每千次数均相同）: {row}")
            seen[row] = n
