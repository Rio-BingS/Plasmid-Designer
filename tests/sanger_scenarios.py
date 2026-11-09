"""goal_sanger_full_fix_v2 §4 场景库（T0）：假✓/假合格（S）、误报（F）、其他（M）。

每个场景 = Scenario(id, source, task, title, build)。build() 返回
dict(ab1_files=[(name, bytes)], reference=str, features=list, kwargs=dict)。
「最终期望」断言集中在 test_sanger_scenarios.py；当前行为不达期望的以
xfail(strict=True, reason=<task>) 标注，由对应任务移除——移除即该场景转绿。

随机序列全部来自固定种子（SCENARIO_SEED），保证可复现；B 审计 2026-11-06
的 /tmp 探针思路已固化进 S9/S10/S14 等场景。
"""

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List

from tests.abif_utils import make_trace_ab1

SCENARIO_SEED = 20261107
BENCH_DIR = Path(__file__).parent / "data" / "sanger_bench"


def _rng(tag: str) -> random.Random:
    return random.Random(f"{SCENARIO_SEED}:{tag}")


def _rand_seq(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("ACGT") for _ in range(n))


def _read_from_ref(ref: str, start: int, end: int, *, subs=(), q=50, **kw) -> bytes:
    """参考 [start, end]（1-based 闭区间）为分子序列的 read；subs=(ref_pos, alt)"""
    mol = list(ref[start - 1:end])
    for pos, alt in subs:
        mol[pos - start] = alt
    return make_trace_ab1("".join(mol), q=q, **kw)


@dataclass
class Scenario:
    id: str
    source: str          # 审计条目（A x.y / B Px-n）
    task: str            # 使其转绿的任务（xfail reason）
    title: str
    build: Callable[[], Dict]


def _s1():
    rng = _rng("s1")
    ref = _rand_seq(rng, 1500)
    blob = _read_from_ref(ref, 1, 1000, subs=[(780, "G" if ref[779] != "G" else "T")], q=50)
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=[])


def _s2():
    rng = _rng("s2")
    ref = _rand_seq(rng, 1200)
    main = ref[449]
    alt = next(b for b in "ACGT" if b != main)
    blob = _read_from_ref(ref, 1, 1000, secondary={450: (alt, 0.35 / 0.65)}, q=50)
    feats = [dict(name="GeneX", type="CDS", start=301, end=600, strand="+")]
    return dict(ab1_files=[("mix.ab1", blob)], reference=ref, features=feats)


def _s3():
    rng = _rng("s3")
    ref = _rand_seq(rng, 1200)
    main = ref[449]
    alt = next(b for b in "ACGT" if b != main)
    blob = _read_from_ref(ref, 1, 1000, secondary={450: (alt, 1.0)}, q=50)
    feats = [dict(name="GeneX", type="CDS", start=301, end=600, strand="+")]
    return dict(ab1_files=[("mix.ab1", blob)], reference=ref, features=feats)


def _s4():
    rng = _rng("s4")
    ref = _rand_seq(rng, 1200)
    main = ref[449]
    alt = next(b for b in "ACGT" if b != main)
    # 65% 突变为主峰：分子在该位已是 alt（called=alt），参考碱基为次级峰 35/65
    mol = list(ref[:1000])
    mol[449] = alt
    blob = make_trace_ab1("".join(mol), q=50, secondary={450: (main, 0.35 / 0.65)})
    feats = [dict(name="GeneX", type="CDS", start=301, end=600, strand="+")]
    return dict(ab1_files=[("mix.ab1", blob)], reference=ref, features=feats)


def _s5():
    rng = _rng("s5")
    ref = _rand_seq(rng, 1500)
    wrong = "".join(rng.choice("ACGT") for _ in range(175))
    blob = make_trace_ab1(wrong + ref[175:1073], q=30)
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=[])


def _s6():
    rng = _rng("s6")
    ref = _rand_seq(rng, 1500)
    wrong = "".join(rng.choice("ACGT") for _ in range(188))
    blob = make_trace_ab1(ref[0:885] + wrong, q=30)
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=[])


def _s18():
    """独立正例（2026-11-08 复审 R7）：CDS 内框内缺失 3bp——真值来自构造
    （planted deletion），不是「跑出来是什么就是什么」；与既有正例
    （替换为主、M14/M15 为未决 indel）互补，覆盖确证缺失这一格"""
    rng = _rng("s18")
    ref = list(_rand_seq(rng, 1500))
    codons = ["ATG"] + [_rand_seq(rng, 3) for _ in range(331)] + ["TAA"]
    codons = [c if c not in ("TAA", "TAG", "TGA") else "GCT" for c in codons]
    ref[300:1299] = list("".join(codons))
    ref = "".join(ref)
    # 500-502 框内缺失（CDS 301-1299 内，远离端部与边界）
    blob = make_trace_ab1(ref[0:500] + ref[503:1100], q=50)
    feats = [dict(name="Solo", type="CDS", start=301, end=1299, strand="+")]
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=feats)


def _s19():
    """独立正例（R7）：骨架（无特征区）确证替换 + read 全覆盖——goal T5
    整参考差异判定口径下唯一事件就是 planted sub，期望 fail 由构造决定"""
    rng = _rng("s19")
    ref = _rand_seq(rng, 900)
    alt = "G" if ref[649] != "G" else "T"
    blob = _read_from_ref(ref, 1, 900, subs=[(650, alt)], q=50)
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=[])


def _s7():
    rng = _rng("s7")
    ref = _rand_seq(rng, 1500)
    blob = _read_from_ref(ref, 1, 1000, subs=[(180, "G" if ref[179] != "G" else "T")], q=50)
    feats = [dict(name="CMV promoter", type="promoter", start=100, end=250, strand="+")]
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=feats)


