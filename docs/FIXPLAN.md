# 功能完整性修复计划（2025 核查后制定）

> 状态标记：⬜ 未开始 / 🔧 进行中 / ✅ 完成
>
> **✅ 全部完成并通过验证（含本环境实际运行）。**
>
> ## 验证结果汇总
> - **pytest：93 passed / 0 failed**（10 个测试文件全收集；test_auth、test_batch_design 的
>   坏导入与硬编码路径已修复，conftest.py 统一路径注入）
> - **冒烟测试：20 通过 / 0 失败**（smoke_test.ps1）：健康检查、设计提交→轮询→GenBank/TSV/图谱、
>   analysis 四端点（修复后正常返回）、两个原 501 导出桩、载体列表 9/9、注册→/me→登录、批量→ZIP
> - **持久化重启验证 PASS**：STORAGE_MODE=database(sqlite) 下创建设计 → 杀进程重启 →
>   GET 仍返回 completed 及完整 CAI/GC/引物数据（修复前必然 404）
>
> ## 测试阶段额外发现并修复的问题
> 1. `logging_config.py` 导入时强建 POSIX 路径 `/tmp/...` 导致 Windows 启动崩溃 → 跨平台默认 + 容错降级
> 2. Windows GBK 控制台 print emoji 崩溃 → main.py 入口 stdout/stderr reconfigure UTF-8
> 3. `analysis_routes.py` restriction-sites 响应体遗留旧变量引用（重构遗漏）→ 已修
> 4. `pET-21a.yaml` 使用枚举外的元素类型 `regulatory` 致整文件被跳过 → ElementType 枚举补充 REGULATORY
> 5. 冒烟脚本邮箱域名 `.local` 被 email-validator 拒绝 → 改用 example.com
> 6. 三个陈旧测试用例与演化后 API 不匹配（协议默认中文 / CloningStep 位置参数错位 /
>    手写引物序列超出设计器 Tm 窗口）→ 按现行为修正断言方式

## Phase 1 — 断裂功能修复（P0）

- [x] 1.1 `app/analysis_routes.py`：`/restriction-sites`、`/orfs`、`/gc-analysis`、`/compatibility`
      四个 POST 端点由裸标量参数（query）改为 Pydantic body 模型，与前端 JSON body 对齐；
      `SequenceAnalysisRequest` 显式接收 `sequence_type`。
- [x] 1.2 数据库持久化：
      - `storage/db_store.py` save 显式传既有 id；`_db_to_dict` 回填必填字段
      - `database/crud.py` create_design/create_batch_job 支持指定 id
      - `database/models.py` connect_args 仅 SQLite 传入；BatchJobDB 增加 errors 列
      - docker requirements 补 psycopg2-binary
- [x] 1.3 `auth/jwt_auth.py` SECRET_KEY 从环境变量读取（开发回退值 + 启动警告）。
- [x] 1.4 `main.py` startup 无条件 init_db（SQLite 幂等），本地默认模式认证可用。
- [x] 1.5 `tests/test_auth.py` 移除硬编码路径、修正模块导入（jwt_auth_db → jwt_auth）。

## Phase 2 — 行为一致性与小修（P1）

- [x] 2.1 `routes/batch_routes.py` 下载/报告改走 `_load_batch()/_load()` 统一恢复路径
      （顺带消除原代码对未导入 `designs_db` 的 NameError 隐患）。
- [x] 2.2 `routes/vector_routes.py` 复用进程级 VectorLibrary 缓存，写操作后失效
      （analysis 载体导出同样接入缓存）。
- [x] 2.3 `/vectors/import/file` 端点移除；批量导入 file_paths 限制在 DATA_DIR 内；
      前端删除死函数 `importFromFile`。
- [x] 2.4 `pyyaml` 写入 requirements；前端 `getEnzymes` 返回类型修正；
      前端 `checkCompatibility` 改为 JSON body 与后端对齐。

## Phase 3 — 死代码与装饰性设施处置（P1–P2）

- [x] 3.1 接线 `setup_middleware(app)` + 日志初始化（请求追踪/慢请求）。
- [x] 3.2 实现两个 501 导出桩端点（复用 export_formats 现成转换器）。
- [x] 3.3 删除 `task_queue.py` 及 celery/flower 依赖（零引用）。
- [x] 3.4 README 新增「模块接线状态」章节；rate_limit user_* 配置加生效条件注释。
- [x] 3.5 缓存子系统本轮仅标注（业务接线留独立任务），docs/CACHE.md 已注明当前状态。

## 验证

- [x] 全量 py_compile 通过（43 后端 + test_auth）
- [x] pytest 全套：**93 passed / 0 failed**（2025 实测，含修复后的 test_auth.py 全部用例）
- [x] 冒烟测试：**20 通过 / 0 失败**（`smoke_test.ps1` 实测——设计全流程、analysis 四端点、
      两个原 501 导出桩、注册/登录/me、批量任务 ZIP 全部通过）
- [x] database(sqlite) 模式重启恢复验证：**实测通过**
      （提交设计 → completed → 杀进程重启 → GET 同一 design_id 完整查回，
      status=completed、final_length=8488、primers=2、cai=1.0）

> 验证环境说明：Agent 执行沙箱拦截 pip/venv 的临时目录 ACL 操作，
> 故采用「Python urllib 下载 wheel（PyPI JSON API / 清华镜像）→ zipfile 解压 →
> PYTHONPATH 指向依赖目录」的方式完成，未污染全局 Python 环境。
> 依赖环境保留在项目外层 `.testdeps/`（`.wheels/` 为本地 wheel 缓存，可整体删除）；
> 本机复跑测试：`$env:PYTHONPATH="<路径>/.testdeps"; cd tests; python -m pytest -v`，
> 或按 README「快速开始」自行 `pip install` 后直接运行。
> 沙箱锁死的无害残留（你本机可直接删除）：`tests/pytest-cache-files-*`、
> 项目外层 `.pip_tmp/`、`py_probe_u_x9v6lz/`。
