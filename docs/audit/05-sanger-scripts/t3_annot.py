import sys, random
sys.path.insert(0, "src/backend"); sys.path.insert(0, "tests")
from core.sanger.annotator import annotate_variant
random.seed(3)
def rnd(n): return "".join(random.choice("AC") for _ in range(n))  # 只用 AC 避免偶然酶位点
# 参考：pos 100 附近，EcoRI 位于变体右侧 9-14bp
left=rnd(99); right="AC"*4+"A"+"GAATTC"+rnd(80)
ref=left+"C"+right  # 变体位点 ref_pos=100 = 'C'
# 插入 5bp（与位点无关）
v={"ref_pos":100,"type":"insertion","alt_base":"ACACA","length":5}
print("insertion 5bp:", annotate_variant(dict(v),[],ref)["enzyme_sites_lost"], annotate_variant(dict(v),[],ref)["enzyme_sites_gained"])
# 缺失 4bp 让窗口右侧拉进新位点
ref2=left+"C"+"ACAC"+"A"*6+"GAATTC"+rnd(80)
v={"ref_pos":101,"type":"deletion","alt_base":"-","ref_base":"ACAC","length":4}
r=annotate_variant(dict(v),[],ref2); print("deletion 4bp: lost",r["enzyme_sites_lost"],"gained",r["enzyme_sites_gained"])
# 同一密码子两个 SNV
cds="ATG"+"GCA"+"TAA"
ref3="CC"+cds+"CC"; feats=[{"name":"g","type":"CDS","start":3,"end":11,"strand":"+"}]
a=annotate_variant({"ref_pos":6,"type":"substitution","ref_base":"G","alt_base":"T","length":1},feats,ref3)
b=annotate_variant({"ref_pos":7,"type":"substitution","ref_base":"C","alt_base":"G","length":1},feats,ref3)
print("MNV GCA->TGA 实为终止; 逐位注释:", a["aa_change"], b["aa_change"])
