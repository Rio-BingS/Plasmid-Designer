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
     整理清单.csv（实际写出路径 + MD5 + 原始路径）与 批次总览.txt。原始文件
     一律只读，不移动、不删除、不覆盖；
     重跑到同一输出目录时，按上次的整理记录（.整理记录.json，旧目录退回
     整理清单.csv）清理本次不再产出的旧副本（如判定变化后旧结论文件夹里的
     read 与报告），只删内容仍与当时写出一致的文件，用户改过或记录之外的
     文件不动；--dry-run 只打印将清理的文件；
  5. 结论回填 Excel 的"测序结果"列（原表先备份到输出目录）。

归组、分析、报告与归档条目全部复用网页端实现（app/sequencing_batch.py、
app/sequencing_report.py），本脚本只负责本地扫描、落盘与 Excel 回填，
两端输出不会再各自漂移。
"""

import argparse
import csv
import hashlib
import io
import json
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

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
MANIFEST_CSV = "整理清单.csv"
# 本工具每次写出的全部文件（相对输出目录的路径 → MD5），供重跑时识别并
# 清理上一次生成、本次已不再产出的旧副本（如判定变化后留在旧结论文件夹的）
RECORD_NAME = ".整理记录.json"
_KEEP_DIRS = {"正确", "错误"}


def _md5(blob: bytes) -> str:
    return hashlib.md5(blob).hexdigest()


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def load_previous_outputs(out_dir: Path) -> Dict[str, str]:
    """上一次运行由本工具写出的文件 {相对路径: MD5}

    优先读 RECORD_NAME（覆盖报告/JSON 等全部生成物）；没有时（旧版本跑出的
    目录）退回 整理清单.csv 的「归档路径 + MD5」，只能覆盖数据文件副本。
    记录缺失或损坏时返回空，即不清理任何东西。"""
    rec = out_dir / RECORD_NAME
    if rec.is_file():
        try:
            data = json.loads(rec.read_text(encoding="utf-8"))
            files = data.get("files") if isinstance(data, dict) else None
            if isinstance(files, dict):
                return {str(k): str(v) for k, v in files.items() if k and v}
        except (OSError, ValueError):
            pass
        return {}
    man = out_dir / MANIFEST_CSV
    if not man.is_file():
        return {}
    try:
        rows = list(csv.DictReader(io.StringIO(man.read_text(encoding="utf-8-sig"))))
    except (OSError, ValueError, csv.Error):
        return {}
    return {r["归档路径"]: r["MD5"] for r in rows
            if r.get("归档路径") and r.get("MD5")}


def _owned_unmodified(dst: Path, out_dir: Path, previous: Dict[str, str],
                      protected: Set[Path]) -> bool:
    """dst 是上次由本工具写出且之后未被改动的文件（可安全替换/删除）"""
    rel = dst.relative_to(out_dir).as_posix()
    md5 = previous.get(rel)
    if not md5 or dst.is_symlink() or not dst.is_file():
        return False
    if not _within(dst, out_dir) or dst.resolve() in protected:
        return False
    try:
        return _md5(dst.read_bytes()) == md5
    except OSError:
        return False


@dataclass
class ArchivePlan:
    dirs: List[Path] = field(default_factory=list)
    writes: List[Tuple[Path, bytes]] = field(default_factory=list)   # 需写出
    unchanged: List[Path] = field(default_factory=list)              # 已是同内容
    renamed: List[Tuple[str, str]] = field(default_factory=list)     # 归档路径 → 实际路径
    remove: List[Path] = field(default_factory=list)                 # 上次生成的旧副本
    kept: List[Path] = field(default_factory=list)                   # 已不再产出但被改过，保留
    record: Dict[str, str] = field(default_factory=dict)             # 本次全部文件 → MD5


def _fix_manifest_paths(text: str, mapping: Dict[str, str]) -> str:
    """把整理清单.csv 的「归档路径」改成实际写出的路径（同名异内容加序号时）"""
    body = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(body))
    fields = reader.fieldnames or []
    rows = list(reader)
    if "归档路径" not in fields:
        return text
    for r in rows:
        r["归档路径"] = mapping.get(r["归档路径"], r["归档路径"])
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
    return "\ufeff" + out.getvalue()


def plan_archive(entries: List[Tuple[str, Optional[object]]], out_dir: Path,
                 previous: Optional[Dict[str, str]] = None,
                 protected: Iterable[Path] = ()) -> ArchivePlan:
    """算出落盘计划，不动磁盘

    规则：内容相同跳过；报告/JSON/清单等生成物直接更新；同名但内容不同的
    数据文件（.ab1/图谱副本）不覆盖、改加序号另存——除非该文件正是上次由本
    工具写出且未被改动（previous 记录的 MD5 仍吻合）。previous 中本次不再
    产出的文件：未改动的列入 remove，已改动的列入 kept（保留不删）。
    protected 为原始输入文件，任何情况下都不会被替换或删除。"""
    previous = previous or {}
    protected = {Path(p).resolve() for p in protected}
    plan = ArchivePlan()
    taken: Set[Path] = set()
    mapping: Dict[str, str] = {}
    manifest_idx: List[int] = []
    files: List[Tuple[str, Path, bytes]] = []

    for arc, data in entries:
        dst = out_dir.joinpath(*[p for p in arc.split("/") if p])
        if data is None:
            plan.dirs.append(dst)
            continue
        blob = data.encode("utf-8") if isinstance(data, str) else data
        clash = dst in taken or (
            dst.is_file() and dst.suffix.lower() not in _GENERATED
            and dst.read_bytes() != blob
            and not _owned_unmodified(dst, out_dir, previous, protected))
        if clash:
            n = 2
            alt = dst
            while alt in taken or (alt.exists() and alt.read_bytes() != blob):
                alt = dst.with_name(f"{dst.stem} ({n}){dst.suffix}")
                n += 1
            rel_arc = dst.relative_to(out_dir).as_posix()
            rel_alt = alt.relative_to(out_dir).as_posix()
            mapping[rel_arc] = rel_alt
            plan.renamed.append((rel_arc, rel_alt))
            dst = alt
        taken.add(dst)
        if dst.relative_to(out_dir).as_posix() == MANIFEST_CSV:
            manifest_idx.append(len(files))
        files.append((arc, dst, blob))

    for i in manifest_idx:         # 清单记录实际写出的路径
        arc, dst, blob = files[i]
        if mapping:
            blob = _fix_manifest_paths(blob.decode("utf-8"), mapping).encode("utf-8")
            files[i] = (arc, dst, blob)

    for _arc, dst, blob in files:
        plan.record[dst.relative_to(out_dir).as_posix()] = _md5(blob)
        if dst.is_file() and dst.read_bytes() == blob:
            plan.unchanged.append(dst)
        else:
            plan.writes.append((dst, blob))

    for rel in sorted(previous):
        if rel in plan.record or rel == RECORD_NAME:
            continue
        parts = [p for p in rel.split("/") if p]
        if not parts or any(p in ("..", ".") for p in parts):
            continue
        old = out_dir.joinpath(*parts)
        if not (old.exists() or old.is_symlink()):
            continue
        if _owned_unmodified(old, out_dir, previous, protected):
            plan.remove.append(old)
        else:
            plan.kept.append(old)
    return plan


def _prune_empty_dirs(removed: List[Path], out_dir: Path) -> None:
    """删掉因清理而变空的文件夹（自下而上，止于输出目录）；正确/ 错误/ 固定保留"""
    root = out_dir.resolve()
    seen: Set[Path] = set()
    for p in removed:
        d = p.parent
        while d != out_dir and d not in seen and _within(d, out_dir):
            seen.add(d)
            if d.resolve() == root or d.name in _KEEP_DIRS or d.is_symlink():
                break
            try:
                d.rmdir()       # 非空即失败，自然停住
            except OSError:
                break
            d = d.parent


def _apply_writes(plan: ArchivePlan) -> List[Path]:
    for d in plan.dirs:
        d.mkdir(parents=True, exist_ok=True)
    for src, alt in plan.renamed:
        print(f"! 输出目录已有同名不同内容的 {Path(src).name}，副本另存为 {Path(alt).name}")
    for dst, blob in plan.writes:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(blob)
    return plan.unchanged + [dst for dst, _b in plan.writes]


def write_archive(entries: List[Tuple[str, Optional[object]]], out_dir: Path) -> List[Path]:
    """把整理包条目写到输出目录，返回写出（或已是同内容）的文件路径

    不清理旧文件、不写整理记录；重跑清理见 sync_archive。"""
    return _apply_writes(plan_archive(entries, out_dir))


def sync_archive(entries: List[Tuple[str, Optional[object]]], out_dir: Path,
                 protected: Iterable[Path] = (), dry_run: bool = False) -> ArchivePlan:
    """重跑安全的落盘：清理上次生成而本次不再产出的旧副本，再写出本次条目

    只删除输出目录内、上次运行记录过且内容仍与当时写出一致的文件；被用户
    改过的、记录之外的文件与原始输入一律不动。随后删掉因此变空的文件夹
    （正确/ 错误/ 保留），并写入新的整理记录。dry_run 只打印计划。"""
    plan = plan_archive(entries, out_dir, load_previous_outputs(out_dir), protected)
    tag = "[预览] 将" if dry_run else ""
    for p in plan.remove:
        print(f"{tag}清理上次生成的旧副本：{p.relative_to(out_dir).as_posix()}")
    for p in plan.kept:
        print(f"! 上次生成的 {p.relative_to(out_dir).as_posix()} 已不在本次结果中，"
              "但内容已被改动或无法核对，保留未删")
    if dry_run:
        for src, alt in plan.renamed:
            print(f"[预览] 同名不同内容的 {src} 将另存为 {alt}")
        return plan
    for p in plan.remove:
        p.unlink()
    _prune_empty_dirs(plan.remove, out_dir)
    _apply_writes(plan)
    (out_dir / RECORD_NAME).write_text(
        json.dumps({"version": 1, "files": plan.record}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return plan


# ---------------------------------------------------------------- 主流程


def main():
    ap = argparse.ArgumentParser(description="批量测序结果自动整理与分析")
    ap.add_argument("--data-dir", required=True, help="测序结果文件夹（含 .ab1/.dna 与信息表）")
    ap.add_argument("--excel", help="信息表路径（默认取文件夹下第一个 .xlsx）")
    ap.add_argument("--out-dir", help="输出目录（默认 <data-dir>/测序分析）")
    ap.add_argument("--min-q", type=int, default=20, help="修剪 Q 阈值（默认 20）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只匹配与预览，不复制、不删除、不回填（输出目录有上次运行记录时"
                    "会只读分析一遍，以预览将清理的旧副本）")
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

    inputs = [f["path"] for f in files] + [excel_path]

    if args.dry_run:
        reads = [f for f in files if f["ext"] in READ_EXTS]
        refs = [f for f in files if f["ext"] not in READ_EXTS]
        _ordered, per_plasmid, unmatched = group_batch_uploads(reads, refs, rows)
        for name, slot in per_plasmid.items():
            ref = slot["reference"]["file"]["path"].name if slot["reference"] else "（缺）"
            print(f"[预览] {name}: 图谱={ref}, reads={[r['file']['path'].name for r in slot['reads']]}")
        for u in unmatched:
            print(f"[未匹配] {u['file']['path'].name}: {u['reason']}")
        # 输出目录留有上次运行记录时，需要本次结论才能知道哪些旧副本会被
        # 清理：只读地跑一遍分析，打印计划，不写、不删、不回填
        if out_dir.is_dir() and load_previous_outputs(out_dir):
            load_bytes(files)
            _payload, entries = run_offline_batch(files, rows, args.min_q)
            plan = sync_archive(entries, out_dir, protected=inputs, dry_run=True)
            if not plan.remove:
                print("[预览] 输出目录没有需要清理的旧副本")
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
    sync_archive(entries, out_dir, protected=inputs)

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
