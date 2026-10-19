"""宿主类别要按解析后的密码子表/别名判定，而不是原始 species 字符串

历史上 _censor_motifs 用 HOST_CLASS.get(species.lower()) 直查，而选表走
_SPECIES_ALIASES 模糊匹配：写 "E. coli" 能加载对 Ecoli_K12.yaml，宿主
类别却落空成 eukaryote，于是漏避让 SD（AGGAGG）/ -10（TATAAT）。
"""
import pytest

from core.codon_optimizer import CENSOR_MOTIFS, CodonOptimizer


@pytest.mark.parametrize("species", ["ecoli", "E.coli", "Escherichia coli", "K12"])
def test_prokaryote_aliases(species):
    opt = CodonOptimizer(species=species)
    assert opt._host_class() == "prokaryote", species


@pytest.mark.parametrize("species", ["human", "Homo sapiens", "cho", "yeast", "S. cerevisiae"])
def test_eukaryote_aliases(species):
    opt = CodonOptimizer(species=species)
    assert opt._host_class() == "eukaryote", species


def test_censor_motifs_follow_resolved_host():
    """大肠杆菌（含别名写法）审 SD/-10，人源审 polyA/TATA"""
    for species in ("ecoli", "E.coli", "Escherichia coli"):
        motifs = CodonOptimizer(species=species)._censor_motifs()
        assert "AGGAGG" in motifs and "TATAAT" in motifs, species
        assert "AATAAA" not in motifs, species
    human = CodonOptimizer(species="human")._censor_motifs()
    assert "AATAAA" in human and "AGGAGG" not in human


def test_unknown_species_defaults_to_eukaryote():
    """无法识别的物种名：_host_class 保持原先的保守默认（按真核审查）。

    注意构造优化器时会因找不到表而报错（终审 A-05 的显式拒绝），
    所以这里只校验判定函数本身的默认值。"""
    opt = CodonOptimizer.__new__(CodonOptimizer)
    opt.species = "danio rerio"
    opt.codon_table_used = ""
    assert opt._host_class() == "eukaryote"
    assert opt._censor_motifs() == CENSOR_MOTIFS["eukaryote"]
