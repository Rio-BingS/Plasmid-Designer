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
2. **Gibson 双酶切 plan 落空时引物与组装语义脱节**：载体缺所选酶位点、组装回退
   「MCS 区间替换」时，引物仍按单位点模型设计（同源臂锚 `resolve_gibson_insert_pos`）。
   出现时结果带「按 MCS 区间替换」告警可察觉；修复需把回退语义传进引物设计。
3. **custom_arms 负链装配的融合克隆检测不覆盖**（正链已做，2026-10-25）：
   负链臂落进 minus CDS 时不做融合判定，起始密码子按载体 ATG 常规处理。
4. **custom_arms 模式不产出载体线性化引物**（背靠背引物）：默认用户自行
   双酶切/购买线性化载体，引物只覆盖插入片段两端。

---

## 部署备忘（1 核 2G VPS，非代码问题）

1. **线上实况（2026-10-26 起）是 Docker Compose**：四容器跑在这台 VPS 上
   （`/opt/plasmid-designer`，见 HANDOVER §2「线上部署」），发版 = 按 tag reset +
   `docker compose build backend frontend` + `up -d --force-recreate`。
   `Dockerfile.frontend` 的 `vite build` 峰值内存偏大（曾评估超 1G 有 OOM 风险），
   实际运行正常但**发版时留意构建阶段内存**；若出现 OOM 再回到「本地构建传 dist」。
2. **裸机部署（`deploy/bare/`）是备选路线，线上未采用**：SQLite +
   `REDIS_ENABLED=false` + `--workers 1` 总占用约 500MB 内，内存紧张时是合理的
   退路；此前「推荐裸机」的表述已过时。
3. **瓶颈是 CPU 不是内存**：密码子优化/引物设计为 CPU 密集的 Python 计算，批量任务
   串行执行（对单核反而友好），单条设计秒级、批量明显排队属预期行为。

---

## 防回潮约束（改相关代码前必读）

- **测试里改 site_settings 状态必须用 `app.database.SessionLocal`**（真实
  文件库），不是测试模块自己的内存库；用例结束必须恢复默认设置并
  `invalidate_cache()`，否则污染同进程后续模块（设计端点会 403）。
- 匿名 sequencing 记录的行为契约：创建响应下发 `access_token`（批量整批
  共用），前端存 sessionStorage 自动带头；列表不返回任何匿名记录 ID
  （含持令牌者）；改造前无 token 的遗留记录仅管理员可读/删、列表不展示
  （v2.6.0 起，4e769ae；不要改回公开）。
- 前端 read↔ref 映射用 `query_start/query_end`（比对块绝对坐标，终审 A-20），
  不要回退到 `L-1-qi` 镜像式。
- 真实 .ab1 回归集（`tests/test_real_data_regression.py`，skip-if-missing）
  中 MX 批次的「疑似混合样品」是该批数据的真实判定，不是误报，勿按理想断言改。
- 在线序列来源（`core/online_sources.py`）：SnapGene 在线库只做低频、单次、人工触发的
  读取（进程级限速 + 结果缓存），**不要加定时同步/批量镜像/对外再分发 zip**；其
  `robots.txt` 明确 `Disallow: /local`，商业用途须先获 GraphPad Software, LLC 许可。
  SnapGene 在线库受独立开关 `SNAPGENE_ONLINE_ENABLED` 约束（**默认 true**，设 false → 404），
  开启后只读端点也须登录（`app/online_import.require_snapgene_user`，匿名 401），落库仍
  管理员；不要把它并回 `ONLINE_IMPORT_ENABLED`、不要放开匿名（见下「已接受风险」）。
  Addgene 全长序列 2026-08 起需登录：无凭据时 `addgene_sequence_bytes` **必须**抛
  `AddgeneLoginRequired`（403 + 指引），任何情况下不得代登录/绕登录墙；有
  `ADDGENE_SESSION_COOKIE`（用户自备，仅单用户自托管适用）时也只以 https 发给
  `addgene.org`：`_http_get` 禁用 urllib 自动重定向、逐跳校验 https + 主机白名单
  （裸主机名），Cookie 用 `add_unredirected_header` 且跨主机即永久丢弃；取序列的
  `try_sequence` 分支仅管理员；不得写入响应/日志。不要改回 `urlopen` 或普通请求头。
- UniProt 是**蛋白库**：`core/online_sources.py` 只允许用它反查 Entrez/EMBL/RefSeq/
  Ensembl 交叉引用（`nucleotide_candidates`）再把用户引到核酸来源；不要把蛋白序列
  当质粒/图谱序列导入（`ENSEMBL_SEQ_TYPES` 拒绝 protein 是同一原则）。
- **`validate_set_id` / `validate_plasmid_id` 不要退回字符白名单**：SnapGene 索引里
  的 `url` 与集合 `path` 实际带 `( ) + ' &`（`pET-28a(+)`、`pcDNA3.1(+)`、
  `pBluescript_II_KS(-)`、`pCDF-2_Ek_LIC_(linearized)`；19 个集合 2822 行里 488 个
  ID 如此），这两个值只拼进查询串且一律 `quote(..., safe="")`、主机由 https 白名单
  钉死，所以只排除 `/ \ ? # %` 与控制字符即可。白名单时代的症状是**搜索能列出、
  点导入/预览 422「序列 ID 无效」**——搜索不校验 ID、导入才校验，两者口径必须一致。
  新增/调整校验时，拿真实索引（`src.snapgene_set_index` 全量跑一遍）回归，别只看样例。
- 导入载体的出处一律写结构化 `data_provenance`（`app/vector_import.provenance_block`，
  形状与内置载体 YAML 一致：source/url/fetched_at/license + 来源特有字段），描述里那句
  可读标注**不能替代**它；新增导入通道时同步写。
- 载体拓扑按真实值落盘（`topology: circular|linear`）：线性片段（PCR 产物、Ensembl 片段、
  线性 GenBank 记录）不要再用「一律环状」画图谱（`app/map_documents._vector_topology`）。
- 载体 YAML 的 `features[]` **必须带 `name`**（缺省回退到 type）：解析入口用
  `core/plasmid_map/parsers`，落盘入口用 `app/vector_import.py`；不要再用
  `external_vector_importer.GenBankImporter` 那种正则解析（.dna 会变空序列）。
- 路由模块里取配置一律写 `config.settings.X`（调用时解析），不要
  `from app.config import settings`——`tests/test_config_cors.py` 会 `reload(app.config)`
  换掉实例，旧引用会让测试打桩失效并写进真实 `data/`。
- **测试要把载体目录指到 tmp 时必须打两处**：`app.config.settings`（路由用）**和**
  `app.design_service.settings`（`get_vector_library` 用，module 级 `from app.config
  import settings` 拿到的是 reload 前的旧对象）；只打前者会出现「刚导入的载体 404」。
  另外载体响应缓存 `vector:<id>` 会跨用例命中，用例开始/结束都要
  `cache.backend.clear_pattern("vector:*")`（`tests/test_online_sources.py::vectors_dir`）。
- **401 只清会话**：接口若要拿 401 表达业务错误（凭据/验证码无效），必须加进
  前端 `api/index.ts` 的 `SESSION_401_EXEMPT_PATHS`；带 `X-Access-Token` 的请求
  一律不清会话。CSRF 豁免只认原样路径（含 `//`、`.`、`..`、`%2e` 的一律不豁免）。
