# -*- coding: utf-8 -*-
"""indel 规范化（goal v3.1 X4；RB P1-4）

两件事，都在变异层做，不动全库比对打分（历史基线依赖现打分）：

1. **最左归一化**（既有的 Tan 2015 算法，bcftools norm / GATK 同款）：
   同一 indel 在重复/同聚物区可被比对到多个等价位置，不归一化则不同 read
   报出的 anchor 差 1–3bp，按 key 合并时支持数被人为分裂。质粒坐标用**左
   对齐**形式——这是数据库与明细表的规范表示。

2. **HGVS 3′ 右移 + 双坐标**（§3.4 第 1 条）：`coords` 同时记录质粒左对齐
   坐标与 HGVS 3′ 形式，c. 表示按 3′ 规则给出（HGVS 的 indel 位置取重复串
   最右端）。后果注释基于规范化后的结果计算。

为什么两条坐标都要留：质粒构建关心「实际改在哪」（左对齐，稳定可比）；
写进构建记录/送检单时按 HGVS 惯例要 3′ 形式（与 SnapGene/Genious 一致）。
只留一种都会在另一端产生无法解释的 1–3bp 位移。
"""

from typing import Dict


def _rotate_left(seq: str) -> str:
    """左移一位时的序列旋转（等价 indel 表示）"""
    return seq[-1] + seq[:-1]


def _rotate_right(seq: str) -> str:
    """右移一位时的序列旋转（等价 indel 表示）"""
    return seq[1:] + seq[0]


def normalize_left(ref: str, v: Dict) -> Dict:
    """最左归一化（Tan 2015）。替换变体原样返回。"""
    if v.get("type") == "insertion":
        seq = v["alt_base"].upper()
        pos = int(v["ref_pos"])  # anchor：插入点左侧参考位置（1-based）
        while pos >= 1 and pos < len(ref) and ref[pos - 1] == seq[-1]:
            seq = _rotate_left(seq)
            pos -= 1
        return {**v, "ref_pos": pos, "alt_base": seq}
    if v.get("type") == "deletion":
        pos = int(v["ref_pos"])
        length = int(v.get("length") or 1)
        seq = ref[pos - 1:pos - 1 + length]
        while pos >= 2 and ref[pos - 2] == seq[-1]:
            seq = _rotate_left(seq)
            pos -= 1
        return {**v, "ref_pos": pos}
    return v


def normalize_right(ref: str, v: Dict) -> Dict:
    """HGVS 3′ 右移：把 indel 推到等价位置的最右端（用于 c. 表示）。

    替换变体原样返回。返回的 `ref_pos` / `alt_base` 是右对齐形式。
    """
    if v.get("type") == "insertion":
        seq = (v.get("alt_base") or "").upper()
        pos = int(v.get("ref_pos") or 0)
        n = len(ref)
        if not seq or n == 0:
            return v
        steps = 0
        # 右移一位等价于：插入点右移、序列右旋一位；条件是该位点右邻 == 序列首位
        while steps < n and pos < n and ref[pos] == seq[0]:
            seq = _rotate_right(seq)
            pos += 1
            steps += 1
        return {**v, "ref_pos": pos, "alt_base": seq}
    if v.get("type") == "deletion":
        pos = int(v.get("ref_pos") or 0)
        length = int(v.get("length") or 1)
        n = len(ref)
        if length <= 0 or n == 0:
            return v
        steps = 0
        while steps < n:
            # 删除区间 [pos, pos+length-1] 右移一位：区间末位的右邻 == 区间首位
            first = ref[(pos - 1) % n]
            nxt = ref[(pos - 1 + length) % n]
            if first != nxt:
                break
            pos += 1
            steps += 1
        return {**v, "ref_pos": pos}
    return v


def indel_coords(ref: str, v: Dict, *, circular: bool = False,
                 to_cds=None) -> Dict:
    """规范化后的坐标块：质粒左对齐 + HGVS 3′ + c. 表示。

    `to_cds(plasmid_pos) -> Optional[Dict]` 由调用方给出（c. 坐标换算只在
    一处，防回潮约束 9）；缺省时不产出 c. 字段。`circular` 只影响 3′ 右移
    是否允许越过原点。
    """
    left = v
    right = normalize_right(ref, v)
    coords: Dict = {
        "plasmid": int(left.get("ref_pos") or 0),      # 左对齐（规范表示）
        "plasmid_3p": int(right.get("ref_pos") or 0),  # HGVS 3′ 形式
    }
    if left.get("type") in ("insertion", "deletion"):
        coords["indel_type"] = left["type"]
        coords["length"] = int(left.get("length") or 1)
    if to_cds is not None:
        left_c = to_cds(int(left.get("ref_pos") or 0))
        if left_c:
            coords["cds"] = left_c
            if left.get("type") in ("insertion", "deletion"):
                # c. 用 3′ 形式（HGVS 惯例），质粒坐标给左对齐
                right_c = to_cds(int(right.get("ref_pos") or 0))
                if right_c and right_c.get("c"):
                    coords["cds"]["c_3p"] = right_c.get("c")
    return coords


def consequence_text(variant: Dict) -> str:
    """变异的后果注释（HGVS 语义的中文表述，goal v3.1 X4 §3.4 第 3 条）。

    基于**规范化后**的结果回答「这个变异对产物做了什么」。取值顺序按证据
    强度：移码 > 氨基酸改变 > 同义 > 编码区内但无蛋白变化 > 非编码区。
    indel 的长度决定移码与否（3 的倍数不改变阅读框）。
    """
    if variant.get("frameshift"):
        n = int(variant.get("length") or 1)
        kind = {"insertion": "插入", "deletion": "缺失"}.get(
            variant.get("type"), variant.get("type") or "")
        return f"{kind} {n}bp 导致移码"
    if variant.get("aa_change"):
        return f"氨基酸改变 {variant['aa_change']}"
    if variant.get("synonymous"):
        return "同义突变（蛋白不变）"
    loc = "、".join(f"{f['name']}({f['type']})" for f in variant.get("features") or [])
    return f"{loc} 内变异" if loc else "非编码区变异"
