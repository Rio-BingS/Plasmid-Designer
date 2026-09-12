"""poly 优化基线快照（docs/poly-final-actionable-list.md 第 1 步）

每个 --data-dir 视为一个质粒组：目录顶层恰好 1 个参考图谱（.dna/.gb/.fasta）
+ 若干 .ab1 测序 read（不递归子目录）。逐组调用 core.sanger.pipeline.analyze()，
把当前行为整体快照成 JSON：

- homopolymers / variants（含峰级证据）/ conclusion / mixed_positions /
  consensus 覆盖 / cds_reports —— 用户直接读到的输出；
- 每条 read 的 _trim_by_quality 边界与 peak_indices 总数 —— 对照"不动清单"；
- FWO_1 与内容检测的通道映射、各通道信号和 —— 对照第 0 步（FWO_1 修复）
  的前后行为。

用法：
  python scripts/poly_baseline.py \
      --data-dir "../plasmid-designer V1.01/sequencing results" \
      --data-dir "../plasmid-designer V1.01/sequencing results/1/1/wrong/M/1" \
      [--out ../docs/baseline-20260912.json] [--legacy]

优化各步（A1–B5）完成后用同一命令重跑（--out 换新文件名），与上一份 diff：
"不改的输出"不得漂移。meta.code 记录 core/sanger/*.py 的 sha256，diff 时先
核对代码哈希变化与当步改动是否一致。
--legacy：以约束 2 的回退开关运行（signal_correct=False, poly_local_ratio=
False），用于把 A2+B1 的贡献与其余改动分离对比。
"""

import argparse
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path

try:  # Windows 控制台默认 GBK，摘要含中文/箭头时防 UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "src" / "backend"
if (BACKEND / "core").is_dir():
    sys.path.insert(0, str(BACKEND))

READ_EXTS = {".ab1", ".ab"}
REF_EXTS = {".dna", ".gb", ".gbk", ".genbank", ".fasta", ".fa", ".fna"}
# 结果里体积大且可由输入文件复原的字段：不入快照（trim 边界另记在 provenance）
_BULK_READ_KEYS = ("trimmed_bases", "trimmed_quality", "trimmed_peaks")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_of(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def read_provenance(blob: bytes) -> dict:
    """read 级原始提取信息：trim 边界、峰数、通道映射（对照不动清单与第 0 步）"""
    import io

    from Bio import SeqIO

    import core.sanger.abif_reader as ab
    from core.sanger.pipeline import _trim_by_quality

    read = ab.extract_read(blob)
    s, e = _trim_by_quality(read["bases"], read["quality"], 20)

    raw = SeqIO.read(io.BytesIO(blob), "abi").annotations.get("abif_raw", {})
    channel_data = {int(k[4:]): list(v) for k, v in raw.items()
                    if k.startswith("DATA") and k[4:].isdigit()
                    and isinstance(v, (list, tuple))}
    fwo = raw.get("FWO_1")
    mapping = {}
    if channel_data and read["bases"] and read["peak_indices"]:
        trace = ab._detect_trace_mapping(channel_data, read["bases"], read["peak_indices"])
        mapping = {b: next((n for n, cd in channel_data.items() if cd == arr), None)
                   for b, arr in trace.items()}

    return {
        "md5": md5_of(blob),
        "raw_bases": len(read["bases"]),
        "peak_indices_total": len(read["peak_indices"]),
        "trim_start": s,
        "trim_end": e,
        "sample_name": read.get("sample_name", ""),
        "dye": read.get("dye", ""),
        "fwo_1": fwo.decode("ascii") if isinstance(fwo, (bytes, bytearray)) else (str(fwo) if fwo else None),
        "detected_mapping": {b: f"DATA{n}" for b, n in sorted(mapping.items())},
        "channel_sum": {b: sum(read["trace"][b]) for b in sorted(read["trace"])},
    }


def snapshot_group(data_dir: Path, legacy: bool = False) -> dict:
    from core.sanger.pipeline import analyze
    from core.sanger.reference_parser import parse_reference

    files = sorted(p for p in data_dir.iterdir()
                   if p.is_file() and not p.name.startswith("~$"))
    refs = [p for p in files if p.suffix.lower() in REF_EXTS]
    reads = [p for p in files if p.suffix.lower() in READ_EXTS]
    if len(refs) != 1:
        raise SystemExit(f"{data_dir}: 期望恰好 1 个参考图谱（.dna/.gb/.fasta），实得 {len(refs)}")
    if not reads:
        raise SystemExit(f"{data_dir}: 目录顶层没有 .ab1 测序文件")

    ref_blob = refs[0].read_bytes()
    reference, features = parse_reference(refs[0].name, ref_blob)
    ab1 = [(p.name, p.read_bytes()) for p in reads]
    res = analyze(ab1, reference, features=features,
                  signal_correct=not legacy, poly_local_ratio=not legacy)

    prov = {p.name: read_provenance(blob) for p, (_, blob) in zip(reads, ab1)}

    return {
        "dir": str(data_dir),
        "reference": {
            "file": refs[0].name,
            "md5": md5_of(ref_blob),
            "length": len(reference),
            "features": len(features or []),
        },
        "reads": [p.name for p in reads],
        "result": {
            "engine": res["engine"],
            "errors": res["errors"],
            "reads": [{k: v for k, v in r.items() if k not in _BULK_READ_KEYS}
                      for r in res["reads"]],
            "variants": res["variants"],
            "consensus": res["consensus"],
            "coverage_ranges": res["coverage_ranges"],
            "coverage_gaps": res["coverage_gaps"],
            "cds_reports": res["cds_reports"],
            "homopolymers": res["homopolymers"],
            "conclusion": res["conclusion"],
            "mixed_detected": res["mixed_detected"],
        },
        "read_provenance": prov,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="poly 优化基线快照（用法见模块 docstring）")
    ap.add_argument("--data-dir", action="append", required=True,
                    help="质粒组目录（1 个参考图谱 + N 条 .ab1），可重复传入多组")
    ap.add_argument("--out", default=None,
                    help="输出 JSON 路径（默认 <仓库上级>/docs/baseline-<今天>.json）")
    ap.add_argument("--legacy", action="store_true",
                    help="回退开关模式（signal_correct=False, poly_local_ratio=False）")
    args = ap.parse_args()

    out = (Path(args.out) if args.out else
           Path(__file__).resolve().parents[2] / "docs"
           / f"baseline-{date.today().strftime('%Y%m%d')}.json")

    doc = {
        "format": "poly-baseline/1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "legacy" if args.legacy else "default",
        "regression_command": ".venv/Scripts/python.exe -m pytest tests/test_sanger_pipeline.py -q",
        "code": {str(p.relative_to(REPO_ROOT)).replace("\\", "/"): sha256_of(p)
                 for p in sorted((BACKEND / "core" / "sanger").glob("*.py"))},
        "groups": [snapshot_group(Path(d), legacy=args.legacy) for d in args.data_dir],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding="utf-8")

    print(f"baseline -> {out} ({out.stat().st_size // 1024} KB)")
    for g in doc["groups"]:
        r = g["result"]
        hp = [(h["base"], h["start"], h["end"], h["ref_repeat_count"],
               h["peak_count_estimate"]) for h in r["homopolymers"]]
        print(f'  [{g["reference"]["file"]}] reads={len(g["reads"])} '
              f'variants={len(r["variants"])} hp={hp or "-"} '
              f'coverage={r["consensus"]["coverage_percent"]}%')


if __name__ == "__main__":
    main()