- **共识投票**：明确碱基优先于 N/IUPAC（`pipeline._pick_base_vote`），单 read 覆盖
  仍如实保留 N；压缩区降级只降 `support_reads < 2` 的单 read 证据
  （`pipeline._merged_zone_artifact`）——这两条是「N 不进共识 / 互证不被单 read 拖累」
  的执行点，别再退回同权计票或无条件降级。
- **合成 oligo 片数恒偶**由 `_equal_step_tiles` 保证（返回片数 == 传入 n_tiles）；
  不要再按 `ceil` 步长逐片推进，那会少返回一片、把错位交替退化成奇数片。

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

**同批其他用户可见变化（v2.8.0）**：

- 限制酶：回文酶不再正反链重复计数，可判为唯一位点；三套酶表统一到
  Bio.Restriction（REBASE），默认扫描 51 种、下拉 47 种；HindIII/SacI
  不再误报兼容。（2.9.0 之后的版本酶表缓存键带版本，升级无需手动清缓存。）
- 酶切模拟新增可选 `circular`（默认 false），环状时首尾片段合并。
- 合成 oligo 按重叠双链 Tm 均一化（1.2 kb 基因 spread ≤5 °C），代价是
  oligo 数量通常多 2–4 条。
- Golden Gate overhang 新增保真度筛查；常见 MCS 回文 overhang 会给出警告。
- Sanger：N/IUPAC 不再计为替换；>20 kb 参考改用 k-mer 定位加窗口比对
  （200 kb 约 726 MB → 40 MB，结果一致）；共识按逐列 Q 加权；tracy 次级
  basecall 作为混合峰印证（`tracy_corroboration`）。
- GenBank `join()` 特征逐段解析，跨原点 CDS 按拼接序列判读。
- 密码子优化滑窗精修只查改动窗口附近，残留 motif 时不再整体失效。
- 删除三个零引用模块（enhanced/advanced primer、enhanced codon）。

### 2026-10-12 在线序列来源导入（SnapGene 在线库 / Ensembl / Addgene）

背景：项目此前只有「本地文件上传 + NCBI 导入」两条外部序列通道，参考取证报告
（SnapGene 6.0.2 逆向 + 官网/Ensembl/Addgene 实测 + 逐条许可核对）里另外三条通道缺失。

| 项 | 结果 |
|---|---|
| 新增 `core/online_sources.py` | SnapGene 在线库（`sets.php` / `fetch.php?set=` / `fetch.php?set=&plasmid=` / `&zip`）、Ensembl REST（`lookup/id`、`sequence/id` 的 genomic/cds/cdna）、Addgene 匿名元数据；统一「取字节」层：进程级限速、体积上限、响应嗅探、`MapParseError.status` 映射 HTTP、`opener` 注入点供测试打桩 |
| 新增 `app/routes/online_source_routes.py` | 来源清单 + 集合/索引/预览（只读，缓存）+ 三个导入端点（管理员、走 `/api/vectors/import` 的 upload 限流档）；Addgene 只给索引检索与元数据，`addgene_sequence_bytes` 一律 403 + 指引，**不绕登录** |
| 新增 `app/vector_import.py` | 导入落盘形状唯一入口（= `data/vectors/*.yaml`），并把出处/引用要求写进载体描述 |
| 前端 | `VectorsView` 新增「SnapGene 在线库 / Ensembl / Addgene」三个页签与在线预览弹窗；`api/index.ts` 同步契约；许可文案随来源下发 |
| 开关与部署 | `ONLINE_IMPORT_ENABLED`（默认 true，false 则零外呼）、`ONLINE_TIMEOUT`、`ONLINE_MAX_BYTES`；docker-compose 透传 + 两份 `.env.example` 说明 |

**顺带修掉的两个真 bug（都有回归锁）**

1. **`.dna` 上传产出空序列**：`/api/vectors/import/upload` 声明支持 `.dna`，却用正则版
   `GenBankImporter` 当文本解析 → 序列为空、`features` 恒空；GenBank 上传同样丢光注释。
   现统一走 `core/plasmid_map/parsers.parse_file`（Biopython，含 SnapGene 特征与颜色），
   并接受 `.fa/.fasta/.fna`。
2. **导入的载体静默消失**：导入写出的 YAML 里 `features` 只有 `{type,start,end}`，而
   `VectorLibrary._parse_vector_dict` 要求 `name` → 整份文件加载抛 KeyError，被
   `load_from_directory` 的兜底 `except` 吞掉（NCBI 与批量导入路径同病）。
   现 `_parse_vector_dict` 对缺 `name` 回退到类型名、`external_vector_importer.export_to_yaml`
   补齐 `name/strand`，新导入一律经 `app/vector_import.py`。

**同批顺带**：路由改用 `config.settings.X` 调用时解析——`tests/test_config_cors.py` 会
`reload(app.config)`，旧引用会让测试打桩失效，实测把载体写进了真实 `data/vectors/`。

**实跑验证（本机联网，2026-10-12）**：SnapGene 19 个集合；`coronavirus_resources` 索引 4 条；
`SARS_Genome.dna` 103,295 字节（与取证报告一致）→ 解析 29,751 bp / 49 特征；
Ensembl `lookup ENSG00000139618` → BRCA2/GRCh38，`ENST00000288602` cds → 2,421 bp 线性；
Addgene `244694` 元数据可取、`.gbk` 直链 404 → 按预期 403 指引；本机 Addgene 索引 169,016 条可离线搜索。
`ensembl_ping` 在本机网络下超时（lookup/sequence 正常），故只作诊断用、不要接成健康检查。

**图谱页侧（同批追加）**：`/api/map/import/sources|snapgene/sets|snapgene|ensembl` —— 图谱页新增
「在线导入」页签（SnapGene 在线库按集合选序列 / Ensembl 按 ID 取 genomic·cds·cdna），
直接打开成可编辑的 MapDocument（含自动注释与酶切）。集合/索引与载体库侧**共用同一批缓存**
（`app/online_import`），图谱页的 MapDocument 按「输入摘要 + 选项 + 元件库/算法版本」分键，
命中时不重复下载；集合/索引与导入写端点走 `ncbi` 限流档（外呼代理）。`MAP_MAX_LENGTH`
（300 kb）在解析阶段生效：Ensembl `seq_type=genomic` 给染色体 ID 会 413。

实跑：`POST /api/map/import/snapgene`（coronavirus_resources/SARS_Genome）→
`plasmid-map/1 SARS Genome 29,751 bp linear 49 特征 41 个酶切位点`。

**已知环境问题（非代码缺陷）**：本机网络下 `rest.ensembl.org` 偶发超时——
`ensembl ping` 常常失败、`POST /api/map/import/ensembl` 实测出现过 504（映射正确，重试即可），
而同一时刻 `lookup`/`sequence` 又可能成功；因此不要把 `ensembl_ping` 接成健康检查或门控。

---

### 2026-10-12 在线来源收尾（出处 / 写权限 / 另存为载体 / UniProt / Addgene 凭据）

用户拍板的范围：A 组（在线导入收尾）全做、酶表不动、UniProt 接入、Addgene 允许
「用自己账号的登录态抓全长序列」但不做 SnapGene 批量镜像。

