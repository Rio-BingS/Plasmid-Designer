"""批量测序结果自动整理与分析（离线批处理，复用平台 Sanger 管线）

用法：
  python scripts/batch_sequencing_report.py --data-dir "<测序结果文件夹>" \
      [--excel "<信息表.xlsx>"] [--out-dir "<输出目录>"] [--min-q 20] [--dry-run]

流程：
  1. 读取 Excel 信息表（表头含"质粒名称/测序引物/测序结果"，引物列填测序文件名，
     多个用分号分隔，与平台网页端"参考文件与测序文件同目录导入"的约定一致）；
  2. 递归扫描数据文件夹中的 .ab1（测序 read）与 .dna/.gb/.fasta（参考图谱），
     按文件名主干与 Excel 引物列 / 质粒名称匹配；跳过本次输出目录和此前
     运行留下的输出目录（带 .整理记录.json / 进行中记录 / 本工具表头的
     整理清单.csv），其中的副本不会被当成新 read 重复分析；
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
     逐质粒落盘：每个质粒文件夹分析完即写出并追加整理清单行，文件字节用到
     时才读、写完即释放；中途中断时已完成的质粒保留，进行中记录
     （.整理记录.进行中.jsonl）供下次重跑一并核对清理；
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
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

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
from app.sequencing_report import (  # noqa: E402
    BatchArchive,
    _conclusion_bucket,
    backfill_excel,
)
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


MANIFEST_CSV = "整理清单.csv"
# 本工具每次写出的全部文件（相对输出目录的路径 → MD5），供重跑时识别并
# 清理上一次生成、本次已不再产出的旧副本（如判定变化后留在旧结论文件夹的）
RECORD_NAME = ".整理记录.json"
# 运行中逐个质粒追加的整理记录（每行一个 {"path","md5"}）；正常结束时并入
# RECORD_NAME 并删除。中途崩溃时它记下已写出的文件，下次重跑据此一并核对清理
JOURNAL_NAME = ".整理记录.进行中.jsonl"


def _is_output_tree(d: Path) -> bool:
    if any((d / m).is_file() for m in (RECORD_NAME, JOURNAL_NAME)):
        return True
    man = d / MANIFEST_CSV
    if not man.is_file():
        return False
    try:
        with open(man, encoding="utf-8-sig", newline="") as fh:
            head = next(csv.reader(fh), [])
    except (OSError, UnicodeError, csv.Error):
        return False
    # 现行表头含「归档路径」；早期平铺版为 plasmid,name,kind,how,src,dst,...
    return "归档路径" in head or {"src", "dst"} <= set(head)


def previous_output_dirs(data_dir: Path) -> List[Path]:
    """数据文件夹下此前运行留下的输出目录（换 --out-dir 重跑或改名挪动后）

    其中是上次整理出的 read / 图谱副本，再当输入会重复参与分析。数据文件夹
    本身不算（即使带整理记录），以免整体跳过。"""
    found: Set[Path] = set()
    for name in (RECORD_NAME, JOURNAL_NAME, MANIFEST_CSV):
        for m in data_dir.rglob(name):
            d = m.parent
            if d != data_dir and m.is_file() and _is_output_tree(d):
                found.add(d)
    # 只保留最外层
    return sorted(d for d in found if not any(o in d.parents for o in found))


def scan_files(data_dir: Path, out_dir: Path,
               exclude: Optional[Iterable[Path]] = None):
    """递归扫描 read 与图谱（跳过输出目录、此前的输出目录与 Excel 临时文件）

    exclude 为要跳过的目录，缺省为 previous_output_dirs(data_dir)。
    条目与网页端上传条目同形（name/ext/stem），另带 path/rel 供追溯；
    字节由 load_bytes 在真正分析前读入（--dry-run 不读）。"""
    skip = [out_dir, *(previous_output_dirs(data_dir) if exclude is None else exclude)]
    files = []
    for p in sorted(data_dir.rglob("*")):
        if not p.is_file() or p.name.startswith("~$"):
            continue
        if any(d == p or d in p.parents for d in skip):
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


def release_bytes(files: Iterable[Dict]) -> None:
    """落盘后丢掉已读入的文件字节（再用到时 load_bytes 会重新读）"""
    for f in files:
        f.pop("bytes", None)


@dataclass
class ArchiveChunk:
    """一个质粒文件夹（或收尾部分）的整理包条目与新增的整理清单行"""
    entries: List[Tuple[str, Optional[object]]]
    rows: List[Dict] = field(default_factory=list)


class OfflineBatch:
    """离线批次：归组 → 逐组分析 → 逐个质粒文件夹产出整理包条目

    与网页端同一实现（归组、管线、结论、条目构建），但不把整批留在内存：
    文件字节在分析该组前才读入，一个质粒文件夹（含表内别名并入的各组）的
    最后一组分析完即产出该文件夹的条目，随后释放字节与分析记录。收尾块为
    未匹配文件与 整理清单.csv / 批次总览.txt；chunks() 迭代完后 payload 可用。
    """

    def __init__(self, files: List[Dict], rows: List[Dict], min_q: int = 20,
                 on_item=None):
        self.files = files
        self.min_q = min_q
        self.on_item = on_item
        reads = [f for f in files if f["ext"] in READ_EXTS]
        refs = [f for f in files if f["ext"] not in READ_EXTS]
        ordered, per_plasmid, self.unmatched = group_batch_uploads(reads, refs, rows)
        self.groups = plasmid_groups(ordered, per_plasmid)
        self.archive = BatchArchive([g["plasmid"] or "" for g in self.groups],
                                    origin="offline", source_paths=True, root="")
        self.items: List[Dict] = []
        self.payload: Optional[Dict] = None
        self._seq = 0

    def _register(self, label: str, ref_seq: str, features: List[Dict], res: Dict):
        # 本地记录与网页端分析记录同形（去掉峰图原始数据），供报告与 JSON
        self._seq += 1
        aid = f"local_{self._seq:03d}"
        rec = {"analysis_id": aid, "sample_name": label,
               "reference": ref_seq.upper(), "features": features,
               "created_at": datetime.now().isoformat(timespec="seconds"),
               **{k: v for k, v in res.items() if k != "traces"}}
        return aid, rec

    @staticmethod
    def _group_files(g: Dict) -> List[Dict]:
        return ([g["reference"]["file"]] if g["reference"] else []) + \
            [r["file"] for r in g["reads"]]

    def _folder_chunk(self, folder: str, idxs: List[int],
                      records: Dict[str, Dict]) -> ArchiveChunk:
        # 文件夹内按结论分桶，桶序与整批构建一致（首组序号先后）
        buckets: Dict[str, List[int]] = {}
        for i in idxs:
            buckets.setdefault(
                _conclusion_bucket(self.items[i].get("conclusion") or ""), []).append(i)
        chunk = ArchiveChunk(entries=list(self.archive.folder_dirs(folder)))
        for bucket, members in buckets.items():
            part, rows = self.archive.bucket_entries(folder, bucket, [
                (self.groups[i], self.items[i],
                 records.get(self.items[i]["analysis_id"])
                 if self.items[i].get("analysis_id") else None)
                for i in members], members[0])
            chunk.entries += part
            chunk.rows += rows
        return chunk

    def chunks(self) -> Iterator[ArchiveChunk]:
        folder_of = [self.archive.folder_of(g) for g in self.groups]
        last = {f: i for i, f in enumerate(folder_of) if f is not None}
        pending: Dict[str, List[int]] = {}
        records: Dict[str, Dict] = {}
        for i, g in enumerate(self.groups):
            load_bytes(self._group_files(g))
            # 逐组调用：离线不设参考长度上限（网页端的 MAX_REF_BP 是服务器
            # 资源防护）
            (item,), recs = run_batch_groups([g], self.min_q, self._register,
                                             max_ref_bp=None)
            self.items.append(item)
            records.update(recs)
            if self.on_item:
                self.on_item(item)
            folder = folder_of[i]
            if folder is None:
                continue
            pending.setdefault(folder, []).append(i)
            if last[folder] != i:
                continue
            idxs = pending.pop(folder)
            yield self._folder_chunk(folder, idxs, records)
            for j in idxs:
                release_bytes(self._group_files(self.groups[j]))
                aid = self.items[j].get("analysis_id")
                if aid:
                    records.pop(aid, None)

        self.payload = batch_payload(self.items, self.unmatched, self.min_q,
                                     excel_mode=True, clone_mode=False, ignored=[])
        for u in self.unmatched:
            load_bytes([u["file"]])
            entry, row = self.archive.unmatched_entry(u)
            yield ArchiveChunk(entries=[entry], rows=[row])
            release_bytes([u["file"]])
        yield ArchiveChunk(entries=[self.archive.manifest_entry(),
                                    self.archive.overview_entry(self.payload)])


def run_offline_batch(files: List[Dict], rows: List[Dict], min_q: int = 20):
    """离线批次一次性跑完，返回 (payload, entries)（库调用/测试用）

    payload 同网页端点响应体（items 即各组结论），entries 为整理包条目
    [(相对输出目录的路径, 内容)]。命令行走 OfflineBatch 逐质粒落盘，
    条目内容与此相同。"""
    run = OfflineBatch(files, rows, min_q)
    entries = [e for chunk in run.chunks() for e in chunk.entries]
    return run.payload, entries


# ---------------------------------------------------------------- 落盘（原件只读、副本不覆盖异内容）


_GENERATED = {".md", ".json", ".csv", ".txt"}
_KEEP_DIRS = {"正确", "错误"}


def _md5(blob: bytes) -> str:
    return hashlib.md5(blob).hexdigest()


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _read_journal(out_dir: Path) -> Dict[str, str]:
    jr = out_dir / JOURNAL_NAME
    found: Dict[str, str] = {}
    if not jr.is_file():
        return found
    try:
        lines = jr.read_text(encoding="utf-8").splitlines()
    except OSError:
        return found
    for ln in lines:
        try:
            d = json.loads(ln)
        except ValueError:
            continue            # 崩溃时最后一行可能只写了一半
        if isinstance(d, dict) and d.get("path") and d.get("md5"):
            found[str(d["path"])] = str(d["md5"])
    return found


def load_previous_outputs(out_dir: Path) -> Dict[str, str]:
    """上一次运行由本工具写出的文件 {相对路径: MD5}

    优先读 RECORD_NAME（覆盖报告/JSON 等全部生成物）；没有时（旧版本跑出的
    目录）退回 整理清单.csv 的「归档路径 + MD5」，只能覆盖数据文件副本。
    上次运行中途中断留下的 JOURNAL_NAME 叠加在上面。记录缺失或损坏时返回
    空，即不清理任何东西。"""
    journal = _read_journal(out_dir)
    rec = out_dir / RECORD_NAME
    if rec.is_file():
        try:
            data = json.loads(rec.read_text(encoding="utf-8"))
            files = data.get("files") if isinstance(data, dict) else None
            if isinstance(files, dict):
                return {**{str(k): str(v) for k, v in files.items() if k and v},
                        **journal}
        except (OSError, ValueError):
            pass
        return journal
    man = out_dir / MANIFEST_CSV
    if not man.is_file():
        return journal
    try:
        rows = list(csv.DictReader(io.StringIO(man.read_text(encoding="utf-8-sig"))))
    except (OSError, ValueError, csv.Error):
        return journal
    return {**{r["归档路径"]: r["MD5"] for r in rows
               if r.get("归档路径") and r.get("MD5")}, **journal}


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


class Archiver:
    """把整理包条目分块落到输出目录（每块即一个质粒文件夹，见 OfflineBatch）

    规则：内容相同跳过；报告/JSON/清单等生成物直接更新；同名但内容不同的
    数据文件（.ab1/图谱副本）不覆盖、改加序号另存——除非该文件正是上次由本
    工具写出且未被改动（previous 记录的 MD5 仍吻合）。protected 为原始输入
    文件，任何情况下都不会被替换或删除。

    每块写完立即追加到 JOURNAL_NAME（以及 append_manifest 追加整理清单行），
    中途崩溃时已完成的质粒文件夹完整保留、可追溯；finish() 清理上次生成而
    本次不再产出的旧副本，写出最终整理记录。dry_run 只打印计划。"""

    def __init__(self, out_dir: Path, previous: Optional[Dict[str, str]] = None,
                 protected: Iterable[Path] = (), dry_run: bool = False,
                 manifest_fields: Optional[List[str]] = None):
        self.out_dir = out_dir
        self.previous = previous or {}
        self.protected = {Path(p).resolve() for p in protected}
        self.dry_run = dry_run
        self.manifest_fields = manifest_fields
        self.taken: Set[Path] = set()
        self.mapping: Dict[str, str] = {}       # 归档路径 → 实际写出路径
        self.record: Dict[str, str] = {}        # 本次全部文件 → MD5
        self.renamed: List[Tuple[str, str]] = []
        self._journal = None

    # ---- 计划（不动磁盘）
    def plan(self, entries: List[Tuple[str, Optional[object]]]) -> ArchivePlan:
        out_dir = self.out_dir
        plan = ArchivePlan()
        manifest_idx: List[int] = []
        files: List[Tuple[str, Path, bytes]] = []

        for arc, data in entries:
            dst = out_dir.joinpath(*[p for p in arc.split("/") if p])
            if data is None:
                plan.dirs.append(dst)
                continue
            blob = data.encode("utf-8") if isinstance(data, str) else data
            clash = dst in self.taken or (
                dst.is_file() and dst.suffix.lower() not in _GENERATED
                and dst.read_bytes() != blob
                and not _owned_unmodified(dst, out_dir, self.previous, self.protected))
            if clash:
                n = 2
                alt = dst
                while alt in self.taken or (alt.exists() and alt.read_bytes() != blob):
                    alt = dst.with_name(f"{dst.stem} ({n}){dst.suffix}")
                    n += 1
                rel_arc = dst.relative_to(out_dir).as_posix()
                rel_alt = alt.relative_to(out_dir).as_posix()
                self.mapping[rel_arc] = rel_alt
                plan.renamed.append((rel_arc, rel_alt))
                dst = alt
            self.taken.add(dst)
            if dst.relative_to(out_dir).as_posix() == MANIFEST_CSV:
                manifest_idx.append(len(files))
            files.append((arc, dst, blob))

        for i in manifest_idx:         # 清单记录实际写出的路径
            arc, dst, blob = files[i]
            if self.mapping:
                blob = _fix_manifest_paths(blob.decode("utf-8"),
                                           self.mapping).encode("utf-8")
                files[i] = (arc, dst, blob)

        for _arc, dst, blob in files:
            plan.record[dst.relative_to(out_dir).as_posix()] = _md5(blob)
            if dst.is_file() and dst.read_bytes() == blob:
                plan.unchanged.append(dst)
            else:
                plan.writes.append((dst, blob))
        self.record.update(plan.record)
        self.renamed += plan.renamed
        return plan

    def stale(self) -> Tuple[List[Path], List[Path]]:
        """上次生成、本次不再产出的文件：(未改动可删, 已改动保留)"""
        remove: List[Path] = []
        kept: List[Path] = []
        for rel in sorted(self.previous):
            if rel in self.record or rel in (RECORD_NAME, JOURNAL_NAME):
                continue
            parts = [p for p in rel.split("/") if p]
            if not parts or any(p in ("..", ".") for p in parts):
                continue
            old = self.out_dir.joinpath(*parts)
            if not (old.exists() or old.is_symlink()):
                continue
            if _owned_unmodified(old, self.out_dir, self.previous, self.protected):
                remove.append(old)
            else:
                kept.append(old)
        return remove, kept

    # ---- 落盘
    def begin(self) -> None:
        """开跑：并入上次中断留下的进行中记录，清单先写表头"""
        if self.dry_run:
            return
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if (self.out_dir / JOURNAL_NAME).exists():
            self._write_record(self.previous)
            (self.out_dir / JOURNAL_NAME).unlink()
        self._journal = open(self.out_dir / JOURNAL_NAME, "a", encoding="utf-8")
        if self.manifest_fields:
            out = io.StringIO()
            csv.DictWriter(out, fieldnames=self.manifest_fields).writeheader()
            (self.out_dir / MANIFEST_CSV).write_text(
                "\ufeff" + out.getvalue(), encoding="utf-8", newline="")

    def write(self, entries: List[Tuple[str, Optional[object]]]) -> ArchivePlan:
        plan = self.plan(entries)
        if self.dry_run:
            for src, alt in plan.renamed:
                print(f"[预览] 同名不同内容的 {src} 将另存为 {alt}")
            return plan
        _apply_writes(plan)
        if self._journal is not None:
            for rel, md5 in plan.record.items():
                self._journal.write(json.dumps({"path": rel, "md5": md5},
                                               ensure_ascii=False) + "\n")
            self._journal.flush()
        return plan

    def append_manifest(self, rows: List[Dict]) -> None:
        """整理清单逐质粒追加（归档路径按实际写出路径）；收尾时整表重写"""
        if self.dry_run or not rows or not self.manifest_fields:
            return
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=self.manifest_fields)
        w.writerows([{**r, "归档路径": self.mapping.get(r["归档路径"], r["归档路径"])}
                     for r in rows])
        with open(self.out_dir / MANIFEST_CSV, "a", encoding="utf-8", newline="") as fh:
            fh.write(out.getvalue())

    def finish(self) -> ArchivePlan:
        """清理旧副本、写出最终整理记录；返回本次汇总（remove/kept/renamed/record）"""
        remove, kept = self.stale()
        summary = ArchivePlan(renamed=list(self.renamed), remove=remove, kept=kept,
                              record=dict(self.record))
        tag = "[预览] 将" if self.dry_run else ""
        for p in remove:
            print(f"{tag}清理上次生成的旧副本：{p.relative_to(self.out_dir).as_posix()}")
        for p in kept:
            print(f"! 上次生成的 {p.relative_to(self.out_dir).as_posix()} 已不在本次结果中，"
                  "但内容已被改动或无法核对，保留未删")
        if self.dry_run:
            return summary
        for p in remove:
            p.unlink()
        _prune_empty_dirs(remove, self.out_dir)
        self._write_record(self.record)
        if self._journal is not None:
            self._journal.close()
            self._journal = None
        (self.out_dir / JOURNAL_NAME).unlink(missing_ok=True)
        return summary

    def abort(self) -> None:
        """中途失败：保留已写出的文件与进行中记录，只关闭记录文件"""
        if self._journal is not None:
            self._journal.close()
            self._journal = None

    def _write_record(self, files: Dict[str, str]) -> None:
        (self.out_dir / RECORD_NAME).write_text(
            json.dumps({"version": 1, "files": files}, ensure_ascii=False, indent=1),
            encoding="utf-8")


def plan_archive(entries: List[Tuple[str, Optional[object]]], out_dir: Path,
                 previous: Optional[Dict[str, str]] = None,
                 protected: Iterable[Path] = ()) -> ArchivePlan:
    """一次性算出整批的落盘计划，不动磁盘（规则见 Archiver）

    previous 中本次不再产出的文件：未改动的列入 remove，已改动的列入 kept。"""
    arch = Archiver(out_dir, previous, protected)
    plan = arch.plan(entries)
    plan.remove, plan.kept = arch.stale()
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
    """重跑安全的一次性落盘：写出本次条目，清理上次生成而本次不再产出的旧副本

    只删除输出目录内、上次运行记录过且内容仍与当时写出一致的文件；被用户
    改过的、记录之外的文件与原始输入一律不动。随后删掉因此变空的文件夹
    （正确/ 错误/ 保留），并写入新的整理记录。dry_run 只打印计划。"""
    arch = Archiver(out_dir, load_previous_outputs(out_dir), protected, dry_run)
    arch.begin()
    arch.write(entries)
    return arch.finish()


def run_to_dir(files: List[Dict], rows: List[Dict], out_dir: Path, min_q: int = 20,
               protected: Iterable[Path] = (), dry_run: bool = False,
               on_item=None) -> Tuple[Dict, ArchivePlan]:
    """命令行主流程：逐质粒分析并落盘（见 OfflineBatch / Archiver）

    返回 (payload, 汇总计划)。中途异常时已写完的质粒文件夹、整理清单行与
    进行中记录都留在输出目录。"""
    run = OfflineBatch(files, rows, min_q, on_item=on_item)
    arch = Archiver(out_dir, load_previous_outputs(out_dir), protected, dry_run,
                    manifest_fields=run.archive.fields)
    arch.begin()
    try:
        for chunk in run.chunks():
            arch.write(chunk.entries)
            arch.append_manifest(chunk.rows)
    except BaseException:
        arch.abort()
        raise
    return run.payload, arch.finish()


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
    old_outs = [d for d in previous_output_dirs(data_dir)
                if d != out_dir and out_dir not in d.parents]
    for d in old_outs:
        print(f"跳过此前的输出目录（其中是上次整理的副本）：{d.relative_to(data_dir)}")
    files = scan_files(data_dir, out_dir, exclude=old_outs)
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
            _payload, plan = run_to_dir(files, rows, out_dir, args.min_q,
                                        protected=inputs, dry_run=True)
            if not plan.remove:
                print("[预览] 输出目录没有需要清理的旧副本")
        return

    def _progress(it: Dict) -> None:
        n_var = it["variant_count"] + it["pending_count"]
        print(f"· {it['plasmid']}: {it['read_count']} read、{n_var} 处变异 → "
              f"{it['conclusion'][:60]}…", flush=True)

    # 逐质粒分析并落盘：每个质粒文件夹分析完即写出，中途中断时已完成的
    # 部分（副本、报告、整理清单行）保留在输出目录
    payload, _plan = run_to_dir(files, rows, out_dir, args.min_q,
                                protected=inputs, on_item=_progress)
    for u in payload["unmatched"]:
        print(f"! 未匹配（原件保留原位，副本归入 未匹配文件/）：{u['filename']} — {u['reason']}")

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
