"""批量测序结果归组与一句话结论（网页端批量分析与离线脚本共用）

测序公司交付常为「Excel 信息表 + 一堆 .ab1/.dna」：信息表的引物列填测序
文件名（可多个，分号分隔），质粒名称列对应参考图谱文件名主干。

本模块只做与入口无关的纯逻辑：
- 解析 Excel 信息表（路径或字节流均可）；
- 按引物列/质粒名称把扫描到的文件归组到各质粒；
- 从单质粒分析结果生成一句话结论。

文件整理（复制归档、MD5 清单）、报告输出与 Excel 回填属于各入口自身
的流程：离线批处理见 scripts/batch_sequencing_report.py，网页批量分析见
POST /api/sequencing/analyze-batch。
"""

import io
import re
from typing import Dict, List, Tuple, Union

READ_EXTS = {".ab1", ".ab"}
REF_EXTS = {".dna", ".gb", ".gbk", ".genbank", ".fasta", ".fa", ".fna"}
PRIMER_SPLIT = re.compile(r"[;；,，\n]+")


def norm_stem(name) -> str:
    """文件名/条目归一化：去空白、小写、去 .ab1 后缀，用于匹配。"""
    t = re.sub(r"\s+", "", str(name)).lower()
    for suf in (".ab1", ".ab"):
        if t.endswith(suf):
            t = t[: -len(suf)]
    return t.strip('"')


def load_excel(src: Union[str, "Path", bytes, bytearray]):
    """解析信息表，返回 (workbook, header_row, {列名: 列号}, [数据行])

    src：文件路径（脚本离线用）或字节流（网页上传用）。
    表头行取第一个含"质粒"的单元格所在行；找不到抛 ValueError。
    """
    import openpyxl

    if isinstance(src, (bytes, bytearray)):
        wb = openpyxl.load_workbook(io.BytesIO(src))
    else:
        wb = openpyxl.load_workbook(src)
    ws = wb.worksheets[0]
    header_row, cols = None, {}
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 20)):
        for c in row:
            if c.value and "质粒" in str(c.value):
                header_row = c.row
                break
        if header_row:
            break
    if header_row is None:
        raise ValueError("Excel 中找不到含'质粒'的表头行")
    for c in ws[header_row]:
        v = str(c.value or "")
        if "质粒" in v:
            cols["plasmid"] = c.column
        elif "引物" in v:
            cols["primer"] = c.column
        elif "结果" in v:
            cols["result"] = c.column
    cols.setdefault("primer", cols["plasmid"] + 1)
    cols.setdefault("result", cols["primer"] + 1)

    rows = []
    for r in range(header_row + 1, ws.max_row + 1):
        name = ws.cell(row=r, column=cols["plasmid"]).value
        prim = ws.cell(row=r, column=cols["primer"]).value
        if name is None and prim is None:
            continue
        entries = [p.strip() for p in PRIMER_SPLIT.split(str(prim or "")) if p.strip()]
        rows.append({
            "row": r,
            "name": str(name or "").strip(),
            "primers": entries,
            "existing_result": ws.cell(row=r, column=cols["result"]).value,
        })
    return wb, header_row, cols, rows