| 项 | 结果 |
|---|---|
| 结构化出处 | 导入载体一律写 `data_provenance`（`provenance_block`：source/url/fetched_at/license + set/plasmid/identifier/filename/origin…），`Vector` 增加 `provenance` 字段、`export_yaml` 回写、`VectorInfo.data_provenance` 下发，载体详情页新增「出处 / 许可」块 |
| 载体拓扑保真 | `Vector.topology` + YAML `topology` + `VectorInfo.topology`；`map_documents._vector_topology` 让线性片段的缩略图/详情/图谱不再画成环状（此前导入的线性记录一律当环状） |
| 图谱类型别名 | `vector_library._TYPE_ALIASES`：`rep_origin/mcs/marker/signal_peptide/polyA_signal/protein_bind/rbs` 不再一律掉成 `other`；仍未列出的（insert/primer_bind/misc_rna…）保持回退 other |
| 写入口按管理员显示 | 载体库的上传/批量导入/编辑/删除、NCBI/SnapGene 预览弹窗的「确认导入」、Addgene 导入按钮都对非管理员隐藏；新增 `composables/useIsAdmin.ts`（无 pinia 宿主如单测降级为非管理员），后端 403 仍是权威 |
| 图谱页「另存为载体」 | 新增 `POST /api/vectors/import/from-map`（仅管理员）：图谱页把**当前文档**（含编辑器里改过的注释）的序列/特征直接提交，后端不再外呼（避免"保存的"与"看到的"不是一份）；落库走 `record_from_parts` + `save_record`，出处记 `origin=<打开方式>` |
| UniProt 通道 | `core/online_sources.py` 增加 `uniprot_search/uniprot_entry/uniprot_fasta/nucleotide_candidates`，路由 `GET /api/vectors/import/uniprot/search|{acc}`（只读外呼，限流 `ncbi` 档），前端载体库新增「UniProt」页签：**只反查** Entrez/EMBL/RefSeq/Ensembl 交叉引用并给跳转，蛋白序列不当质粒序列导入 |
| Addgene 用户凭据 | `ADDGENE_SESSION_COOKIE`（可选，用户自备会话）：`_addgene_session_headers` 只对 `addgene.org`/`*.addgene.org` 附带 Cookie（上游元数据给的直链指向别处绝不带）；成功即可 `POST /api/vectors/import/addgene/{id}?fmt=genbank\|dna` 导入；失败（无凭据或凭据过期）依旧 403 + 指引，**不代登录、不绕登录墙**，凭据不落盘/不回显/不进日志 |
| 联网冒烟 | 新增 `scripts/smoke_online_sources.py`：默认什么都不做（`--run` 或 `ONLINE_SMOKE=1` 才联网），逐个来源打印 ok/FAIL/info，只读不写；Addgene 匿名项按 info 处理（失败属预期） |
| 部署与文档 | `deploy/docker/docker-compose.yml` 透传 `ONLINE_TIMEOUT/ONLINE_MAX_BYTES/ADDGENE_SESSION_COOKIE`，两份 `.env.example` 补 `UNIPROT_BASE`、`ADDGENE_SESSION_COOKIE` 说明 |

**实跑验证（本机联网，2026-10-12）**：`scripts/smoke_online_sources.py --run` 全绿——
SnapGene 19 集合 / `coronavirus_resources` 4 条 / `SARS_Genome.dna` 103,295 字节 → 29,751 bp·49 特征；
Ensembl `lookup ENSG00000139618` → BRCA2、`ENST00000288602` cds 2,421 bp；
UniProt `TP53` 检索命中 P04637（p53/Homo sapiens/141 条核酸交叉引用：EMBL X02469、RefSeq NM_000546.6、Ensembl ENST00000269305…）；
Addgene `52961` 匿名元数据可取（genbankUrl/snapgeneUrl/mapPngUrl），未配会话时按预期跳过序列获取。

**踩到的两个坑（已修，勿回退）**

1. **测试里只打 `app.config.settings.VECTORS_DIR` 不够**：`app.design_service` 是
   module 级 `from app.config import settings`，`tests/test_config_cors.py` reload 过
   `app.config` 后它拿的是旧对象 → 路由把载体写进 tmp、`GET /api/vectors/{id}` 却从
   真实 `data/vectors` 加载，表现为「刚导入的载体 404」（单独跑该文件时是绿的，全量才红）。
   同时载体响应缓存 `vector:<id>` 跨用例命中会让断言读到别的用例的载体。
2. **冒烟脚本不要打印 `↳` 之类字符**：Windows 控制台默认 GBK，`print` 直接
   `UnicodeEncodeError` 让冒烟整体判失败；`_report` 现按 GBK 编码兜底。

---

### 2026-10-12 v2.12.0 审查修复：在线来源（`fix/rv12-online`）

审查报告：v2.12.0 代码审查 01（在线序列来源导入）。

| 项 | 结果 |
|---|---|
| H1 Cookie 随重定向泄露 | `_http_get` 改用自建 opener（`_NoRedirectHandler`），手动跟随 ≤3 跳，每跳校验 https + 主机白名单（`_checked_host`，裸主机名）；Cookie 用 `add_unredirected_header`，跨主机即永久丢弃；上游直链非 https/非 addgene 主机直接拒绝（`UnsafeUrlError`）。测试走真实 OpenerDirector |
| H2 SnapGene 在线库 | 见下「已接受风险」：`SNAPGENE_ONLINE_ENABLED` 默认关、登录可用、落库管理员；前端按开关 + 登录态显示 |
| M1 匿名 try_sequence | 仅管理员（匿名 401、普通用户 403）；`session_configured` 只对管理员如实下发 |
| M2 Cookie 多用户 | 文档改为仅单用户自托管；开放注册或 >1 启用账号时启动告警 |
| M3 节流 | 按站点预占时间槽、锁外 sleep；排队超 `ONLINE_THROTTLE_MAX_WAIT`（默认 10 s）→ 429 |
| M4 限流档 | 载体库侧 SnapGene 集合/索引/预览、Ensembl lookup/预览归入 `ncbi` 档 |
| M5 出处 URL | `SaveMapAsVectorRequest.url` 只收 http(s)；落盘与 `_parse_provenance` 读取时丢弃非 http(s) 的 `url`/`*_url`（前端 href 白名单由图谱前端分支负责） |
| L1–L7 | polyA 别名小写；落盘撞车 409 + 同 ID 覆盖标 `replaced`、ID 与文件名同一归一；NCBI/批量导入写 LOCUS 拓扑；Addgene 本地索引按 mtime 缓存；上传按 `MAP_MAX_LENGTH` 限长、回执去掉 `path`；错误不回显上游字节、`*_BASE` 去 userinfo；Addgene robots 描述更正 |

**已接受风险（决策记录，避免下次审查重复争论）——SnapGene 在线库**

- 事实：SnapGene 网站条款 (a) 禁止 "reproduce or store material from this website in any
  public or private electronic retrieval system"；`snapgene.com/robots.txt` 仍有
  `Disallow: /local`（2026-10-12 复核）；`/local/sets.php`、`/local/fetch.php` 来自对
  SnapGene 客户端的逆向，客户端 EULA 禁止 reverse engineer。v2.12.0 把它做成了「存入
  本地载体库」+ 匿名可触发的代理，且默认开启。
- 用户决定（2026-10-12）：**保留功能，但默认关闭、仅登录用户可用**。新增独立开关
  `SNAPGENE_ONLINE_ENABLED`（默认 false，与 `ONLINE_IMPORT_ENABLED` 无关）；关闭时
  载体库侧集合/索引/预览/导入与图谱页集合/索引/打开全部 404（核心层 `_ensure_snapgene_enabled`
  兜底，零外呼）；开启后只读端点需登录（匿名 401），存入载体库仍需管理员；前端在开关
  关闭或未登录时隐藏 SnapGene 页签。