def _s8():
    rng = _rng("s8")
    ref = list(_rand_seq(rng, 1500))
    # T16 护栏：CDS 标注须是窗口内干净 ORF（ATG..TAA，999bp=333 密码子），
    # 否则 orf_annotation_suspect 未决位点会盖过本场景的覆盖语义
    codons = ["ATG"] + [_rand_seq(rng, 3) for _ in range(331)] + ["TAA"]
    codons = [c if c not in ("TAA", "TAG", "TGA") else "GCT" for c in codons]
    ref[300:1299] = list("".join(codons))
    ref = "".join(ref)
    blob = _read_from_ref(ref, 301, 646, q=50)   # 345bp ≈ 34.5% of CDS 301-1299
    feats = [dict(name="Solo", type="CDS", start=301, end=1299, strand="+")]
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=feats)


def _s9():
    rng = _rng("s9")
    ref = _rand_seq(rng, 1500)
    blob = _read_from_ref(ref, 1, 1000, subs=[(20, "G" if ref[19] != "G" else "T")], q=40)
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=[])


def _s10():
    rng = _rng("s10")
    ref = _rand_seq(rng, 1200)
    blob = make_trace_ab1(ref[:1000], q=40, n_at=[500])
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=[])


def _s11():
    rng = _rng("s11")
    ref = _rand_seq(rng, 1200)
    # N 放在 ORF 校正后的真实编码区内（固定种子下 _find_orf 把 301-600 校正到
    # 474-600；340 落在编码区外不触发 n_in_cds）——场景意图是「翻译区被 N 遮蔽」
    blob = make_trace_ab1(ref[:1000], q=40, n_at=[500])
    feats = [dict(name="GeneX", type="CDS", start=301, end=600, strand="+")]
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=feats)


def _s12():
    rng = _rng("s12")
    ref = _rand_seq(rng, 1200)
    good = _read_from_ref(ref, 1, 400, q=50)
    corrupt = b"ABIF2GARBAGE" + bytes(range(256))          # 头合法、目录损坏
    truncated = b"ABIF" + b"\x00" * 40                     # 截断
    return dict(ab1_files=[("good.ab1", good), ("bad1.ab1", corrupt),
                           ("bad2.ab1", truncated)],
                reference=ref, features=[])


def _s13():
    rng = _rng("s13")
    ref = _rand_seq(rng, 2000)
    subs = [(15, "G" if ref[14] != "G" else "T"),
            (950, "G" if ref[949] != "G" else "T")]
    # T12 起可信由信号决定：700bp 后峰宽 ×3（真实尾端退化形态，Q 仍在），
    # 使「覆盖靠不可信碱基凑满」的前提在信号口径下成立
    blob = _read_from_ref(ref, 1, 1000, subs=subs, q=50, tail_width=(700, 3.0))
    feats = [dict(name="Solo", type="CDS", start=1, end=1000, strand="+")]
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=feats)


def _s14():
    rng = _rng("s14")
    ref = _rand_seq(rng, 1200)
    sec = {p: (next(b for b in "ACGT" if b != ref[p - 1]), 0.45 / 0.55)
           for p in (100, 200, 300, 400, 500, 600)}
    r1 = make_trace_ab1(ref[:800], q=50, secondary=sec)
    r2 = make_trace_ab1(ref[:800], q=25)
    return dict(ab1_files=[("mixed.ab1", r1), ("verify.ab1", r2)],
                reference=ref, features=[])


def _s15():
    """CDS 模式：载体-基因接合区 ±30bp 内非 low 差异（模拟 cds_extract 输出）"""
    rng = _rng("s15")
    ref = _rand_seq(rng, 3000)
    # 提取区 = 质粒 151-750（CDS 301-600 ± 150 侧翼），坐标平移 -150
    local = ref[150:750]
    blob = _read_from_ref(local, 1, 600, subs=[(140, "G" if local[139] != "G" else "T")], q=50)
    feats = [
        dict(name="GeneX", type="CDS", start=151, end=450, strand="+",
             analysis_target=True),
        dict(name="GeneX_flank5", type="misc_feature", start=1, end=150, strand="+"),
        dict(name="GeneX_flank3", type="misc_feature", start=451, end=600, strand="+"),
    ]
    return dict(ab1_files=[("cds.ab1", blob)], reference=local, features=feats)


def _s16():
    """polyA20 @100-119；122 处真实滑移 echo（A 通道 0.5×run 高）+ 150 处
    真实混合（次级碱基也是 A，0.35×）——现行 echo 链会把两者连成滑移序列
    （间距 28 ≫ STR stutter 物理间距），150 的混合位点被剔除"""
    rng = _rng("s16")
    ref = list(_rand_seq(rng, 1500))
    ref[99:119] = list("A" * 20)                     # polyA20 @100-119
    ref[149] = "C" if ref[149] != "C" else "T"       # 混合位点主碱基非 A
    ref = "".join(ref)
    blob = make_trace_ab1(ref[:800], q=45, secondary={
        122: ("A", 0.5),     # 真 echo（STR stutter，紧贴 run 末端）
        150: ("A", 0.35),    # 真实混合位点的次级峰（也是 A 通道）
    })
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=[])


def _s17():
    rng = _rng("s17")
    ref = _rand_seq(rng, 1500)
    sub = (800, "G" if ref[799] != "G" else "T")
    # T12 起可信由信号决定：750bp 后峰宽 ×3（真实尾端峰展宽，Q 保持 50），
    # 变异位于可信区之外但峰证据齐全——多 read 支持确证 ✗，注记 out_of_zone
    r1 = _read_from_ref(ref, 1, 1100, subs=[sub], q=50, tail_width=(750, 3.0))
    r2 = _read_from_ref(ref, 1, 1100, subs=[sub], q=50, tail_width=(750, 3.0))
    return dict(ab1_files=[("a.ab1", r1), ("b.ab1", r2)],
                reference=ref, features=[])


def _f1():
    rng = _rng("f1")
    raw = (BENCH_DIR / "3730.ab1").read_bytes()
    from core.sanger.abif_reader import extract_read
    seq = str(extract_read(raw)["bases"])
    ref = _rand_seq(rng, 170) + seq + _rand_seq(rng, 170)
    return dict(ab1_files=[("3730.ab1", raw)], reference=ref, features=[],
                kwargs=dict(circular=True))


