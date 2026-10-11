# 已知问题与修复历史

> 本文件记录**仍未解决**的已知问题，以及历次审计的**修复索引**。
> 代码注释里的「KNOWN_ISSUES x.y」「终审 X-NN」编号均指向下文对应条目；
> 已修复项只保留一行摘要，逐项细节（复现步骤、回归用例）请看对应提交。
>
> 状态标记：⬜ 未解决 / ✅ 已修复

---

## 未解决 / 挂账 ⬜

1. **poly 内部「缺第几个 A」信息论不可解**：同聚物内的缺失落点无法由峰图唯一
   确定，维持「计数确证但定位存疑」注记即正确答案，非 bug。

---

## 部署备忘（1 核 2G VPS，非代码问题）

1. **前端不要在 VPS 上构建**：`Dockerfile.frontend` 多阶段构建中的 `vite build` 峰值内存
   可能超 1G，小机器上易 OOM。应本地构建后仅上传 `dist/`。
2. **推荐裸机部署**：SQLite（`DATABASE_URL` 默认即可）+ `REDIS_ENABLED=false` +
   `--workers 1`，总内存占用约 500MB 内；Docker 全家桶（约 0.9–1.2G）可用但余量小。
3. **瓶颈是 CPU 不是内存**：密码子优化/引物设计为 CPU 密集的 Python 计算，批量任务
   串行执行（对单核反而友好），单条设计秒级、批量明显排队属预期行为。

---

## 防回潮约束（改相关代码前必读）

- **测试里改 site_settings 状态必须用 `app.database.SessionLocal`**（真实
  文件库），不是测试模块自己的内存库；用例结束必须恢复默认设置并
  `invalidate_cache()`，否则污染同进程后续模块（设计端点会 403）。
- 匿名 sequencing 记录的行为契约：创建响应下发 `access_token`（批量整批
  共用），前端存 sessionStorage 自动带头；列表不返回任何匿名记录 ID
  （含持令牌者）；改造前无 token 的遗留记录保持公开。
- 前端 read↔ref 映射用 `query_start/query_end`（比对块绝对坐标，终审 A-20），
  不要回退到 `L-1-qi` 镜像式。
- 真实 .ab1 回归集（`tests/test_real_data_regression.py`，skip-if-missing）
  中 MX 批次的「疑似混合样品」是该批数据的真实判定，不是误报，勿按理想断言改。

---

## 修复历史 ✅

### 2025 功能完整性核查（原 docs/FIXPLAN.md）

analysis 四个 POST 端点改 JSON body；数据库持久化 id 错位与字段回填；
SECRET_KEY 改读环境变量；启动无条件 init_db；批量下载/报告走统一恢复路径；
载体库进程级缓存；移除 `/vectors/import/file` 端点并限制批量导入路径；
接线请求追踪中间件；实现两个 501 导出桩；删除零引用的 `task_queue.py` 与
celery/flower。冒烟脚本 `smoke_test.ps1`（20 项）即当时的验证清单。

### 2026-08-31 代码审计（公网 VPS 场景）

