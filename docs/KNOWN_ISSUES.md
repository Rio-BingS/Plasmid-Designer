# 已知问题清单（2026-08-31 代码审计）

> 状态标记：⬜ 未修复 / 🔧 进行中 / ✅ 已修复
>
> 来源：针对公网 VPS（1 核 2G）部署场景的代码审计。本文档只记录问题与修复建议，
> 未改动任何代码。修复时请逐项更新状态并在文末登记变更。
>
> **✅ 全部 12 项已修复并通过验证（2026-08-31）。**
>
> ## 验证结果汇总
> - **pytest：121 passed / 0 failed**（修复前基线 93 项时套件已扩充至 121，全绿）
> - **冒烟测试：20 通过 / 0 失败**（`smoke_test.ps1` 实测，STORAGE_MODE=database 默认模式）：
>   设计全流程、analysis 四端点（现经线程池执行）、载体 9/9、注册/登录/me、批量任务 ZIP
> - **定向验证 13 项全过**：伪造 XFF 打同一限流桶（第 6 次 429）、可信代理采信
>   X-Real-IP/XFF 末段、限流 key 上限回收、CORS 三种写法解析、MemoryCache/内存存储
>   上限淘汰、DATABASE_URL 单一来源、上传文件名白名单、缓存延迟初始化、lifespan 建表
>
> 严重程度：🔴 高危（安全底线，公网部署前必须处理）/ 🟡 中危（稳定性，影响可用性）/
> 🟢 低危（顺手可修的加固项）

---

## 🔴 高危

### 1.1 docker-compose 将 PostgreSQL 与 Redis 暴露到公网 ✅

- **位置**：`deploy/docker/docker-compose.yml`（db 服务 `5432:5432`、redis 服务 `6379:6379`）
- **现象**：两个端口映射到宿主机所有网卡；Redis 未设密码，PostgreSQL 使用 compose 内
  默认口令 `plasmid_secure_2026`。
- **影响**：公网部署时 Redis 未授权访问（可清库、可借写文件提权），PostgreSQL 可被暴力破解。
- **建议**：删除这两个 `ports` 映射（容器网络内互通不需要对外暴露），或改为
  `127.0.0.1:5432:5432` 仅本机访问；同时为 Redis 配置 `requirepass`。

### 1.2 限流可被伪造 X-Forwarded-For 绕过 ✅

- **位置**：`src/backend/app/rate_limit.py:136-141`（`get_client_ip` 取 XFF 第一段）；
  `deploy/docker/nginx.conf:35`（`$proxy_add_x_forwarded_for` 保留客户端自带的 XFF 前缀）
- **现象**：客户端每次请求携带不同的伪造 `X-Forwarded-For` 头，即可获得全新的限流 key。
- **影响**：所有限流失效，包括 `auth` 的 5 次/分钟（密码可暴破）；同时伪造的 IP 会作为
  key 永久留在 `InMemoryRateLimiter._requests` 字典中（`rate_limit.py:32`），构成内存泄漏。
- **建议**：`get_client_ip` 改为信任 nginx 已设置的 `X-Real-IP`，或只取 XFF 的**最后一段**
  （即本机前置代理追加的那段）；限流器增加 key 数量上限或定期清理。

### 1.3 CORS 星号 + 凭证组合，且配置项未接线 ✅

- **位置**：`src/backend/app/main.py:33-39`（硬编码 `allow_origins=["*"]` +
  `allow_credentials=True`）；`src/backend/app/config.py:48`（`CORS_ORIGINS` 定义后从未被引用）
- **影响**：Bearer token 模式下 CSRF 实际风险有限，但「在 `.env` 里改 CORS 不会生效」
  是隐性配置陷阱。
- **建议**：main.py 改为引用 `settings.CORS_ORIGINS`，生产环境配置具体域名；
  通配源与凭证组合本身也不符合 CORS 规范。

---

## 🟡 中危

### 2.1 NCBI 导入与序列分析阻塞事件循环 ✅

- **位置**：`src/backend/app/routes/vector_routes.py`（全部端点为 `async def`，却同步调用
  NCBI 客户端——单次超时 30s、限速间隔 0.6s，见 `core/external_vector_importer.py:60`）；
  `src/backend/app/analysis_routes.py`（ORF/GC 分析等 CPU 密集计算直接跑在事件循环上）
- **影响**：一次 NCBI 导入期间整个后端对所有用户无响应；长序列分析时单核机器全站卡死。
  `/api/design/batch` 的批量导入会成串阻塞。
- **建议**：外部 HTTP 调用与 CPU 密集计算改用 `anyio.to_thread.run_sync` 或
  `loop.run_in_executor`（设计主路径的 `run_design_task` 已是同步函数交线程池，可参考）。
- **备注**：这是 1 核 VPS 上使用体验的第一瓶颈，优先级高于其余中危项。

### 2.2 三处进程内存储只涨不降 ✅

