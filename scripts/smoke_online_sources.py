#!/usr/bin/env python
"""联网冒烟：真打 SnapGene 在线库 / Ensembl / UniProt / Addgene（默认不跑）

单元测试全部打桩（`opener=` 注入），因此 CI 只能验解析与错误映射，验不了
「上游接口今天还通不通」。本脚本补这一环：**默认什么都不做**，必须显式
`--run`（或 `ONLINE_SMOKE=1`）才联网，避免误触外呼 / 拖慢流水线。

用法::

    python scripts/smoke_online_sources.py                 # 跳过（退出码 0）
    python scripts/smoke_online_sources.py --run           # 全跑
    python scripts/smoke_online_sources.py --run --only snapgene,uniprot
    python scripts/smoke_online_sources.py --run --timeout 40

约定：

* 只读不写：不落库、不改 data/、不写缓存文件（进程退出即散）。
* 低频：每个站点串行、间隔由 core.online_sources 的进程级节流保证。
* 退出码：0 全部通过（Addgene 匿名项按「信息」处理，不算失败——它本就
  需要登录，失败是预期）；1 有硬失败（SnapGene/Ensembl/UniProt 任一步）。
* 需要 `ONLINE_IMPORT_ENABLED` 未被设为 false；本脚本不导入 app.*，
  所以不需要 SECRET_KEY。

许可提醒：SnapGene 在线库学术/非营利/政府可用但须引用 www.snapgene.com/resources，
商业用途须获 GraphPad 许可，其 robots.txt 明确 Disallow: /local → 本脚本只做
单次低频读取，请勿改成批量镜像；Addgene 全长序列需登录，本脚本不绕登录。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# 允许直接以脚本方式运行（仓库根 / src/backend 两种工作目录都能 import core）
_ROOT = Path(__file__).resolve().parents[1]
for candidate in (_ROOT / "src" / "backend", _ROOT):
    if candidate.is_dir():
        sys.path.insert(0, str(candidate))

from core import online_sources as src  # noqa: E402

STEPS = ("snapgene", "ensembl", "uniprot", "addgene")

# 冒烟用的固定样本（挑稳定的公开记录，避免每次跑换 ID 导致结果不可比）
SNAPGENE_SET = "coronavirus_resources"
SNAPGENE_PLASMID = "SARS_Genome"
ENSEMBL_ID = "ENST00000288602"      # BRCA2 转录本
UNIPROT_QUERY = "TP53 human"
UNIPROT_ACC = "P04637"              # 人 p53
ADDGENE_ID = "52961"                # pLentiCRISPR v2


def _report(status: str, name: str, detail: str) -> None:
    # 只输出 GBK 可编码的字符：Windows 控制台默认 GBK，非 BMP/生僻符号会让
    # print 直接抛 UnicodeEncodeError（曾用 "↳" 踩到），冒烟脚本不该因日志挂掉
    text = f"[{status}] {name}: {detail}"
    print(text.encode("gbk", errors="replace").decode("gbk"), flush=True)


def smoke_snapgene(timeout: float) -> bool:
    try:
        sets = src.snapgene_sets(timeout=timeout)
        _report("ok", "snapgene.sets", f"{len(sets)} 个集合")
        if not sets:
            _report("FAIL", "snapgene.sets", "集合列表为空（接口可能已变化）")
            return False
        sid = SNAPGENE_SET if any(s["set_id"] == SNAPGENE_SET for s in sets) else sets[0]["set_id"]
        rows = src.snapgene_set_index(sid, timeout=timeout)
        _report("ok", f"snapgene.index[{sid}]", f"{len(rows)} 条")
        pid = SNAPGENE_PLASMID if any(r["plasmid_id"] == SNAPGENE_PLASMID for r in rows) \
            else (rows[0]["plasmid_id"] if rows else "")
        if not pid:
            _report("FAIL", "snapgene.index", "索引为空，无法继续")
            return False
        data = src.snapgene_plasmid_bytes(sid, pid, timeout=timeout)
        rec = src.snapgene_record(data, name=pid)
        _report("ok", f"snapgene.fetch[{sid}/{pid}]",
                f"{len(data)} 字节 → {len(rec.sequence)} bp {rec.topology} {len(rec.features)} 特征")
        return len(rec.sequence) > 0
    except Exception as e:  # noqa: BLE001 - 冒烟脚本要打印原因而不是抛栈
        _report("FAIL", "snapgene", f"{type(e).__name__}: {e}")
        return False


def smoke_ensembl(timeout: float) -> bool:
    try:
        entry = src.ensembl_lookup("ENSG00000139618", timeout=timeout)
        _report("ok", "ensembl.lookup[ENSG00000139618]", str(entry.get("display_name") or entry.get("id")))
        rec = src.ensembl_record(ENSEMBL_ID, "cds", timeout=timeout)
        _report("ok", f"ensembl.sequence[{ENSEMBL_ID}/cds]",
                f"{len(rec.sequence)} bp {rec.topology}")
        return len(rec.sequence) > 0
    except Exception as e:  # noqa: BLE001
        _report("FAIL", "ensembl", f"{type(e).__name__}: {e}（rest.ensembl.org 偶发超时，可重试）")
        return False


def smoke_uniprot(timeout: float) -> bool:
    try:
        hits = src.uniprot_search(UNIPROT_QUERY, limit=3, timeout=timeout)
        _report("ok", "uniprot.search", f"{len(hits)} 条命中（{hits[0]['accession'] if hits else '-'}）")
        entry = src.uniprot_entry(UNIPROT_ACC, timeout=timeout)
        cands = src.nucleotide_candidates(entry)
        _report("ok", f"uniprot.entry[{UNIPROT_ACC}]",
                f"{entry.get('name') or '-'} / {entry.get('organism') or '-'} / "
                f"{len(cands)} 个核酸入口")
        for c in cands[:3]:
            _report("info", "  可取 DNA", f"{c['label']} -> {c['url']}")
        return bool(entry.get("accession")) and bool(cands)
    except Exception as e:  # noqa: BLE001
        _report("FAIL", "uniprot", f"{type(e).__name__}: {e}")
        return False


def smoke_addgene(timeout: float) -> None:
    """Addgene：匿名元数据应通；全长序列需登录，失败属预期（不影响退出码）"""
    try:
        meta = src.addgene_metadata(ADDGENE_ID, timeout=timeout)
        keys = [k for k in ("genbankUrl", "snapgeneUrl", "mapPngUrl") if meta.get(k)]
        _report("ok", f"addgene.metadata[{ADDGENE_ID}]", f"字段 {keys or '（空）'}")
    except Exception as e:  # noqa: BLE001
        _report("info", f"addgene.metadata[{ADDGENE_ID}]",
                f"{type(e).__name__}: {e}（匿名元数据偶发不可用，不判失败）")
        return
    if src.addgene_has_session():
        try:
            data = src.addgene_sequence_bytes(ADDGENE_ID, timeout=timeout)
            _report("ok", f"addgene.sequence[{ADDGENE_ID}]", f"{len(data)} 字节（用配置的会话取得）")
        except Exception as e:  # noqa: BLE001
            _report("info", "addgene.sequence",
                    f"{type(e).__name__}: {e}（凭据可能过期，重登录后更新 ADDGENE_SESSION_COOKIE）")
    else:
        _report("info", "addgene.sequence",
                "未配置 ADDGENE_SESSION_COOKIE：跳过（本工具不代登录、不绕登录墙）")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="联网冒烟（默认不跑，需 --run）")
    ap.add_argument("--run", action="store_true",
                    help="真的联网（默认只打印说明；也可用 ONLINE_SMOKE=1）")
    ap.add_argument("--only", default="",
                    help=f"逗号分隔，仅跑指定来源：{','.join(STEPS)}")
    ap.add_argument("--timeout", type=float, default=30.0, help="单次外呼超时秒数（默认 30）")
    args = ap.parse_args(argv)

    run = args.run or os.environ.get("ONLINE_SMOKE", "").strip().lower() in ("1", "true", "yes")
    if not run:
        print("跳过联网冒烟（默认不联网）。要真跑："
              "python scripts/smoke_online_sources.py --run"
              "  或  ONLINE_SMOKE=1 python scripts/smoke_online_sources.py")
        return 0
    if not src.import_enabled():
        print("ONLINE_IMPORT_ENABLED=false：外呼被部署方关闭，冒烟无法进行")
        return 0

    wanted = [s.strip() for s in args.only.split(",") if s.strip()] or list(STEPS)
    unknown = [s for s in wanted if s not in STEPS]
    if unknown:
        print(f"未知来源：{unknown}（可选：{','.join(STEPS)}）")
        return 2

    print(f"联网冒烟开始：{', '.join(wanted)}（超时 {args.timeout:.0f}s；单次低频读取）")
    t0 = time.time()
    ok = True
    if "snapgene" in wanted:
        ok &= smoke_snapgene(args.timeout)
    if "ensembl" in wanted:
        ok &= smoke_ensembl(args.timeout)
    if "uniprot" in wanted:
        ok &= smoke_uniprot(args.timeout)
    if "addgene" in wanted:
        smoke_addgene(args.timeout)

    print(f"耗时 {time.time() - t0:.1f}s → {'全部通过' if ok else '存在失败项'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