- 风险归属：开启即视为部署方知悉并接受上述条款冲突；仅建议学术/非营利/政府的自用实例
  开启，须引用 `www.snapgene.com/resources`，商业用途须先获 GraphPad 书面许可。中长期
  方案仍是向 GraphPad 申请接口授权或改用 NCBI 对应记录。
- 用户再决定（2026-10-12 23:56，v2.14.1）：**改回默认开启，仍仅登录用户可用**（匿名 401、
  落库管理员不变）；需要关闭的部署设 `SNAPGENE_ONLINE_ENABLED=false`。
- `scripts/smoke_online_sources.py` 默认不再跑 SnapGene（需 `--only snapgene` 且开关为 true）。

**Addgene 会话 Cookie 模式的定位（审查 M2）**：只适合**单用户自托管**（部署者 = 唯一使用者 =
Addgene 账号持有人）。多用户实例里它等同共享一个人的 Addgene 账号，导入的序列也对全站可见；
启动时若配置了 `ADDGENE_SESSION_COOKIE` 且「开放注册」或已有 >1 个启用账号，
`app/online_import.log_addgene_shared_session_warning` 记一条 WARNING（不回显 Cookie）。此前
文档所说「不落盘」不成立（`.env`、容器环境、`docker inspect` 都能看到），已改为「不写入应用
数据/响应/日志」；推荐替代是浏览器下载后走「上传文件」。

---

### 2026-10-18 测试红灯清账（Windows 误报 + 真实数据基线）

来源：交接文档 `docs/HANDOVER.md` §7 列出的 5 项本机红灯（此前长期被当作「环境问题」挂着）。

| 项 | 结果 |
|---|---|
| 2 项 POSIX 权限位断言 | Windows 不表达 rwx（目录恒 `0o777`、`chmod` 只切只读位）→ 新增 `_assert_private()`，只在 `os.name == "posix"` 时逐位校验，Linux 覆盖不减（`test_batch_report_disk_store`、`test_sequencing_upload_sessions`） |
| 1 项被误判为「线程时序」 | 实为用例自己的 spy 用 `p.rsplit("/", 1)[-1]` 取文件名：`DiskBlob.path` 在 Windows 用反斜杠，取到整条路径 → `names[path]` KeyError 抛在 `analyze` 内、被批次管线记成「分析失败」→ 断言处表现为「analyze 从未调用」；改用 `os.path.basename` |
| 2 项真实数据回归 | 按 v2.16.0 可信区口径更新基线：同批数据旧口径 22/31/16 处双峰 → 新口径 9/10/4（差值全在 700bp 之后的尾部噪声）；结论措辞锁到「互检排除 … 判为该 read 测序误差，不计入判定」并显式断言不再出现「互检矛盾」。新断言额外锁「报出的双峰位点必须落在该 read 可信区内」 |
| 附带显示修正 | 混合双峰详情行（修剪后 read 坐标）与结论尾部 `zone_text`（原始 read 坐标）不一致，同一条结论里两处「可信区」看起来互相矛盾；两者不同时现在一并给出原始区间 |

**教训（防回潮）**：Windows 上的红灯先怀疑「测试自己处理了路径/权限位」，而不是产品逻辑；
真实数据回归必须对照另一套口径跑一遍取证，再决定是改断言还是改算法。


### 2026-10-18 SnapGene 在线库「搜得到、导不进」

用户报告：SnapGene 在线库能搜到质粒，点导入失败（图谱页）。

| 项 | 结果 |
|---|---|
| 现象 | `POST /api/map/import/snapgene` 返回 422「序列 ID 无效：pET-28a(+)」；载体库侧的预览/导入同样 422 |
| 根因 | `core/online_sources.validate_plasmid_id` 用字符白名单 `[A-Za-z0-9_.\- ]`，而 SnapGene 索引 `Plasmid@url` 实际带 `( ) + ' &`（`pET-28a(+)`、`pcDNA3.1(+)`、`pBC_KS(-)`、`pCDF-2_Ek_LIC_(linearized)`）。全量扫 19 个集合 2822 行 → 488 个 ID（18.4%）被拒 |
| 为什么搜索正常 | 搜索/集合索引只校验 `set_id`，从不校验 `plasmid_id`；校验只发生在导入/预览 → 症状恰好是「列表里有，点了就报错」 |
| 修法 | 改为「排除法」：只拒 `/ \ ? # %` 与控制字符，长度上限不变（集合 64 / 序列 120）；拼 URL 仍一律 `quote(..., safe="")`，主机仍由 https 白名单钉死，不新增外呼面 |
| 验证 | 上游实测 `fetch.php?set=..&plasmid=pcDNA3.1%28%2B%29` 等 4 个特殊 ID 均 200 + SnapGene 魔法串；本地解析 3400/2961/3956/5428 bp 图谱正常（`(linearized)` 仍判 linear）；真实网络下 `pET-28a(+)` 经完整路由导入成功（5369 bp / 16 特征） |
| 回归 | 校验参数化用例补 5 个真实 ID + 3 个非法串；新增 `test_snapgene_ids_with_parens_and_plus_import`（搜索 → 图谱页导入 → 载体库预览 → 落库 `snapgene_pET-28a`） |

**教训（防回潮）**：ID 校验要拿真实索引全量回归，不要只按示例写白名单；同一 ID 在
「列出来」和「打开」两条路径上的校验口径必须一致，否则永远是「搜得到、点不开」。

### 2026-10-20 四视角代码审查（安全 / 前端状态 / 后端算法 / 前端渲染）

来源：对本仓库做的一轮只读代码审查（四个互不重叠的视角），逐条复核后修复。
报告里的结论先取证再采信——本轮有 2 条 P1 在核对端点真实状态码后被推翻
（`/auth/verify` 永不 401、`/auth/verify-email` 失败是 400、测序越权是 403），
降级为韧性建议；确认成立的才动手。

| 项 | 结果 |
|---|---|
| 测序共识 | 压缩区降级补 `support_reads >= 2` 豁免；N/IUPAC 不再与明确碱基同权（`a5dd7f0`） |
| 密码子 | 宿主类别按解析后的表名/别名判定（`E.coli` 等别名此前落成真核）；`CODON_OPT_ALGO` v2.2→v2.3；表清单缓存键带目录内容指纹（`af01c47`） |
| 引物 | 兜底引物逐项写明不达标项；反义 oligo 的 5'/3' 重叠标注按自身方向；片数恒偶真正成立（`6b01f87`/`b4b8f23`/`5aa546f`） |
| 前端状态 | 文档切换整体复位搜索与隐藏项；峰图失败不写永久缓存、卸载后迟到响应不再写父组件；403/429/5xx 中文兜底（`80ec842`/`a35c225`/`e0e25dc`） |
| 安全 | CSRF 豁免路径 fail-closed（`//`、`.`、`..`、`%2e` 不豁免）；XFF 畸形跳跳过而非整链作废；401 显式白名单 + `X-Access-Token` 不清会话 |
| 会话代际 | 跨标签页同步（BroadcastChannel + localStorage 兜底）：别的标签页登录/登出后本页重新核实，迟到的 401 不再误清 |
| 其它 | 允许以 `*` 结尾的蛋白（`dcdbd73`）；缓存统计改用 `except Exception`（`e632bfe`）；分析页共用清洗后的序列（`8011c36`） |

