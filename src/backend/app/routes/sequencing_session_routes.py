"""批量测序分块上传（会话式）路由

Cloudflare 免费版单请求体上限 100 MB，整批一次 POST 的
/api/sequencing/analyze-batch 无法承载大批次。分块上传流程：

1. POST   /api/sequencing/batch-sessions                 建会话（可带信息表 excel 与
   文件清单 manifest；带清单时立即按文件名归组并检查 read 上限，超限在上传
   任何数据之前 413）
2. POST   /api/sequencing/batch-sessions/{id}/files      追加一个分块（≤90 MB、≤200 个
   文件；chunk_index 幂等，重试不会重复落盘）
3. POST   /api/sequencing/batch-sessions/{id}/finalize   提交分析：走与单请求入口相同
   的管线（归组、按本账号生效值检查 read 上限、并发闸、整理包）。
   SEQUENCING_BATCH_FINALIZE_WAIT 秒内完成直接返回与 analyze-batch 完全相同的
   响应体；否则返回 202，客户端轮询 4.
4. GET    /api/sequencing/batch-sessions/{id}            查询状态（done 时带 result）
5. DELETE /api/sequencing/batch-sessions/{id}            取消并删除暂存文件

finalize 改为「限时等待 + 轮询」而非一直挂着连接：Cloudflare 代理对源站
响应有 100 秒超时（524），大批次逐组比对可能远超该时长。

会话只属于创建者（登录账号 id，或匿名时创建响应下发的 access_token，经
X-Access-Token 头回传）；他人一律 404，不暴露会话存在性。
"""

import asyncio
import contextlib
import json
import logging
import os
import secrets
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app import sequencing_upload_sessions as store
from app.auth.jwt_auth import User, get_current_user
from app.gating import require_feature
from app.rate_limit import get_client_key_ip
from app.routes import sequencing_routes as sr

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sequencing"])

# finalize 请求内最多等待的秒数（须低于 Cloudflare 的 100 s 源站超时）
FINALIZE_WAIT = float(os.environ.get("SEQUENCING_BATCH_FINALIZE_WAIT", "60"))
# 后台分析排队等待并发槽的时限（不占 HTTP 连接，可比单请求入口的 30 s 长得多）
FINALIZE_QUEUE_TIMEOUT = float(os.environ.get("SEQUENCING_BATCH_FINALIZE_QUEUE_TIMEOUT", "600"))
SWEEP_INTERVAL = 600

# 本进程正在执行的 finalize 任务：session_id → Task（持有引用防止被 GC）
_JOBS: Dict[str, "asyncio.Task"] = {}

_GATE = [Depends(require_feature("sequencing_batch"))]
_INTERRUPTED = {"status_code": 500, "detail": "批量分析被中断（服务重启），请重新提交"}


def _http(e: store.SessionError) -> HTTPException:
    return HTTPException(status_code=e.status_code, detail=e.detail)


def _owner_key(request: Request, user: Optional[User]) -> str:
    return f"user:{user.id}" if user is not None else f"anon:{get_client_key_ip(request)}"


def _authorize(meta: Dict, user: Optional[User], token: Optional[str]) -> None:
    if not store.can_access(meta, user, token):
        raise store.SessionError(404, store.NOT_FOUND)


# ---------------------------------------------------------------- 1. 建会话


def _parse_manifest(raw: Optional[str]) -> Optional[List[Dict]]:
    if raw is None or raw == "":
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="文件清单格式错误（应为 JSON 数组）")
    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="文件清单格式错误（应为 JSON 数组）")
    if len(data) > store.SESSION_MAX_FILES:
        raise HTTPException(status_code=413,
                            detail=f"本批次文件数超过上限（{store.SESSION_MAX_FILES} 个）")
    out = []
    total = 0
    for it in data:
        if not isinstance(it, dict) or not isinstance(it.get("size"), int) \
                or isinstance(it.get("size"), bool) or it["size"] < 0:
            raise HTTPException(status_code=400, detail="文件清单格式错误（每项须含 name 与 size）")
        try:
            name = store.validate_filename(it.get("name"))
        except store.SessionError as e:
            raise _http(e)
        if it["size"] > sr.MAX_FILE_SIZE:
            raise HTTPException(status_code=400,
                                detail=f"文件过大（>{sr.MAX_FILE_SIZE // (1024 * 1024)}MB）: {name}")
        total += it["size"]
        out.append({"name": name, "size": it["size"]})
    if total > store.SESSION_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"本批次总体积超过上限（{store.SESSION_MAX_BYTES // (1024 * 1024)} MB），"
                   "请拆分为多个批次提交")
    return out