- **位置**：
  - `src/backend/app/cache.py:119-166` `MemoryCache` 惰性过期——仅再次访问的 key 才删除，
    无人再查的设计结果/密码子优化缓存永久驻留（设计结果 TTL 24h、载体 7 天形同虚设）
  - `src/backend/app/rate_limit.py:32` 限流 key 无清理（另见 1.2）
  - `src/backend/app/storage/memory_store.py` 设计/批量结果 dict 无淘汰上限
- **影响**：长期常驻进程内存缓慢增长；对 HF 演示无碍，对常驻 VPS 是慢性病。
- **建议**：MemoryCache 写入时顺带清理过期项，或增加最大条目数（如 LRU 上限）。

### 2.3 多 worker 与进程内状态互相冲突 ✅

- **位置**：`deploy/docker/Dockerfile.backend:75`（`--workers 2`）；进程内状态包括
  `batch_jobs` 字典（`routes/batch_routes.py:31`）、内存限流器、内存缓存。
- **影响**：限流实际放宽为两倍；memory 模式下批量进度查询可能落在另一 worker 上返回 404
  （database 模式有 DB 兜底不受影响）。
- **建议**：小机器单 worker；或把限流/缓存外移 Redis——`RedisRateLimiter`
  （`rate_limit.py:80`）已实现但从未接线。workers 数建议改为环境变量可配置。

### 2.4 DATABASE_URL 双来源，`.env` 对实际建库不生效 ✅

- **位置**：`src/backend/app/config.py:32`（pydantic Settings，读 `.env`，默认 postgres）vs
  `src/backend/app/database/models.py:13`（`os.getenv`，**不读 `.env`**，默认 sqlite）
- **现象**：只在 `.env` 写 `DATABASE_URL=postgresql://...` 时，实际仍静默使用 SQLite。
- **建议**：models.py 改为引用 `settings.DATABASE_URL`，单一来源。
- **附带**：README 写 `STORAGE_MODE=database`（默认），而 `storage/__init__.py:6` 代码默认
  是 `memory`——文档与代码有一处说反了，需统一。

### 2.5 nginx 未设 `client_max_body_size` ✅

- **位置**：`deploy/docker/nginx.conf`（无该指令，默认 1MB）
- **影响**：稍大的 GenBank 载体文件上传即 413。
- **建议**：`/api` location 中加 `client_max_body_size 10m;`（量级按需调整）。

---

## 🟢 低危

### 3.1 批量持久化失败静默吞掉 ✅

- **位置**：`src/backend/app/routes/batch_routes.py:36-40` `_persist_batch`
  的 `except Exception: pass`
- **建议**：至少 `logger.warning` 记录。

### 3.2 弃用 API 与默认值 ✅

- **位置**：`src/backend/app/main.py:97` `@app.on_event("startup")`（新版 FastAPI 已弃用，
  应改 lifespan）；`src/backend/app/config.py:25` `DEBUG` 默认 `True`。

### 3.3 上传载体文件写入路径加固 ✅