**教训（防回潮）**：审查报告与代码现实之间要先取证再采信；每条结论带文件:行号，
修复前先补能失败的用例、修复后跑全量。跨标签页的会话状态必须显式同步，
不能假设「同一浏览器 = 同一份 JS 状态」。


### 2026-10-20 设计页只能从载体库选载体（使用中的缺陷）

用户报告：设计页的「目标载体」只有一个下拉框，只能从载体库选——手里的
`.dna`/`.gb`、或者在线来源（SnapGene 在线库 / NCBI / Ensembl / Addgene）的质粒
完全没法参与设计。而载体库是**共享库**，写入口（`/api/vectors/import/*`）
全部要求管理员，普通用户连「临时用一下」都做不到。

| 项 | 结果 |
|---|---|
| 根因 | 设计页 UI 只有载体库下拉框；`DesignRequest` 只认 `vector_id`（库内 ID），没有「自带载体」的通道；即便有，库里也没有它的序列 |
| 为什么不复用 `/api/vectors/import/*` | 那条路把载体**写进共享库**且需管理员：普通用户的设计需求会污染共享库，也不该为此获得库写权限 |
| 修法 | 新增「一次性载体」通道：`/api/design/vector/{upload,online}` 只解析/下载并回执载荷（序列+元件+MCS 判定+告警，不写 YAML、不落盘），前端把它随 `POST /api/design` 的 `vector` 字段内联提交；`app/adhoc_vector.py` 现场构造 `Vector`（与载体库共用 `VectorLibrary.parse_dict` 的归一口径） |
| MCS 口径 | 文件标了 MCS 元件 → 按注释区间整段替换（与载体库一致）；没标 → 取「唯一位点最密集段」的切点作**纯插入点**（`start=point, end=point-1`：`assemble_construct` 替换的是空区间，骨架一个碱基都不删）；都没有 → 骨架中点 + 明确告警，绝不静默 |
| 顺带修正 | ① `resolve_gibson_insert_pos` 无 MCS 时原先固定返回 100 bp（对真实载体是任意位置），改为骨架中点，并支持「唯一位点表」让线性化点可指定；② `CloningSite.overhang` 必须由序列算真实 4 nt 碱基——扫描结果的 `overhang` 是 `"5prime"` 这类类型标记，直接塞进去会让 Golden Gate overhang 变成 `"5PRI"` |
| 权限与限流 | 上传/在线导入挂 `design` 功能开关（不依赖 `vectors`/`map` 开关）；限流 `upload`/`ncbi` 档（精确规则先于 `POST /api/design` 前缀规则）；SnapGene 需登录、Addgene 仅管理员（沿用载体库侧口径） |
| 回归 | 后端 `tests/test_design_vector_sources.py`（22 项：上传不写库、越界元件丢弃、非法碱基 422、纯插入语义、overhang 取真碱基、在线四来源、限流档、请求体上限）；前端 `frontend_DesignVectorPicker.test.ts` / `frontend_DesignView_inline_vector.test.ts`（11 项） |

**教训（防回潮）**：共享库与「用户自带数据」必须是两条通道——共享库要有写权限与
来源管控，「自带」要能匿名、不落盘、不越权；一次性载体的组装语义（MCS 整段替换 vs
纯插入点）必须写进代码与告警，因为两者对骨架的影响差 100+ bp。

### 2026-10-20 设计页两处使用缺陷（氨基酸 * 与表单被清空）

用户在同一天报告的两个使用问题，都是「设计页」层面的：

| 项 | 结果 |
|---|---|
| 现象 | ① 氨基酸序列里的 `*` 无法被识别；② 切到别的页面再回来，整张表单被清空 |
| ① 根因 | 后端其实早就认 `*`（`CODON_TABLE` 有终止密码子，`dcdbd73` 已放开），真正被拒的是**全角＊（U+FF0A）**——中文输入法下 Shift+8 打出来的就是它，连同 `﹡`（U+FE61）、`∗`（U+2217）都会报「无效的氨基酸代码」，用户看到的就是「`*` 不认」；复制来的序列还常带全角字母、全角空格、NBSP、零宽字符 |
| ① 修法 | 新增 `core/seq_input.py`：NFKC 全角→半角、异体星号→`*`、删各类空白与零宽字符（U→T 只对 DNA 做，蛋白里的 U 是硒代半胱氨酸）。归一只放最外层（`DesignRequest`/`BatchDesignRequest` 的 before 校验器 + `process_sequence`），非法字符仍留到校验阶段报「哪个字符、第几 位」——不在归一阶段静默丢弃。前端同时做失焦归一与提交前字符检查，提示精确到位置 |
| ② 根因 | 表单状态全在组件内 `ref` 里，路由切换即销毁 |
| ② 修法 | `utils/designDraft.ts` + 设计页草稿：写入 sessionStorage（400ms 合并写），**卸载时按当前值补写一次**（`watch` 默认下一帧批处理，「输完立刻点别的页面」会丢最后几个字符）；读取按字段白名单与类型校验，旧/坏草稿不会把页面带进非法状态；新增「清空表单」按钮 |
| 为什么用 sessionStorage | 序列与载体属于用户数据：同标签页有效、关标签页即清除，不用 localStorage 以免多人共用机器时留下上一个人的序列（与 /map 页的文档暂存同一策略） |
| 回归 | `tests/test_sequence_input_normalize.py`（26 项：全部异体星号/空白/零宽归一、请求模型归一、端到端设计产出终止密码子、错误信息带位置）；前端 `frontend_DesignView_persist.test.ts`（7 项：恢复/坏草稿/卸载补写/清空、全角＊归一、非法字符拦截、FASTA 分行不误报） |

**教训（防回潮）**：「用户说某个字符不认」先取实际码点——中文输入法打出的标点
常常不是 ASCII，按 `== '＊'` 的单点修补会在下一个异体字符上复发，应该做一次
NFKC + 变体表归一；表单持久化必须覆盖「输完立刻离开」这条路径，只在 change
或防抖计时器里写存储都会丢最后一次输入。

### 2026-10-22 组装图谱没有按克隆方法拼接（残留应切除的片段）

用户反馈（第 4 项）：全基因合成 / PCR 引物设计出的片段与载体组装后，「图谱上没有
准确识别位点、还残留着应该被限制酶除去的部分」。