def _precheck(manifest: List[Dict], excel_bytes: Optional[bytes], read_limits: Dict) -> None:
    """按文件清单预演归组与 read 上限（只看文件名，不需要文件内容）。

    与 finalize 用同一套归类/归组/上限函数，判定一致；finalize 时仍会按
    实际收到的文件再检查一遍（清单只是让超限尽早暴露，不被信任）。"""
    blobs = [(m["name"], b"\0" if m["size"] else b"") for m in manifest]
    reads, refs, _ignored = sr._classify_batch_blobs(blobs)
    rows = None
    if excel_bytes is not None:
        _cols, rows = sr._load_batch_excel(excel_bytes)
    sr._group_and_check(reads, refs, rows, read_limits, max_files=None)


@router.post("/sequencing/batch-sessions", dependencies=_GATE)
async def create_batch_session(
    request: Request,
    excel: Optional[UploadFile] = File(None, description="信息表 .xlsx（可选）"),
    manifest: Optional[str] = Form(None, description='文件清单 JSON：[{"name": "...", "size": 123}]；提供时立即预检 read 上限'),
    user: Optional[User] = Depends(get_current_user),
):
    """创建分块上传会话。匿名会话的 access_token 只在本响应下发一次。"""
    entries = _parse_manifest(manifest)
    read_limits = sr._batch_read_limits_for(user)
    excel_payload = None
    if excel is not None:
        excel_bytes = await sr._read_limited(excel)
        sr._check_batch_excel(excel.filename, excel_bytes)
        try:
            excel_name = store.validate_filename(excel.filename or "信息表.xlsx")
        except store.SessionError as e:
            raise _http(e)
        excel_payload = {"name": excel_name, "bytes": excel_bytes}
    if entries is not None:
        # 信息表解析（openpyxl）属重型 CPU，移交线程池
        await run_in_threadpool(_precheck, entries,
                                excel_payload["bytes"] if excel_payload else None, read_limits)
    try:
        created = await run_in_threadpool(
            store.create, user.id if user else None, _owner_key(request, user),
            excel_payload, set(_JOBS))
    except store.SessionError as e:
        raise _http(e)
    body = {**store.public_view(created["meta"]), "limits": store.limits(),
            "file_max_mb": sr.MAX_FILE_SIZE // (1024 * 1024)}
    if created["access_token"]:
        body["access_token"] = created["access_token"]
    return body


# ---------------------------------------------------------------- 2. 追加分块


def _append_sync(sid: str, user, token, chunk_index: int, uploads) -> Dict:
    with store.locked(sid) as meta:
        _authorize(meta, user, token)
        res = store.append_files(meta, chunk_index, uploads, sr.MAX_FILE_SIZE)
        if not res["duplicate"]:
            store.save_meta(meta)
        return {**store.public_view(meta), "chunk_index": chunk_index,
                "received_files": res["files"], "received_bytes": res["bytes"],
                "duplicate": res["duplicate"]}


@router.post("/sequencing/batch-sessions/{session_id}/files", dependencies=_GATE)
async def upload_batch_chunk(
    session_id: str,
    request: Request,
    files: List[UploadFile] = File(..., description="本分块的测序/图谱文件"),
    chunk_index: int = Form(..., ge=0, le=1_000_000, description="分块序号（从 0 起，重试时保持不变）"),
    user: Optional[User] = Depends(get_current_user),
):
    """追加一个分块。文件流式写入会话私有目录（0700/0600），不整读进内存。"""
    token = sr._request_access_token(request)
    if len(files) > store.CHUNK_MAX_FILES:
        raise HTTPException(status_code=413, detail=f"单个分块最多 {store.CHUNK_MAX_FILES} 个文件")
    try:
        store.check_free_space(sum(f.size or 0 for f in files))
        return await run_in_threadpool(
            _append_sync, session_id, user, token, chunk_index,
            [(f.filename, f.file) for f in files])
    except store.SessionError as e:
        raise _http(e)


# ---------------------------------------------------------------- 3. 提交分析


def _begin_finalize(sid: str, user, token) -> None:
    with store.locked(sid) as meta:
        _authorize(meta, user, token)
        if meta["state"] != store.OPEN:
            raise store.SessionError(409, "该上传会话已提交分析，请勿重复提交")
        if not meta["files"]:
            raise store.SessionError(400, "尚未上传任何测序文件")
        meta["state"] = store.QUEUED
        meta["pid"] = os.getpid()
        store.save_meta(meta)


def _set_running(sid: str) -> None:
    with store.locked(sid) as meta:
        meta["state"] = store.RUNNING
        store.save_meta(meta)


def _finalize_sync(sid: str, min_q: int, owner_id: Optional[str],
                   read_limits: Dict) -> Dict:
    """同步：读回暂存文件 → 与单请求入口相同的归类与批量管线"""
    meta = store.load_meta(sid)
    blobs, excel = store.read_all(meta)
    reads, refs, ignored = sr._classify_batch_blobs(blobs)
    excel_name = excel_bytes = None
    if excel is not None:
        excel_name, excel_bytes = excel
        sr._check_batch_excel(excel_name, excel_bytes)
    batch_token = secrets.token_urlsafe(16) if owner_id is None else None
    return sr._batch_pipeline(reads, refs, ignored, min_q, excel_name, excel_bytes,
                              owner_id, batch_token, read_limits, max_files=None)


def _finish(sid: str, outcome: Dict) -> None:
    try:
        with store.locked(sid) as meta:
            meta["state"] = outcome["state"]
            meta["error"] = outcome.get("error")
            meta["expires_ts"] = time.time() + store.RESULT_TTL
            if outcome["state"] == store.DONE:
                store.save_result(sid, outcome["result"])
            store.save_meta(meta)
        store.drop_files(sid)
    except store.SessionError:
        pass  # 会话目录已被清理


async def _finalize_job(sid: str, min_q: int, owner_id: Optional[str],
                        read_limits: Dict) -> None:
    outcome: Dict
    try:
        async with sr._heavy_slot(timeout=FINALIZE_QUEUE_TIMEOUT):
            await run_in_threadpool(_set_running, sid)
            payload = await run_in_threadpool(_finalize_sync, sid, min_q, owner_id, read_limits)
        outcome = {"state": store.DONE, "result": payload}
    except HTTPException as e:
        outcome = {"state": store.FAILED,
                   "error": {"status_code": e.status_code, "detail": e.detail}}
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            _finish(sid, {"state": store.FAILED, "error": _INTERRUPTED})
        raise
    except Exception:  # noqa: BLE001
        logger.exception("批量测序分块会话分析失败: %s", sid)
        outcome = {"state": store.FAILED,
                   "error": {"status_code": 500, "detail": "批量分析失败，请稍后重试"}}
    await run_in_threadpool(_finish, sid, outcome)


def _outcome_response(meta: Dict):
    """done → 与 analyze-batch 相同的响应体；failed → 原样的 HTTP 错误"""
    if meta["state"] == store.DONE:
        result = store.load_result(meta["id"])
        if result is not None:
            return result
        raise HTTPException(status_code=404, detail=store.NOT_FOUND)
    err = meta.get("error") or _INTERRUPTED
    raise HTTPException(status_code=err["status_code"], detail=err["detail"])


@router.post("/sequencing/batch-sessions/{session_id}/finalize", dependencies=_GATE)
async def finalize_batch_session(
    session_id: str,
    request: Request,
    min_q: int = Form(default=20, ge=0, le=60, description="末端修剪质量阈值（0-60）"),
    user: Optional[User] = Depends(get_current_user),
):
    """提交分析。限时内完成 → 200 + 与 /sequencing/analyze-batch 相同的响应体；
    仍在运行 → 202 {"session_id", "state"}，经 GET 轮询取结果。"""
    token = sr._request_access_token(request)
    # read 上限按提交者当下的生效值解析（管理员中途调整即时生效）
    read_limits = sr._batch_read_limits_for(user)
    try:
        await run_in_threadpool(_begin_finalize, session_id, user, token)
    except store.SessionError as e:
        raise _http(e)
    task = asyncio.create_task(
        _finalize_job(session_id, min_q, user.id if user else None, read_limits))
    _JOBS[session_id] = task
    task.add_done_callback(lambda _t, sid=session_id: _JOBS.pop(sid, None))
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=FINALIZE_WAIT)
    except asyncio.TimeoutError:
        meta = await run_in_threadpool(store.load_meta, session_id)
        return JSONResponse(status_code=202, content=store.public_view(meta))
    try:
        meta = await run_in_threadpool(store.load_meta, session_id)
    except store.SessionError as e:
        raise _http(e)
    return _outcome_response(meta)


