"""Sanger 测序全自动分析路由

上传参考序列文件（.gb/.fasta/.dna，与 .ab1 放同一文件夹一起导入）→
全自动管线（解析→修剪→比对→拼接→注释）→ 结果含自动结论、突变表、
共识序列与峰图数据。

- POST /api/sequencing/analyze：单样品入口（1 个参考序列 + 它的 .ab1）
- POST /api/sequencing/analyze-batch：批量入口（Excel 信息表/整个交付文件夹
  里的多个质粒，逐质粒归组分析，返回每质粒一句话结论；归组、分析与
  整理包逻辑与 scripts/batch_sequencing_report.py 共用 core/sanger/batch.py、
  app/sequencing_batch.py、app/sequencing_report.py）。
  信息表带「克隆号」列时为克隆模式：一个质粒的多个克隆每行一个、独立分析
  （ab1 文件名 = 克隆号-引物，如 S99678-M13F-75.ab1），质粒名称支持两段式
  部分匹配（"17648 MBYSTC" 可只写 "17648" / "MBYSTC" / 全名）。

另保留两个按 ID 取参考的便捷端点：
- 设计结果（POST /api/designs/{design_id}/sequencing/analyze）
- 载体库中有序列的载体（POST /api/vectors/{vector_id}/sequencing/analyze）

功能门控（main.py 路由级 + 本文件端点级）：
- 「测序分析」（sequencing）：单样品分析三入口（/sequencing/analyze、
  designs/{id}/…、vectors/{id}/…）
- 「批量测序分析」（sequencing_batch）：/sequencing/analyze-batch 与
  /sequencing/batches/{batch_id}/report
- 分析记录的查看/峰图/导出/删除为两者共用，任一功能开放即可用

分析记录为进程级内存存储（会话级数据）：每条记录 15 分钟后自动删除
（与批量整理包缓存一致），服务重启同样清空；容量兜底只保留最近若干次。
trace 峰图原始数据体积大，只保留最近若干条——更早的分析（在 15 分钟内）
仍可查看变体/共识结论，仅峰图不可再加载。

批量分析完成后按上传原始字节现场打包「整理包」并缓存：同一质粒一个文件夹
（表内别名写法合并）、其下 正确/错误 按结论分置副本与各组测序分析报告.md、
整理清单.csv、结论回填的信息表，15 分钟内可经
GET /sequencing/batches/{batch_id}/report 重复下载，超时自动清理。
"""

import asyncio
import os
import re
import secrets
import threading
import weakref
import time
import uuid
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from openpyxl.utils.exceptions import InvalidFileException
from starlette.concurrency import run_in_threadpool
from zipfile import BadZipFile

from app.auth.jwt_auth import User, get_current_user
from app.design_service import get_vector_library
from app import batch_read_limits, site_settings
from app.gating import require_feature
from app import batch_report_store, sequencing_store
from app.sequencing_upload_sessions import DiskBlob, LazyFileEntry
from app.sequencing_batch import (
    batch_payload, estimate_batch_cost, group_batch_uploads, parse_group_references,
    plasmid_groups, reference_circular, rescue_references_by_features,
    run_batch_groups,
)
from app.sequencing_report import backfill_excel, build_batch_zip
from core.genbank_format import (
    format_feature_line, format_locus_line, format_qualifier, render_origin_block,
)
from app.filename import content_disposition, sanitize_filename
from core.sanger.batch import (
    REF_EXTS, READ_EXTS, match_clone_files, norm_stem, open_excel_for_write,
    parse_excel, rows_have_clones,
)
from core.sanger.pipeline import analyze, _try_tracy_decompose
from core.sanger.reliable_zone import ZoneParams
from core.sanger.reference_parser import parse_reference_topology


def parse_zone_form(text: Optional[str]) -> Optional[ZoneParams]:
    """表单 reliable_zone（"30,60,700,900"）→ ZoneParams；非法值 422"""
    if text is None or not str(text).strip():
        return None
    try:
        return ZoneParams.parse(text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"可信区参数无效：{e}")

router = APIRouter(prefix="/api", tags=["sequencing"])

# 进程级内存存储：analysis_id → 完整结果（含 trace 峰图）
_ANALYSES: Dict[str, Dict] = {}
# 批量整理包不在进程内存里：写入磁盘私有目录（app.batch_report_store），
# BACKEND_WORKERS>1 时任一 worker 都能提供下载
# 终审 C-07：全局 dict 由事件循环线程与线程池（_batch_pipeline/
# _sweep_expired 都在线程池里）并发增删遍历。dict 自身的单次操作是原子的，
# 但「遍历 + 删除」的组合会抛 dictionary changed size during iteration。
# 用一把可重入锁包住所有读写路径（同一线程内 sweep→register 可重入）。
_STORE_LOCK = threading.RLock()
MAX_FILE_SIZE = 20 * 1024 * 1024      # 单文件 20MB
MAX_FILES = 24                        # 单次最多 24 条 read
MAX_STORED = 200                      # 内存最多保留 200 次分析记录
MAX_TRACE_STORED = 40                 # 峰图原始数据体积大，只保留最近 40 次
MAX_BATCH_FILES = 400                 # 批量单次最多 400 个文件
MAX_BATCH_PLASMIDS = 60               # 批量单次最多 60 个质粒
MAX_BATCH_GROUPS = 120                # 克隆模式下最多 120 个克隆分组
# 审查 02 M6：批量此前只限文件数与分组数，一个分组可塞近 400 条 read
# （200 kb 参考约 6 s/条），单请求可占住并发槽数十分钟。每组默认沿用
# 单样品的 MAX_FILES，整批参与比对的 read 总数另设上限，均可经环境变量调整。
# 这两个是服务器默认：管理员可按分组（访问层级）或单个账号覆盖，
# 生效值解析与安全上限见 app.batch_read_limits
MAX_BATCH_READS_PER_GROUP = batch_read_limits.DEFAULT_READS_PER_GROUP
MAX_BATCH_TOTAL_READS = batch_read_limits.DEFAULT_TOTAL_READS
ANALYSIS_TTL = 15 * 60                # 分析记录 15 分钟后自动删除
BATCH_TTL = 15 * 60                   # 批量整理包同样 15 分钟后清理
MAX_BATCH_CACHE_BYTES = 256 * 1024 * 1024   # 整理包缓存上限（超出则本次不提供下载）
# 终审 C-01：参考序列长度上限（质粒 50-200 kb 足够；无上限时局部比对
# O(len(ref)×len(read)) 会让单个匿名请求占满内存与线程池）
MAX_REF_BP = 200_000
# 终审 C-02：批量请求级总字节上限（单文件上限挡不住多文件叠加：
# 400 × 20MB = 8GB 会先读进内存才被拒绝）
MAX_BATCH_REQUEST_BYTES = 500 * 1024 * 1024
# 整理包存储（磁盘，全部 worker 共享）的全局上限（此前无条数/总量上限：
# 单批 256MB × 15 分钟内不限批次 → 存储可被反复打满）
MAX_BATCH_CACHE_ENTRIES = 8
# 整理包缓存的总字节上限（此前按「单包上限 × 条数」= 2 GB 计算，形同虚设）
MAX_BATCH_CACHE_TOTAL_BYTES = int(os.environ.get(
    "SEQUENCING_BATCH_CACHE_TOTAL_BYTES", str(512 * 1024 * 1024)))