| 项 | 结果 |
|---|---|
| 根因 | 三种克隆方法共用同一套 `assemble_construct`：**把 MCS 注释区间整段换成插入片段**。于是 ① Gibson 本应只在线性化点插入、一个碱基都不切掉，却被删掉整段 MCS（pET-28a 少了 144 bp）；② 双酶切的真实切点落在 MCS 注释之外时，该被切走的废片段继续留在图谱里；③ Golden Gate 两个 Type IIS 切点之间的片段同样没被切掉；④ 只把「裸基因」插进切点，连接处的位点残留没补回，图谱上少 5–6 bp、位点也复原不了 |
| 修法 | 新增 `core/construct_assembly.py`，把三种方法统一成「用 head + 基因 + tail 替换载体顶链 (left_cut, right_cut] 这一段」：Gibson 纯插入（left = right）；双酶切用 `find_enzyme_sites` 的真实切点，基因两端补 site5[k5:] / site3[:k3]，连接处位点复原；Golden Gate 切走两个切点之间、两端补 4 nt overhang。`design_service.assemble_construct` 增加 `method` 等参数，无法建模（载体上没有对应酶位点）时**回退**旧的 MCS 区间替换并明确告警 |
| 切点挑选 | 按「切点是否落在 MCS 区间内 → 切除长度」排序，**两个方向都要试**。只按 e5→e3 单向找会把 5.3 kb 主链当废片段——pET-28a（MCS 158–301，BamHI 198 / XhoI 158）用中点做锚点会选中 5329 bp 的切除，实测把质粒删成 40 bp |
| 方向一致性 | 左切点属于哪把酶与引物端 5'/3' 不一致时告警「基因会反向插入，请对调两端酶」——双酶切的粘端只在同一位点间互补，指派反了物理上接不上 |
| 图谱元件 | 落在切除区间的元件移除、跨切点的按保留比例截断/移位、纯插入点落在元件内部时元件被撑开（起点不变、终点后移）；插入片段的图谱坐标只算**基因本身**（不含接头），否则 CDS 从 `GATCC` 开始、ORF 整体平移 |
| 端到端验证 | pET-28a + XhoI/BamHI：构建体 = 5369 − 40 + 5 + 基因 + 1 = 5431 bp，`TCGAG…基因…G|GATCC` 两个位点原样复原，被切走的 158–198 不再出现；图谱的酶切位点从构建体序列重算，XhoI/BamHI 都在。Gibson：构建体 = 5369 + 基因，删掉插入片段后与载体逐碱基相同 |
| 回归 | `tests/test_construct_assembly.py`（15 项：位点去重、切点挑选两个方向、切除区间、位点复原、单切点纯插入、无位点回退告警、方向告警、元件增删移、Gibson 不删碱基、端到端设计+图谱） |

**教训（防回潮）**：「组装」不是「把基因塞进 MCS」——每种克隆方法的切除区间和连接
处碱基都不同，必须按真实切点建模；切点挑选不能拿 MCS 中点当锚点（中点可能根本不在
两个切点之间），要按「区间落在 MCS 内 + 最短」排序并显式支持两个方向；元件坐标要
跟着切除区间增删移，否则图谱会画出已经不存在的东西。

### 2026-10-22 插入片段的方向不对（负链插入被画成正链）

用户反馈：「新的问题好像是插入的方向有问题」。这是上一条修复的后续——切除区间对了，
但插入片段的**链方向**没建模。

| 项 | 结果 |
|---|---|
| 根因 | 双酶切的两个连接处，粘端只在**同一位点**之间互补。引物把 enzyme_5 的位点放在基因 5' 端，若载体上 enzyme_3 的切点在前（enzyme_5 在后），基因只能以**反向互补**进构建体。此前只发了一条「会反向插入」的告警，序列仍按正链拼，导致图谱上的 CDS/ORF 整个反过来 |
| 为什么 pET-28a 必然踩到 | 它的整个表达盒（T7 promoter 368-386、RBS 307-312、6xHis/T7-tag）都在**负链**，MCS 的转录方向是 NcoI(296) → BamHI(198) → XhoI(158)。用 BamHI 做 5'、XhoI 做 3'（教科书做法）时，基因必须反向才与启动子同向；画成正链就是「基因箭头朝反」 |
| 修法 | `AssemblyPlan` 增加 `insert_reverse`：`plan_restriction` 在「左切点属于 enzyme_3」时置位，`apply_plan` 放入 `revcomp(基因)`、注释 `strand = "-"`、描述注明「reverse complement on the minus strand」；图谱与 GenBank 导出都按该 strand 渲染/写 `complement()` |
| 另一种选择 | 对调两端酶（5' 用 XhoI、3' 用 BamHI）则基因落在正链——两种都是物理上成立的构建体，工具按用户选的酶如实建模，并在告警里说清如何切换 |
| 验证 | pET-28a + BamHI/XhoI：正链 span == revcomp(基因)、`doc.features` 中 design 元件 strand=-1、按注释链翻译得到目标蛋白；XhoI/BamHI：span == 基因、strand=+1；Gibson 恒为正链（方向由同源臂决定） |
| 回归 | `tests/test_construct_assembly.py` 增加 4 项（反向互换/正链对照/三条链方向端到端），共 19 项 |

**教训（防回潮）**：「插在哪里」和「朝哪个方向」是两件事。粘端互补关系决定方向，
不能用告警代替建模——图谱上的箭头反了，使用者会照着做错实验。判断依据是**左切点
属于哪把酶**，不是 MCS 中点、也不是「看起来更顺」的排布。

### 2026-10-22 切点选到整圈那一边（废片段没切走、基因落到位点之外）

用户反馈（第 5 项复查）：pET-30a(+) 用 NdeI 5' / XhoI 3'，「两个酶切位点中间的
片段被切除，但片段却插入在了 NdeI 一侧」——重新核对后确认是插入片段方向反了，
同时发现切点挑选本身还有一处会选反方向。

| 项 | 结果 |
|---|---|
| 现象 | 同一条设计在部分载体上表现为：废片段一个碱基都没切走、基因落在两个位点之外、方向恒为正链，告警里出现「切点跨越载体原点，已按 MCS 区间替换构建体」 |
| 根因 | 一次性载体的 MCS 是**推测点**（`start=point, end=point-1`）。`_pair_score` 按「切除区间是否包含该点」给 penalty，而同一对切点反着算就是「整圈减废片段」：pET-30a 的 XhoI 158 / NdeI 346，正方向 188 bp（点 158 不在 (158,346] 内 → penalty 2），反方向 5234 bp（点 158 在圈内 → penalty 1）→ 选中反方向 → `apply_plan` 判 `right < left` 抛 ValueError → 回退 MCS 整段替换 |
| 修法一 | `_candidate_pairs` 两趟挑选：先只考虑**不超过半圈**的切除区间（环状载体上要切掉的废片段必然比半圈短），挑不出来才放宽到整圈并新增告警「切除区间 N bp 超过载体一半」 |
| 修法二 | `apply_plan` 支持跨原点区间：`right < left` 时把载体旋转到切除区间不跨原点、走同一套逻辑（构建体与元件坐标一起转到「旧坐标 right+1」这个新起点；旧编号的 1 号碱基本来就在废片段里，无法保留），不再抛错回退 |
| 附带 | `AssemblyPlan.removed_bp` 显式记录切除长度——跨原点时 `right - left` 是负数，不能靠坐标差反推 |
| 验证 | pET-30a(+) 上传载体（5422 bp，XhoI 158 / NdeI 346，推测 MCS 点 = 158）+ NdeI 5'/XhoI 3'：切除 188 bp、构建体 6006 bp、CDS `complement(164..928)`、按注释链翻译得到目标蛋白；跨原点用例（340 bp 载体转 170 bp）切成 40 bp 废片段后位点复原、基因仍夹在两位点之间 |
| 回归 | `tests/test_construct_assembly.py`（+2：MCS 点压在切点上时仍选短区间、跨原点旋转组装）、`tests/test_design_service.py`（+1：一次性载体推测 MCS 点落在切点上时不回退整段替换），全量 1616 passed |

**教训（防回潮）**：环状载体上「两个切点之间」有两段，只有**短的那段**是废片段。
任何「这个点是否落在区间里」的打分都不能奖励整圈方向——那等于把 5 kb 主链当废片
段扔掉。坐标算术（`right - left`）在环状序列上会失效，切除长度要显式记录；跨原点
是正常情形（上传载体的 ORIGIN 是任意的），必须旋转后照样组装，不能回退。

