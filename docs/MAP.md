# 质粒图谱后端（契约 plasmid-map/1）

图谱是独立的基础功能：后端把任何来源（上传文件、粘贴文本、编辑后的序列、设计
结果、载体库）统一成一个 **MapDocument**，前端查看器（`src/frontend/src/map/`）
只消费这一种数据形状。代码：`src/backend/core/plasmid_map/`（纯函数包）、
`src/backend/app/routes/map_routes.py`（路由）、`src/backend/app/map_documents.py`
（设计 / 载体 → MapDocument）。

## MapDocument 要点

- 坐标一律 **1-based 闭区间**；环状分子上 `start > end` 表示跨原点
  （如 5000 bp 上 `start=4800,end=200`）。
- `sequence` 恒为大写 IUPAC DNA（U→T），长度上限 `MAP_MAX_LENGTH`。
- `features[]`：`type` 已归一为契约 FeatureType（GenBank `CDS` → `cds`，抗性 CDS →
  `marker`，`regulatory` 按 `/regulatory_class` 归类，misc_feature 按名称推断
  promoter / polyA_signal / ltr / mcs…）；`join()` 保留为 `segments`（单段省略），
  **按生物学顺序**排列：负链特征沿负链 5'→3'，即坐标降序（`complement(join(10..50,100..200))`
  → `[{100,200},{10,50}]`；负链跨原点 `complement(join(900..1000,1..100))` → `[{1,100},{900,1000}]`）。
  特征序列 = 各段依次取（负链取反向互补）后拼接（`parsers.extract_feature`），GenBank 导出
  按同一顺序写出，Biopython / SnapGene 读回后序列一致；
  `color` 只在文件给出时出现（`/ApEinfo_fwdcolor|revcolor`、`/note="color: #…"`、
  SnapGene 特征颜色）；`source` 为 `file | auto | design | vector`；`id` 在文档内稳定
  （调用方给的合法唯一 id 原样保留，其余按 `来源-序号` 补齐）。
  GenBank 的 `gene` 与同跨度 `CDS` 成对出现时只保留 CDS；`primer_bind` 进 `primers[]`。
- `enzymes[]`：只含至少切一次的酶。`cut_count` 恒为完整位点数；位点明细有上限：
  单把酶最多 `MAP_MAX_SITES_PER_ENZYME`（默认 500）个（按位置保留最前面的），整份文档
  最多 `MAP_MAX_TOTAL_SITES`（默认 60 000）个（超出时从切点最多的酶起清空明细）；
  被截断的酶带 `sites_truncated: true`，`meta.warnings` 列出酶名。`sites[].position` 为识别序列在正向链上的
  最左碱基；`cut_top` / `cut_bottom` 为该链在「第几位碱基**之后**」被切（正向链坐标）。
  5' 突出端恒有 `cut_bottom > cut_top`，3' 突出端相反，平末端相等。环状分子切点
  取模到 `1..length`（跨原点的位点与切点都会报出）；线性分子上切点落在分子外或
  恰在末端的位点不算切割。回文位点只报一次（`strand=1`），非回文按识别方向给
  `strand=±1`。`recognition` 用 NEB 记法（`G^AATTC`、`GGTCTC(1/5)`）；Type IIS 的
  `overhang` 是 REBASE 的 `NNNN`（酶级模板）；每个粘端位点另有 `sites[].overhang`：
  两切点之间的**实际碱基**，按正向链 5'→3' 读（Type IIS、AvaI 等简并识别的
  突出端因位点而异；平末端位点省略）。
- Dam / Dcm 甲基化：对 E. coli Dam（G**A**TC）或 Dcm（C**C**WGG）重叠敏感的酶带
  `methylation: ["dam"|"dcm"]`；位点所在上下文会形成阻断性甲基化时给出
  `sites[].blocked_by`（如 XbaI 的 `TCTAGAtc` / `gaTCTAGA`、StuI 的 `AGGCCTgg`）。
  `cut_count` 仍统计全部位点。敏感性表人工整理自 NEB「Dam and Dcm Methylases of
  E. coli」（数据源自 REBASE，见 `core/plasmid_map/methylation.py`），只收表中明确
  列出的酶；需要多处重叠才阻断的 Acc65I / BsaHI / BslI 不标。
- `orfs[]`：ATG 起始、遇终止结束、只报最上游 ATG（不报嵌套）；`aa_length` 不含终止
  密码子；坐标含终止密码子；环状分子上跨原点的 ORF 报一次。`frame` = 该 ORF 所在链
  （负链按反向互补序列计）起点坐标 mod 3。
- `meta.source_format`：`genbank | fasta | snapgene | raw | design | vector`；
  `meta.enzyme_set` 为所用集合 id，显式点名 `enzymes` 时为 `custom`；`meta.warnings`
  为中文提示（多记录只取第一条、未标拓扑按环状处理、未知酶已忽略…）。

## 端点（前缀 `/api/map`，功能开关 `map`）