# 重型比对（单样品分析 / 批量分析）的进程级并发上限：200 kb 参考 × 1 条
# read 即约 6 s / 650 MB，不限并发时少量请求就能占满线程池与内存。
# 超出的请求排队，等待超过 HEAVY_QUEUE_TIMEOUT 秒返回 503
MAX_CONCURRENT_ALIGNMENTS = max(1, int(os.environ.get(
    "SEQUENCING_MAX_CONCURRENT", str(max(2, (os.cpu_count() or 2) // 2)))))
HEAVY_QUEUE_TIMEOUT = float(os.environ.get("SEQUENCING_QUEUE_TIMEOUT", "30"))


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, str(default))))
    except ValueError:
        return default


# 审查 02 M6：批次计算预算，单位 Mbp·read = Σ(参考长度 bp × 该组 read 数) / 10^6。
# read 数上限管不住「大参考 × 多 read」：管理员调高上限后单批可达数小时。
# 默认 50 覆盖默认 read 上限下的最坏情况（240 条 × 200 kb ≈ 48），正常质粒
# 批次（10 kb × 240 条 ≈ 2.4）远低于此；0 关闭
BATCH_COMPUTE_BUDGET_MBP = _env_float("SEQUENCING_BATCH_COMPUTE_BUDGET_MBP", 50)
# 批次墙钟软时限（秒）：超时后剩余分组不再比对、记为失败，已完成的分组照常
# 返回。只在组与组之间检查（工作线程无法安全中断），0 关闭
BATCH_SOFT_TIMEOUT = _env_float("SEQUENCING_BATCH_SOFT_TIMEOUT", 1800)
_HEAVY_SEMAPHORES: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _heavy_semaphore() -> asyncio.Semaphore:
    """当前事件循环的并发闸（按循环区分：测试/多 TestClient 各有自己的循环）"""
    loop = asyncio.get_running_loop()
    sem = _HEAVY_SEMAPHORES.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(MAX_CONCURRENT_ALIGNMENTS)
        _HEAVY_SEMAPHORES[loop] = sem
    return sem


@asynccontextmanager
async def _heavy_slot(timeout: Optional[float] = None):
    """占用一个并发槽；排队超时（缺省 HEAVY_QUEUE_TIMEOUT）返回 503。

    分块上传的后台 finalize 不占着 HTTP 连接，可传入更长的排队时限。

    审查 02 M5：上传字节读入内存、Excel 解析、记录落库（json+zlib+DB）、
    峰图回灌解压、整理包打包与比对同属重型内存/CPU 阶段，都须在槽内完成，
    否则并发闸只限住了比对本身，少量并发请求仍可打满内存。"""
    sem = _heavy_semaphore()
    try:
        await asyncio.wait_for(
            sem.acquire(), timeout=HEAVY_QUEUE_TIMEOUT if timeout is None else timeout)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503,
                            detail="测序分析任务繁忙，请稍后重试",
                            headers={"Retry-After": "30"})
    try:
        yield
    finally:
        sem.release()


async def _run_heavy(fn, *args, **kwargs):
    """在并发闸内把重型同步工作移交线程池；排队超时返回 503"""
    async with _heavy_slot():
        return await run_in_threadpool(fn, *args, **kwargs)


def _sweep_expired() -> None:
    """清理过期的分析记录（每次访问入口处惰性触发）

    终审 C-07：加锁——遍历中删除在并发下会抛 dictionary changed size
    during iteration（_batch_pipeline 在线程池同样会触碰 _ANALYSES）。"""
    now = time.time()
    with _STORE_LOCK:
        for aid in [aid for aid, r in _ANALYSES.items()
                    if now - r.get("_created_ts", 0) > ANALYSIS_TTL]:
            del _ANALYSES[aid]


def _sweep_reports() -> int:
    """清理磁盘上的过期整理包（同步磁盘 I/O，调用方须在线程池中执行）。

    终审 C-02：除 TTL 外再加「条数 + 总字节」硬上限，从最旧开始淘汰——
    15 分钟内不限批次时，单批最大 256MB × 任意次数即可打满存储。
    整理包目录为全部 worker 共享，上限对整个服务生效。"""
    return batch_report_store.sweep(BATCH_TTL, MAX_BATCH_CACHE_ENTRIES,
                                    MAX_BATCH_CACHE_TOTAL_BYTES)


def _can_access(record: Dict, user: Optional[User],
                access_token: Optional[str] = None) -> bool:
    """分析记录属主校验：管理员全可见；创建者可见；无属主记录（匿名创建）
    凭 access_token 访问——终审 B-02：匿名记录曾对所有访客公开可列举、
    可读取、可删除（实测访客 B 列出 A 的记录、读取成功、DELETE 200 后
    创建者 404）。登录用户创建的记录仍按属主判定。"""
    owner = record.get("owner_id")
    if owner is None:
        # 匿名创建的记录：仅当持有创建响应下发的 access_token 才可见。
        # 改造前创建、无 token 的遗留记录不再公开（其 ID 曾可从旧列表接口
        # 批量收集），只有管理员可读/删
        if user is not None and user.is_admin:
            return True
        expected_hash = record.get("access_token_hash")
        if expected_hash:
            return _token_hash_matches(access_token, expected_hash)
        # 兼容：落库迁移前驻留内存/库中的明文令牌
        expected = record.get("access_token")
        if expected:
            return _token_matches(access_token, expected)
        return False
    return user is not None and (user.id == owner or user.is_admin)


def _token_matches(given: Optional[str], expected: Optional[str]) -> bool:
    """常量时间比较访问令牌。按 UTF-8 字节比较：compare_digest(str, str)
    遇非 ASCII 会抛 TypeError → 500，畸形令牌应与错误令牌一样被拒绝"""
    if not given or not expected:
        return False
    return secrets.compare_digest(
        str(given).encode("utf-8", "surrogatepass"),
        str(expected).encode("utf-8", "surrogatepass"))


def _token_hash_matches(given: Optional[str], expected_hash: Optional[str]) -> bool:
    """与存储的 SHA-256 摘要比较（存储侧只留摘要，库/内存泄露不等于令牌泄露）"""
    if not given or not expected_hash:
        return False
    return _token_matches(sequencing_store.hash_access_token(given), expected_hash)


def _request_access_token(request: Request) -> Optional[str]:
    """从请求提取匿名记录访问令牌：只认 X-Access-Token 头。

    不再接受 ?token= 查询参数——查询串会进 uvicorn/nginx 访问日志，令牌
    随日志外泄；前端所有调用都走请求头。"""
    return request.headers.get("X-Access-Token") or None


def _get_analysis(analysis_id: str, user: Optional[User] = None,
                  access_token: Optional[str] = None) -> Dict:
    _sweep_expired()
    with _STORE_LOCK:
        result = _ANALYSES.get(analysis_id)
    if result is not None:
        if not _can_access(result, user, access_token):
            raise HTTPException(status_code=403, detail="无权访问该分析记录")
        return result
    # 内存缓存未命中（过期/重启）：先只读记录本体做鉴权，通过后才解压峰图
    # 并回灌缓存，且回灌与登记共用容量淘汰（审查 02 M3：此前无令牌请求
    # 也会解压峰图驻留内存，回灌条数不受 MAX_STORED/MAX_TRACE_STORED 约束）
    result = sequencing_store.load_record_meta(analysis_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if not _can_access(result, user, access_token):
        raise HTTPException(status_code=403, detail="无权访问该分析记录")
    result["_trace_data"] = sequencing_store.load_trace(analysis_id)
    result["_created_ts"] = time.time()
    with _STORE_LOCK:
        cached = _ANALYSES.get(analysis_id)  # 并发回灌：以先到者为准
        if cached is not None:
            return cached
        _ANALYSES[analysis_id] = result
        _enforce_caps_locked()
    return result


def _get_cached_analysis(analysis_id: str, user: Optional[User] = None,
                         access_token: Optional[str] = None) -> Optional[Dict]:
    """只查内存缓存（廉价，可在事件循环上执行）；未命中返回 None"""
    _sweep_expired()
    with _STORE_LOCK:
        result = _ANALYSES.get(analysis_id)
    if result is not None and not _can_access(result, user, access_token):
        raise HTTPException(status_code=403, detail="无权访问该分析记录")
    return result


async def _aget_analysis(analysis_id: str, user: Optional[User] = None,
                         access_token: Optional[str] = None) -> Dict:
    """路由用：缓存命中直接返回；未命中时的回灌（DB 读取、json 解析、峰图
    zlib 解压）在并发闸内移交线程池，不阻塞事件循环（审查 02 M5）"""
    cached = _get_cached_analysis(analysis_id, user, access_token)
    if cached is not None:
        return cached
    return await _run_heavy(_get_analysis, analysis_id, user, access_token)


def _enforce_caps_locked() -> None:
    """内存缓存容量淘汰（登记与回灌共用；调用方须持有 _STORE_LOCK）。

    按进入缓存的时间（_created_ts）排序：回灌的旧记录视为最新访问，
    不会刚回灌就被淘汰。记录超过 MAX_STORED 时删最旧记录；超过
    MAX_TRACE_STORED 时只保留最新记录的峰图，其余记录仍可查看结论。"""
    if len(_ANALYSES) <= min(MAX_STORED, MAX_TRACE_STORED):
        return
    ordered = sorted(_ANALYSES, key=lambda aid: _ANALYSES[aid].get("_created_ts", 0))
    while len(ordered) > MAX_STORED:
        del _ANALYSES[ordered.pop(0)]
    for aid in ordered[:max(0, len(ordered) - MAX_TRACE_STORED)]:
        if _ANALYSES[aid].get("_trace_data"):
            _ANALYSES[aid]["_trace_data"] = {}


async def _read_limited(f: UploadFile, max_bytes: int = MAX_FILE_SIZE) -> bytes:
    """分块读取上传文件，累计超限立即中断——避免先整读进内存再校验被超大文件打爆"""
    name = f.filename or "unknown"
    buf = bytearray()
    while True:
        chunk = await f.read(1024 * 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise HTTPException(
                status_code=400, detail=f"文件过大（>{max_bytes // (1024 * 1024)}MB）: {name}"
            )
    return bytes(buf)


async def _read_ab1_files(files: List[UploadFile]) -> List[Tuple[str, bytes]]:
    if not files or len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"请上传 1-{MAX_FILES} 个 .ab1 文件")
    blobs: List[Tuple[str, bytes]] = []
    for f in files:
        name = f.filename or "unknown.ab1"
        if not name.lower().endswith(".ab1"):
            raise HTTPException(status_code=400, detail=f"仅支持 .ab1 文件，收到: {name}")
        blobs.append((name, await _read_limited(f)))
    return blobs


def _run_full_analysis(
    reference: str,
    sample_name: str,
    features: List[Dict],
    ab1_blobs: List[Tuple[str, bytes]],
    min_q: int,
    allow_decompose: bool,
    circular: bool = True,
    zone: Optional[ZoneParams] = None,
) -> Dict:
    """同步执行全自动分析（调用方负责移交线程池）

    circular：参考是否按环状比对（跨原点 read 整条比对、坐标折回）。"""
    result = analyze(ab1_blobs, reference, features, min_q=min_q, circular=circular,
                     zone_params=zone)

    # 混合样品解卷积（tracy 可用时）：只对 read 级判为「疑似混合样品」
    # （widespread）的 read 执行——个别双峰位点（scattered）多为噪声，
    # 对它们 decompose 只会产出无意义的等位基因
    to_decompose = {
        fn: p.get("positions") or []
        for fn, p in (result.get("mixed_profiles") or {}).items()
        if p.get("class") == "widespread"
    }
    if allow_decompose and to_decompose:
        alleles: Dict[str, List[Dict]] = {}
        with tempfile.TemporaryDirectory() as td:
            ref_fasta = os.path.join(td, "ref.fasta")
            with open(ref_fasta, "w", encoding="utf-8") as fh:
                fh.write(f">{sample_name or 'reference'}\n{reference.upper()}\n")
            by_name = dict(ab1_blobs)
            for filename in to_decompose:
                blob = by_name.get(filename)
                if not blob:
                    continue
                ab1_path = os.path.join(td, "sample.ab1")
                with open(ab1_path, "wb") as fh:
                    fh.write(blob)
                dec = _try_tracy_decompose(ab1_path, ref_fasta)
                if dec:
                    alleles[filename] = dec
        result["decomposed_alleles"] = alleles
        if alleles:
            result["engine"] = "internal+biopython+tracy"
    return result


def _register_analysis(sample_name: str, reference: str, features: List[Dict], result: Dict,
                       owner_id: Optional[str] = None,
                       access_token: Optional[str] = None
                       ) -> Tuple[str, Optional[str], Dict]:
    """把一次完整分析写入内存存储（单样品与批量共用），返回 (analysis_id, access_token, record)

    record 一并返回：打包等后续步骤直接用它，不必回查全局 dict（终审 C-07）。

    批量克隆模式一次可产生上百条记录：分析记录本体（变体/共识/比对）很小，
    上限放宽到 MAX_STORED；峰图原始数据（四通道全分辨率采样）体积大，
    超过 MAX_TRACE_STORED 时淘汰最旧记录的峰图，记录仍可查看结论。
    内存记录 15 分钟后由 _sweep_expired 淘汰（作为读取缓存）；database 模式下
    记录同时落库 sequencing_analyses 表，持久可回看，内存只是缓存。
    """
    _sweep_expired()
    analysis_id = f"seq_{uuid.uuid4().hex[:12]}"
    # 终审 B-02：匿名创建的记录需要随机访问令牌——后续读取/导出/删除必须
    # 携带（登录用户创建的记录走属主校验，不需要令牌）。批量一次产生上百
    # 条记录，整批共用一个令牌（由调用方生成后传入）。
    if owner_id is None and access_token is None:
        access_token = secrets.token_urlsafe(16)
    elif owner_id is not None:
        access_token = None
    record = {
        "analysis_id": analysis_id,
        "sample_name": sample_name,
        "reference": reference.upper(),
        "features": features,
        "created_at": datetime.now().isoformat(),
        "_created_ts": time.time(),
        "owner_id": owner_id,
        # 只存 SHA-256 摘要：明文令牌仅随创建响应下发一次
        "access_token_hash": (sequencing_store.hash_access_token(access_token)
                              if access_token else None),
        **result,
    }
    # trace 峰图数据按 read 序号存放，供 /trace/{read_index} 取用
    record["_trace_data"] = {i: t for i, t in enumerate(result.get("traces", []))}
    # result["traces"] 与 _trace_data 是同一份峰图数据的原始副本，落盘/驻留都是双倍体积
    record.pop("traces", None)
    with _STORE_LOCK:  # 终审 C-07：登记 + 容量淘汰整体串行
        _ANALYSES[analysis_id] = record
        _enforce_caps_locked()
    # 持久化：database 模式下重启/内存过期后记录仍可回看（峰图一并压缩落库）
    sequencing_store.persist_record(record)
    return analysis_id, access_token, record


def _analyze_and_register(
    reference: str, sample_name: str, features: List[Dict],
    ab1_blobs: List[Tuple[str, bytes]], min_q: int, allow_decompose: bool,
    circular: bool, owner_id: Optional[str],
    zone: Optional[ZoneParams] = None,
) -> Tuple[Dict, Optional[str]]:
    """同步：全自动分析 + 登记落库 + 摘要（调用方负责并发闸与线程池）"""
    result = _run_full_analysis(reference, sample_name, features, ab1_blobs,
                                min_q, allow_decompose, circular, zone)
    _aid, access_token, record = _register_analysis(
        sample_name, reference, features, result, owner_id=owner_id)
    return _summary(record), access_token


async def _analyze_endpoint(
    reference: str,
    sample_name: str,
    features: List[Dict],
    files: List[UploadFile],
    min_q: int,
    allow_decompose: bool,
    user: Optional[User] = None,
    circular: bool = True,
    zone: Optional[ZoneParams] = None,
) -> Dict:
    if not reference or len(reference) < 50:
        raise HTTPException(status_code=400, detail="参考序列缺失或过短，无法比对")
    # 终审 C-01：此前只校验 ≥50bp 无上限——局部比对是 O(len(ref)×len(read))
    # 且正反向各做一次完整回溯，实测 300 kb 参考 × 1 条 read = 11 s / 1.09 GB，
    # 单个匿名请求即可打满内存与线程池。质粒/病毒基因组 200 kb 足够覆盖。
    if len(reference) > MAX_REF_BP:
        raise HTTPException(
            status_code=413,
            detail=f"参考序列过长（{len(reference)} bp > {MAX_REF_BP} bp）："
                   "请截取待验证区段后重新分析（局部比对开销随参考长度线性增长）")
    if not files or len(files) > MAX_FILES:  # 数量校验先于占槽
        raise HTTPException(status_code=400, detail=f"请上传 1-{MAX_FILES} 个 .ab1 文件")
    # 审查 02 M5：读入（最多 24×20MB）、比对、登记落库（json+zlib+DB 提交）
    # 全部在同一个并发槽内；同步部分移交线程池，不阻塞事件循环
    async with _heavy_slot():
        ab1_blobs = await _read_ab1_files(files)
        summary, access_token = await run_in_threadpool(
            _analyze_and_register, reference, sample_name, features, ab1_blobs,
            min_q, allow_decompose, circular, user.id if user else None, zone)
    if access_token:
        # 匿名创建：令牌只随创建响应下发一次（前端存会话供后续读取/导出/删除）
        summary["access_token"] = access_token
    return summary


# 参考拓扑判定与批量归组/逐组分析与离线脚本共用 app.sequencing_batch
_reference_circular = reference_circular

_group_batch_uploads = group_batch_uploads
# 缺省 = 运行时读取 MAX_BATCH_FILES（可被测试/配置打桩）；None = 不限文件数
_DEFAULT_MAX_FILES = object()


def _run_batch(
    reads: List[Dict], refs: List[Dict], rows: Optional[List[Dict]],
    ignored: List[str], min_q: int, owner_id: Optional[str] = None,
    access_token: Optional[str] = None, read_limits: Optional[Dict] = None,
    max_files=_DEFAULT_MAX_FILES, zone: Optional[ZoneParams] = None,
) -> Tuple[Dict, List[Dict], List[Dict], Dict[str, Dict]]:
    """同步执行批量分析（调用方负责移交线程池）：归组 → 逐组跑管线 → 一句话结论

    信息表带「克隆号」列时为克隆模式（core.sanger.match_clone_files）：一个
    质粒的每个克隆独立成组、独立分析；同一质粒的参考图谱解析一次供其全部
    克隆复用。否则按质粒归组（旧行为）。归组与逐组分析的实现在
    app.sequencing_batch（与离线脚本共用），这里只加请求级上限与记录登记。

    返回 (payload, groups, raw_unmatched, records_by_id)：payload 为对外 JSON；
    groups 与 raw_unmatched 保留原始文件条目（含字节），供整理包打包使用；
    records_by_id 是本次分析产物的记录快照（终审 C-07：打包不再回查全局
    dict，避免并发淘汰时 KeyError）。

    max_files：单请求入口的文件数上限；分块上传会话传 None——会话的体积与
    文件数已在上传阶段按会话配额限制，批次规模由 read 上限治理。
    """
    groups, unmatched, clone_mode = _group_and_check(
        reads, refs, rows, read_limits, max_files=max_files)

    def _register(label: str, ref_seq: str, features: List[Dict], res: Dict):
        aid, _tok, rec = _register_analysis(
            label, ref_seq, features, res,
            owner_id=owner_id, access_token=access_token)
        return aid, rec

    # 图谱只解析一次：预算估算与逐组比对共用同一份解析结果
    ref_cache = parse_group_references(groups, MAX_REF_BP)
    _check_batch_compute_budget(estimate_batch_cost(groups, ref_cache))
    items, records_by_id = run_batch_groups(
        groups, min_q, _register, max_ref_bp=MAX_REF_BP, ref_cache=ref_cache,
        soft_timeout=BATCH_SOFT_TIMEOUT or None, zone=zone)
    payload = batch_payload(items, unmatched, min_q, rows is not None,
                            clone_mode, ignored)
    return payload, groups, unmatched, records_by_id


def _group_and_check(
    reads: List[Dict], refs: List[Dict], rows: Optional[List[Dict]],
    read_limits: Optional[Dict] = None, max_files=_DEFAULT_MAX_FILES,
) -> Tuple[List[Dict], List[Dict], bool]:
    """归组并执行比对前的全部上限检查（文件数/质粒数/分组数/read 数）。

    只依赖文件名，不读字节：分块上传在创建会话时即可凭文件清单预检，
    超限在上传任何数据之前就拒绝。返回 (groups, unmatched, clone_mode)。"""
    if max_files is _DEFAULT_MAX_FILES:
        max_files = MAX_BATCH_FILES
    if max_files is not None and len(reads) + len(refs) > max_files:
        raise HTTPException(status_code=400, detail=f"文件数超过上限（{max_files} 个）")

    clone_mode = rows is not None and rows_have_clones(rows)
    if clone_mode:
        groups, unmatched = match_clone_files(rows, reads + refs)
        if len({g["plasmid"] for g in groups}) > MAX_BATCH_PLASMIDS:
            raise HTTPException(status_code=400, detail=f"质粒数超过上限（{MAX_BATCH_PLASMIDS} 个）")
        if len(groups) > MAX_BATCH_GROUPS:
            raise HTTPException(status_code=400, detail=f"克隆分组数超过上限（{MAX_BATCH_GROUPS} 个）")
    else:
        ordered, per_plasmid, unmatched = _group_batch_uploads(reads, refs, rows)
        if len(ordered) > MAX_BATCH_PLASMIDS:
            raise HTTPException(status_code=400, detail=f"质粒数超过上限（{MAX_BATCH_PLASMIDS} 个）")
        groups = plasmid_groups(ordered, per_plasmid)
    _check_batch_read_caps(groups, read_limits)
    # 文件名对不上的图谱再按文件内 CDS/gene 名称试一次（只读未匹配的那几张）
    unmatched = rescue_references_by_features(groups, unmatched, rows)
    return groups, unmatched, clone_mode


def _check_batch_compute_budget(cost_bp_reads: int) -> None:
    """估算计算量超过预算时在比对前拒绝（413）"""
    budget = BATCH_COMPUTE_BUDGET_MBP
    if not budget or cost_bp_reads <= budget * 1_000_000:
        return
    raise HTTPException(
        status_code=413,
        detail=f"本批次估算计算量约 {cost_bp_reads / 1_000_000:.1f} Mbp·read"
               f"（各组参考长度 × read 数之和），超过服务器上限（{budget:g} Mbp·read）："
               "请拆分为多个批次提交，或只保留参考图谱中待验证的区段")


def _batch_read_limits_for(user) -> Dict:
    """当前请求者的批量 read 上限（个人覆盖 → 分组设置 → 服务器默认）"""
    return batch_read_limits.effective_limits(
        site_settings.get_settings(), user,
        defaults=(MAX_BATCH_READS_PER_GROUP, MAX_BATCH_TOTAL_READS))


def _check_batch_read_caps(groups: List[Dict], limits: Optional[Dict] = None) -> None:
    """按分组统计将参与比对的 read 数，超限在比对前拒绝（413）。

    只计有参考图谱的分组（无图谱的分组不跑比对，不消耗计算）。limits 为
    _batch_read_limits_for 的结果；缺省按服务器默认（离线/内部调用）。
    提示里写明生效的上限值及其来源，便于用户判断是找管理员还是拆批。"""
    if limits is None:
        limits = batch_read_limits.effective_limits(
            {}, None, defaults=(MAX_BATCH_READS_PER_GROUP, MAX_BATCH_TOTAL_READS))
    per_group = limits[batch_read_limits.PER_GROUP]
    total_cap = limits[batch_read_limits.TOTAL]
    analyzable = [g for g in groups if g["reference"] and g["reads"]]
    for g in analyzable:
        n = len(g["reads"])
        if n > per_group["value"]:
            label = f"{g['clone']} {g['plasmid']}".strip() or g["plasmid"]
            raise HTTPException(
                status_code=413,
                detail=f"分组「{label[:60]}」匹配到 {n} 条测序 read，超过每组上限"
                       f"（{per_group['value']} 条）：该上限来自"
                       f"{batch_read_limits.source_label(per_group)}；"
                       "请检查信息表引物列或文件名归组，或将该质粒拆分后单独分析")
    total = sum(len(g["reads"]) for g in analyzable)
    if total > total_cap["value"]:
        raise HTTPException(
            status_code=413,
            detail=f"本批次共有 {total} 条测序 read 需要比对，超过单次上限"
                   f"（{total_cap['value']} 条）：该上限来自"
                   f"{batch_read_limits.source_label(total_cap)}；"
                   "请拆分为多个批次提交，或联系管理员调整上限")


async def _read_batch_uploads(files: List[UploadFile], excel: Optional[UploadFile]):
    """批量上传读入与分类（调用方须已占并发槽），返回 (reads, refs, ignored, excel_bytes)"""
    blobs: List[Tuple[str, bytes]] = []
    # 终审 C-02：批量接口此前无请求级总字节上限——400 文件 × 20MB = 8 GB
    # 会先全部读进内存。单文件上限挡不住「多文件叠加」，这里按累计值
    # 在读取过程中即时中断（未读的文件不再读）
    total_bytes = 0
    for f in files:
        blob = await _read_limited(f)
        total_bytes += len(blob)
        if total_bytes > MAX_BATCH_REQUEST_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"本次上传总字节超过上限（>{MAX_BATCH_REQUEST_BYTES // (1024 * 1024)}MB），"
                       "请拆分为多个批次提交")
        blobs.append((f.filename or "unnamed", blob))
    reads, refs, ignored = _classify_batch_blobs(blobs)

    excel_bytes = None
    if excel is not None:
        excel_bytes = await _read_limited(excel)
        # 信息表同样计入本次请求总字节（此前只累计测序/图谱文件）
        if excel_bytes and total_bytes + len(excel_bytes) > MAX_BATCH_REQUEST_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"本次上传总字节超过上限（>{MAX_BATCH_REQUEST_BYTES // (1024 * 1024)}MB），"
                       "请拆分为多个批次提交")
        _check_batch_excel(excel.filename, excel_bytes)
    return reads, refs, ignored, excel_bytes