同轮附带修掉一个导出缺陷：`generate_genbank_from_result` 的兜底 CDS 判据是
「有没有一个元件**叫 insert**」，用户给序列起了名（A01）时就补一条**不带
`complement()` 的正链 CDS**——负链插入的构建体导入 SnapGene 会同时看到两条
方向相反的 CDS，看起来就是「方向还是反的」。判据改成「有没有元件的坐标正好覆盖
插入区间」，`tests/test_design_service.py::test_genbank_does_not_add_a_second_plus_strand_insert_cds` 锁住。

### 2026-10-22 「方向还是反的」——两端酶填反了，工具一声不响

| 项 | 结果 |
|---|---|
| 现象 | 用户第 6 次复查：pET-30a 导出的 .gb 里是 `CDS 164..928`（正链），基因 5' 端贴着 XhoI、3' 端贴着 NdeI；而 T7 表达盒要求反过来（5' 端贴 NdeI） |
| 排查 | 用用户自己导出的构建体反推载体（5422 bp，XhoI 158 / NdeI 346），把两种填法都跑一遍：`enzyme_5=NdeI` → 负链 `complement(164..928)`（正确）；`enzyme_5=XhoI` → 正链，与用户的文件**逐字节一致**。即工具行为与两端酶的指派完全一致——问题在输入 |
| 为什么难发现 | 两个输入框只写了「5' 端限制酶 / 3' 端限制酶」，没说是**插入片段的** 5'/3'；用户按「载体上左边那个位点」填了 XhoI。填反之后粘端照样配得上、构建体照样拼得出来、全程零告警，只有把 .gb 拖进 SnapGene 看箭头才发现；结果页也没有「插入方向」这一项 |
| 修法一（后端） | `core.construct_assembly.cassette_direction`：用载体自己的启动子注释判定基因该读哪条链（正链启动子在切除区间上游 → 5' 端贴左切点；负链启动子在区间下游 → 5' 端贴右切点）。与本次装配相反时 `assemble_construct` 追加告警，直接给出「5′ 端填 X、3′ 端填 Y」。判定只在证据唯一时成立（双向启动子 / 无注释 / 切点跨原点 → 不猜也不告警），**不静默改用户填写** |
| 修法二（前端） | 设计页/批量页两个框改成「5′ 端限制酶（基因起始 / ATG 那一端）」与「3′ 端（基因末尾 / 终止密码子那一端）」并加一句说明；结果页新增「插入方向」指标（正链/负链）与置顶的方向告警横幅——原先告警埋在页尾，很容易漏 |
| 验证 | pET-30a 复现：`enzyme_5=NdeI` → strand=-、无方向告警；`enzyme_5=XhoI` → strand=+、告警含「插入方向和载体启动子相反…把两端酶对调（5′ 端填 NdeI、3′ 端填 XhoI）」；无启动子注释的载体不多给告警 |
| 回归 | `tests/test_construct_assembly.py` +6（cassette_direction 六种情形）、`tests/test_design_service.py` +1（填反时告警 / 填对时不告警 / 无注释时沉默），全量 1624 passed / 39 skipped |

**教训（防回潮）**：一个字段名有两种合理解读，就一定会被填反；而「填反了也能拼出构建体」
是最危险的路径——不报错、不告警，只在产物的语义上错。方向不能只听用户填的 5'/3'
（那是**输入**，不是事实），载体自己的启动子/表达盒才是事实，两者冲突必须说出来。
另外，约束信息（「插入方向」）要摆在用户看结果的地方，不能只体现在导出文件里。

### 2026-10-23 双酶切方向不再让用户填（按载体判定 + 实时预览）

告警版修完，用户反馈「只提醒填错的后果、没帮我填对」。根子在于表单问了一个
用户无法可靠回答的问题：基因 5′ 端接哪把酶，是载体表达盒的物理事实，不是偏好。

| 项 | 结果 |
|---|---|
| 改法 | 表单只留两个位点（名字里不再有 5′/3′），方向由 `resolve_restriction_ends` 判定；解析结果同时喂给组装与引物 |
| 判定顺序 | ① 载体启动子注释（紧邻区间、唯一）→ 定；② 没有启动子注释就对骨架跑元件库自动识别（`core.plasmid_map.annotate`，5 kb ≈130–230 ms，按序列摘要缓存）；③ 两侧都有启动子时用 RBS/终止子位置打破平局（启动子 +3、RBS/终止子 +2，差 <2 记平局）；④ 判不出 → 用户按预览图手选 |
| 覆盖方式 | `insert_direction`：auto（默认）/ reverse（与判定相反，反义构建，表单上不折叠的复选框）/ plus、minus（预览判不出时的手动指定） |
| 预览接口 | `POST /api/design/restriction-preview`（default 限流档）：用占位基因跑一遍真实组装再回坐标，所以画出来的就是**成品构建体**——被切走的片段不在图上（真实质粒上也没有），图上只有基因箭头（方向=链）、两个复原的切点、附近启动子（箭头=自身 strand） |
| 为什么预览要跑真实组装 | 手算坐标迟早和 `apply_plan` 的 head/tail 补碱基对不上；用同一条代码路径出数，图与结果天然一致 |
| 验证 | pET-30a NdeI/XhoI：auto → 负链、5′ 接 NdeI；`plus` → 正链且结果里记「按你的选择」；两把酶填反结果逐字节相同；无注释载体自动识别出 T7（source=auto_detected）；双向启动子 → ambiguous + 提示手选；`(1633 passed / 39 skipped)`、vitest 590、vue-tsc 0 |

**教训（防回潮）**：让用户回答「程序能算出来的事实」是设计错误，补一句告警只治症状。
凡是要用户填一个自己无法验证的字段，先问：这个值我能不能从数据推出来？能推就推；
推不出来才让用户选，而且选择必须配一张「选完会得到什么」的图。预览图要画**产物**，
不要画中间态——用户要看的是拿去构建的那个质粒。

### 2026-10-25 第三轮设计功能审计（Gibson 双酶切引物 / 无缝越界 / 起始密码子边界）

来源：对 v2.20–v2.21 新功能（起始密码子去除、Gibson 双酶切线性化、无缝连接、
custom_arms、质粒命名）做的一轮专注逻辑审查，全部疑点先用探针脚本实证再修。

| 项 | 结果 | 提交 |
|---|---|---|
| P1 Gibson 双酶切反向引物同源臂取自被切除的废片段（锚定左切点下游而非右切点下游，pET-28a 默认参数实测 R 臂 = `revcomp(backbone[left:left+20])`，线性化载体上无处退火） | `design_gibson_primers` 改为接收左右两个切点，臂按连接点锚定、环状切片绕回原点；负链装配时片段基因取反向互补（修复前协议产物质粒与图谱所示链向相反） | a4ce238 |
| P2 无缝连接（keep_sites=False）切点外扩越界：`left -= k_left / right += …` 是裸算术，跨原点位点扩出 [1..L] 域，apply_plan 的 Python 切片静默产出错误构建体（实测分别多 5 bp 与 210 bp） | 环状下扩完归一回定义域、线性越界显式回退 MCS；`apply_plan` 加越域防护（负数/超长切点直接 ValueError 走既有回退）；单切点纯插入分支同样遵守 keep_sites=False | 1f00b83 |
| P3 custom_arms 起始密码子去除缺融合克隆检查（同场景与 Gibson 单点行为不一致：单点只告警、custom_arms 静默删 ATG） | 正链装配先做 `gibson_junction_fusion` 判定，融合场景与单点同口径（告警读码框/内部 Met，不动序列），告警文案抽 `_fusion_frame_warnings` 共用 | d2413d2 |
| P3 同酶双填回退单位点：文案「未提供完整位点」误导（用户填了，只是两把相同），且回退到 MCS 起点而非该酶自己的切点；引物分支还自行重算位置、与被覆盖的组装位置脱节 | 文案写明「两把酶相同」；线性化点取该酶 MCS 窗口内切点；`design_primers_for_method` 接收组装用的 `gibson_insert_pos` | d2413d2 |
| P3 Gibson 单点跨原点连接点 ATG（pos<3）漏检 | 连接点前 3bp 按环状绕回窗口取（`_circular_backbone_window`），线性载体维持无 3bp 可看即不判定 | d2413d2 |
| CDS 扩展的「head/tail 为空 = 无缝」启发式会被平末端酶误伤 | `AssemblyPlan` 新增 `seamless` 标志，判断改用标志位 | 1f00b83 |

