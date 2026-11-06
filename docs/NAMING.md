# NAMING.md — 各部分功能命名对应关系速查

> 目的：把「用户看到的功能 / 前端页面 / 前端 API / 后端路由 / core 模块 / DTO /
> 枚举值」之间的命名对应关系收拢到一处，新会话与排查时按表索骥。
> 依据：2026-11-05 对源码的逐一核对（路由装饰器、schemas、api/*.ts、router、
> core/sanger/batch.py 等）。结构与契约变化后请同步更新本文件。

## 目录

1. [功能开关键（最高层稳定契约）](#1-功能开关键)
2. [页面 → 前端 API → 后端路由](#2-页面--前端-api--后端路由)
3. [端点对照（前端函数 ↔ 端点 ↔ core 模块）](#3-端点对照)
4. [共享契约（DTO）](#4-共享契约dto)
5. [枚举值字典](#5-枚举值字典)
6. [core 算法模块 ↔ 上层调用方](#6-core-算法模块--上层调用方)
7. [前端组件命名](#7-前端组件命名)
8. [版本与缓存键](#8-版本与缓存键)
9. [最容易混淆的几组命名](#9-最容易混淆的几组命名)
10. [质粒/基因名称的识别关系（专项）](#10-质粒基因名称的识别关系专项)

---

## 1. 功能开关键

`src/backend/app/features.py` 的 `FEATURE_REGISTRY`。键名存进数据库
site_settings 表（JSON 数组），**只能新增不能改名**，前后端共用；
拆分继承见 `SPLIT_INHERIT`（sequencing_batch 继承 sequencing、map 继承 analysis）。

| 键 | 界面名称 | 前端路由 meta | 后端门控的 URL 前缀 |
|---|---|---|---|
| `design` | 引物/序列设计 | `/design`、`/result/:id` | `/api/design`、`/api/design/vector` |
| `batch` | 批量设计 | `/batch` | `/api/design/batch` |
| `vectors` | 载体库 | `/vectors`、`/vectors/:id` | `/api/vectors` |
| `sequencing` | 测序分析 | `/sequencing` | `/api/sequencing/analyze` 等单样品入口 |
| `sequencing_batch` | 批量测序分析 | `/sequencing/batch` | `/api/sequencing/analyze-batch`、`batch-sessions` |
| `analysis` | 序列工具 | `/analysis` | `/api/analysis` |
| `map` | 质粒图谱 | `/map` | `/api/map` |
| `codon` | 密码子表 | （设计页内用） | `/api/codon-tables` |

分析记录的查看/导出/删除端点为 `sequencing` 与 `sequencing_batch` **共用**
（`require_any_feature`，任一开放即可用）。

## 2. 页面 → 前端 API → 后端路由

| 页面（views/） | 路由 | 前端 API（src/api/） | 后端路由 | URL 前缀 |
|---|---|---|---|---|
| HomeView | `/` | — | — | — |
| DesignView | `/design` | `design.ts` + `online_sources.ts` | `routes/design_routes.py` + `routes/design_vector_routes.py` | `/api/design` |
| ResultView | `/result/:designId` | `design.ts` | 同上 | `/api/design` |
| BatchDesignView | `/batch` | `batch.ts` | `routes/batch_routes.py` | `/api/design/batch` |
| VectorsView / VectorDetailView | `/vectors(/:id)` | `vectors.ts` + `online_sources.ts` | `routes/vector_routes.py` + `routes/online_source_routes.py` | `/api/vectors` |
| AnalysisView | `/analysis` | `analysis.ts` | `app/analysis_routes.py`（不在 routes/ 下） | `/api/analysis` |
| SequencingView | `/sequencing` | `sequencing.ts` | `routes/sequencing_endpoints.py` + `routes/sequencing_session_routes.py` | `/api/sequencing` |
| BatchSequencingView | `/sequencing/batch` | `sequencing.ts` | 同上 | `/api/sequencing` |
| MapView | `/map` | `map/api.ts`（专用，不经 api/ 目录） | `routes/map_routes.py` + `routes/map_online_routes.py` | `/api/map` |
| CacheView | `/cache` | `cache.ts` | `app/cache_routes.py` | `/api/cache` |
| AdminView | `/admin` | `admin.ts` | `app/admin_routes.py` | `/api/admin` |
| ForbiddenView | `/forbidden` | — | — | — |

壳文件提醒：前端 `api/index.ts` 只是转发壳，真身在各 `api/*.ts`；
后端 `app/routes/models.py` 同理是兼容壳，DTO 真身在 `app/schemas.py`。

## 3. 端点对照

### 3.1 设计主线（`design_routes.py` → `design_service.py`）

| 前端函数 | 后端端点 | 背后的 core |
|---|---|---|
| `submitDesign` | `POST /api/design` | `design_service` → `codon_optimizer` + `primer_designer` + `construct_assembly` + `clone_strategy` |
| `getRestrictionPreview` | `POST /api/design/restriction-preview` | `construct_assembly`（占位基因真实组装跑一遍回坐标） |
| `getDesign` | `GET /api/design/{id}` | — |
| `downloadGenbank` / `downloadPrimers` | `GET /api/design/{id}/download/genbank` / `/primers` | `export_formats` / `genbank_format` |
| （图谱标签页） | `GET /api/design/{id}/map` | `plasmid_map`（契约 `plasmid-map/1`） |

### 3.2 设计页一次性载体（`DesignVectorPicker.vue`，只取不存）

路由文件 `design_vector_routes.py` + `app/adhoc_vector.py`：

| 前端函数 | 端点 |
|---|---|
| `getDesignVectorSources` | `GET /api/design/vector/sources` |
| `uploadDesignVector` | `POST /api/design/vector/upload` |
| `importDesignVectorOnline` | `POST /api/design/vector/online` |
| `searchDesignVectorSnapgene` | `GET /api/design/vector/snapgene/search` |

### 3.3 批量设计（`batch_routes.py`）

| 前端函数 | 端点 |
|---|---|
| `submitBatchDesign` | `POST /api/design/batch` |
| `getBatchProgress` | `GET /api/design/batch/{batch_id}` |
| `getBatchReport` | `GET /api/design/batch/{batch_id}/report` |
| `downloadBatchResults` | `GET /api/design/batch/{batch_id}/download` |
| `downloadBatchPrimers` | `GET /api/design/batch/{batch_id}/download/primers` |
| `parseSpreadsheetFile` | `POST /api/design/batch/parse-spreadsheet` |
| `downloadBatchTemplate` | `GET /api/design/batch/template` |

### 3.4 载体库（`vector_routes.py` → `vector_library.py`，读 `data/vectors/*.yaml`）

| 前端函数 | 端点 |
|---|---|
| `getVectors` / `getVector` / `updateVector` / `deleteVector` | `GET /api/vectors`、`GET/PUT/DELETE /api/vectors/{id}` |
| `getVectorSequence` | `GET /api/vectors/{id}/sequence` |
| `fetchVectorGenbankFile` | `GET /api/vectors/{id}/map` |
| `searchNcbi` | `GET /api/vectors/search/ncbi` |
| `previewNcbi` | `GET /api/vectors/preview/ncbi/{seq_id}` |
| `importFromNcbiId` | `POST /api/vectors/import/ncbi-id`（另有 `POST /import/ncbi`） |
| `uploadVectorFile` | `POST /api/vectors/import/upload` |
| `batchImportVectors` | `POST /api/vectors/import/batch` |
| `saveMapAsVector` | `POST /api/vectors/import/from-map`（图谱页「另存为载体」） |

### 3.5 在线序列来源（载体库侧 + 图谱页侧，共用 `app/online_import.py` + `core/online_sources.py` 与 `online:snapgene:*` 缓存）

| 来源 | 载体库侧（写端点需管理员，`vectors` 门控） | 图谱页侧（`map` 门控） |
|---|---|---|
| SnapGene | `/api/vectors/import/snapgene/*` | `/api/map/import/snapgene*` |
| Ensembl | `/api/vectors/import/ensembl/*` | `POST /api/map/import/ensembl` |
| Addgene | `/api/vectors/import/addgene/*` | — |
| UniProt（只反查交叉引用，蛋白不导入） | `/api/vectors/import/uniprot/*` | — |
| NCBI | `POST /api/vectors/import/ncbi-id` | `POST /api/map/import/ncbi` |

前端函数按来源命名：`getSnapgeneSets` / `getSnapgeneSetIndex` / `searchSnapgene` /
`previewSnapgene` / `importFromSnapgene`；`previewEnsembl` / `importFromEnsembl`；
`searchAddgeneIndex` / `getAddgeneMetadata` / `importFromAddgene`；
`searchUniprot` / `getUniprotEntry`。

### 3.6 序列工具（`app/analysis_routes.py` → `core/sequence_analysis.py`）

| 前端函数 | 端点 |
|---|---|
| `analyzeSequence` | `POST /api/analysis/analyze` |
| `findRestrictionSites` | `POST /api/analysis/restriction-sites` |
| `predictORFs` | `POST /api/analysis/orfs` |
| `simulateDigest` | `POST /api/analysis/digest` |
| `analyzeGC` | `POST /api/analysis/gc-analysis` |
| `checkCompatibility` | `POST /api/analysis/compatibility` |
| `getEnzymes` | `GET /api/analysis/enzymes` |
| `exportSequence` / `getExportFormats` | `POST /api/analysis/export(/all)`、`GET /export/formats` |
| （设计/载体快捷导出） | `GET /api/analysis/design/{id}/export`、`GET /api/analysis/vector/{id}/export` |

### 3.7 Sanger 测序（`routes/sequencing_endpoints.py` 等 → `core/sanger/`）

| 前端函数 | 端点 | 对应管线模块 |
|---|---|---|
| `analyzeSequencingFiles` | `POST /api/sequencing/analyze` | `pipeline`（修剪→比对→共识→结论） |
| （参考解析预览） | `POST /api/sequencing/parse-reference` | `reference_parser` |
| `analyzeSequencingBatch` | `POST /api/sequencing/analyze-batch` | `core/sanger/batch` + `app/sequencing_batch` |
| `downloadBatchSequencingReport` | `GET /api/sequencing/batches/{batch_id}/report` | `app/sequencing_report` |
| （设计结果直测） | `POST /api/designs/{id}/sequencing/analyze` | 同 analyze |
| （库载体直测） | `POST /api/vectors/{id}/sequencing/analyze` | 同 analyze |
| `listSequencingAnalyses` / `getSequencingAnalysis` / `deleteSequencingAnalysis` | `GET/DELETE /api/sequencing/analyses...` | `sequencing_store` |
| `getReadTrace` | `GET .../analyses/{id}/trace/{read_index}` | `abif_reader` |
| `exportConsensus` | `GET .../analyses/{id}/consensus/export` | — |

批量分块上传会话（`routes/sequencing_session_routes.py`，各端点自带
`sequencing_batch` 门控）：`POST /api/sequencing/batch-sessions` →
`/{session_id}/files` → `/{session_id}/finalize`，`GET`/`DELETE` 同路径；
前端经 `api/sequencing.ts` 的 `batchSessionTransport` 封装。

### 3.8 认证 / 管理 / 限流

| 前端 | 后端 | 前缀 |
|---|---|---|
| `auth.ts` | `app/auth/routes.py`（register、login、verify-email、resend-verification、site-config、me、logout、verify、users） | `/api/auth` |
| `admin.ts` | `app/admin_routes.py`（settings、users） | `/api/admin` |
| — | `app/rate_limit_routes.py`（status、config、health） | `/api/rate-limit` |
| `cache.ts` | `app/cache_routes.py`（stats、clear、invalidate/design、invalidate/vector、health） | `/api/cache` |

## 4. 共享契约（DTO）

后端 `app/schemas.py` ↔ 前端 `src/types/index.ts`（各自定义、名字刻意一致）。

| 分组 | DTO |
|---|---|
| 设计请求 | `DesignOptions` → `DesignRequest` / `BatchDesignRequest` |
| 一次性载体 | `DesignVectorFeature` / `DesignVectorMcs` / `DesignVectorInput` / `DesignVectorOnlineRequest` / `DesignVectorPayload` |
| 预览 | `RestrictionPreviewRequest` |
| 设计结果 | `PrimerInfo`、`DesignResult` |
| 载体 | `VectorInfo`、`VectorMapPreview`、`VectorUpdateRequest`、`VectorPreviewResponse`、`BatchImportRequest` |
| 批量设计 | `RowParams`、`BatchDesignStatus`、`BatchProgressResponse` |
| 在线导入 | `SnapGeneImportRequest`、`EnsemblImportRequest`、`OnlinePreviewResponse` |
| 图谱（契约 `plasmid-map/1`，docs/MAP.md） | `MapDocument`、`MapSegment`、`MapFeature`、`MapEnzymeSite`、`MapEnzyme`、`MapOrf`、`MapPrimer`、`MapMeta`、`SaveMapAsVectorRequest` |

## 5. 枚举值字典

| 字段 | 取值 | 备注 |
|---|---|---|
| `sequence_type` | `amino_acid` \| `dna` | 无 "protein" 写法 |
| `insert_source` | `pcr` \| `gene_synthesis` | 与克隆方法**正交** |
| `cloning_method` | `gibson` \| `golden_gate` \| `restriction` \| `gene_synthesis` | 旧值 `gene_synthesis` 被校验器归一为 `insert_source=gene_synthesis` + `restriction`（且仍须给 enzyme_5/enzyme_3，否则 422）；`RestrictionPreviewRequest` 里另有 `custom_arms`（自定义同源臂，仅预览用） |
| `insert_direction` | `auto` \| `reverse` \| `plus` \| `minus` | auto 听载体的；方向由程序判定，不进表单手选位点 |
| `gibson_linearization` | `double_digest` \| `single_site` \| `custom_arms` | |
| `DesignStatus` | `pending` → `running` → `completed` / `failed` | |
| `topology` | `circular` \| `linear` | 贯穿载体/图谱/测序参考；文件未明确标 linear 一律按环状比对 |
| `vector_source` | `library` \| `inline` | inline = 一次性载体（随 `POST /api/design` 内联提交） |
| 在线来源 `source` | `snapgene` \| `ensembl` \| `ncbi` \| `addgene` | UniProt 不在此列（只反查引用） |
| `seq_type`（Ensembl） | `genomic` \| `cds` \| `cdna` | |
| `MapFeature.source` | `file` \| `auto` \| `design` \| `vector` | 元件出处：文件注释/自动识别/设计插入/载体带入 |
| `strand` | 一次性载体接受 `+`/`-`/`1`/`-1`/`0` | 图谱内部归一为 `1`/`-1`/`0` |
| `MapEnzyme.ends` | `5'` \| `3'` \| `blunt` | |
| 酶位点甲基化屏蔽 | `dam` \| `dcm` | `MapEnzymeSite.blocked_by` |

## 6. core 算法模块 ↔ 上层调用方

| core 模块 | 被谁调用 |
|---|---|
| `codon_optimizer.py` | `design_service`（缓存键含 `CODON_OPT_ALGO` + 密码子表摘要 + exclude_enzymes） |
| `primer_designer.py` | `design_service`（PCR/Gibson/GoldenGate/双酶切引物 + 合成 oligo） |
| `construct_assembly.py` | `design_service.assemble_construct` + `restriction-preview` 端点 |
| `sequence_analysis.py` / `enzyme_sites.py` | `analysis_routes` + `plasmid_map`（唯一限制酶模型，默认菜单 51 种） |
| `clone_strategy.py` / `export_formats.py` / `genbank_format.py` | `design_service` / 导出端点 |
| `vector_library.py` | `vector_routes`（`data/vectors/*.yaml`） |
| `vector_import.py`（app 层） | 所有外部/在线序列 → 载体 YAML 的统一导入入口 |
| `plasmid_map/` | `map_routes`、`vector_routes`、`design_routes` 的 `/map` 端点 |
| `seq_input.py` | `DesignRequest`/`BatchDesignRequest` 归一入口（NFKC、异体星号→`*`、U→T 仅 DNA） |
| `sanger/`（`reference_parser`/`abif_reader`/`aligner`/`annotator`/`signal`/`pipeline`/`batch`/`cds_extract`/`reliable_zone`/`homopolymer`） | `sequencing_endpoints` + `app/sequencing_batch` + `app/sequencing_report`（网页端点与离线脚本共用） |

## 7. 前端组件命名

- **全站统一**：`EnzymeAutocomplete.vue`（酶选择器，按酶名/识别序列搜索；API 失败回退内置酶表）
- **设计页**：`DesignVectorPicker.vue`（三条载体来源：库/上传/在线）、`RestrictionPreview.vue`（只画图，坐标来自预览接口）、`SnapgeneSetPicker.vue`
- **载体库**：`VectorUploadModal.vue` / `VectorBatchImportModal.vue` / `VectorEditModal.vue`
- **图谱**：`map/components/MapViewer.vue` = `CircularMap` + `LinearMap` + `SequenceMap` 的组合；纯算法在 `map/core/`（坐标/分道/标签布局/酶过滤/搜索/Tm，均有单测），共享状态 `map/store.ts`
- **测序**：`SequencingPanel.vue`（上传→分析→展示主壳）+ `components/sequencing/` 展示卡（`PolyCard`/`ConclusionCards`/`ReadTable`/`VariantTable`/`AlleleCard`/`ConsensusCard`/`MatchMap`→内部复用 `map/LinearMap`）；峰图融合视图逻辑在 `composables/useSeqViz.ts`

## 8. 版本与缓存键

| 键 | 位置 | 守护什么 |
|---|---|---|
| `CODON_OPT_ALGO` | `app/cache.py` | 密码子优化缓存键里的算法版本 |
| `ANALYSIS_ENZYMES_VERSION` | `app/cache.py` | `/analysis/enzymes` 结果口径 |
| `plasmid-map/1` | 图谱契约 | `MapDocument` 结构版本（前端 `map/types.ts` 同名） |
| `online:snapgene:*` | Redis/内存 | SnapGene 集合索引缓存（载体库侧与图谱页**共用**） |
| `APP_VERSION` | `core/_version.py` | 与 package.json 同步，`scripts/check_docs_sync.py` 门禁把守 |

改变结果口径的代码（含酶表数据）必须递增对应版本，否则命中旧结果；
只改 `data/codon_tables/*.yaml` 时摘要自动变化，无需递增。

## 9. 最容易混淆的几组命名

1. **四个 "batch" 互不相干**：`/api/design/batch`＝批量设计；`/api/sequencing/analyze-batch`＝批量测序分析；`/api/sequencing/batch-sessions/*`＝批量测序**分块上传会话**；`/api/sequencing/batches/{id}/report`＝批量测序**整理包下载**。
2. **`cloning_method` vs `insert_source`**：正交维度（怎么克隆 × 片段哪来的）；`gene_synthesis` 出现在 `cloning_method` 里是旧契约残留，会被归一掉。
3. **三种「载体」对象**：`VectorInfo`（库载体，共享库、刻意无属主）／`DesignVectorPayload`（一次性载体，只取不存、随设计请求内联提交）／`MapDocument`（图谱页文档，`vector_source: library|inline` 区分前两者）。
4. **`custom_arms` 一词两用**：`gibson_linearization` 的选项之一，也是 `RestrictionPreviewRequest.cloning_method` 的取值。
5. **在线导入有两套平行端点**：`/api/vectors/import/*`（进库，写端点需管理员）和 `/api/map/import/*`（图谱页临时看），共享底层与缓存但门控不同（`vectors` vs `map`）。
6. **模型壳文件**：`app/routes/models.py` → 真身在 `app/schemas.py`；`src/api/index.ts` → 真身在 `api/*.ts`。
7. **`sequencing` vs `sequencing_batch`**：两个独立功能键（单样品/批量），但分析记录的查看/导出/删除端点两者共用（任一开放即可用）。
8. **交付文件命名**：`10001 PNPM5.DNA`（编号+半角空格+质粒名称），`.DNA` 扩展名大小写不敏感；批量归组多级匹配、候选不唯一一律不猜（详见 §10）。

---

## 10. 质粒/基因名称的识别关系（专项）

### 10.1 各类名称的「出生地」（名字在哪产生）

| 名称 | 来源 | 代码位置 |
|---|---|---|
| 单样品分析的样品名 | **参考文件名主干**（截 60 字符）——不看 GenBank LOCUS、不看 FASTA 头 | `routes/sequencing_endpoints.py` `sample_name = splitext(basename(ref_name))[0]` |
| 批量分析里的质粒名 | **信息表「质粒名称」列**——文件名只用于匹配、不当名字用 | `core/sanger/batch.py` `parse_excel` |
| read（峰图）名称 | .ab1 上传文件名，原样进结果 | `core/sanger/pipeline.py` |
| CDS/gene 名称 | 参考文件**内部特征**：GenBank qualifier 优先级 `label > gene > product > name > apifinfo`；name 截 40 字符、description 截 120 | `core/sanger/reference_parser.py` `_from_genbank` |
| 序列分析页输入名称 | FASTA 头 `>` 后**第一个空白前的 token**（`>PNPM5 xyz` → `PNPM5`），用作显示名 + 导出文件名 | 前端 `utils/sequence.ts` `parseNamedSequence` |
| 图谱页元件名 | 文件注释（同上 qualifier）或元件库自动识别 | `reference_parser` / `core/plasmid_map.annotate` |

⚠️ 同样是 FASTA：**测序参考**的名字取文件名（后端 `_from_fasta` 不读头），
**序列分析输入**的名字取 FASTA 头（前端解析）——两条链路口径不同。

### 10.2 名称归一化函数（匹配前先统一形态）

| 函数（`core/sanger/batch.py`） | 规则 | 例子 |
|---|---|---|
| `norm_stem` | 去空白 + 小写 + 去引号 + 去 `.ab1/.ab` 后缀 | `10001 PNPM5.DNA` → 主干 `10001 pnpm5` |
| `squash_name` | 小写 + 去掉空白和 `-`/`_` | `S99678-M13F-75` → `s99678m13f75` |
| `_cell_str`（Excel 单元格） | 数值 `123.0` → 字符串 `'123'`（克隆号/编号不能带 .0） | |
| read 与图谱的分流 | `READ_EXTS = {.ab1, .ab}` 是 read；其余扩展名全是图谱 | 扩展名各入口 `.lower()` 后分派 |

### 10.3 信息表列的识别（按关键词认，不是固定列名）

`parse_excel`：

- 表头行 = **前 20 行内第一个含「质粒」字样的行**，找不到直接报错
- 列按关键词识别：含「质粒」→质粒列；含「引物」→引物列；含「结果」→结果列；含「克隆」**或**「样品」→克隆号列
- 引物列、结果列缺省时回退 = 质粒列 / 引物列的**下一列**
- 一格里多个引物按 `[;；,，\n]+` 拆分（`PRIMER_SPLIT`）
- 带「克隆号」列且至少一行有值 → **克隆模式**（每行一个克隆、独立分析）

### 10.4 图谱文件名 → 质粒名：四级匹配（全站统一口径）

`_match_ref_name`（`match_files` 与克隆模式共用）：

```
① 全名精确          10001 PNPM5.DNA ↔ "10001 PNPM5"
② 忽略分隔符精确     10001_PNPM5.DNA ↔ "10001 PNPM5"（squash 后相等）
③ 与名称某段一致     MBYSTC.DNA ↔ "17648 MBYSTC"（两段式名称的任一段）
④ 双向包含           短侧 ≥4 字符（防 "MX" 误命中 "MX2"）
```

**每级候选不唯一一律不猜**，返回的原因文本原样展示给用户。
克隆模式下同一质粒名被多张图谱声明时：按匹配强度 0–3 取最精确者；
同强度只有①②视为同一图谱的重复副本取其一，两段式部分匹配命中不同文件 → 全部不猜。

### 10.5 救场链：文件名对不上 → 用图谱内部 CDS 名反查

`app/sequencing_batch.py` `rescue_references_by_features`：

```
10001.DNA（只有编号）→ 文件名匹配失败
  → 解析图谱特征 → feature_alias_names 只取 type ∈ {cds, gene} 的特征名
    （启动子/ori 等通用元件名刻意排除，防歧义），norm_stem 去重
  → match_reference_aliases 把每个 CDS 名走同一套四级口径反查信息表质粒名
  → 多个别名指向同一个质粒、候选唯一 → 认领（挂到该质粒所有缺图谱的组；
    克隆模式下同一质粒的多个克隆共用一张图谱）
  → 指向不同质粒 / 都不匹配 / 解析失败 → 保持未匹配，
    并保留「文件名不匹配」这条更具体的原因
```

### 10.6 read 文件名 → 归属

**普通模式**（`match_files`）：ab1 主干 = 引物列条目精确（`norm_stem`）→
唯一被包含才认（`S99678-M13F-75_extra` 包含 `s99678-m13f-75`）→ 否则未匹配。

**克隆模式**（`match_clone_files`），read 四级优先级：

| 级 | 规则 | 例子 |
|---|---|---|
| ① | `克隆号-引物` 组合精确（含忽略分隔符形式） | `S99678-M13F-75.ab1` ↔ 克隆号 `S99678` + 引物 `M13F-75` |
| ② | 引物列条目本身精确（兼容旧表直接填完整文件名） | |
| ③ | 主干**包含**某组合（复制版后缀） | `xxx-2` / `_copy` |
| ④ | 主干**以克隆号开头**（后面是分隔符或结尾）——信息表漏填引物时兜底 | `S99678.ab1` |

歧义防护：同一「克隆号+引物」组合写在多行 → 不猜；
同一引物名出现在多个克隆行 → 只写引物名的文件无法归属。
图谱侧支持两段式部分匹配（`17648` / `MBYSTC` / `17648 MBYSTC` 混用，
命中唯一候选的图谱共享）。

### 10.7 目的基因模式的 CDS 识别（`cds_mode=True`）

单样品端点参数 `cds_mode` / `cds_name` → `core/sanger/cds_extract.py`：

- 候选 = 图谱内 type ∈ {cds, gene} 的特征（`cds_candidates`）：多段 CDS 按
  `feature_id` 合并；**嵌套去重**——外沿被更大候选完全包含的小特征
  （6xHis 标签、嵌套报告基因）不单列（与管线 `_build_cds_reports` 同款判定）
- 定位优先级：**显式 `cds_name`**（前端下拉）> 自动 `resolve_auto`
- 自动识别（`_stem_hits`）：参考文件主干 + **所有 read 文件名**都拿去匹配候选名
  ——全名四级匹配之外，还按 `[\s._\-]+` 拆 token 逐个试
  （`S99675-17647-seqF1` 的 token `17647` ↔ CDS 名 `17647 MUTPK`）
- 参考名与 read 名的命中取**并集**，收敛到唯一 CDS 才认；多候选/零候选报错，
  错误信息附「可选 CDS：…」（最多 10 个）
- 提取后参考替换为 CDS 编码序列（恒线性），坐标全部变成 CDS 内坐标，
  伪全长特征的 description 记录原区间与定位方式

### 10.8 识别结果的流向

| 识别结果 | 流向 | 展示位置 |
|---|---|---|
| `{"file", "how"}` 匹配说明 | 每个归组项的 `how` 字段 | 批量结果「匹配方式」（`BatchSequencingView` 透出） |
| 质粒名 | 分析记录 → 整理包文件夹名 | `app/sequencing_report.py`（同一质粒一个文件夹，正确/错误分置） |
| 图谱内 CDS 名 | CDS 判定（嵌套去重与 cds_candidates 同款） | 结论「【xxx CDS】…」 |
| FASTA 头名称 | 显示名 + 导出文件名 | 序列分析页输入区、导出下载名 |
| 元件名 | 图谱页搜索兜底：序列命中优先，名称 `contains`（lowercase）兜底并提示「N 个元件」 | 前端 `map/core/search.ts` `searchFeatures` |

回归锁：命名规范与扩展名大小写 `tests/test_extension_case.py`；
CDS 名反查 `tests/test_reference_cds_names.py`。

### 10.9 一句话总结

名字的**权威来源**是信息表/文件名，图谱内容里的名字只是救场证据；
所有匹配都走 `norm_stem`/`squash_name` 两级归一 + 四级强度 + **候选唯一才认**。