def _classify_batch_blobs(blobs: List[Tuple[str, bytes]]):
    """[(文件名, 字节)] → (reads, refs, ignored)：跳过 ~$ 临时文件与空文件，
    按扩展名分为测序 read / 参考图谱 / 忽略。单请求入口与分块上传 finalize
    共用，两条路径的归类完全一致（字节为 None 时视为非空，供文件清单预检）。

    分块 finalize 传入 DiskBlob：条目包装为 LazyFileEntry，内容按需从磁盘
    读取，整批不必同时驻留内存。"""
    reads: List[Dict] = []
    refs: List[Dict] = []
    ignored: List[str] = []
    for name, blob in blobs:
        name = name or "unnamed"
        on_disk = isinstance(blob, DiskBlob)
        if name.startswith("~$") or (blob.size == 0 if on_disk else blob == b""):
            continue
        ext = os.path.splitext(name)[1].lower()
        entry = {
            "name": name, "ext": ext, "bytes": blob,
            "stem": norm_stem(os.path.splitext(os.path.basename(name))[0]),
        }
        if on_disk:
            entry = LazyFileEntry(entry, blob)
        if ext in READ_EXTS:
            reads.append(entry)
        elif ext in REF_EXTS:
            refs.append(entry)
        else:
            ignored.append(name)

    if not reads and not refs:
        raise HTTPException(
            status_code=400,
            detail="未找到可用的测序文件（.ab1）或参考图谱（.dna/.gb/.fasta）")
    return reads, refs, ignored


