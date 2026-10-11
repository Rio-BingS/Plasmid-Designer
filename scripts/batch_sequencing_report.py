"""批量测序结果自动整理与分析（离线批处理，复用平台 Sanger 管线）

用法：
  python scripts/batch_sequencing_report.py --data-dir "<测序结果文件夹>" \
      [--excel "<信息表.xlsx>"] [--out-dir "<输出目录>"] [--min-q 20] [--dry-run]

流程：
  1. 读取 Excel 信息表（表头含"质粒名称/测序引物/测序结果"，引物列填测序文件名，
     多个用分号分隔，与平台网页端"参考文件与测序文件同目录导入"的约定一致）；
  2. 递归扫描数据文件夹中的 .ab1（测序 read）与 .dna/.gb/.fasta（参考图谱），
     按文件名主干与 Excel 引物列 / 质粒名称匹配；
  3. 逐质粒运行 Sanger 全自动分析管线（峰级证据、置信度分层、CDS 结论、
     poly 判读、双峰疑似混合检测）；
  4. 在输出目录写出与网页端「整理包」完全相同的结构：每个质粒一个文件夹
     （参考图谱在其根），其下 正确/ 错误/ 无法判定/ 按结论分置 read 副本、
     测序分析报告.md 与 分析结果.json；未匹配文件副本在 未匹配文件/；另有
     整理清单.csv（归档路径 + MD5 + 原始路径）与 批次总览.txt。原始文件
     一律只读，不移动、不删除、不覆盖；
  5. 结论回填 Excel 的"测序结果"列（原表先备份到输出目录）。

归组、分析、报告与归档条目全部复用网页端实现（app/sequencing_batch.py、
app/sequencing_report.py），本脚本只负责本地扫描、落盘与 Excel 回填，
两端输出不会再各自漂移。
"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "src" / "backend"
if (BACKEND / "core").is_dir():
    sys.path.insert(0, str(BACKEND))

from app.sequencing_batch import (  # noqa: E402
    batch_payload,
    group_batch_uploads,
    plasmid_groups,
    run_batch_groups,
)
from app.sequencing_report import backfill_excel, batch_archive_entries  # noqa: E402
# excel_conclusion / match_files 不在本脚本内直接使用，保留为模块属性以兼容
# 既有 `import batch_sequencing_report` 的调用方
from core.sanger.batch import (  # noqa: E402,F401
    READ_EXTS,
    REF_EXTS,
    excel_conclusion,
    load_excel,
    match_files,
    norm_stem as _norm,
    rows_have_clones,
)


# ---------------------------------------------------------------- 文件扫描


def scan_files(data_dir: Path, out_dir: Path):
    """递归扫描 read 与图谱（跳过输出目录与 Excel 临时文件）

    条目与网页端上传条目同形（name/ext/stem），另带 path/rel 供追溯；
    字节由 load_bytes 在真正分析前读入（--dry-run 不读）。"""
    files = []
    for p in sorted(data_dir.rglob("*")):
        if not p.is_file() or p.name.startswith("~$"):
            continue
        if out_dir == p or out_dir in p.parents:
            continue
        ext = p.suffix.lower()
        if ext in READ_EXTS or ext in REF_EXTS:
            files.append({"path": p, "rel": p.relative_to(data_dir), "name": p.name,
                          "ext": ext, "stem": _norm(p.stem)})
    return files


def load_bytes(files: List[Dict]) -> None:
    for f in files:
        if "bytes" not in f:
            f["bytes"] = f["path"].read_bytes()


# ---------------------------------------------------------------- 分析（复用网页端流程）


def run_offline_batch(files: List[Dict], rows: List[Dict], min_q: int = 20):
    """离线批次的核心：归组 → 逐组分析 → 整理包条目（与网页端同一实现）

    files 为 scan_files 条目（需已 load_bytes）；rows 为 load_excel 的行
    （质粒模式）。返回 (payload, entries)：payload 同网页端点响应体
    （items 即各组结论），entries 为整理包条目 [(相对输出目录的路径, 内容)]。
    """
    reads = [f for f in files if f["ext"] in READ_EXTS]
    refs = [f for f in files if f["ext"] not in READ_EXTS]
    ordered, per_plasmid, unmatched = group_batch_uploads(reads, refs, rows)
    groups = plasmid_groups(ordered, per_plasmid)

    seq = iter(range(1, 1 << 30))

    def _register(label: str, ref_seq: str, features: List[Dict], res: Dict):
        # 本地记录与网页端分析记录同形（去掉峰图原始数据），供报告与 JSON
        aid = f"local_{next(seq):03d}"
        rec = {"analysis_id": aid, "sample_name": label,
               "reference": ref_seq.upper(), "features": features,
               "created_at": datetime.now().isoformat(timespec="seconds"),
               **{k: v for k, v in res.items() if k != "traces"}}
        return aid, rec

    # 离线不设参考长度上限（网页端的 MAX_REF_BP 是服务器资源防护）
    items, records = run_batch_groups(groups, min_q, _register, max_ref_bp=None)
    payload = batch_payload(items, unmatched, min_q, excel_mode=True,
                            clone_mode=False, ignored=[])
    entries = batch_archive_entries(payload, groups, unmatched, None, None, None,
                                    records, origin="offline", source_paths=True,
                                    root="")
    return payload, entries


# ---------------------------------------------------------------- 落盘（原件只读、副本不覆盖异内容）


_GENERATED = {".md", ".json", ".csv", ".txt"}


def write_archive(entries: List[Tuple[str, Optional[object]]], out_dir: Path) -> List[Path]:
    """把整理包条目写到输出目录，返回写出的文件路径

    重跑时内容相同的文件跳过；报告/JSON/清单等生成物直接更新；同名但内容
    不同的数据文件（.ab1/图谱副本）永不覆盖，改加序号另存并提示。"""
    written: List[Path] = []
    for arc, data in entries:
        dst = out_dir.joinpath(*[p for p in arc.split("/") if p])
        if data is None:
            dst.mkdir(parents=True, exist_ok=True)
            continue
        blob = data.encode("utf-8") if isinstance(data, str) else data
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.suffix.lower() not in _GENERATED:
            if dst.read_bytes() == blob:
                written.append(dst)
                continue
            n = 2
            alt = dst
            while alt.exists():
                alt = dst.with_name(f"{dst.stem} ({n}){dst.suffix}")
                n += 1
            print(f"! 输出目录已有同名不同内容的 {dst.name}，副本另存为 {alt.name}")
            dst = alt
        dst.write_bytes(blob)
        written.append(dst)
    return written


# ---------------------------------------------------------------- 主流程


def main():
    ap = argparse.ArgumentParser(description="批量测序结果自动整理与分析")
    ap.add_argument("--data-dir", required=True, help="测序结果文件夹（含 .ab1/.dna 与信息表）")
    ap.add_argument("--excel", help="信息表路径（默认取文件夹下第一个 .xlsx）")
    ap.add_argument("--out-dir", help="输出目录（默认 <data-dir>/测序分析）")
    ap.add_argument("--min-q", type=int, default=20, help="修剪 Q 阈值（默认 20）")
    ap.add_argument("--dry-run", action="store_true", help="只匹配与预览，不复制、不分析、不回填")
    ap.add_argument("--no-write-back", action="store_true", help="不回填 Excel 结论")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.is_dir():
        raise SystemExit(f"数据文件夹不存在：{data_dir}")
    excel_path = Path(args.excel).resolve() if args.excel else next(
        (p for p in sorted(data_dir.glob("*.xlsx")) if not p.name.startswith("~$")), None)
    if excel_path is None:
        raise SystemExit(f"数据文件夹下找不到 Excel 信息表（.xlsx）：{data_dir}")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else data_dir / "测序分析"

    try:
        wb, header_row, cols, rows = load_excel(excel_path)
    except ValueError as e:
        raise SystemExit(f"{e}：{excel_path}")
    print(f"信息表：{excel_path.name}（{len(rows)} 个质粒）")
    if rows_have_clones(rows):
        raise SystemExit(
            "信息表带「克隆号」列（克隆模式，每个克隆独立分析）："
            "请使用网页端批量测序分析（/batch-sequencing）；"
            "本脚本目前仅支持无克隆号列的质粒模式信息表"
        )
    files = scan_files(data_dir, out_dir)
    n_ab1 = sum(1 for f in files if f["ext"] in READ_EXTS)
    print(f"扫描到 {len(files)} 个文件（{n_ab1} 个 .ab1，{len(files) - n_ab1} 个图谱）")

    if args.dry_run:
        reads = [f for f in files if f["ext"] in READ_EXTS]
        refs = [f for f in files if f["ext"] not in READ_EXTS]
        _ordered, per_plasmid, unmatched = group_batch_uploads(reads, refs, rows)
        for name, slot in per_plasmid.items():
            ref = slot["reference"]["file"]["path"].name if slot["reference"] else "（缺）"
            print(f"[预览] {name}: 图谱={ref}, reads={[r['file']['path'].name for r in slot['reads']]}")
        for u in unmatched:
            print(f"[未匹配] {u['file']['path'].name}: {u['reason']}")
        return

    load_bytes(files)
    payload, entries = run_offline_batch(files, rows, args.min_q)
    for it in payload["items"]:
        n_var = it["variant_count"] + it["pending_count"]
        print(f"· {it['plasmid']}: {it['read_count']} read、{n_var} 处变异 → "
              f"{it['conclusion'][:60]}…")
    for u in payload["unmatched"]:
        print(f"! 未匹配（原件保留原位，副本归入 未匹配文件/）：{u['filename']} — {u['reason']}")
    out_dir.mkdir(parents=True, exist_ok=True)
    write_archive(entries, out_dir)

    # Excel 备份 + 回填
    if args.no_write_back:
        print("已跳过 Excel 回填（--no-write-back）")
        return
    backup = out_dir / f"{excel_path.stem}_原始备份{excel_path.suffix}"
    n = 2
    while backup.exists():
        backup = out_dir / f"{excel_path.stem}_原始备份({n}){excel_path.suffix}"
        n += 1
    shutil.copy2(excel_path, backup)
    filled = backfill_excel(wb, cols, rows, payload["items"], clone_mode=False)
    if filled is not None:
        excel_path.write_bytes(filled)
    print(f"√ 结论已回填 {excel_path.name}（原表备份：{backup.name}）")
    print(f"√ 输出目录：{out_dir}")


if __name__ == "__main__":
    main()