def _f2():
    rng = _rng("f2")
    raw = (BENCH_DIR / "3100.ab1").read_bytes()
    from core.sanger.abif_reader import extract_read
    seq = str(extract_read(raw)["bases"])
    ref = _rand_seq(rng, 170) + seq + _rand_seq(rng, 170)
    return dict(ab1_files=[("3100.ab1", raw)], reference=ref, features=[],
                kwargs=dict(circular=True))


def _f3():
    raw = (BENCH_DIR / "310.ab1").read_bytes()
    rng = _rng("f3")
    from core.sanger.abif_reader import extract_read
    ref = _rand_seq(rng, 170) + str(extract_read(raw)["bases"]) + _rand_seq(rng, 170)
    return dict(ab1_files=[("310.ab1", raw)], reference=ref, features=[],
                kwargs=dict(circular=True))


def _m1():
    rng = _rng("m1")
    ref = list(_rand_seq(rng, 1200))
    # CDS 101-400；密码子 2 = ref 104-106 = AAA → TGA（104 A>T、106 A>G）
    ref[103:106] = list("AAA")
    ref = "".join(ref)
    subs = [(104, "T"), (106, "G")]
    blob = _read_from_ref(ref, 1, 1000, subs=subs, q=50)
    feats = [dict(name="GeneX", type="CDS", start=101, end=400, strand="+")]
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=feats)


def _m2():
    rng = _rng("m2")
    ref = _rand_seq(rng, 1200)
    sub = (300, "G" if ref[299] != "G" else "T")
    r1 = _read_from_ref(ref, 1, 1000, subs=[sub], q=40)
    r2 = _read_from_ref(ref, 1, 1000, subs=[sub], q=45)
    return dict(ab1_files=[("a.ab1", r1), ("b.ab1", r2)],
                reference=ref, features=[], order_sensitive=True)


def _m3():
    rng = _rng("m3")
    ref = list(_rand_seq(rng, 1200))
    ref[499] = "C"
    ref = "".join(ref)
    r1 = _read_from_ref(ref, 1, 1000, subs=[(500, "A")], q=50)
    r2 = _read_from_ref(ref, 1, 1000, subs=[(500, "G")], q=50)
    return dict(ab1_files=[("a.ab1", r1), ("b.ab1", r2)],
                reference=ref, features=[])


def _m4():
    """CDS 标注 1100-1551，窗口内真实最长 ORF 自 1228 起（偏移 128bp）"""
    rng = _rng("m4")
    ref = list(_rand_seq(rng, 4000))
    # 1100-1227 段：三个读框都频繁出现终止子，阻止更长 ORF
    stops = ["TAA", "TAG", "TGA"]
    for i in range(1100, 1228, 9):
        ref[i - 1:i + 2] = list(stops[(i // 9) % 3])
    # 1228 起完整 ORF：ATG + 106 个无终止密码子（1228..1548，324bp）
    codons = ["ATG"] + [_rand_seq(rng, 3) for _ in range(2, 108)]
    codons = [c if c not in stops else "GCT" for c in codons]
    ref[1227:1227 + 324] = list("".join(codons))
    # ORF 后紧跟终止 + 非 ATG 区段，防止窗口外延
    ref[1551:1554] = list("TAA")
    ref = "".join(ref)
    feats = [dict(name="MisAnn", type="CDS", start=1100, end=1551, strand="+")]
    blob = _read_from_ref(ref, 1, 2000, q=50)
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=feats)


def _m5():
    rng = _rng("m5")
    ref = _rand_seq(rng, 1500)
    primer = dict(name="M13F", seq=ref[99:130], direction="+")   # 结合 100-130
    blob = _read_from_ref(ref, 350, 1350, q=50)                  # 起点 ≈ 引物 3' 下游 220bp
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=[],
                kwargs=dict(primer=primer))


def _m6():
    rng = _rng("m6")
    ref = list(_rand_seq(rng, 1200))
    ref[199:219] = list("A" * 20)                    # polyA20 @200-219
    ref = "".join(ref)
    wrong = "".join(rng.choice("ACGT") for _ in range(50))
    clipped = make_trace_ab1(wrong + ref[0:800], q=45)   # 首端 50bp 软剪切
    plain = make_trace_ab1(ref[0:800], q=45)
    return dict(ab1_files=[("clipped.ab1", clipped), ("plain.ab1", plain)],
                reference=ref, features=[])


def _m7():
    """A 3.7：调用碱基为第二高通道（basecaller 拾取次峰）。T12 起 3100 的
    609 位落在信号衰减段（可信区外，双峰不记录——尾端不可信是本意），
    改确定性合成：位点 300 次级峰 1.2 倍于 called 峰（调用碱基非最高通道）"""
    rng = _rng("m7")
    ref = _rand_seq(rng, 700)
    alt = next(b for b in "ACGT" if b != ref[299])
    blob = make_trace_ab1(ref[:600], q=50, secondary={300: (alt, 1.2)})
    return dict(ab1_files=[("m7.ab1", blob)], reference=ref, features=[])


def _m8():
    raw = (BENCH_DIR / "3730.ab1").read_bytes()
    return dict(bench_file="3730.ab1", raw=raw)


def _m9():
    rng = _rng("m9")
    ref = _rand_seq(rng, 1000)
    blob = _read_from_ref(ref, 1, 800, q=50)
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=[],
                kwargs=dict(_no_circular=True))


def _m10():
    """反向 read：电泳 3' 端 1 个错碱基（软剪切）+ 两处低 Q 差异被正向 read 排除"""
    rng = _rng("m10")
    ref = list(_rand_seq(rng, 1200))
    ref = "".join(ref)
    mol = list(ref[100:1100])                        # 分子链（read 为其反向互补）
    mol[0] = next(b for b in "ACGT" if b != mol[0])  # 电泳 3' 端错碱基 → 软剪切
    subs_mol = [(200, next(b for b in "ACGT" if b != mol[199])),
                (230, next(b for b in "ACGT" if b != mol[229]))]
    for pos, alt in subs_mol:
        mol[pos - 1] = alt
    quals = [45] * 1000
    for pos, _alt in subs_mol:                       # 电泳坐标 = 1000 - mol_pos + 1
        quals[1000 - pos] = 12
    from core.seq_utils import revcomp
    r1 = make_trace_ab1(revcomp("".join(mol)), q=quals)
    r2 = _read_from_ref(ref, 1, 1200, q=50)          # 正向验证 read（干净）
    return dict(ab1_files=[("rev.ab1", r1), ("fwd.ab1", r2)],
                reference=ref, features=[])