def _check_batch_excel(filename: Optional[str], excel_bytes: bytes) -> None:
    if not excel_bytes:
        raise HTTPException(status_code=400, detail="信息表文件为空")
    if (filename or "").startswith("~$"):
        raise HTTPException(
            status_code=400,
            detail="所选信息表是 Excel 临时文件（~$ 开头），请选择正式的信息表 .xlsx")


def _load_batch_excel(excel_bytes: bytes):
    """流式解析信息表；格式问题统一转 400 中文提示。返回 (cols, rows)。

    只读流式解析不构建整表对象；回填结论时再单独以可写模式打开"""
    try:
        _header, cols, rows = parse_excel(excel_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (InvalidFileException, BadZipFile):
        raise HTTPException(
            status_code=400,
            detail="信息表不是有效的 .xlsx 文件（可能正被 Excel/WPS 占用或已损坏），请关闭后重新选择")
    return cols, rows


def _entry_size(entry: Dict) -> int:
    """文件条目的字节数（磁盘条目取记录的大小，不为计数把文件读进内存）"""
    if isinstance(entry, LazyFileEntry):
        return entry.blob.size
    return len(entry["bytes"])


def _batch_pipeline(
    reads: List[Dict], refs: List[Dict], ignored: List[str], min_q: int,
    excel_name: Optional[str], excel_bytes: Optional[bytes],
    owner_id: Optional[str], batch_token: Optional[str],
    read_limits: Optional[Dict] = None, max_files=_DEFAULT_MAX_FILES,
    zone: Optional[ZoneParams] = None,
) -> Dict:
    """同步：信息表解析 → 批量分析 → 整理包打包（调用方负责并发闸与线程池）"""
    excel_pack = None
    excel_rows = None
    if excel_bytes is not None:
        cols, excel_rows = _load_batch_excel(excel_bytes)
        excel_pack = (excel_name, excel_bytes, cols, excel_rows)

    payload, groups, raw_unmatched, records_by_id = _run_batch(
        reads, refs, excel_rows, ignored, min_q,
        owner_id=owner_id, access_token=batch_token, read_limits=read_limits,
        max_files=max_files, zone=zone)
    if batch_token:
        payload["access_token"] = batch_token

    # 整理包（离线脚本产物的网页版）：按上传原始字节现场打包并缓存，15 分钟内
    # 可重复下载。体积超上限时跳过打包（report_ready=False，前端不显示下载入口）
    total_bytes = sum(_entry_size(e["file"])
                      for g in groups
                      for e in ([g["reference"]] if g["reference"] else []) + g["reads"])
    total_bytes += sum(_entry_size(u["file"]) for u in raw_unmatched)
    report_ready = total_bytes <= min(MAX_BATCH_CACHE_BYTES, MAX_BATCH_CACHE_TOTAL_BYTES)
    payload["report_ready"] = report_ready
    if report_ready:
        excel_original = excel_filled = None
        if excel_pack:
            excel_name, excel_original, cols, rows = excel_pack
            # 回填需要可写的完整工作簿：只在真正打包时加载，用完即弃
            excel_filled = backfill_excel(open_excel_for_write(excel_original), cols,
                                          rows, payload["items"], payload["clone_mode"])
        # 终审 C-07：打包所需的记录来自本次 _run_batch 的直接产物
        # （records_by_id），不再事后回查全局 dict——回查会在记录被
        # 并发淘汰时 KeyError
        zip_bytes = build_batch_zip(payload, groups, raw_unmatched, excel_name,
                                    excel_original, excel_filled, records_by_id)
        saved = batch_report_store.save(payload["batch_id"], zip_bytes, {
            "created_ts": time.time(),
            "zip_name": datetime.now().strftime("测序整理_%Y%m%d_%H%M"),
            # 终审 B-03：整理包含全部原始 .ab1 与图谱，下载绑定创建者；
            # 匿名批次绑定整批访问令牌
            "owner_id": owner_id,
            "access_token_hash": (sequencing_store.hash_access_token(batch_token)
                                  if batch_token else None),
        })
        del zip_bytes
        # 写入失败（目录不可写/磁盘满）时不提供下载入口，分析结果照常返回
        payload["report_ready"] = saved
        # 写入后再淘汰：条数与总字节上限在每次写入后立即恢复
        _sweep_reports()
    return payload


def _summary(record: Dict) -> Dict:
    """对外摘要：剔除参考序列与 trace 原始数据"""
    reads = []
    for i, r in enumerate(record["reads"]):
        reads.append({
            "index": i,
            "filename": r["filename"],
            "sample_name": r.get("sample_name", ""),
            "raw_length": r["raw_length"],
            "trimmed_length": r["trimmed_length"],
            "mean_q": r["mean_q"],
            "grade": r.get("grade"),
            "q20_ratio": r.get("q20_ratio"),
            # read 可信区（位置先验 + Q 值与峰型逐条判定）；旧记录缺省
            **({"reliable_zone": {k: r["reliable_zone"].get(k) for k in (
                "start", "end", "raw_start", "raw_end", "front_reason", "tail_reason")}}
               if r.get("reliable_zone") else {}),
            "direction": r["alignment"]["direction"],
            "ref_start": r["alignment"]["ref_start"],
            # 环状参考跨原点的 read：ref_end 为展开坐标（> reference_length，
            # 按 ((p-1) % L) + 1 折回），wraps_origin/ref_segments 给出折回
            # 后的两段；线性/不跨原点时两字段缺省（向后兼容）
            "ref_end": r["alignment"]["ref_end"],
            **({"wraps_origin": True,
                "ref_segments": r["alignment"].get("ref_segments")}
               if r["alignment"].get("wraps_origin") else {}),
            # 终审 A-20：对齐块在原始电泳 read 内的 1-based 起止（软剪切
            # 偏移），前端 origIdx 换算与 read2ref 同口径
            "query_start": r["alignment"].get("query_start"),
            "query_end": r["alignment"].get("query_end"),
            "identity": r["alignment"]["identity"],
            "mixed_positions": r.get("mixed_positions", []),
            "mixed_detail": r.get("mixed_detail", []),
            "mixed_class": (r.get("mixed_profile") or {}).get("class"),
            "mixed_count": (r.get("mixed_profile") or {}).get("count", 0),
            # 逐列对齐视图（read 与参考的原始证据，供人工核对）
            "alignment_view": r["alignment"].get("aligned"),
        })
    return {
        "analysis_id": record["analysis_id"],
        "sample_name": record.get("sample_name", ""),
        "created_at": record["created_at"],
        "engine": record["engine"],
        "conclusion": record["conclusion"],
        "reads": reads,
        "variants": record["variants"],
        "consensus": record["consensus"],
        "coverage_ranges": record["coverage_ranges"],
        "coverage_gaps": record.get("coverage_gaps", []),
        # 参考是否按环状比对（旧记录无此字段，按 False 呈现）
        "circular": bool(record.get("circular", False)),
        "cds_reports": record.get("cds_reports", []),
        "homopolymers": record.get("homopolymers", []),
        "mixed_detected": record.get("mixed_detected", {}),
        "mixed_profiles": record.get("mixed_profiles", {}),
        "decomposed_alleles": record.get("decomposed_alleles", {}),
        "errors": record["errors"],
        "reference_length": len(record["reference"]),
        "features": record["features"],
    }


def _mask_uncovered(seq: str, segs: List[Tuple[int, int]]) -> str:
    """未测位置按 N 屏蔽（区间为 1 起始闭区间，坐标与特征不位移）。

    线性复杂度：此前逐碱基扫描全部区间是 O(len × 区间数)，200 kb 共识
    配上碎片化覆盖时可达秒级"""
    out = ["N"] * len(seq)
    for s, e in segs:
        lo, hi = max(s, 1) - 1, min(e, len(seq))
        if lo < hi:
            out[lo:hi] = seq[lo:hi]
    return "".join(out)


def _render_consensus_export(record: Dict, analysis_id: str, format: str,
                             covered_only: bool) -> Tuple[str, str, str]:
    """组装共识序列导出内容，返回 (content, ext, safe_name)。

    纯 CPU 工作（N 屏蔽、逐行排版），由路由放进线程池执行"""
    seq = record["consensus"]["sequence"]
    # 文件名白名单过滤（统一实现 app/filename.py）：sample_name 源自用户上传
    # 文件名，未过滤可注入 Content-Disposition 响应头（引号/CR/LF）
    safe_name = sanitize_filename(f"{record['sample_name']}-consensus",
                                  fallback="consensus")
    # 实测片段：新记录带 consensus.segments（共识下标，插入/缺失后仍对得上）；
    # 旧记录退回参考坐标的 coverage_ranges（无插入缺失时两者一致）
    read_segs = (record["consensus"].get("segments") or []) if covered_only else []
    if read_segs:
        segs = [(int(g["cons_start"]), int(g["cons_end"])) for g in read_segs]
        ref_spans = [(int(g["ref_start"]), int(g["ref_end"])) for g in read_segs]
    else:
        segs = ([(int(s), int(e)) for s, e in (record.get("coverage_ranges") or [])]
                if covered_only else [])
        ref_spans = list(segs)

    if format.lower() == "genbank":
        # poly run 判读注释（Step B verdict）：确证的重复区写入确认注释，
        # 矛盾/不可判定的显式标注——导出物脱离本系统后仍能自证判读状态
        verdict_note = {
            "accepted": "重复数确证 {obs} 个（峰图互证一致{joint}）",
            "deficit_observed": "可见缺失证据，实测约 {obs} 个（以峰图为准，计数不确证）",
            "contradictory": "可分辨 read 计数互证矛盾，重复数不可信",
            "undetermined": "峰合并不可判读，重复数不可判定（宽度法估计 {est} 个）",
        }
        feats: List[str] = []
        for e in record.get("homopolymers") or []:
            if e.get("tier") != "poly" or e.get("run_verdict") not in verdict_note:
                continue
            label = f"poly({e['base']})" if e.get("base") else "repeat"
            txt = verdict_note[e["run_verdict"]].format(
                obs=e.get("observed_repeat_count"),
                joint="；联合覆盖拼接" if e.get("joint_coverage") else "",
                est=e.get("length_estimate"),
            )
            feats.append(format_feature_line("misc_feature", f"{e['start']}..{e['end']}\n")
                         + format_qualifier("note", f"{label} {e['start']}-{e['end']}：{txt}"))
        definition = "Sanger consensus sequence"
        if segs:
            # 未测位置 N 屏蔽：坐标与特征不位移，下游软件可直接定位实测段
            seq = _mask_uncovered(seq, segs)
            feats = [
                format_feature_line("misc_feature", f"{s}..{e}\n")
                + format_qualifier(
                    "note",
                    f"实测覆盖区 {s}-{e}"
                    + ("" if (s, e) == (rs, re_) else f"，参考 {rs}-{re_}")
                    + "（未测位置已按 N 屏蔽）")
                for (s, e), (rs, re_) in zip(segs, ref_spans)
            ] + feats
            definition = "Sanger consensus (unverified positions N-masked)"
        lines = [
            # 共识序列由线性 read 拼接而来，按 linear 标注
            format_locus_line(safe_name, len(seq), topology="linear"),
            f"DEFINITION  {definition}",
            f"ACCESSION   {analysis_id}",
            "FEATURES             Location/Qualifiers",
            *feats,
            "ORIGIN",
        ]
        lines.extend(render_origin_block(seq))
        lines.append("//")
        content = "\n".join(lines)
        ext = "gb"
    else:
        # FASTA 头带判读摘要：确证 run 数 / 存疑 run 数
        hps = [e for e in record.get("homopolymers") or [] if e.get("tier") == "poly"]
        ok_n = sum(1 for e in hps if e.get("run_verdict") == "accepted")
        bad = [e for e in hps if e.get("run_verdict") in ("contradictory", "undetermined")]
        flag = ""
        if bad:
            spans = "、".join(f"{e['start']}-{e['end']}" for e in bad[:3])
            flag = f" poly_unverified={spans}" + ("..." if len(bad) > 3 else "")
        elif hps:
            flag = f" poly_verified={ok_n}/{len(hps)}"
        if segs:
            # 每段连续覆盖区间一条记录——不携带未测的参考填充序列
            records = []
            for i, ((s, e), (rs, re_)) in enumerate(zip(segs, ref_spans), 1):
                records.append(
                    f">{safe_name}_verified_{rs}-{re_} ref_pos={rs}-{re_}"
                    f" region={i}/{len(segs)}{flag}\n{seq[s - 1:e]}")
            content = "\n".join(records)
        else:
            content = (
                f">{safe_name} coverage={record['consensus']['coverage_percent']}%"
                f"{flag}\n{seq}"
            )
        ext = "fasta"

    return content, ext, safe_name
