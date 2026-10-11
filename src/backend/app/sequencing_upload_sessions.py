"""批量测序分块上传会话：磁盘暂存、属主、配额与过期清理

背景：站点在 Cloudflare 免费版之后，单个请求体上限 100 MB；批量页原先
整批一次 POST，管理员即使调高某账号的 read 上限，用户也提交不了大批次。
分块上传把一批文件拆成多个 ≤90 MB 的请求依次送达，服务器在磁盘上拼成
一个会话，finalize 时一次性走原有批量管线（归组、read 上限、并发闸、
整理包），结论与单请求入口完全一致。

存储布局（SESSION_DIR，默认 DATA_DIR/batch_uploads，目录 0700、文件 0600）：

    <root>/<session_id>/meta.json   会话元数据（属主、状态、文件清单、配额计数）
    <root>/<session_id>/result.json 分析结果（含匿名整批令牌，故同样 0600）
    <root>/<session_id>/.lock       跨进程互斥（fcntl.flock；多 worker 共享磁盘）
    <root>/<session_id>/f/000001    上传文件本体：按序号落盘，原始文件名只记在
                                     meta 里——磁盘路径与用户输入无关，杜绝路径穿越，
                                     同名文件也能像单请求入口一样原样保留

会话状态：open（可追加）→ queued/running（已提交分析）→ done/failed；
DELETE 直接删除目录。状态全部落盘，多 worker 时任一进程都能接续追加或查询。

本模块不依赖 FastAPI；违反约束抛 SessionError(status_code, detail)，由路由转 HTTP。
"""

import contextlib
import json
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from typing import Dict, Iterator, List, Optional

try:  # Windows 开发机没有 fcntl：退化为进程内锁（单进程开发足够）
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

MB = 1024 * 1024


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


# 单个分块请求的上限（留出 multipart 开销，低于 Cloudflare 免费版 100 MB）
CHUNK_MAX_BYTES = _env_int("SEQUENCING_BATCH_CHUNK_MAX_MB", 90) * MB
CHUNK_MAX_FILES = _env_int("SEQUENCING_BATCH_CHUNK_MAX_FILES", 200)
# 整个会话（= 一个批次）的磁盘字节上限：取代单请求入口的 500 MB 作为批次级体积限制
SESSION_MAX_BYTES = _env_int("SEQUENCING_BATCH_SESSION_MAX_MB", 2048) * MB
# 文件数只作兜底（inode/元数据体积）；批次规模由 read 上限治理
SESSION_MAX_FILES = _env_int("SEQUENCING_BATCH_SESSION_MAX_FILES", 10000)
# 会话有效期：创建后 2 小时内须完成上传与提交；结果保留 RESULT_TTL
SESSION_TTL = _env_int("SEQUENCING_BATCH_SESSION_TTL", 2 * 3600)
RESULT_TTL = 15 * 60
# 同一属主（账号或匿名来源 IP）同时打开的会话数、全站同时打开的会话数
MAX_OPEN_PER_OWNER = _env_int("SEQUENCING_BATCH_SESSIONS_PER_OWNER", 2)
MAX_OPEN_TOTAL = _env_int("SEQUENCING_BATCH_SESSIONS_TOTAL", 16)
# 磁盘可用空间低于该值时拒绝继续接收分块
MIN_FREE_BYTES = _env_int("SEQUENCING_BATCH_SESSION_MIN_FREE_MB", 512) * MB

OPEN, QUEUED, RUNNING, DONE, FAILED = "open", "queued", "running", "done", "failed"
ACTIVE_STATES = (OPEN, QUEUED, RUNNING)

_SID_RE = re.compile(r"^[0-9a-f]{32}$")
_THREAD_LOCKS: Dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
NOT_FOUND = "上传会话不存在或已过期，请重新开始批量分析"


class SessionError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def session_root() -> str:
    """会话根目录：SEQUENCING_BATCH_SESSION_DIR，缺省 DATA_DIR/batch_uploads。

    放在数据目录（容器内即挂载卷 /app/data）而不是 /tmp：systemd 的
    PrivateTmp、容器只读根文件系统或 tmpfs 都可能让 /tmp 不可用或吃内存。"""
    root = os.environ.get("SEQUENCING_BATCH_SESSION_DIR")
    if not root:
        from app.config import settings
        root = os.path.join(settings.DATA_DIR, "batch_uploads")
    return root