def _m11():
    return dict(precheck_manifest=[{"name": "ref.gb", "size": 1}],
                excel_bytes=None, read_limits=None)


def _m12():
    """PLOC2 比 PBAS2 短 5 条：静默截短 → 最终须明确告警"""
    import struct
    from tests.abif_utils import _pack_entries
    rng = _rng("m12")
    ref = _rand_seq(rng, 600)
    n = 500
    calls = ref[50:50 + n]
    centers = [i * 12 for i in range(n)]
    # 每碱基 1 采样的方波 trace（峰形无关本场景，PLOC 条数才是焦点）
    entries = [
        (b"PBAS", 2, 2, 1, n, calls.encode()),
        (b"PCON", 2, 2, 1, n, bytes([50] * n)),
        (b"PLOC", 2, 5, 4, n - 5, struct.pack(f">{n - 5}I", *centers[:n - 5])),
    ]
    for i, ch in enumerate("GATC"):
        tr = [100 if b == ch else 4 for b in calls]
        entries.append((b"DATA", 9 + i, 5, 4, n, struct.pack(f">{n}i", *tr)))
    blob = _pack_entries(entries)
    return dict(ab1_files=[("short_ploc.ab1", blob)], reference=ref, features=[])


def _m14():
    """参考末端（原点前）插入：环状 read 跨原点，TTTT 插在 ref 999/1000 之间
    （插入变体最左归一化停在 999 → idx==L → end_insert_votes）。T12 起位置
    不再强制 low，单 low 票前提改由信号证据承担：插入段 Q 降至 28（indel
    置信地板 30 之下、单票 → low）且峰幅压到 5%（无独立插入峰）——现行
    口径仍按最大权重定插"""
    rng = _rng("m14")
    ref = list(_rand_seq(rng, 1000))
    if ref[998] == "T":                     # 999 位非 T：最左归一化停在 999
        ref[998] = "C"
    ref[997] = "A" if ref[997] != "A" else "G"   # 998 位非 T，防继续左移
    ref = "".join(ref)
    mol = ref[200:999] + "TTTT" + ref[999:] + ref[0:120]
    blob = make_trace_ab1(mol, q=[40] * 799 + [28] * (len(mol) - 799),
                          amp_override={p: 0.05 for p in range(800, 804)})
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=[],
                kwargs=dict(circular=True))


def _m15():
    rng = _rng("m15")
    ref = list(_rand_seq(rng, 1000))
    ref[21:27] = list("GAATTC")                       # EcoRI @22-27
    ref = "".join(ref)
    # 未决 indel @30（首端硬区 1-30 内）→ 酶位归属待定
    mol = list(ref[:600])
    mol.insert(29, "T")                               # 30 位插 1bp
    blob = make_trace_ab1("".join(mol), q=40)
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=[])


# ==================== goal v3（sanger scope v3）§4 场景 ====================
# 真值先行（goal v3 §2 硬约束）：本节场景的「最终期望」在 W0 写定并单独
# 提交，之后不得为了让测试通过而修改真值（需要改时停下汇报）。
# 期望集中写在 test_sanger_scenarios.py 的对应断言里；未实现的任务以
# xfail(strict=True, reason=Wxx) 标注，任务落地后移除标记即转绿。


def _scope_base():
    """SC 系列共用质粒：4000bp 环状——GeneX CDS(301-900，干净 ORF)、
    AmpR promoter(2001-2100)、AmpR gene(2101-2900，type=gene 避开蛋白
    判读机器；名称命中关键元件表)、ori(3101-3900)。干净 Q50 read：
    fwd 覆盖 201-1101、rev 覆盖 151-1051（反向互补），两者信号可信区
    都完整罩住 GeneX"""
    rng = _rng("sc-base")
    ref = list(_rand_seq(rng, 4000))
    codons = ["ATG"] + [_rand_seq(rng, 3) for _ in range(198)] + ["TAA"]
    codons = [c if c not in ("TAA", "TAG", "TGA") else "GCT" for c in codons]
    ref[300:900] = list("".join(codons))
    ref = "".join(ref)
    feats = [
        dict(name="GeneX", type="CDS", start=301, end=900, strand="+"),
        dict(name="AmpR promoter", type="promoter", start=2001, end=2100, strand="+"),
        dict(name="AmpR", type="gene", start=2101, end=2900, strand="+"),
        dict(name="ori", type="rep_origin", start=3101, end=3900, strand="+"),
    ]
    return ref, feats


def _scope_reads(ref, fwd_span=(201, 1101), rev_span=(151, 1051)):
    from core.seq_utils import revcomp
    out = [("F.ab1", make_trace_ab1(ref[fwd_span[0] - 1:fwd_span[1]], q=50))]
    if rev_span:
        out.append(("R.ab1", make_trace_ab1(
            revcomp(ref[rev_span[0] - 1:rev_span[1]]), q=50)))
    return out


def _sc1():
    ref, feats = _scope_base()
    return dict(ab1_files=_scope_reads(ref), reference=ref, features=feats,
                kwargs=dict(scope="GeneX", circular=True),
                ref_name="GeneX.gb")


def _sc2():
    """不声明 scope，参考文件名 GeneX 与 read 名可命中 → filename_match"""
    ref, feats = _scope_base()
    files = [("GeneX_F.ab1", _scope_reads(ref)[0][1]),
             ("GeneX_R.ab1", _scope_reads(ref)[1][1])]
    return dict(ab1_files=files, reference=ref, features=feats,
                kwargs=dict(circular=True), ref_name="GeneX.gb")


