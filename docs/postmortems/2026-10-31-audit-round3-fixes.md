# 2026-10-31 第三轮审计修复（全量扫描）

## 现象

用户要求「遍历项目所有文件，找出潜在 Bug、安全隐患、配置错误及死代码」。距上一轮
（2026-10-29 audit-round2-fixes，12 项）仅两天，本轮为全量第三遍：后端 app/core/sanger、
前端全部视图与组件、CI/部署/脚本，分五个域并行审计。

## 发现与修法（17 项，21 文件，净 -107 行）

### Bug 类（6）
1. **后台设计任务无兜底**（`design_routes.run_design_task`）：`run_design` 内部有
   catch-all，但它之外的意外（结果对象构造、存储交互）会让后台任务静默死亡——
   前端轮询永久 pending、无日志。修：包 try/except，兜底构造 FAILED 结果落库 +
   `logger.exception`。
2. **空骨架回退被当环状参考**（`sequencing_endpoints`）：查不到载体时
   `construct_sequence` 是纯线性插入片段，`bool(construct_sequence)` 恒 True →
   按环状延长比对，跨「原点」产生假环化坐标。修：判据改 `insert_start != 1`
   （空骨架回退是唯一 insert_start=1 的组装路径，已核实 MCS 回退等其余路径不落 1）。
3. **分页 limit 负数绕上限**（`sequencing_endpoints` + `sequencing_store`）：
   `?limit=-1` 在 SQLite/PG 里负 LIMIT 语义为「不限」，整表一次性返回。
   修：FastAPI `Query(ge=0, le=1000)` + 存储层防御性钳制，双保险。
4. **AnalysisView 四函数共享 loading 无代次**：综合分析/酶切/ORF/GC 乱序响应
   互相覆盖、loading 卡死。同仓库 MapView 等早已修过同款，这里是漏网。修：
   模块级 `analysisGen` 代次计数。
5. **批量报告竞态**（BatchDesignView）：轮询完成取报告后用户切历史批次，旧批次
   慢响应回填新批次进度页。修：快照 batchId，回来后比对才赋值。
6. **引物表渲染无兜底**（ResultView）：`primer.tm.toFixed(1)` 旧数据缺字段直接
   白屏；v-for 用不保证唯一的引物名做 key。修：`?.toFixed(1) ?? '—'`、
   key 加序号。

### 安全/加固（3）
7. `_export_response` 的 Content-Disposition 直接拼 f-string，与同文件 461/510
   行已消毒的口径不一致（当前调用方传服务端 ID 不可注入，属防患统一）。
8. `main.py` 直接运行分支绑 `0.0.0.0`，且 `settings.HOST/PORT` 是全仓库零引用的
   死配置。修：直接运行改用 settings，HOST 默认改回环 `127.0.0.1`。
9. **限流表只增不减**（`rate_limit._evict_locked`）：普通 key 表容量未满时永不
   回收，长期运行涨满 50k 后洪泛期会挤掉活跃用户的配额桶。修：表过半容量后
   摊销式 TTL 回收（每请求至多清 16 个过期头，均摊 O(1) 不变）。

### 配置（2）
10. `deploy/scripts/Makefile` 的 `make test-cov` 引用 pytest-cov 但依赖里没有，
    实测必然失败，且全仓库无文档引用——死目标删除。
11. **REDIS_ENABLED 与 REDIS_URL 脱节**：裸机部署只配 `REDIS_URL` 不配开关时
    静默回退内存缓存。f529dd1 修了 URL 的双真相源，漏了开关面。修：开关缺省
    时跟随 REDIS_URL 是否显式配置（进程环境或 .env 文件）；显式设置仍最优先。

### 死代码（删 8 处，净 -150 行）
- `core/sanger/pipeline.py`：`peak_heights`/`DROPOUT_WINDOW_BASES` 死导入
- `core/cut_pairs.py`：if/else 两支相同的死分支
- `core/external_vector_importer.py`：裸 `except:`（吞 KeyboardInterrupt）→
  `except Exception` + 日志
- `app/rate_limit.py`：`RedisRateLimiter` 类、`rate_limit()` 依赖工厂、
  `rate_limited()` 装饰器全部零引用（装饰器内部对同步函数 `await` 会崩）
- `app/cache.py`：`cache_batch_progress`/`get_batch_progress`/`invalidate_batch`
  零调用（批量进度实际走 BoundedMirror）
- 前端：`CutPairPicker.vue` 整文件（三图分工改版遗漏删除）、`importFromNcbi` +
  `NcbiImportResult` 类型、`SequencingPanel.fileError` 与
  `BatchSequencingView.ignoredNames` 两个从未赋值的死状态
- 另：`CacheManager.backend` 加双检锁（与终审 C-07 全路径加锁口径一致）

### 评估后不删（留档）
`VectorDB`/`VectorFeatureDB` ORM 整套载体 CRUD 零路由调用，但与既有库表结构、
`delete_user` 属主置空级联耦合——删除有回归风险，在 `database/__init__.py` 加
注释留档。

## 验证

- 后端全量 pytest **1828 passed / 43 skipped / 0 failed**（修复前后各跑一遍）
- 前端 vitest **666 passed**、`vue-tsc` 0 error、`npm run build` 通过
- 尺寸预算门禁通过（BatchDesignView 白名单文件不得增长，B1 修复压成净 0 行）
- REDIS_ENABLED 双路径实测：无配置→False、仅配 REDIS_URL→True

## 教训

- **审计报告要逐条复核再动手**：本轮 app 层审计 agent 报了 15 项，其中 4 项
  （A2/A3/A4、B1-B4）在写修复前复核时发现不成立并撤回——agent 的「自我更正」
  声明也要自己验，不能直接采信。
- **「看似 bug」先查是否刻意设计**：AGENTS.md「关键机制」的口径（载体库无属主、
  匿名 token、SnapGene 不绕登录墙等）排掉了大量误报；动口径前先读它。
- **死代码删除要有「为什么还在」的检查**：`RedisRateLimiter` 这类从未接线的类
  能存活多轮，是因为每次审计只看「有没有错」不看「有没有人用」。AST 扫描
  unused imports + grep 交叉引用的组合 10 分钟就能扫完全仓库。
- **re-export 块是死导入重灾区**：pipeline.py 的 homopolymer re-export 块
  （`# noqa: F401`）覆盖了合法的兼容导出，但 signal 那行的 `peak_heights`
  混在其中成了真死导入——逐名 grep 消费方才能区分。