- **位置**：`src/backend/app/routes/vector_routes.py` `upload_vector_file`——上传的 YAML
  直接写入 `VECTORS_DIR`（与内置模板同目录），文件名仅过滤 `/` 未过滤 `\`。
- **影响**：Linux 部署无碍；Windows 下 `\` 可构成路径分隔，属加固项。
- **建议**：文件名做白名单字符过滤；考虑上传文件与内置模板分目录存放。

### 3.4 缓存后端启动时序 ✅

- **位置**：`src/backend/app/cache.py:333` `cache = CacheManager()` 在模块导入时连接 Redis。
  若容器启动顺序中 Redis 晚于后端就绪，将永久回退内存缓存直到重启。
- **建议**：延迟初始化或首次使用时重试连接。

---

## 部署备忘（1 核 2G VPS，非代码问题）

1. **前端不要在 VPS 上构建**：`Dockerfile.frontend` 多阶段构建中的 `vite build` 峰值内存
   可能超 1G，小机器上易 OOM。应本地构建后仅上传 `dist/`。
2. **推荐裸机部署**：SQLite（`DATABASE_URL` 默认即可）+ `REDIS_ENABLED=false` +
   `--workers 1`，总内存占用约 500MB 内；Docker 全家桶（约 0.9–1.2G）可用但余量小。
3. **瓶颈是 CPU 不是内存**：密码子优化/引物设计为 CPU 密集纯 Python 计算，批量任务
   串行执行（对单核反而友好），单条设计秒级、批量明显排队属预期行为。

---

## 做得好的方面（无需改动）

- NCBI 请求有 30s 超时；序列输入 `max_length=100_000`、批量限 100 条
  （`routes/models.py:83,161`）
- JWT 未配置 SECRET_KEY 时启动告警（`auth/jwt_auth.py:28`）
- bcrypt 版本锁定 `>=4,<5`，规避 passlib 1.7.4 兼容问题
- 批量设计逐条串行执行，对单核机器友好
- 生信算法纯标准库实现，运行时内存占用小（约 150–250MB）

---

## 修复记录

> 每完成一项，在此追加：日期 / 编号 / 简述 / 验证方式。

| 日期 | 编号 | 简述 | 验证方式 |
|------|------|------|----------|
| 2026-08-31 | 1.1 | `docker-compose.yml`：db/redis 端口映射改绑 `127.0.0.1`（不再暴露公网）；Redis 增加 `--requirepass`（`REDIS_PASSWORD` 可覆盖，默认与 backend 的 `REDIS_URL` 一致）；healthcheck 带认证 | 文件核查；compose 配置一致性人工核对 |
| 2026-08-31 | 1.2 | `rate_limit.py`：`get_client_ip` 重写——仅当 socket 对端为回环/私网（可信代理）时采信 nginx 覆写的 `X-Real-IP`，XFF 只取最后一段；公网直连一律用对端地址。`InMemoryRateLimiter` 增加 `MAX_TRACKED_KEYS=10000` 上限与超限回收 | 定向验证：TestClient 连发 6 次不同 XFF 登录全部计入同一 key，第 6 次 429；key 超限回收实测 10000 上限 |
| 2026-08-31 | 1.3 | `main.py` 接线 `settings.CORS_ORIGINS`；通配源时自动关闭 `allow_credentials`（消除规范冲突组合）。`config.py` 增加 validator，支持逗号分隔与 JSON 数组两种 `.env` 写法 | 定向验证三种写法解析；pytest 全绿 |
| 2026-08-31 | 2.1 | `vector_routes.py` 全部 NCBI 导入/搜索/预览/上传解析/批量导入改走 `run_in_threadpool`；`analysis_routes.py` 的 analyze/restriction-sites/orfs/digest/gc-analysis/compatibility/export 全部移交线程池 | 冒烟测试 analysis 四端点 + 导出全过；pytest 全绿 |
| 2026-08-31 | 2.2 | `MemoryCache` 写入时顺带清理过期项 + `MAX_ENTRIES=5000` 按最早到期淘汰，并补线程锁（配合线程池并发）；`MemoryDesignStore`（1000）/`MemoryBatchStore`（500）增加条目上限按写入顺序淘汰；限流 key 上限见 1.2 | 定向验证：写入 5200 条淘汰至 5000，短 TTL 优先淘汰且未误删存活项；pytest 全绿 |
| 2026-08-31 | 2.3 | `Dockerfile.backend` 改 shell 形式 CMD，workers 由 `BACKEND_WORKERS` 环境变量配置、默认 1；compose 注入该变量。Redis 限流器接线维持不选（推荐部署为单 worker，进程内限流即够用，见部署备忘） | 文件核查；定向验证确认 Dockerfile CMD 读取环境变量 |
| 2026-08-31 | 2.4 | `config.py` `DATABASE_URL` 默认改为 `DATA_DIR` 下 SQLite；`database/models.py` 改引 `settings.DATABASE_URL`（并自动创建 SQLite 父目录），.env 配置从此生效；`storage/__init__.py` 默认 `STORAGE_MODE=database` 与 README 对齐 | 定向验证：models 与 settings 同源、SQLite 目录自动创建；pytest 全绿 |
| 2026-08-31 | 2.5 | `nginx.conf` `/api` location 增加 `client_max_body_size 10m;` | 文件核查 |
| 2026-08-31 | 3.1 | `batch_routes.py` `_persist_batch` 异常改为 `logger.warning` 留痕 | 代码审查；pytest 全绿 |
| 2026-08-31 | 3.2 | `main.py` `@app.on_event("startup")` 改为 `lifespan` 上下文；`config.py` `DEBUG` 默认 `False` | 定向验证 lifespan 启动建表正常（users/designs/batch_jobs 等 9 表）；pytest 全绿 |
| 2026-08-31 | 3.3 | `vector_routes.py` 新增 `_safe_filename` 白名单（仅 `[A-Za-z0-9._-]`），上传载体 YAML 输出名不再受 `vector.name` 中 `\` 等字符影响；顺带修复 `file.filename` 为 None 时 500。上传与内置模板分目录存放暂不做（白名单已消除路径注入，且现有 delete/update 按目录扫描逻辑依赖同目录） | 定向验证：`..\..\evil <x>`、`a/b\c:d`、`///` 均归一为安全文件名 |
| 2026-08-31 | 3.4 | `cache.py` `CacheManager.backend` 改为延迟初始化 property：首次使用才连 Redis，连不上回退内存并每 60s 重试升级回 Redis（`REDIS_RETRY_SECONDS` 可调） | 定向验证：实例化不触发连接（Redis 未启动时首用解析为 MemoryCache）；pytest 全绿 |
| 2026-09-04 | 4.1 | 修复反向互补映射错误：`primer_designer.py` `cross_hybridization_count` 与 `codon_optimizer.py` `_five_prime_hairpin_count` 误用 `maketrans("ATGC","TAGC")`（G/C 映射到自身），改为正确的 `maketrans("ATGC","TACG")`；交叉杂交检查同步排除目标区域重叠的相邻 oligo（预期 overlap 配对不再计入）；删除 `_smooth_gc` 中未使用的死变量。| 300bp 随机序列实测：修复前交叉杂交恒为 0（漏检）、GC 茎发夹漏检；修复后均正确检出。新增回归测试 4 项，pytest 126 passed |