def _sc3():
    """不声明 scope，文件名也匹配不到 → 未声明范围：已测区段口径"""
    ref, feats = _scope_base()
    return dict(ab1_files=_scope_reads(ref), reference=ref, features=feats,
                kwargs=dict(circular=True), ref_name="Unknown-123.gb")


def _sc4():
    """3000bp 无 CDS 参考单 read 只测 1-900（RV P0-1 回归）→ 未声明范围
    「已测区段」口径，不得再出「构建与设计一致」"""
    rng = _rng("sc4")
    ref = _rand_seq(rng, 3000)
    return dict(ab1_files=[("r.ab1", _read_from_ref(ref, 1, 900, q=50))],
                reference=ref, features=[], kwargs=dict(circular=False),
                ref_name="noCDS.gb")


def _sc5():
    """scope=301-1500 只有正向 read（起点 200）→ 尾部缺口引物够不到"""
    ref, feats = _scope_base()
    return dict(ab1_files=_scope_reads(ref, fwd_span=(200, 1250), rev_span=None),
                reference=ref, features=feats,
                kwargs=dict(scope="301-1500", circular=True))


def _sc6():
    """scope=GeneX，正向 read 在 read 坐标约 400 起信号退化 → 测序未达"""
    ref, feats = _scope_base()
    blob = make_trace_ab1(ref[200:1101], q=50, tail_width=(400, 3.0))
    return dict(ab1_files=[("F.ab1", blob)], reference=ref, features=feats,
                kwargs=dict(scope="GeneX", circular=True))


def _sc7():
    """scope=GeneX，read 伸到 R 外，R 外骨架 1200 有确证替换 → 附带结果"""
    ref, feats = _scope_base()
    alt = "G" if ref[1199] != "G" else "T"
    blob = _read_from_ref(ref, 201, 1400, subs=[(1200, alt)], q=50)
    return dict(ab1_files=[("F.ab1", blob)], reference=ref, features=feats,
                kwargs=dict(scope="GeneX", circular=True))


def _sc8():
    """scope=GeneX，R 外 ori（3101-3900）内 3500 确证替换 → 警示 pending"""
    ref, feats = _scope_base()
    alt = "G" if ref[3499] != "G" else "T"
    blob_scope = _read_from_ref(ref, 201, 1101, q=50)
    blob_ori = _read_from_ref(ref, 2901, 3901, subs=[(3500, alt)], q=50)
    return dict(ab1_files=[("F.ab1", blob_scope), ("F2.ab1", blob_ori)],
                reference=ref, features=feats,
                kwargs=dict(scope="GeneX", circular=True))


def _sc9():
    """同一份 result 三种 scope 重算（不重跑分析）——spec 与 SC1 同构造，
    但 analyze 不传 scope（scope 重算只发生在 build_verdict）"""
    ref, feats = _scope_base()
    return dict(ab1_files=_scope_reads(ref), reference=ref, features=feats,
                kwargs=dict(circular=True))


def _sc10():
    """CDS 301-1299（干净 ORF），read 覆盖到 1320 → measured 100%；
    尾端 50bp Q=24（高于修剪阈值不被切、低于可信窗均值 TRUST_MEAN_Q=25
    → 可信区止于 ~1289，ROI verified ≈98.6%）。构造刻意不叠加峰形退化
    ——早期 tail_width=3.0 会在段内造出密集次峰触发 widespread 判
    mixed，偏离 §4「纯 verified 缺口」的场面：pending + roi_unverified，
    pass 与 blocking 不得并存（RV P0-2）"""
    rng = _rng("sc10")
    ref = list(_rand_seq(rng, 1800))
    codons = ["ATG"] + [_rand_seq(rng, 3) for _ in range(331)] + ["TAA"]
    codons = [c if c not in ("TAA", "TAG", "TGA") else "GCT" for c in codons]
    ref[300:1299] = list("".join(codons))
    ref = "".join(ref)
    feats = [dict(name="Solo", type="CDS", start=301, end=1299, strand="+")]
    seq = ref[240:1320]
    blob = make_trace_ab1(seq, q=[50] * (len(seq) - 50) + [24] * 50)
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=feats,
                kwargs=dict(scope="301-1299", circular=False))


def _sc11():
    """scope=GeneX 且要求双链：GeneX 后半段只有正向单链 → partial 按 E 归因"""
    ref, feats = _scope_base()
    files = _scope_reads(ref, fwd_span=(201, 1101), rev_span=(151, 651))
    scope = {"ranges": [[301, 900]], "source": "user", "declared": True,
             "require_double_strand": True, "elements": ["GeneX"]}
    return dict(ab1_files=files, reference=ref, features=feats,
                kwargs=dict(scope=scope, circular=True))


def _cm_base():
    """CM 系列共用：4000bp 环状，GeneX CDS 1001-1600（干净 ORF）"""
    rng = _rng("cm-base")
    ref = list(_rand_seq(rng, 4000))
    codons = ["ATG"] + [_rand_seq(rng, 3) for _ in range(198)] + ["TAA"]
    codons = [c if c not in ("TAA", "TAG", "TGA") else "GCT" for c in codons]
    ref[1000:1600] = list("".join(codons))
    ref = "".join(ref)
    feats = [dict(name="GeneX", type="CDS", start=1001, end=1600, strand="+")]
    return ref, feats


def _cm1():
    """目的基因模式 + 长 read 超出 ±150 侧翼（CM P0-A）：正反 read 覆盖
    700-1701 / 1000-1900，序列全对 → pass，无 unaligned/partial"""
    ref, feats = _cm_base()
    from core.seq_utils import revcomp
    files = [("GeneX_F.ab1", make_trace_ab1(ref[699:1701], q=50)),
             ("GeneX_R.ab1", make_trace_ab1(revcomp(ref[999:1901]), q=50))]
    return dict(ab1_files=files, reference=ref, features=feats,
                kwargs=dict(circular=True), ref_name="GeneX.gb", cds_mode=True)