| 端点 | 说明 |
|------|------|
| `POST /parse` | multipart `file`（.gb/.gbk/.genbank、.fa/.fasta/.fas、.dna）或 JSON `{text, name?, topology?}`；参数 `annotate`（缺省：没有特征才注释）、`enzyme_set`（默认 `common`）、`orf_min_aa`（默认 100），可放查询串或表单 / JSON 字段 |
| `POST /analyze` | JSON `{sequence, topology, name?, features?, primers?, annotate?, enzyme_set?, enzymes?, orf_min_aa?}`：编辑后重算。`annotate=true` 时丢弃传入的 `source=auto` 特征重新注释 |
| `GET /enzyme-sets` | `common`（默认 51 种）/ `unique6`（可商购、≥6 个确定碱基）/ `commercial`（全部可商购单切割对酶，**不含修饰依赖型酶**，见下）/ `golden_gate`（BsaI、BsmBI、BbsI、SapI、Esp3I、PaqCI）/ `type_iis`（全部可商购 Type IIS 酶，判定规则见下） |
| `GET /enzymes/{name}` | 酶详情 `{name, site, recognition, ends, overhang, type_iis, modification_dependent, golden_gate, suppliers:[{code,name}], isoschizomers, neoschizomers, methylation?}`；同切酶 = 识别序列与切点都相同，新切酶 = 识别序列相同、切点不同，均只列可商购的。未知酶 404 |
| `GET /suppliers` | `[{code, name, enzymes}]`：REBASE 厂商代码 / 名称及其可供的酶，按酶数降序；前端据此做显示层的厂商筛选 |
| `POST /import/ncbi` | JSON `{accession, annotate?, enzyme_set?, orf_min_aa?}` → MapDocument（`source_format=genbank`）。accession 校验（GenBank / WGS / RefSeq，版本号可选）→ E-utilities efetch `db=nuccore&rettype=gbwithparts&retmode=text`（带 `tool`、`email`、`api_key`）→ 现有 GenBank 解析。错误：422 格式无效、404 未找到、413 超限、502 NCBI 异常、503 NCBI 限流、504 超时 |
| `POST /export/genbank` | 请求体 MapDocument → `.gb` 下载；颜色写 `/ApEinfo_fwdcolor` + `/ApEinfo_revcolor`，自动注释特征附 `/note` 说明来源库与 identity/coverage |

`GET /api/design/{id}/map`（插入片段 `source=design`，载体元件 `source=vector`）与
`GET /api/vectors/{id}/map`（`source=vector`）也返回 MapDocument，均可加
`enzyme_set`、`orf_min_aa` 查询参数。

`GET /api/vectors` 列表的每一项另带 `map_preview: {length, topology, features:[{name,
type, start, end, strand}]}`（类型已归一，与 `/api/vectors/{id}/map` 的特征一致，
不含序列 / 酶切 / ORF），供载体列表画缩略图（MapThumbnail）。

错误一律中文 `detail`：413 超长 / 超体积，415 不支持的格式，422 非法序列或参数。

**Type IIS 判定**（`core.enzyme_sites.is_type_iis`，图谱 `type_iis` 酶组、酶详情与 Golden Gate
共用）：识别序列非回文（按 IUPAC 反向互补比较）、确定碱基（ACGT）≥ 4 个（排除 MspJI / FspEI /
LpnPI / AbaSI 等修饰依赖型酶），且两条链的切点都在识别序列同一侧之外（全部 ≥ 末端且至少一个
严格在外，或全部 ≤ 起点且至少一个严格在外）。BssSI 这类非回文但一条链切在位点内的酶不算；
两侧双切的 Type IIB 不在可扫描酶之列。Golden Gate 候选另要求识别序列纯 ACGT、切出 4 nt 5' 突出端。

**修饰依赖型酶**（`core.enzyme_sites.is_modification_dependent`）：只切 5mC / 5hmC 等修饰过的 DNA，
对普通质粒图谱没有意义，且位点数爆炸（AbaSI 的「位点」是单个 C）。规则：识别序列确定碱基 ≤3 个且
两条链切点都在识别序列之外（REBASE 把 AbaSI `C`、FspEI `CC`、MspJI `CNNR`、SgeI `CNNG`、LpnPI
`CCDG` 记作在位点外 12–16 nt 切割；Bio.Restriction 没有专门标记），另加规则抓不到的 SibEnzyme
9 把 5mC 依赖 MD 酶（AoxI、BisI、BlsI、GlaI、GluI、KroI、MteI、PcsI、PkrI）。`commercial`、
`unique6`、`type_iis`、`/suppliers` 都排除它们；显式点名 `enzymes` 仍可扫描（受位点上限约束），
酶详情带 `modification_dependent: true`。Dam 依赖的 DpnI / MalI 不排除（dam⁺ 菌株抽提的质粒
确实带 GATC 甲基化）。

酶的最适 / 失活温度不提供：Biopython 1.88 的 `opt_temp` / `inact_temp` 对全部酶都是 37 / 65 °C
的占位值（TaqI 也是），作为数据展示会误导。

## 自动注释

元件库 `data/features/common_features.json`（66 条，来源与扩展方法见
[data/features/README.md](../data/features/README.md)）。比对流程：

