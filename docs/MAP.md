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
  promoter / polyA_signal / ltr / mcs…）；`join()` 保留为 `segments`（单段省略）；
  `color` 只在文件给出时出现（`/ApEinfo_fwdcolor|revcolor`、`/note="color: #…"`、
  SnapGene 特征颜色）；`source` 为 `file | auto | design | vector`；`id` 在文档内稳定
  （调用方给的合法唯一 id 原样保留，其余按 `来源-序号` 补齐）。
  GenBank 的 `gene` 与同跨度 `CDS` 成对出现时只保留 CDS；`primer_bind` 进 `primers[]`。
- `enzymes[]`：只含至少切一次的酶。`sites[].position` 为识别序列在正向链上的
  最左碱基；`cut_top` / `cut_bottom` 为该链在「第几位碱基**之后**」被切（正向链坐标）。
  5' 突出端恒有 `cut_bottom > cut_top`，3' 突出端相反，平末端相等。环状分子切点
  取模到 `1..length`（跨原点的位点与切点都会报出）；线性分子上切点落在分子外或
  恰在末端的位点不算切割。回文位点只报一次（`strand=1`），非回文按识别方向给
  `strand=±1`。`recognition` 用 NEB 记法（`G^AATTC`、`GGTCTC(1/5)`）；Type IIS 的
  `overhang` 是 REBASE 的 `NNNN`。`methylation` 暂不提供（Bio.Restriction 不含
  dam/dcm 敏感性数据）。
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
| `GET /enzyme-sets` | `common`（默认 51 种）/ `unique6`（可商购、≥6 个确定碱基）/ `commercial`（全部可商购单切割对酶）/ `golden_gate`（BsaI、BsmBI、BbsI、SapI、Esp3I、PaqCI） |
| `POST /export/genbank` | 请求体 MapDocument → `.gb` 下载；颜色写 `/ApEinfo_fwdcolor` + `/ApEinfo_revcolor`，自动注释特征附 `/note` 说明来源库与 identity/coverage |

`GET /api/design/{id}/map`（插入片段 `source=design`，载体元件 `source=vector`）与
`GET /api/vectors/{id}/map`（`source=vector`）也返回 MapDocument，均可加
`enzyme_set`、`orf_min_aa` 查询参数。

错误一律中文 `detail`：413 超长 / 超体积，415 不支持的格式，422 非法序列或参数。

## 自动注释

元件库 `data/features/common_features.json`（66 条，来源与扩展方法见
[data/features/README.md](../data/features/README.md)）。比对流程：

1. ≥40 bp 的元件：库序列 14-mer 精确种子（库内出现 >6 次的低复杂度 k-mer 不用）
   → 按（元件, 链, 对角线）聚类 → 候选窗口上 `PairwiseAligner` 局部比对，
   得 identity（相同列 / 比对列，含 gap）与 coverage（覆盖元件长度占比）；
2. 更短的元件双链精确查找；肽标签 / 蛋白酶位点六框翻译后查找肽段；
3. 环状分子在序列末尾接上开头再比，跨原点命中输出 `start > end`、只报一次；
4. identity < 0.90 或 coverage < 0.30 丢弃；coverage < 0.95 标 `auto.fragment=true`；
5. 同一元件、或同类型 / 同名元件重叠超过较短者一半时只留最好的（完整优先 →
   identity 高 → 比对长）；与文件已有特征（同类型或同名、重叠 ≥80%）重复的命中丢弃。

候选比对数封顶 400；10 kb 典型质粒 <0.2 s，300 kb 随机序列约 0.4 s。

## 限制、缓存与限流

| 变量 | 默认 | 说明 |
|------|------|------|
| `MAP_MAX_LENGTH` | 300000 | 单条序列长度上限（bp） |
| `MAP_MAX_UPLOAD_MB` | 5 | 上传文件 / JSON 文本体积上限 |
| `MAP_CACHE_MAX_BP` | 50000 | 只缓存不超过该长度的结果（防内存缓存被大文档撑满） |
| `MAP_FEATURE_LIBRARY` | — | 可选：元件库 JSON 路径（默认 `$DATA_DIR/features/` 或仓库 `data/features/`） |

解析、比对、酶切扫描都在线程池执行。结果缓存 1 小时，键含输入摘要、选项、
元件库版本（库文件 sha256）与 `MAP_ALGO_VERSION`（`map_routes.py`；改变结果口径的
代码改动须递增）。限流沿用中间件 default 档，与 `/api/analysis` 相同。
