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


SCENARIOS: List[Scenario] = [
    Scenario("S1", "A 1.1 / B P0-1", "T12", "尾端灰区 Q50 错义被强制 low → ✓", _s1),
    Scenario("S2", "A 3.2", "T7", "CDS 单点 35% 混合静默 ✓", _s2),
    Scenario("S3", "A 3.2", "T7", "CDS 单点 50:50 混合静默 ✓", _s3),
    Scenario("S4", "A 3.2", "T7", "65% 突变为主峰 → 低置信伪影 ✓", _s4),
    Scenario("S5", "A 2.1", "T6", "read 5' 端 175bp 连接错误被软剪切吞掉", _s5),
    Scenario("S6", "A 2.1", "T6", "read 3' 端 188bp 差异被软剪切吞掉", _s6),
    Scenario("S7", "A 4.1 / A 5.5", "T5", "启动子确证突变网页✗/Excel 合格/归档正确", _s7),
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
]