1. ≥40 bp 的元件：库序列 14-mer 精确种子（库内出现 >6 次的低复杂度 k-mer 不用）
   → 按（元件, 链, 对角线）计票、对角线按 ≤60 bp 分块 → 每块一个定长窗口
   （元件长 + 2×30 bp）上 `PairwiseAligner` 局部比对，
   得 identity（相同列 / 比对列，含 gap）与 coverage（覆盖元件长度占比）；
2. 更短的元件双链精确查找；肽标签 / 蛋白酶位点六框翻译后查找肽段；
3. 环状分子在序列末尾接上开头再比，跨原点命中输出 `start > end`、只报一次；
4. identity < 0.90 或 coverage < 0.30 丢弃；coverage < 0.95 标 `auto.fragment=true`；
5. 同一元件、或同类型 / 同名元件重叠超过较短者一半时只留最好的（完整优先 →
   identity 高 → 比对长）；与文件已有特征（同类型或同名、重叠 ≥80%）重复的命中丢弃。

性能边界：单次比对窗口恒为元件长 + 60 bp，与序列长度无关；候选按「种子覆盖的元件跨度、
票数」从高到低比对（两条链统一排序），每个请求最多 400 次比对、DP 单元格总量不超过
`MAP_ANNOTATE_MAX_CELLS`（默认 6×10⁷，约 1 s），预算用尽时余下候选跳过并在
`meta.warnings` 提示。`annotate` 缺省时超过 `MAP_AUTO_ANNOTATE_MAX_BP`（默认 50 000 bp）
的序列不自动注释（告警提示），显式 `annotate=true` 仍会注释。参考耗时：10 kb 典型质粒
<0.2 s，300 kb 随机序列约 0.5 s，300 kb 元件片段串联重复（对抗输入）约 1.7 s / 30 MB。

## 限制、缓存与限流

| 变量 | 默认 | 说明 |
|------|------|------|
| `MAP_MAX_LENGTH` | 300000 | 单条序列长度上限（bp） |
| `MAP_MAX_UPLOAD_MB` | 5 | 上传文件 / JSON 文本体积上限 |
| `MAP_CACHE_MAX_BP` | 50000 | 只缓存不超过该长度的结果（防内存缓存被大文档撑满） |
| `MAP_AUTO_ANNOTATE_MAX_BP` | 50000 | `annotate` 缺省时只对不超过该长度的序列自动注释 |
| `MAP_ANNOTATE_MAX_CELLS` | 60000000 | 单次请求自动注释比对的 DP 单元格总预算 |
| `MAP_MAX_SITES_PER_ENZYME` | 500 | 单把酶返回的位点明细上限（`cut_count` 不受影响） |
| `MAP_MAX_TOTAL_SITES` | 60000 | 单份文档的位点明细总上限 |
| `MAP_CACHE_MAX_DOC_BYTES` | 1048576 | 序列化超过该字节数的文档不缓存 |
| `MAP_CACHE_MAX_TOTAL_BYTES` | 67108864 | 本进程写入缓存的图谱文档总字节上限（超出按写入先后淘汰） |
| `MAP_FEATURE_LIBRARY` | — | 可选：元件库 JSON 路径（默认 `$DATA_DIR/features/` 或仓库 `data/features/`） |
| `NCBI_EMAIL` | — | 建议设置：NCBI E-utilities 要求的联系邮箱（随请求发送） |
| `NCBI_API_KEY` | — | 可选：NCBI API key，速率上限由 3 次/秒提高到 10 次/秒 |
| `NCBI_TOOL` | `PlasmidDesigner` | E-utilities `tool` 参数 |
| `NCBI_TIMEOUT` | 30 | 单次导入的**总时长**上限（秒）：节流排队 + 连接 + 全部读取，超时 504（排队就超时 503） |
| `NCBI_MAX_CONCURRENCY` | 4 | 同时进行的 NCBI 导入数（独立线程令牌，不占全局线程池） |
| `NCBI_TEXT_CACHE_MAX_BYTES` | 2097152 | 只缓存不超过该体积的 NCBI GenBank 文本（缓存 24 小时） |

解析、比对、酶切扫描都在线程池执行。结果缓存 1 小时，键含输入摘要、选项、
元件库版本（库文件 sha256）与 `MAP_ALGO_VERSION`（`map_routes.py`；改变结果口径的
代码改动须递增）。限流沿用中间件 default 档，与 `/api/analysis` 相同。

NCBI 导入：下载体积上限沿用 `MAP_MAX_UPLOAD_MB`，序列长度上限沿用 `MAP_MAX_LENGTH`（读到 LOCUS
行即判断，超长记录不必下载完）；efetch 在独立的 anyio CapacityLimiter（`NCBI_MAX_CONCURRENCY`）
上执行，不占 `/parse`、`/analyze` 等共用的全局线程池；进程内相邻请求间隔 ≥0.34 s（有 API key
时 0.11 s，锁内只预约时间点、锁外等待），按客户端走限流 `ncbi` 档（匿名 10 次/分钟、登录用户 30 次/分钟）；GenBank 文本缓存
24 小时，生成的 MapDocument 走上面的常规缓存。