def _cm2():
    """载体通用引物 read，起点距 ATG 300bp → pass（不因离基因远判 ✗）"""
    ref, feats = _cm_base()
    return dict(ab1_files=[("T7_Uni.ab1", make_trace_ab1(ref[700:1701], q=50))],
                reference=ref, features=feats,
                kwargs=dict(circular=True), ref_name="GeneX.gb", cds_mode=True)


def _cm3():
    """跨原点 CDS join(3701..4000,1..300)（干净 ORF 600bp）：ATG 前、终止
    密码子后各一条含 1bp 插入的 read → 都检出并判 junction_variant（CM P0-B）"""
    rng = _rng("cm3")
    ref = list(_rand_seq(rng, 4000))
    stops = ("TAA", "TAG", "TGA")
    body = []
    for _ in range(198):
        c = _rand_seq(rng, 3)
        body.append(c if c not in stops else "GCT")
    # 编码方向：3701-4000（300bp）+ 1-300（300bp）；ATG@3701，TAA@298-300
    ref[3700:3703] = list("ATG")
    ref[3703:4000] = list("".join(body[:99]))
    ref[0:297] = list("".join(body[99:]))
    ref[297:300] = list("TAA")
    ref = "".join(ref)
    ins = "T" if ref[3699] != "T" else "A"       # ATG 前插 1bp（3700/3701 之间）
    ins2 = "T" if ref[300] != "T" else "A"       # 终止后插 1bp（300/301 之间）
    r1 = make_trace_ab1(ref[3299:3700] + ins + ref[3700:4000] + ref[0:450], q=50)
    r2 = make_trace_ab1(ref[3299:4000] + ref[0:300] + ins2 + ref[301:450], q=50)
    feats = [
        dict(name="CrossGene", type="CDS", start=3701, end=4000, strand="+",
             feature_id="cm3cds", segment_index=0, segment_count=2,
             analysis_target=True),
        dict(name="CrossGene", type="CDS", start=1, end=300, strand="+",
             feature_id="cm3cds", segment_index=1, segment_count=2,
             analysis_target=True),
    ]
    return dict(ab1_files=[("CG_F.ab1", r1), ("CG_R.ab1", r2)],
                reference=ref, features=feats,
                kwargs=dict(circular=True), ref_name="CrossGene.gb", cds_mode=True)


def _cm4():
    """近原点 CDS 31-630（干净 ORF），ATG 前 1bp 插入 → 检出且与
    「全质粒 + scope=该 CDS」结果一致"""
    rng = _rng("cm4")
    ref = list(_rand_seq(rng, 2000))
    codons = ["ATG"] + [_rand_seq(rng, 3) for _ in range(198)] + ["TAA"]
    codons = [c if c not in ("TAA", "TAG", "TGA") else "GCT" for c in codons]
    ref[30:630] = list("".join(codons))
    ref = "".join(ref)
    ins = "T" if ref[29] != "T" else "A"
    blob = make_trace_ab1(ref[0:30] + ins + ref[30:1000], q=50)
    feats = [dict(name="NearCDS", type="CDS", start=31, end=630, strand="+",
                  analysis_target=True)]
    return dict(ab1_files=[("N_F.ab1", blob)], reference=ref, features=feats,
                kwargs=dict(circular=True), ref_name="NearCDS.gb", cds_mode=True)


def _cm5():
    """同一组干净 read：cds_mode 与「全质粒 + scope=该 CDS」逐项一致"""
    ref, feats = _cm_base()
    from core.seq_utils import revcomp
    files = [("GeneX_F.ab1", make_trace_ab1(ref[699:1701], q=50)),
             ("GeneX_R.ab1", make_trace_ab1(revcomp(ref[999:1901]), q=50))]
    return dict(ab1_files=files, reference=ref, features=feats,
                kwargs=dict(circular=True), ref_name="GeneX.gb", cds_mode=True)


def _cm6():
    """参考名 GFP_backbone 与 read 名 mCherry_F 指向不同候选 → 判歧义"""
    rng = _rng("cm6")
    ref = list(_rand_seq(rng, 3000))
    codons = ["ATG"] + [_rand_seq(rng, 3) for _ in range(198)] + ["TAA"]
    codons = [c if c not in ("TAA", "TAG", "TGA") else "GCT" for c in codons]
    ref[300:900] = list("".join(codons))          # GFP 301-900
    codons2 = ["ATG"] + [_rand_seq(rng, 3) for _ in range(198)] + ["TAA"]
    codons2 = [c if c not in ("TAA", "TAG", "TGA") else "GCT" for c in codons2]
    ref[1500:2100] = list("".join(codons2))       # mCherry 1501-2100
    ref = "".join(ref)
    feats = [dict(name="GFP", type="CDS", start=301, end=900, strand="+"),
             dict(name="mCherry", type="CDS", start=1501, end=2100, strand="+")]
    blob = _read_from_ref(ref, 1401, 2400, q=50)
    return dict(ab1_files=[("mCherry_F.ab1", blob)], reference=ref,
                features=feats, kwargs=dict(circular=False),
                ref_name="GFP_backbone.gb")


def _cm7():
    """嵌套 6xHis 可被显式选中；重名显式选择报错（构造在测试内补一份）"""
    rng = _rng("cm7")
    ref = list(_rand_seq(rng, 2000))
    codons = ["ATG"] + [_rand_seq(rng, 3) for _ in range(388)] + ["TAA"]
    codons = [c if c not in ("TAA", "TAG", "TGA") else "GCT" for c in codons]
    ref[300:1469] = list("".join(codons))         # BigCDS 301-1469（390 密码子）
    ref[400:418] = list("CATCATCATCATCATCAT")     # 6xHis 401-418（嵌套）
    ref = "".join(ref)
    feats = [dict(name="BigCDS", type="CDS", start=301, end=1469, strand="+"),
             dict(name="6xHis", type="CDS", start=401, end=418, strand="+")]
    blob = _read_from_ref(ref, 201, 1200, q=50)
    return dict(ab1_files=[("H_F.ab1", blob)], reference=ref, features=feats,
                kwargs=dict(circular=False), ref_name="HisTag.gb")