| 编号 | 问题 | 修复 |
|---|---|---|
| 1.1 | compose 把 PostgreSQL/Redis 暴露到公网，Redis 无密码 | 端口改绑 127.0.0.1；Redis `requirepass` |
| 1.2 | 伪造 `X-Forwarded-For` 绕过限流 | 仅可信代理对端采信 `X-Real-IP`/XFF 末段；限流 key 上限回收 |
| 1.3 | CORS 硬编码 `*` + 凭证，`CORS_ORIGINS` 未接线 | 接线 `settings.CORS_ORIGINS`；通配时关闭凭证 |
| 2.1 | NCBI 导入与序列分析阻塞事件循环 | 移交线程池（`run_in_threadpool`） |
| 2.2 | MemoryCache / 限流 key / 内存存储只涨不降 | 写入清理过期项 + 条目上限淘汰 |
| 2.3 | 多 worker 与进程内状态冲突 | `BACKEND_WORKERS` 环境变量，默认 1 |
| 2.4 | `DATABASE_URL` 双来源，`.env` 不生效；STORAGE_MODE 默认值文档与代码相反 | 统一引用 `settings.DATABASE_URL`；默认 `database` |
| 2.5 | nginx 未设 `client_max_body_size` | 已设置（现为 `/api` 100m、测序上传端点 520m） |
| 3.1 | 批量持久化失败被静默吞掉 | 改 `logger.warning` |
| 3.2 | `on_event("startup")` 已弃用；`DEBUG` 默认 True | 改 lifespan；`DEBUG` 默认 False |
| 3.3 | 上传载体文件名未过滤 `\` | `_safe_filename` 白名单 |
| 3.4 | 模块导入即连 Redis，晚就绪则永久回退内存 | 延迟初始化 + 每 60s 重试 |
| 4.1（09-04） | 反向互补映射误用 `maketrans("ATGC","TAGC")` | 改 `"TACG"`；交叉杂交排除预期 overlap 配对 |

### 2026-09-07 第二轮审查（09-11 修复）

| 编号 | 问题 | 修复 |
|---|---|---|
| 5.1 | gene_synthesis 设计 100% 失败（`PrimerInfo` 缺 target 坐标） | `PrimerInfo` 补 `target_start/target_end` |
| 5.2 | 邮箱验证令牌可当登录令牌 | `decode_token` 拒绝带 `purpose` 的令牌 |
| 5.3 | 批量设计丢弃一半设计参数 | `DesignRequest(**request.model_dump(...))` 整体转发 |
| 5.4 | 可选认证不查 `is_active`；401 不清 store；批量任务直接解引用内存字典 | 分别修复 |

### 2026-10-07 真实数据修复批次

| 编号 | 问题 | 提交 |
|---|---|---|
| 6.1 | BsaHI 识别序列误写为 GAYG（应为 GRCGYC） | a3e73d3 |
| 6.2 | 批量文件数上限在读取之后才生效 | df7148d |
| 6.3 | 克隆方案占位符残留、去磷参数被忽略 | 7e241ae |
| 6.4 | 酶兼容按理论而非实际突出端判定；ORF 反链坐标错 | df7cac3 |
| 6.5 | 5' ramp 遇 poly 结构打断时死循环 | 0db5df7 |
| 6.6 | Tm 宣称与算法不符；GoldenGate 未知酶报错不清 | 59058c3（Tm 模型已于 2026-10-11 全面换成最近邻法，见下文「2026-10-11 算法审计」） |
| 6.7 | 密码散列未按 bcrypt 72 字节显式截断 | 7af608e |
| 6.8 | 部署链清理（移除 HuggingFace 部署、deploy.sh/Makefile 路径） | 3dabb27 等 |

### 2026-10-08 遗留清账

| 编号 | 问题 | 修复 |
|---|---|---|
| 7.1 | `peak_count_estimate` 被合并 read 的 0 计数污染 | 仅取 >0 计数，全合并 → None |
| 7.2 | 锚定分级只标注不把关 | accepted 且锚 ≥ marginal 时结论加「定位存疑」注记 |
| 7.3 | 无真实峰型回归 | 新增 `tests/test_real_data_regression.py`（skip-if-missing） |

### 2026-10-09 终审报告 P0（22 项）

| 项 | 修复 |
|---|---|
| G-01/G-02 | conftest 导入期注入 DATA_DIR/DATABASE_URL/日志目录，测试不再写开发库 |
| B-01 | `export_design` 补属主校验 |
| D-04 | design_service 不再丢 warnings |
| A-02/A-03 | motif 避让覆盖反向链 |
| C-08/C-09 | CDS 判读整体兜底；tracy 修剪窗过窄守卫 |
| C-05 | CORS 移到最外层 + OPTIONS 不计限流 |
| B-04 | 占位/过短/低熵 SECRET_KEY 启动即拒绝 |
| B-05 | bootstrap 不再提升既有非管理员账号 |
| H-01/H-02 | Dockerfile 只 COPY data 子目录；.dockerignore 重写；nginx -t 先校验 |
| A-01 | 密码子迭代循环收敛检查 |
| A-04 | 4 物种密码子表按 Kazusa 原始数据重建 |
| A-05 | 未知物种改 ValueError |
| A-07 | Gibson/GoldenGate 引物 anchor 坐标回填 |
| A-19 | 双峰锚判据放宽（0.8×主峰） |
| A-20 | read↔ref 映射改用比对块绝对坐标 |
| B-02/B-03 | 匿名测序记录 access_token；整理包绑 owner |
| C-01 | `MAX_REF_BP=200000` 三入口校验 |
| C-02 | 批量请求级总字节上限 + `_BATCHES` LRU |
| C-03/C-06 | 设计序列按类型收紧上限；分析类模型统一上限 |
| C-04 | 限流按（方法 + 路由模板）分类，轮询不吃业务配额 |
| C-07 | `_ANALYSES/_BATCHES` 全路径 RLock |
| D-01/D-02 | 存储往返补 user_id/clone_protocol；功能清单空集不被默认值覆盖 |
| A-09 | 酶切方案带真实切点坐标 + 破坏必需元件告警 |
| H-03/H-04/H-05、B-08 | 裸机安装目录 751；后端只监听 127.0.0.1、/docs 内网限制；卸载需确认 |

### 2026-10-11 算法审计

| 项 | 修复 |
|---|---|
| 算法 #1 | **Tm 模型换成最近邻法**：`core.primer_designer.calculate_tm` 为全库唯一实现（Biopython `Tm_NN`，DNA_NN4，Na⁺ 50 mM / Mg²⁺ 1.5 mM / dNTP 0.6 mM / 引物 50 nM，Owczarzy 盐校正 saltcorr=7），`PrimerDesigner._calculate_tm` 与 `sequence_validator.predict_tm` 均委托于此。6.6 条「Tm 宣称与算法不符」由此彻底解决 |

**用户可见的行为变化（#1）**：同一条 18–25mer 的 Tm 比旧经验式（Wallace /
Marmur-Doty）系统性高约 7°C，且相同长度与 GC 的不同序列不再得到同一个 Tm。
默认筛选窗口仍是 58–62°C——该窗口本就是最近邻时代的惯例，所以是换模型而不是
改窗口；随机模板实测通过筛选的引物反而更多（回退到「放宽条件」的引物从
216/400 降到 120/400）。已下单的旧引物序列不受影响，只是显示的 Tm 变了，
订单前仍建议用供应商工具按自己的缓冲体系复核。极短序列（约 8nt 以下）最近邻
给出负值，统一钳到 0.0；全简并（N）序列同样返回 0.0。
设计耗时从约 2.6 ms/对 升到约 5.6 ms/对（已对候选序列做 Tm 缓存），仍远低于
单次设计的其他开销。
