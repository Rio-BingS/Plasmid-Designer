"""引物库与方向校验（goal T17；A 1.5）

Sanger read 的比对起点由引物决定：read 应起自引物 3′ 端下游约 20-150bp
（可配置）。此前管线完全不看引物——引物加错/方向搞反/模板换位时 read
比对到别处，差异判读全错却没有信号（A 1.5）。

引物来源（按优先级）：

1. 批量信息表的引物列（batch 侧已归组，此处接收结构化 primer）；
2. 单样品上传接口的可选 `primer` 字段（{name, seq, direction?}）；
3. 可选库 `data/primers/*.yaml`（常用通用引物：T7/T7term/SP6/M13F/M13R/
   CMV-F/BGH-R 等；条目注明来源）。无法确定引物时只记 info「未校验引物」，
   不做任何推断。

校验（能确定引物时）：

- 定位结合位点（链方向取匹配链；多个等长匹配取 first——重复结合的通用
  引物本就常见，此处只校验「与 read 起点的关系」）；
- 方向一致：read 比对方向与引物链方向相反 → primer_mismatch（fail）；
- 距离：比对起点（+ 链 read 的 ref_start / − 链 read 的 ref_end）与引物
  3′ 端的距离须落在 [MIN_3DIST, MAX_3DIST]（默认 20-150，可配置）——
  否则 primer_mismatch（默认 fail；pending 可由调用方配置，T22 对接
  设计记录后收窄）。
"""

import re
from pathlib import Path
from typing import Dict, List, Optional

from core.seq_utils import revcomp as _seq_revcomp

# 引物 3′ 端到 read 比对起点的合理距离（Sanger 工作液读长爬升区）
MIN_3DIST = 20
MAX_3DIST = 150

LIB_DIR = Path(__file__).resolve().parents[4] / "data" / "primers"
# 相对 core/sanger/ 上溯四级 = 项目根的 data/primers


def library_primers() -> Dict[str, Dict]:
    """可选引物库 data/primers/*.yaml：{名称小写: {seq, direction, source}}。

    文件/解析库缺席时返回空表（引物库是可选增强，不是运行时依赖）。"""
    out: Dict[str, Dict] = {}
    if not LIB_DIR.is_dir():
        return out
    try:
        import yaml  # PyYAML 为既有依赖（deploy/config 使用）
    except ImportError:
        return out
    for p in sorted(LIB_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 库文件损坏不阻塞分析
            continue
        for name, item in (data.get("primers") or {}).items():
            if isinstance(item, dict) and item.get("seq"):
                out[str(name).lower()] = {
                    "name": str(name), "seq": str(item["seq"]).upper(),
                    "direction": item.get("direction") or "+",
                    "source": item.get("source") or f"data/primers/{p.name}",
                }
    return out


def resolve_primer(primer: Optional[Dict]) -> Optional[Dict]:
    """补全引物信息：无 seq 时按 name 查库；仍无法确定 → None（记 info）"""
    if not primer or not isinstance(primer, dict):
        return None
    seq = (primer.get("seq") or "").upper()
    if not seq and primer.get("name"):
        lib = library_primers().get(str(primer["name"]).lower())
        if lib:
            return {**lib, **{k: v for k, v in primer.items() if v}}
        return None
    if not re.fullmatch(r"[ACGTNRYSWKMBDHV]+", seq or ""):
        return None
    return {"name": primer.get("name") or "custom",
            "seq": seq,
            "direction": primer.get("direction") or "+",
            "source": primer.get("source") or "upload"}


def locate_binding(primer_seq: str, ref: str) -> Optional[Dict]:
    """引物结合位点（参考坐标 1-based 闭区间）。

    正链精确匹配优先，其次负链（revcomp）；多命中取首个（通用引物重复
    结合常见，校验只关心与 read 起点的距离关系）。找不到（模板换位/
    序列不匹配）→ None。"""
    if not primer_seq or len(primer_seq) > len(ref):
        return None
    idx = ref.find(primer_seq)
    if idx >= 0:
        return {"start": idx + 1, "end": idx + len(primer_seq), "strand": "+"}
    rc = _seq_revcomp(primer_seq)
    idx = ref.find(rc)
    if idx >= 0:
        return {"start": idx + 1, "end": idx + len(rc), "strand": "-"}
    return None


def check_reads(primer: Optional[Dict], read_results: List[Dict],
                ref: str, min_dist: int = MIN_3DIST,
                max_dist: int = MAX_3DIST) -> List[Dict]:
    """逐 read 校验（goal T17）。返回 [{read, status, detail}]：

    - unverified：无法确定引物（info「未校验引物」，不推断）；
    - mismatch：结合位点找不到 / 方向不一致 / 距离出窗（默认 fail）；
    - ok：方向一致且距离在窗内。
    """
    resolved = resolve_primer(primer)
    if resolved is None:
        return [{"read": r["filename"], "status": "unverified",
                 "detail": "未校验引物（信息表/上传参数未提供可用引物）"}
                for r in read_results]
    binding = locate_binding(resolved["seq"], ref)
    if binding is None:
        return [{"read": r["filename"], "status": "mismatch",
                 "detail": (f"引物 {resolved['name']} 序列在参考上无结合位点"
                            "——模板可能换位或引物加错"),
                 "primer": resolved["name"]} for r in read_results]
    # 引物 3′ 端：+ 链 = 结合区末端；− 链 = 结合区起点
    three_end = binding["end"] if binding["strand"] == "+" else binding["start"]
    out: List[Dict] = []
    for r in read_results:
        aln = r.get("alignment") or {}
        rs, re_ = int(aln.get("ref_start") or 0), int(aln.get("ref_end") or 0)
        direction = aln.get("direction") or "+"
        name = r["filename"]
        if direction != binding["strand"]:
            out.append({"read": name, "status": "mismatch",
                        "primer": resolved["name"],
                        "detail": (f"引物 {resolved['name']} 为 {binding['strand']} 链，"
                                   f"read 比对方向为 {direction}——方向不一致")})
            continue
        # read 比对起点：+ 链 = ref_start；− 链 = ref_end（5′ 起点在参考低坐标侧）
        read_start = rs if direction == "+" else re_
        dist = (read_start - three_end) if direction == "+" \
            else (three_end - read_start)
        if not (min_dist <= dist <= max_dist):
            out.append({"read": name, "status": "mismatch",
                        "primer": resolved["name"],
                        "detail": (f"read 比对起点距引物 {resolved['name']} 3′ 端 "
                                   f"{dist}bp（合理区间 {min_dist}-{max_dist}bp）"
                                   "——引物与 read 来源可能不匹配")})
            continue
        out.append({"read": name, "status": "ok", "primer": resolved["name"],
                    "detail": f"引物 {resolved['name']} 方向与距离校验通过"
                              f"（3′ 端下游 {dist}bp）"})
    return out