def _cm8():
    """负链基因：质粒 1100 位替换 → coords.plasmid=1100、c./p. 按编码方向"""
    rng = _rng("cm8")
    from core.seq_utils import revcomp
    ref = list(_rand_seq(rng, 3000))
    codons = ["ATG"] + [_rand_seq(rng, 3) for _ in range(198)] + ["TAA"]
    codons = [c if c not in ("TAA", "TAG", "TGA") else "GCT" for c in codons]
    ref[1000:1600] = list(revcomp("".join(codons)))   # 负链 CDS 1001-1600
    ref = "".join(ref)
    alt = "G" if ref[1099] != "G" else "T"
    blob = _read_from_ref(ref, 201, 1400, subs=[(1100, alt)], q=50)
    feats = [dict(name="GeneX", type="CDS", start=1001, end=1600, strand="-")]
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=feats,
                kwargs=dict(scope="GeneX", circular=False))


def _af1_make(af: float):
    """AF1 工厂：CDS 内单 read，450 位次等位丰度 af（0.12/0.15/0.18/0.20）"""
    rng = _rng("af1")
    ref = _rand_seq(rng, 1200)
    alt = next(b for b in "ACGT" if b != ref[449])
    blob = make_trace_ab1(ref[:1000], q=50, secondary={450: (alt, af / (1 - af))})
    feats = [dict(name="GeneX", type="CDS", start=301, end=600, strand="+")]
    return dict(ab1_files=[("mix.ab1", blob)], reference=ref, features=feats,
                kwargs=dict(scope="GeneX", circular=False))


def _af1():
    return _af1_make(0.15)


def _af2():
    """骨架散在双峰 AF 0.15：经 unresolved_sites 判 △，不升级 mixed"""
    rng = _rng("af2")
    ref = _rand_seq(rng, 1500)
    sec = {300: (next(b for b in "ACGT" if b != ref[299]), 0.15 / 0.85),
           700: (next(b for b in "ACGT" if b != ref[699]), 0.15 / 0.85)}
    blob = make_trace_ab1(ref[:1000], q=50, secondary=sec)
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=[],
                kwargs=dict(circular=False))


def _pr_base():
    """PR 系列共用：1500bp 线性参考，正向引物结合 100-130、反向引物
    结合 1400-1430（负链）。正向 read 起点 200（距 F 3′ 端 70bp）、
    反向 read 起点 1500（距 R 3′ 端 70bp）——均在 T17 窗口内"""
    rng = _rng("pr")
    ref = _rand_seq(rng, 1500)
    from core.seq_utils import revcomp
    fwd_primer = {"name": "P-F", "seq": ref[99:130], "direction": "+"}
    rev_primer = {"name": "P-R", "seq": revcomp(ref[1399:1430]), "direction": "-"}
    return ref, fwd_primer, rev_primer


def _pr1():
    """正反两条引物各自对应自己的 read → 两条校验都 ok，不报方向不一致"""
    ref, fwd_primer, rev_primer = _pr_base()
    from core.seq_utils import revcomp
    files = [("F.ab1", make_trace_ab1(ref[199:1200], q=50)),
             ("R.ab1", make_trace_ab1(revcomp(ref[499:1500]), q=50))]
    return dict(ab1_files=files, reference=ref, features=[],
                kwargs=dict(circular=False,
                            primer={"F.ab1": fwd_primer, "R.ab1": rev_primer}))


def _pr2():
    """提供了引物但没有对应的 read → 「引物无信号」，归 read_shortfall"""
    ref, fwd_primer, rev_primer = _pr_base()
    return dict(ab1_files=[("F.ab1", make_trace_ab1(ref[199:1200], q=50))],
                reference=ref, features=[],
                kwargs=dict(circular=False, primer={"R.ab1": rev_primer}))


def _pr3():
    """read 起点与引物 3′ 端距离出窗 → primer_mismatch（保持 T17 口径）"""
    ref, fwd_primer, _rev = _pr_base()
    blob = _read_from_ref(ref, 350, 1350, q=50)   # 距 F 3′ 端 220bp > 150
    return dict(ab1_files=[("r.ab1", blob)], reference=ref, features=[],
                kwargs=dict(circular=False, primer=fwd_primer))


