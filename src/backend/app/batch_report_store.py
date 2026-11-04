"""批量测序整理包（ZIP）的磁盘存储：多 worker 共享、带 TTL 与容量上限

此前整理包放在各 worker 进程自己的内存里（_BATCHES）：BACKEND_WORKERS>1
时，下载请求落到另一个 worker 就 404。现在写入私有目录，任一 worker 都能
按 batch_id 读到。

存储布局（REPORT_DIR，缺省为分块上传暂存根目录下的 reports/，即
$DATA_DIR/batch_uploads/reports，Docker 为具名卷 batch-uploads；目录 0700、
文件 0600）：

    <root>/<batch_id>.zip    整理包本体（含全部原始 .ab1 与图谱）
    <root>/<batch_id>.json   元数据：属主 / 匿名令牌哈希 / 文件名 / 创建时间 / 字节数

写入顺序：先 zip 后 json（各自临时文件 + 原子 rename），读者看到 json 时
zip 必然已就位；删除顺序相反。batch_id 经白名单校验后才进入路径。

本模块不依赖 FastAPI。
"""

import contextlib
import json
import logging
import os
import re
import time
import uuid
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_BID_RE = re.compile(r"^seqbatch_[0-9a-f]{12}$")
_TMP_SUFFIX = ".tmp"


def report_root() -> str:
    """整理包目录：SEQUENCING_BATCH_REPORT_DIR，缺省为分块上传暂存根目录下的
    reports/（与会话目录同卷，Docker 下非 root 进程可写；会话清理只认 32 位
    十六进制目录名，不会误删这里）"""
    root = os.environ.get("SEQUENCING_BATCH_REPORT_DIR")
    if root:
        return root
    from app.sequencing_upload_sessions import session_root
    return os.path.join(session_root(), "reports")


def _paths(batch_id: str) -> Optional[Tuple[str, str]]:
    if not isinstance(batch_id, str) or not _BID_RE.match(batch_id):
        return None
    root = report_root()
    return (os.path.join(root, f"{batch_id}.zip"), os.path.join(root, f"{batch_id}.json"))


def _ensure_root() -> str:
    root = report_root()
    os.makedirs(root, mode=0o700, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(root, 0o700)
    return root


def _write_atomic(path: str, data: bytes) -> None:
    tmp = f"{path}.{uuid.uuid4().hex[:8]}{_TMP_SUFFIX}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def save(batch_id: str, zip_bytes: bytes, meta: Dict) -> bool:
    """写入整理包；目录不可写或磁盘满时记日志并返回 False（批次结果照常返回，
    只是不提供下载）"""
    paths = _paths(batch_id)
    if paths is None:
        raise ValueError(f"非法 batch_id: {batch_id!r}")
    zip_path, meta_path = paths
    record = {**meta, "batch_id": batch_id, "size": len(zip_bytes),
              "created_ts": float(meta.get("created_ts") or time.time())}
    try:
        _ensure_root()
        _write_atomic(zip_path, zip_bytes)
        _write_atomic(meta_path, json.dumps(record, ensure_ascii=False).encode("utf-8"))
    except OSError:
        logger.exception("整理包写入失败（检查 SEQUENCING_BATCH_REPORT_DIR 的权限与空间）")
        delete(batch_id)
        return False
    return True


def load_meta(batch_id: str) -> Optional[Dict]:
    paths = _paths(batch_id)
    if paths is None:
        return None
    try:
        with open(paths[1], "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def open_zip(batch_id: str):
    """打开整理包供下载（返回二进制文件对象，调用方负责关闭）；不存在返回 None。

    先打开再流式发送：发送途中即使被清理删除，已打开的句柄仍可读完。"""
    paths = _paths(batch_id)
    if paths is None:
        return None
    try:
        return open(paths[0], "rb")
    except OSError:
        return None


def delete(batch_id: str) -> None:
    paths = _paths(batch_id)
    if paths is None:
        return
    zip_path, meta_path = paths
    for p in (meta_path, zip_path):
        with contextlib.suppress(OSError):
            os.remove(p)


def _list() -> List[Dict]:
    root = report_root()
    try:
        names = os.listdir(root)
    except OSError:
        return []
    out = []
    for name in names:
        if name.endswith(".json") and _BID_RE.match(name[:-5]):
            meta = load_meta(name[:-5])
            if meta is not None:
                out.append(meta)
    return out


def sweep(ttl: float, max_entries: int, max_total_bytes: int,
          now: Optional[float] = None) -> int:
    """清理过期整理包，并按条数 / 总字节上限从最旧开始淘汰。

    多个 worker 并发清理时删除互不冲突（文件已不在即跳过）。另清理残缺
    条目：无元数据的 zip、遗留临时文件，超过 ttl 后删除。返回删除条数。"""
    now = time.time() if now is None else now
    root = report_root()
    removed = 0
    metas = _list()
    live = []
    for m in metas:
        if now - float(m.get("created_ts", 0)) > ttl:
            delete(m["batch_id"])
            removed += 1
        else:
            live.append(m)
    live.sort(key=lambda m: float(m.get("created_ts", 0)))
    total = sum(int(m.get("size", 0)) for m in live)
    while live and (len(live) > max_entries or total > max_total_bytes):
        m = live.pop(0)
        total -= int(m.get("size", 0))
        delete(m["batch_id"])
        removed += 1
    # 残缺条目：写入中途崩溃留下的临时文件、缺元数据的 zip
    known = {m["batch_id"] for m in live}
    try:
        names = os.listdir(root)
    except OSError:
        return removed
    for name in names:
        path = os.path.join(root, name)
        stem = name[:-4] if name.endswith(".zip") else None
        orphan = name.endswith(_TMP_SUFFIX) or (stem is not None and stem not in known)
        if not orphan:
            continue
        try:
            stale = now - os.path.getmtime(path) > ttl
        except OSError:
            continue
        if stale:
            with contextlib.suppress(OSError):
                os.remove(path)
    return removed