# ---------------------------------------------------------------- 4. 查询 / 5. 取消


def _status_sync(sid: str, user, token) -> Dict:
    meta = store.load_meta(sid)
    _authorize(meta, user, token)
    view = store.public_view(meta)
    if store.is_orphaned(meta, set(_JOBS)):
        view["state"] = store.FAILED
        view["error"] = _INTERRUPTED
    elif meta["state"] == store.DONE:
        result = store.load_result(sid)
        if result is None:
            raise store.SessionError(404, store.NOT_FOUND)
        view["result"] = result
    elif meta["state"] == store.FAILED:
        view["error"] = meta.get("error") or _INTERRUPTED
    return view


@router.get("/sequencing/batch-sessions/{session_id}", dependencies=_GATE)
async def get_batch_session(session_id: str, request: Request,
                            user: Optional[User] = Depends(get_current_user)):
    """会话状态：open / queued / running / done（带 result，结构同 analyze-batch
    响应）/ failed（带 error: {status_code, detail}）"""
    try:
        return await run_in_threadpool(
            _status_sync, session_id, user, sr._request_access_token(request))
    except store.SessionError as e:
        raise _http(e)


def _delete_sync(sid: str, user, token) -> None:
    with store.locked(sid) as meta:
        _authorize(meta, user, token)
        if meta["state"] in (store.QUEUED, store.RUNNING) and not store.is_orphaned(meta, set(_JOBS)):
            raise store.SessionError(409, "批量分析正在进行，无法取消")
    store.delete(sid)


@router.delete("/sequencing/batch-sessions/{session_id}", dependencies=_GATE)
async def delete_batch_session(session_id: str, request: Request,
                               user: Optional[User] = Depends(get_current_user)):
    """取消上传并删除暂存文件（已完成的会话同样可删，结果随之清除）"""
    try:
        await run_in_threadpool(_delete_sync, session_id, user,
                                sr._request_access_token(request))
    except store.SessionError as e:
        raise _http(e)
    return {"deleted": True}


# ---------------------------------------------------------------- 过期清理


def sweep_now() -> int:
    try:
        return store.sweep(local_jobs=set(_JOBS))
    except Exception:  # noqa: BLE001 清理失败不影响服务
        logger.exception("批量上传会话清理失败")
        return 0


async def sweeper_loop(interval: float = SWEEP_INTERVAL) -> None:
    """启动时清理一次残留，之后周期清理过期会话（lifespan 中启动）"""
    while True:
        await run_in_threadpool(sweep_now)
        await asyncio.sleep(interval)