def _ensure_root() -> str:
    root = session_root()
    os.makedirs(root, mode=0o700, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(root, 0o700)
    return root


def _session_dir(sid: str) -> str:
    if not isinstance(sid, str) or not _SID_RE.match(sid):
        raise SessionError(404, NOT_FOUND)
    return os.path.join(session_root(), sid)


def _write_json_atomic(path: str, data: Dict) -> None:
    tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _read_json(path: str) -> Optional[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


@contextlib.contextmanager
def locked(sid: str) -> Iterator[Dict]:
    """持会话锁读取 meta；with 块内修改 meta 后调用 save_meta 落盘"""
    d = _session_dir(sid)
    if not os.path.isdir(d):
        raise SessionError(404, NOT_FOUND)
    with _THREAD_LOCKS_GUARD:
        tlock = _THREAD_LOCKS.setdefault(sid, threading.Lock())
    with tlock:
        try:
            fh = open(os.path.join(d, ".lock"), "a+")
        except OSError:
            raise SessionError(404, NOT_FOUND)
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            meta = _read_json(os.path.join(d, "meta.json"))
            if meta is None:
                raise SessionError(404, NOT_FOUND)
            yield meta
        finally:
            fh.close()


def save_meta(meta: Dict) -> None:
    _write_json_atomic(os.path.join(_session_dir(meta["id"]), "meta.json"), meta)


def load_meta(sid: str) -> Dict:
    meta = _read_json(os.path.join(_session_dir(sid), "meta.json"))
    if meta is None:
        raise SessionError(404, NOT_FOUND)
    return meta


def _pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _iter_metas() -> List[Dict]:
    root = session_root()
    out = []
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in names:
        if not _SID_RE.match(name):
            continue
        meta = _read_json(os.path.join(root, name, "meta.json"))
        if meta is not None:
            out.append(meta)
    return out


def _remove_dir(sid: str) -> None:
    shutil.rmtree(_session_dir(sid), ignore_errors=True)
    with _THREAD_LOCKS_GUARD:
        _THREAD_LOCKS.pop(sid, None)


# 已提交分析的会话无论如何超过「过期时间 + 该时长」都清理（容器重启后
# 新进程恰好复用旧 pid 时，存活判断会误认为分析仍在进行）
BUSY_GRACE = 6 * 3600


def is_orphaned(meta: Dict, local_jobs=None) -> bool:
    """已提交分析、但执行它的进程已不在（服务重启/崩溃）。

    local_jobs：本进程正在执行的会话 id 集合；pid 就是本进程却不在集合里，
    说明是重启前的旧任务（pid 被复用），同样视为中断。"""
    if meta.get("state") not in (QUEUED, RUNNING):
        return False
    pid = meta.get("pid")
    if local_jobs is not None and pid == os.getpid():
        return meta.get("id") not in local_jobs
    return not _pid_alive(pid)


def sweep(now: Optional[float] = None, local_jobs=None) -> int:
    """清理过期会话目录（启动时与周期任务调用，创建会话时也惰性触发）。

    - 过期（expires_ts 已过）且不在分析中 → 删除
    - 分析中但执行进程已不在 → 删除（结果无从产生，前端轮询会得到 404）
    - 无法读取 meta 的残缺目录（创建中途崩溃）超过 TTL → 删除
    返回删除的目录数。"""
    now = time.time() if now is None else now
    root = session_root()
    removed = 0
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(root, name)
        if not _SID_RE.match(name) or not os.path.isdir(path):
            continue
        meta = _read_json(os.path.join(path, "meta.json"))
        if meta is None:
            try:
                stale = now - os.path.getmtime(path) > SESSION_TTL
            except OSError:
                stale = True
            if stale:
                _remove_dir(name)
                removed += 1
            continue
        expires = float(meta.get("expires_ts", 0))
        orphaned = is_orphaned(meta, local_jobs)
        busy = meta.get("state") in (QUEUED, RUNNING) and not orphaned
        if orphaned or now > expires + (BUSY_GRACE if busy else 0):
            _remove_dir(name)
            removed += 1
    return removed


def create(owner_id: Optional[str], owner_key: str,
           excel: Optional[Dict] = None, local_jobs=None) -> Dict:
    """新建会话。owner_id 为登录用户 id；匿名时为 None，返回一次性访问令牌。

    excel: {"name", "bytes"}（可选，信息表随创建一并上传）。
    返回 {"meta": meta, "access_token": str|None}。"""
    try:
        root = _ensure_root()
    except OSError:
        raise SessionError(
            503, "服务器暂存目录不可写，暂时无法批量上传，请联系管理员"
                 "（检查 SEQUENCING_BATCH_SESSION_DIR 的权限）")
    sweep(local_jobs=local_jobs)
    metas = [m for m in _iter_metas() if m.get("state") in ACTIVE_STATES]
    if sum(1 for m in metas if m.get("owner_key") == owner_key) >= MAX_OPEN_PER_OWNER:
        raise SessionError(
            429, f"同时进行中的批量上传已达上限（{MAX_OPEN_PER_OWNER} 个），"
                 "请等待之前的批次完成或取消后再试")
    if len(metas) >= MAX_OPEN_TOTAL:
        raise SessionError(503, "服务器当前批量上传任务较多，请稍后重试")

    sid = uuid.uuid4().hex
    d = os.path.join(root, sid)
    os.makedirs(os.path.join(d, "f"), mode=0o700)
    os.chmod(d, 0o700)
    os.chmod(os.path.join(d, "f"), 0o700)
    token = secrets.token_urlsafe(24) if owner_id is None else None
    from app.sequencing_store import hash_access_token
    now = time.time()
    meta = {
        "id": sid,
        "owner_id": owner_id,
        "owner_key": owner_key,
        "token_hash": hash_access_token(token) if token else None,
        "created_ts": now,
        "expires_ts": now + SESSION_TTL,
        "state": OPEN,
        "files": [],
        "bytes": 0,
        "chunks": {},
        "excel": None,
        "pid": None,
        "error": None,
    }
    if excel is not None:
        disk = "excel"
        _write_blob(os.path.join(d, "f", disk), excel["bytes"])
        meta["excel"] = {"name": excel["name"], "disk": disk, "size": len(excel["bytes"])}
        meta["bytes"] = len(excel["bytes"])
    _write_json_atomic(os.path.join(d, "meta.json"), meta)
    return {"meta": meta, "access_token": token}


def _write_blob(path: str, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def can_access(meta: Dict, user, access_token: Optional[str]) -> bool:
    """会话只属于创建者：登录用户按账号 id；匿名会话凭创建时下发的令牌。
    管理员也不能向他人的会话追加或提交——会话不是可共享的资源。"""
    owner = meta.get("owner_id")
    if owner is not None:
        return user is not None and getattr(user, "id", None) == owner
    if user is not None or not access_token or not meta.get("token_hash"):
        return False
    from app.sequencing_store import hash_access_token
    return secrets.compare_digest(
        hash_access_token(access_token).encode(), str(meta["token_hash"]).encode())


def validate_filename(name: Optional[str]) -> str:
    """上传文件名校验：只接受纯文件名（浏览器 File.name 本就不含路径）。

    磁盘按序号落盘，文件名不会进入任何路径；这里仍拒绝分隔符、.. 与控制
    字符，避免它们流进整理包 zip 的条目名。"""
    n = (name or "").strip()
    if (not n or len(n) > 255 or n in (".", "..") or "/" in n or "\\" in n
            or any(ord(c) < 32 or ord(c) == 127 for c in n)):
        shown = (name or "")[:60].encode("unicode_escape").decode("ascii")
        raise SessionError(400, f"文件名不合法（不能含路径分隔符或控制字符）：{shown}")
    return n


def check_free_space(incoming: int) -> None:
    try:
        free = shutil.disk_usage(session_root()).free
    except OSError:
        return
    if free - incoming < MIN_FREE_BYTES:
        raise SessionError(507, "服务器暂存空间不足，请稍后重试或联系管理员")


def append_files(meta: Dict, chunk_index: int, uploads: List, per_file_max: int) -> Dict:
    """把一个分块的文件写入会话（调用方持 locked(sid) 并在成功后 save_meta）。

    uploads: [(filename, fileobj)]，fileobj 为同步可读对象（UploadFile.file）。
    同一 chunk_index 重复提交（客户端重试而上次其实已成功）直接返回原结果，
    不重复落盘。分块中途超限时回滚本分块已写入的文件，会话保持原状。"""
    if meta.get("state") != OPEN:
        raise SessionError(409, "该上传会话已提交分析，不能再追加文件")
    if time.time() > float(meta.get("expires_ts", 0)):
        raise SessionError(410, "上传会话已过期（创建 2 小时内须完成上传），请重新开始批量分析")
    key = str(chunk_index)
    if key in meta["chunks"]:
        return {"duplicate": True, **meta["chunks"][key]}
    if not uploads:
        raise SessionError(400, "分块内没有文件")
    if len(uploads) > CHUNK_MAX_FILES:
        raise SessionError(413, f"单个分块最多 {CHUNK_MAX_FILES} 个文件")
    if len(meta["files"]) + len(uploads) > SESSION_MAX_FILES:
        raise SessionError(413, f"本批次文件数超过上限（{SESSION_MAX_FILES} 个）")
    names = [validate_filename(n) for n, _ in uploads]

    d = _session_dir(meta["id"])
    written: List[str] = []
    new_entries: List[Dict] = []
    chunk_bytes = 0
    seq = len(meta["files"])
    try:
        for name, (_n, fobj) in zip(names, uploads):
            seq += 1
            disk = f"{seq:06d}"
            path = os.path.join(d, "f", disk)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            written.append(path)
            size = 0
            with os.fdopen(fd, "wb") as out:
                while True:
                    buf = fobj.read(MB)
                    if not buf:
                        break
                    size += len(buf)
                    chunk_bytes += len(buf)
                    if size > per_file_max:
                        raise SessionError(
                            400, f"文件过大（>{per_file_max // MB}MB）: {name}")
                    if chunk_bytes > CHUNK_MAX_BYTES:
                        raise SessionError(
                            413, f"单个分块超过 {CHUNK_MAX_BYTES // MB} MB，请更新页面后重试")
                    if meta["bytes"] + chunk_bytes > SESSION_MAX_BYTES:
                        raise SessionError(
                            413, f"本批次总体积超过上限（{SESSION_MAX_BYTES // MB} MB），"
                                 "请拆分为多个批次提交")
                    out.write(buf)
            new_entries.append({"name": name, "disk": disk, "size": size})
        check_free_space(0)
    except BaseException:
        for p in written:
            with contextlib.suppress(OSError):
                os.remove(p)
        raise
    meta["files"].extend(new_entries)
    meta["bytes"] += chunk_bytes
    summary = {"files": len(new_entries), "bytes": chunk_bytes}
    meta["chunks"][key] = summary
    return {"duplicate": False, **summary}


def read_all(meta: Dict):
    """finalize 用：按上传顺序读回 [(name, bytes)] 与信息表 (name, bytes)|None"""
    d = os.path.join(_session_dir(meta["id"]), "f")
    blobs = []
    for e in meta["files"]:
        with open(os.path.join(d, e["disk"]), "rb") as fh:
            blobs.append((e["name"], fh.read()))
    excel = None
    if meta.get("excel"):
        with open(os.path.join(d, meta["excel"]["disk"]), "rb") as fh:
            excel = (meta["excel"]["name"], fh.read())
    return blobs, excel


def drop_files(sid: str) -> None:
    """分析结束后立即删除上传原件（结果另存 result.json，保留到 RESULT_TTL）"""
    shutil.rmtree(os.path.join(_session_dir(sid), "f"), ignore_errors=True)


def save_result(sid: str, result: Dict) -> None:
    _write_json_atomic(os.path.join(_session_dir(sid), "result.json"), result)


def load_result(sid: str) -> Optional[Dict]:
    return _read_json(os.path.join(_session_dir(sid), "result.json"))


def delete(sid: str) -> None:
    _remove_dir(sid)


def public_view(meta: Dict) -> Dict:
    return {
        "session_id": meta["id"],
        "state": meta["state"],
        "file_count": len(meta.get("files") or []),
        "bytes": meta.get("bytes", 0),
        "expires_at": int(meta.get("expires_ts", 0)),
    }


def limits() -> Dict:
    return {
        "chunk_max_files": CHUNK_MAX_FILES,
        "chunk_max_mb": CHUNK_MAX_BYTES // MB,
        "session_max_mb": SESSION_MAX_BYTES // MB,
        "session_max_files": SESSION_MAX_FILES,
        "session_ttl_seconds": SESSION_TTL,
    }