def match_files(rows: List[Dict], files: List[Dict]):
    """按 Excel 引物列（=测序文件名）与质粒名称（=图谱文件名）匹配。

    files 的每项为 {"ext": ".ab1"/".dna"/…, "stem": 归一化主干, "name"(可选):
    显示文件名, "path"(可选): 离线脚本场景的完整路径}——至少有其一。

    返回 (per_plasmid, unmatched)。per_plasmid: 质粒名 -> {"reference":…, "reads":[…]}
    每个文件项为 {"file": 原文件项, "how": 匹配方式说明}
    """

    def fname(f: Dict) -> str:
        return f.get("name") or str(f["path"].name)

    primer_map = {}  # 归一化文件名主干 -> (质粒名, 匹配方式)
    for row in rows:
        for e in row["primers"]:
            primer_map.setdefault(norm_stem(e), (row["name"], "Excel 引物列精确匹配"))

    per_plasmid: Dict[str, Dict] = {row["name"]: {"reference": None, "reads": []} for row in rows}
    unmatched: List[Dict] = []

    for f in files:
        if f["ext"] in READ_EXTS:
            hit = primer_map.get(f["stem"])
            if hit is None:
                cands = {(p, how) for stem, (p, how) in primer_map.items() if stem in f["stem"]}
                if len(cands) == 1:
                    hit = next(iter(cands))
                    hit = (hit[0], f"文件名包含引物条目（{f['stem']}）")
            if hit is None:
                unmatched.append({"file": f, "reason": "未出现在任何质粒的引物列中"})
                continue
            per_plasmid.setdefault(hit[0], {"reference": None, "reads": []})["reads"].append(
                {"file": f, "how": hit[1]}
            )
        else:  # 图谱文件 → 按质粒名称匹配
            names = {norm_stem(r["name"]): r["name"] for r in rows if r["name"]}
            if f["stem"] in names:
                plasmid = names[f["stem"]]
                how = "文件名与质粒名称精确匹配"
            else:
                cands = {n for n in names if n and n in f["stem"]}
                if len(cands) != 1:
                    unmatched.append({"file": f, "reason": "文件名不匹配任何质粒名称"})
                    continue
                plasmid = names[cands.pop()]
                how = "文件名包含质粒名称"
            slot = per_plasmid.setdefault(plasmid, {"reference": None, "reads": []})
            if slot["reference"] is not None:
                unmatched.append({"file": f,
                                  "reason": f"质粒 {plasmid} 已有图谱 {fname(slot['reference']['file'])}，本文件未采用"})
                continue
            slot["reference"] = {"file": f, "how": how}
    return per_plasmid, unmatched


def main_sentence(verdict: str) -> str:
    """从 CDS verdict 中取核心主句（去掉覆盖度前缀与待复核尾句）。"""
    seg = verdict.split("。")
    return seg[1] if len(seg) > 1 else verdict


def excel_conclusion(plasmid: str, res, n_reads: int, has_ref: bool) -> str:
    """一句话结论（网页结果表与离线 Excel 回填共用同一口径）。"""
    if not has_ref:
        return f"已整理 {n_reads} 个测序文件，但缺少同名参考图谱（.dna/.gb/.fasta），无法自动比对"
    if res is None:
        return "分析失败"
    if not res["reads"]:
        errs = "；".join(e.get("error", "") for e in res["errors"][:2]) or "无可分析 read"
        return f"分析失败：{errs}"
    confirmed = [v for v in res["variants"] if v.get("confidence") != "low"]
    pending = len(res["variants"]) - len(confirmed)
    cov = res["consensus"]["coverage_percent"]
    bad = [c for c in res["cds_reports"]
           if c["coverage_status"] != "uncovered" and c["protein_identical"] is False]
    tail = []
    if pending:
        tail.append(f"{pending} 处低置信差异疑似测序噪声（详见报告）")
    # A4：poly 区峰图计数与碱基调用不一致——此前批量 Excel 丢掉这条关键告警
    hp_bad = [e for e in (res.get("homopolymers") or [])
              if e.get("tier", "poly") == "poly" and e.get("count_reliable") is False]
    if hp_bad:
        tail.append("poly 区峰图计数与碱基调用不一致（重复数存疑，详见报告）")
    if cov < 95:
        tail.append(f"测序覆盖 {cov:.0f}%")

    if bad:
        body = "；".join(f"[{c['name']}] {main_sentence(c['verdict'])}" for c in bad[:2])
        if len(bad) > 2:
            body += f"（另有 {len(bad) - 2} 个 CDS 不一致）"
        out = f"不合格：{body}"
    elif confirmed:
        feats = "、".join(sorted({f["name"] for v in confirmed for f in v.get("features", [])})) or "无特征区"
        out = (f"合格：编码区蛋白与设计一致；编码区外有 {len(confirmed)} 处确证差异"
               f"（位于 {feats}），不影响编码产物")
    else:
        out = "合格：与设计一致"
    if tail:
        out += "；" + "；".join(tail)
    return out