SCENARIOS: List[Scenario] = [
    Scenario("S1", "A 1.1 / B P0-1", "T12", "尾端灰区 Q50 错义被强制 low → ✓", _s1),
    Scenario("S2", "A 3.2", "T7", "CDS 单点 35% 混合静默 ✓", _s2),
    Scenario("S3", "A 3.2", "T7", "CDS 单点 50:50 混合静默 ✓", _s3),
    Scenario("S4", "A 3.2", "T7", "65% 突变为主峰 → 低置信伪影 ✓", _s4),
    Scenario("S5", "A 2.1", "T6", "read 5' 端 175bp 连接错误被软剪切吞掉", _s5),
    Scenario("S6", "A 2.1", "T6", "read 3' 端 188bp 差异被软剪切吞掉", _s6),
    Scenario("S7", "A 4.1 / A 5.5", "T5", "启动子确证突变网页✗/Excel 合格/归档正确", _s7),
    Scenario("S18", "复审R7", "R7", "独立正例：CDS 内框内缺失 3bp 确证（真值来自构造）", _s18),
    Scenario("S19", "复审R7", "R7", "独立正例：骨架确证替换全覆盖（真值来自构造）", _s19),
    Scenario("S8", "A 4.2 / B P0-4", "T3", "唯一 CDS 覆盖 34.5% 照样 ✓/合格", _s8),
    Scenario("S9", "B P0-1", "T1", "首端 30bp 硬区 Q40 变异 → ✓（疑似伪影）", _s9),
    Scenario("S10", "B P0-2", "T1", "read 中段 N 隐身（variants=[] identity=1.0 ✓）", _s10),
    Scenario("S11", "B P0-2", "T1", "CDS 内错义位点被 N 遮蔽 → 蛋白一致", _s11),
    Scenario("S12", "B P0-3", "T4", "3 read 2 条损坏 → 合格并分入「正确/」", _s12),
    Scenario("S13", "B P0-4", "T3", "覆盖靠不可信碱基凑满（首硬区+尾>900）", _s13),
    Scenario("S14", "B P0-5", "T8", "45% 真混合被低标准互检翻成 ✓", _s14),
    Scenario("S15", "B P1-11", "T5", "CDS 模式接合区 ±30bp 差异被吸收", _s15),
    Scenario("S16", "B P2-20", "T7", "poly 后 20+bp 弱峰被连成滑移序列吞掉", _s16),
    Scenario("S17", "B P1-7", "T3", "双 read 尾端灰区同报 → ✗ 无 out_of_zone 注记", _s17),
    Scenario("F1", "A 3.1", "T11", "3730.ab1 染料肩峰误判「疑似混合样品」", _f1),
    Scenario("F2", "A 3.1", "T11", "3100.ab1 信号衰减段 61 处误判混合", _f2),
    Scenario("F3", "A 1.3", "T24", "310.ab1 无 PCON 被拒（峰图可用）", _f3),
    Scenario("M1", "A 4.5", "T25", "同密码子双替换注释 K2*/K2R 而非终止", _m1),
    Scenario("M2", "A 3.4 / B P1-6", "T14", "支持 read 上传顺序改变证据归属", _m2),
    Scenario("M3", "A 4.4", "T21", "重叠区高质量分歧无报告、共识静默取胜者", _m3),
    Scenario("M4", "A 4.3", "T16", "ORF 校正改写标注 128bp 无边界", _m4),
    Scenario("M5", "A 1.5", "T17", "引物期望位置/方向无校验", _m5),
    Scenario("M6", "A 2.2", "T9", "软剪切+poly 三处窗口不一致", _m6),
    Scenario("M7", "A 3.7", "T24", "3100 第 609 位 secondary_base=None", _m7),
    Scenario("M8", "A 1.4", "T24", "PLOC −1 峰窗中心系统性偏移", _m8),
    Scenario("M9", "A 2.4", "T25", "核心 analyze() 默认线性拓扑", _m9),
    Scenario("M10", "B P1-12", "T9", "反向软剪切时裁决行引物内坐标错位", _m10),
    Scenario("M11", "B P1-13", "T18", "_precheck 假字节放行损坏参考", _m11),
    Scenario("M12", "B P2-18", "T24", "PLOC 短于 PBAS 静默截短", _m12),
    Scenario("M13", "B P1-8", "T2", "verdict/resolved_regions 未结构化下发", _s1),
    Scenario("M14", "B P2-16", "T25", "参考末端插入单 low 票即定插", _m14),
    Scenario("M15", "B P2-17", "T25", "计划酶位附近未决 indel 无提示", _m15),
    # ---- goal v3 §4（W0 真值先行；task = 使其转绿的工作项）----
    Scenario("SC1", "goal v3 §4.1", "W5", "scope=GeneX 全覆盖 → pass + headline + 元件未评估", _sc1),
    Scenario("SC2", "goal v3 §4.1", "W10", "文件名命中 → filename_match scope", _sc2),
    Scenario("SC3", "goal v3 §4.1", "W5", "未声明范围 → 已测区段口径，禁「构建与设计一致」", _sc3),
    Scenario("SC4", "goal v3 §4.1", "W5", "无 CDS 参考只测 1/3 → 同 SC3（RV P0-1）", _sc4),
    Scenario("SC5", "goal v3 §4.1", "W6", "scope 尾部引物够不到 → beyond_primer", _sc5),
    Scenario("SC6", "goal v3 §4.1", "W14", "read 中途衰减 → read_shortfall 进复核队列", _sc6),
    Scenario("SC7", "goal v3 §4.1", "W2", "R 外骨架确证差异 → out_of_scope_variant info", _sc7),
    Scenario("SC8", "goal v3 §4.1", "W2", "R 外关键元件确证差异 → pending", _sc8),
    Scenario("SC9", "goal v3 §4.1", "W1", "同一 result 换三种 scope 重算可复现", _sc9),
    Scenario("SC10", "goal v3 §4.1", "W3", "verified 99% 不得 pass 与 blocking 并存（RV P0-2）", _sc10),
    Scenario("SC11", "goal v3 §4.1", "W6", "要求双链而后半段单链 → partial 按 E 归因", _sc11),
    Scenario("CM1", "goal v3 §4.2", "W8", "目的基因模式长 read 超侧翼 → pass（CM P0-A）", _cm1),
    Scenario("CM2", "goal v3 §4.2", "W8", "载体通用引物 read 距 ATG 300bp → pass", _cm2),
    Scenario("CM3", "goal v3 §4.2", "W8", "跨原点 CDS 接合区插入检出（CM P0-B）", _cm3),
    Scenario("CM4", "goal v3 §4.2", "W8", "近原点 CDS 与全质粒+scope 一致", _cm4),
    Scenario("CM5", "goal v3 §4.2", "W8", "cds_mode 与全质粒+scope 结论逐项一致", _cm5),
    Scenario("CM6", "goal v3 §4.2", "W9", "参考名与 read 名指向不同候选 → 歧义", _cm6),
    Scenario("CM7", "goal v3 §4.2", "W9", "嵌套 6xHis 可显式选中；重名报错", _cm7),
    Scenario("CM8", "goal v3 §4.2", "W9", "负链基因替换 → 双坐标正确", _cm8),
    Scenario("AF1", "goal v3 §4.3", "W4", "CDS 内 AF 0.12-0.20 至少 pending（RV P0-3）", _af1),
    Scenario("AF2", "goal v3 §4.3", "W4", "骨架散在双峰 AF 0.15 → △ 不升级混合", _af2),
    Scenario("PR1", "goal v3 §4.4", "W7", "逐 read 引物对应 → 两条都 ok", _pr1),
    Scenario("PR2", "goal v3 §4.4", "W7", "引物无对应 read → 引物无信号", _pr2),
    Scenario("PR3", "goal v3 §4.4", "", "引物距离出窗 → primer_mismatch（T17 既有口径）", _pr3),
]
