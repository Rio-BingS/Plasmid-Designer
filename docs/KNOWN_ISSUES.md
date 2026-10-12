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
  （含持令牌者）；改造前无 token 的遗留记录仅管理员可读/删、列表不展示
  （v2.6.0 起，4e769ae；不要改回公开）。
- 前端 read↔ref 映射用 `query_start/query_end`（比对块绝对坐标，终审 A-20），
  不要回退到 `L-1-qi` 镜像式。
- 真实 .ab1 回归集（`tests/test_real_data_regression.py`，skip-if-missing）
  中 MX 批次的「疑似混合样品」是该批数据的真实判定，不是误报，勿按理想断言改。
- 在线序列来源（`core/online_sources.py`）：SnapGene 在线库只做低频、单次、人工触发的
  读取（进程级限速 + 结果缓存），**不要加定时同步/批量镜像/对外再分发 zip**；其
  `robots.txt` 明确 `Disallow: /local`，商业用途须先获 GraphPad Software, LLC 许可。
  SnapGene 在线库受独立开关 `SNAPGENE_ONLINE_ENABLED` 约束（**默认 false**，关 → 404），
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
- `scripts/smoke_online_sources.py` 默认不再跑 SnapGene（需 `--only snapgene` 且开关为 true）。

**Addgene 会话 Cookie 模式的定位（审查 M2）**：只适合**单用户自托管**（部署者 = 唯一使用者 =
Addgene 账号持有人）。多用户实例里它等同共享一个人的 Addgene 账号，导入的序列也对全站可见；
启动时若配置了 `ADDGENE_SESSION_COOKIE` 且「开放注册」或已有 >1 个启用账号，
`app/online_import.log_addgene_shared_session_warning` 记一条 WARNING（不回显 Cookie）。此前
文档所说「不落盘」不成立（`.env`、容器环境、`docker inspect` 都能看到），已改为「不写入应用
数据/响应/日志」；推荐替代是浏览器下载后走「上传文件」。