**验证**：新增 `tests/test_seamless_boundary.py`（5 项，含两个越界探针场景的构建体
内容级断言）、`tests/test_gibson_double_primers.py`（5 项，两臂锚定/取向一致/无缝/
跨原点臂）、`tests/test_start_codon_edge_cases.py`（5 项）；后端全量 1703 passed /
43 skipped，前端 vue-tsc 0 错误、vitest 613 passed。

**教训（防回潮）**：把「单位点模型」的函数复用到「双切点模型」时，凡是只接收一个
位置的参数都要重审——左右两个连接点一旦分开，所有「取该点邻域」的切片都会 silently
指向错误的一侧；而这类错误用「断言非空/断言前缀」的测试抓不住，必须断言**内容等于
按几何推导的期望值**。环状序列上的所有坐标算术（± 偏移）都要先问一句：结果还在
切点定义域里吗？Python 切片对越界值不报错，这正是静默出错的最佳温床。





### 2026-10-29 第二轮代码审计与终审（安全 + 健壮性批次）

来源：对后端路由/基础设施与前端的一轮全面复审（安全视角为主），全部项均先复核
代码路径确认可触发再修。提交列统一为本批次（2026-10-29，分支
`feature/snapgene-primer-view`，见当日 git log）。

| 项 | 结果 | 提交 |
|---|---|---|
| 客户端可控 `X-Request-ID` 原样进访问日志并回显响应头：可注入日志行；非 latin-1 字符使写响应头抛 `UnicodeEncodeError` → 500 | 白名单消毒（`[A-Za-z0-9._-]`，截断 64）后非空才采用，否则生成 UUID | 本批次 |
| 外部载体库导出文件名仅替换 `/`：LOCUS 名来自外部 GenBank，Windows 上 `..\` 仍可越出目标目录 | 收敛到 `re.sub(r"[^A-Za-z0-9._-]+", "_")` + 去首尾点/空 + 兜底名 | 本批次 |
| console 邮件渠道把含验证码的正文写进滚动日志（5×10MB 长期留存）——能读日志即可接管新注册账号 | 日志只记收件人/主题，正文（含验证码）走 stdout 即看即弃 | 本批次 |
| 载体序列下载 `Content-Disposition` 手工拼接 vector_id（头注入 + 非 ASCII 乱码） | 统一走 `app/filename.content_disposition`（防回潮测试已锁定单实现） | 本批次 |
| NCBI 导入/搜索 `limit` 无上界：可传 10⁹ 让外部请求与 YAML 序列化失控 | `Query(ge=1, le=100)` | 本批次 |
| 载体上传临时文件：`tmp_path` 只在正常写完才赋值，写入中途异常时 `finally` 的 `os.unlink` 抛 `NameError` 且残留临时文件 | 进入 `with` 即记录路径 | 本批次 |
| BoundedMirror 竞态：`_load`/`_load_batch` 用 `in` + `[]` 两步，条目可在两步间被容量/TTL 淘汰 → 落到存储层或误判缺失 | 改 `dict.get()` 一次持锁（design_routes、batch_routes） | 本批次 |
| 后台设计任务属主丢失：镜像淘汰后重建的 result 不带 `user_id` → 落成「无属主公开」 | 创建端点显式把 `user_id` 传入 `run_design_task`（单任务与批量同口径） | 本批次 |
| `REDIS_URL` 只读进程环境变量：pydantic-settings 从 `.env` 读到的值不导出 `os.environ`，裸机部署在 `.env` 单独配的 Redis 被静默忽略 | 环境变量 → `settings.REDIS_URL` → 本地默认 三级回退（与 KNOWN_ISSUES 2.4 的 DATABASE_URL 同型） | 本批次 |
| 载体/密码子表目录缺失（配置错误/未初始化）时 `os.listdir` 直接 500 | 目录读取容错：返回空清单/404 | 本批次 |
| 测序记录/峰图/列表读路径异常静默吞掉 → 用户见 404，无线索排查 | 读失败 `logger.warning(..., exc_info=True)` 留痕（终审 D-01 同型） | 本批次 |
| `export_design` / 设计测序分析的 `_load` 在事件循环里同步走 SQLite/PG | 移交 `run_in_threadpool`（2.1 口径延伸） | 本批次 |
| 前端：环形图空序列（L=0）点击取模得 NaN 进入 caret；Addgene 搜索/元数据迟到旧响应覆盖新结果；批量进度页重复 `startPolling` 叠加 interval；localStorage 不可用（隐私模式）读模式键即白屏 | NaN 加 `L>0` 守卫；Addgene 加请求代次（同 SnapGene `sgIndexGen` 口径）；`startPolling` 先 `stopPolling()`；localStorage 读写收敛到 `utils/batchHistory.storageGet/storageSet` | 本批次 |
| 死代码堆积：Pinia store `design`/`vectors`、`exportAllFormats`、rate-limit API、Ensembl lookup、图谱 `xToPos`/`PRIMER_FOCUS`、config 死键 `UPLOAD_DIR`/`OUTPUT_DIR` 等 | 全量删除，净 −180 行；引用零残留（grep 复核） | 本批次 |
| 部署配置：`.gitignore` 缺 SQLite WAL/SHM 与 `data/backups/`；README 端口绑定与 `FRONTEND_BIND` 默认值漂移；deploy.sh mkdir 不存在的 ssl 目录 | 忽略规则补齐；文档对齐；mkdir 修正。**同批的 compose 健康检查 curl→wget 是误修**（「nginx:alpine 无 curl」前提错误，真机 wget 因 ::1 解析 + 301 反而 unhealthy），v2.34.1 已回滚 curl | 本批次 |

**验证**：后端全量 1823 passed / 43 skipped；前端 vitest 661 passed、`vue-tsc` 0 error、
build 通过、`npm audit --omit=dev` 0 漏洞；尺寸预算门禁与文档同步门禁通过。

**教训（防回潮）**：①一切「客户端可控字符串」进日志/响应头/文件路径前都要过白名单——
本批 4 处同型问题（X-Request-ID、LOCUS 文件名、Content-Disposition、NCBI limit）都是
「能跑通正常输入」的代码，恶意/极端输入才显形；②内存镜像 + 持久层的双层读取，
`in` + `[]` 两步访问在容量淘汰下不是原子操作，一律 `get()`；③后台任务重跑时不能依赖
「镜像里还留着属主」——显式传参；④读路径的 `except: return None` 会把存储故障伪装成
404，至少要留 warning。
