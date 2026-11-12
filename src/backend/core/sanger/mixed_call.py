"""单点双峰次等位调用（goal T7；A 3.2；B P2-20）

双峰位点（管线 mixed_detail：已排除 pull-up、真实滑移、合并压缩区、
可信区外）经 ReadRefMap 映射到参考；{called_base, secondary_base} 折回
参考链后含非参考碱基 → MixedVariant（反向 read 取互补）。AF（次等位
丰度）= 峰面积比 r/(1+r)——ratio 是次级/主峰面积比，次等位占两者之和的
份额（S2/S3 契约：35% 混合 → AF 0.35）。

verdict 侧再按区域与 AF 分流：AF ≥ MINOR_AF_MIN 且在 ROI/CDS/关键元件/
接合区 → mixed_variant 阻断（status mixed）；其余 info。P0 阶段沿用
管线现有 30% 双峰判定（MIXED_PEAK_RATIO），T11 后改自适应模型输出。
"""

from typing import Dict, List

from core.sanger.readcall import in_mixed_segment
from core.sanger.readmap import ReadRefMap

# 次等位丰度门槛：低于此的双峰只记 info（噪声级或极低频亚群）
MINOR_AF_MIN = 0.15
# goal v3 W4 af 分带（RV P0-3）：≥MIXED_AF_MIN 判疑似混合（mixed）；
# [MINOR_AF_MIN, MIXED_AF_MIN) 判 minor_af_site（pending 复核项——丰度不足
# 判混但不得静默）；[MINOR_AF_SOFT, MINOR_AF_MIN) 软检出带（模型地板 0.18
# 之下、soft_ratio_floor=0.13 之上；0.13 对应 af 0.115，与分带下限一致）
# 仅在声明范围×功能区出 pending，否则 info
MINOR_AF_SOFT = 0.115
MIXED_AF_MIN = 0.23

_COMP = str.maketrans("ACGTN", "TGCAN")


def collect_mixed_variants(read_results: List[Dict], ref_seq: str) -> List[Dict]:
    """从各 read 的 mixed_detail 收集参考链口径的混合位点记录。

    返回按 ref_pos 合并的 [{ref_pos, ref_base, called_base, secondary_base,
    af, reads}]：同一参考位点多条 read 同报时取最大 AF 并合并 read 名
    （阻断按位点一条）；两条 read 报的次等位不同时以 AF 大者为主记录，
    另一条的碱基并入 secondary_base 备查。两个等位折回参考链后都是参考
    碱基的位点（纯噪声双峰）不收。"""
    by_pos: Dict[int, Dict] = {}
    for r in read_results:
        rmap = ReadRefMap.from_alignment(r)
        if not rmap.ref2call:
            continue
        bases = (r.get("trimmed_bases") or "").upper()
        reverse = rmap.direction == "-"
        for e in (list(r.get("mixed_detail") or [])
                  + list(r.get("mixed_soft") or [])):
            if in_mixed_segment(r.get("read_mixing"), e.get("pos")):
                continue  # 二期：套峰段由 read 级 mixed_template 一条报出，不逐位记
            rp = rmap.read_to_ref(e.get("pos"))
            if not rp:
                continue  # 插入列/无映射列：无参考位点可判
            rp = int(rp)
            if not (1 <= rp <= len(ref_seq)):
                continue
            ref_b = ref_seq[rp - 1].upper()
            i = e["pos"] - 1
            called = bases[i] if 0 <= i < len(bases) else ""
            sec = (e.get("secondary_base") or "").upper()
            if reverse:
                called, sec = called.translate(_COMP), sec.translate(_COMP)
            if not ({b for b in (called, sec) if b in "ACGT"} - {ref_b}):
                continue  # 两等位都是参考碱基：噪声双峰，不是变异
            af = round(float(e.get("ratio") or 0) / (1 + float(e.get("ratio") or 0)), 3)
            ent = by_pos.setdefault(rp, {
                "ref_pos": rp, "ref_base": ref_b, "called_base": called,
                "secondary_base": sec, "af": af, "reads": [],
            })
            if af > ent["af"]:
                ent.update({"called_base": called, "secondary_base": sec, "af": af})
            elif af == ent["af"] and sec and sec != ent["secondary_base"]:
                ent["secondary_base"] = f"{ent['secondary_base']}/{sec}"
            if r["filename"] not in ent["reads"]:
                ent["reads"].append(r["filename"])
            ent.setdefault("read_pos", {}).setdefault(r["filename"], int(e["pos"]))
    return [by_pos[k] for k in sorted(by_pos)]
