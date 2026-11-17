# Sanger 测序对比算法能力报告

> **范围**：单样品全自动分析管线（`core/sanger/`）与目的基因模式
> （`core/sanger/cds_extract.py`）。**目的**：完整回答「这套对比算法能准确判读什么、
> 判不准什么」。所有能力点均标注代码依据，所有不足均为实测/挂账确认，无推测成分。
> **日期**：初版 2026-11-05；2026-11-18 按分阶段重构（一期结构拆分 + 二期口径调整 +
> 统一酶库）更新第 1 节、补 §2.7 二期口径变化与 §7 环境变量开关。
> 结果口径版本仍是 `SANGER_ALGO_VERSION = "v17"`（`core/sanger/pipeline.py`），
> 下文按「做了什么、口径怎么变」描述，不另起版本号。

---

## 1. 算法总体架构

### 1.1 分阶段流水线（2026-11-12 至 11-18 重构后）

整条链路拆成一串**阶段**，每个阶段只读上一阶段的产物、产出新的结构化对象，
不回头改上游。判定只在 S4/S6 做，文字只在 S7 的 `render_*` 里写——中间任何
一层都不靠拼出来的中文串做判断。

```
参考 + 请求参数
  └─ S0a 参考画像  refprofile.build_reference_profile
  │      归一参考、拓扑、重复结构（poly）清单、元件补全（elements）、设计酶集合（design_enzymes）
  └─ S0b 范围      scope.resolve_scope（唯一范围入口；Form / 批量表格 / coerce_scope 都经它）
.ab1 ─→ S1 read 阶段  pipeline.read_stage（逐条 read：解码修复 → 可信区 → 双向比对 → 端部/split-read）
            （可按阶段缓存：stage_cache，见 §7）
     ─→ S2 合并        pipeline.merge_variants_stage（各 read 差异按参考位点汇总）
     ─→ S3 证据        跨引物互检、置信分层（_variant_confidence_pass 已改纯函数：拷贝后标注、返回新列表）
     ─→ S4 三种裁决（全系统只在这里定状态）
            ReadCall  readcall.build_read_calls   → result.read_calls（read 级：正常 / 全程套峰 / 某点后套峰 / 局部双峰集中）
            SiteCall  sitecall.decide_variant     → 每个差异位点的唯一状态 + 原因码；site_status 统一读三态
            PolyCall  polycall.build_poly_calls   → result.poly_calls（每段 poly 的计数结论，6 种状态）
     ─→ S5 派生        共识、覆盖、缺口、CDS 后果（cds_facts）——只读 S4
     ─→ 影响分级       impact.build_impact_items（范围外元件/设计酶位点/骨架，P1/P2/P3；与范围无关）
     ─→ S6 判定        judge.judge → verdict.build_verdict（全系统唯一调用点）
            按范围把条目分三层，给出 status / blocking / advisories / headline
     ─→ S7 呈现        render_site / render_poly / render_mixed / render_cds / render_impact
            网页结论、Excel、批量结论、整理包都从同一份结构化条目渲染
```

各阶段的要点：

| 阶段 | 模块 | 产出 | 说明 |
|---|---|---|---|
| S0a 参考画像 | `refprofile.py`、`elements.py`、`design_enzymes.py` | 参考画像 dict | 纯函数，只看参考与注释，不读 read |
| S0b 范围 | `scope.py resolve_scope` | 范围对象 | 唯一范围入口；范围只在 S6 起作用 |
| S1 read | `pipeline.read_stage` | 每条 read 的信号、可信区、比对 | 参数见 `params.py` 的 S1 组 |
| S2 合并 | `pipeline.merge_variants_stage` | 位点级差异表 | — |
| S3 证据 | `crosscheck.py`、`evidence.py`、`scoring.py` | 互检、置信、低置信原因（`low_cause` 结构化码） | 只测量、只记证据 |
| S4 裁决 | `readcall.py`、`sitecall.py`、`polycall.py` | ReadCall / SiteCall / PolyCall | 状态与原因码；不含范围字段 |
| S5 派生 | `coverage.py`、`homopolymer.py`、pipeline 的 CDS 部分 | 覆盖、缺口、`cds_facts`、`poly_dropouts` | 只读 S4 |
| 影响分级 | `impact.py` | ImpactItem 列表 | 一个对象一条，取最高级 |
| S6 判定 | `judge.py`（调 `verdict.build_verdict`） | verdict | 唯一调用点；`rejudge` 供范围重算、人工复核、decompose 后重建共用 |
| S7 呈现 | `render_*.py`、`conclusion_fold.py` | 结论行、Excel 列 | 只拼字，不判定 |

几条规矩（改代码前看一眼）：

- **各阶段尽量是纯函数**：不改入参，换范围或改 poly 阈值只需重跑 S6–S7
  （`judge.rejudge`），不重新比对。
- **判定只在 SiteCall / judge 里**：SiteCall 的硬阻断显式列在
  `sitecall.HARD_BLOCKS`（正反向重叠区分歧、不同 read 同区段套峰、合并压缩区、
  跨 read 互检矛盾、染料团）；下游只读 `site_status` / `poly_calls_of` / verdict。
- **文案只在 `render_*`**：结论行、低置信原因、双峰行、CDS 判读、poly 行、
  附带发现都由呈现层从结构化字段生成；`_build_conclusion` 只负责排序与折叠。
- **参数集中在 `params.py`**：按阶段分组（`STAGE_PARAMS`），pipeline 以旧名
  re-export；只有 S1–S3 的参数进缓存键，S6 阈值（如 poly 大量缺失）不进。
- **旧记录兼容**：旧存档没有 `read_calls` / `poly_calls` / `low_cause` 时，
  `poly_calls_of`、`ReadCalls.from_result`、`site_status` 按旧字段即时推导，
  不重算。

### 1.2 底层模块（各阶段内部用到）

```
abif_reader（碱基/Q值/四通道峰形提取）
reliable_zone（read 可信区逐条判定：位置先验 + Q值 + 峰型）
aligner（双向局部比对：k-mer 种子定位 + PairwiseAligner 延伸）
signal（峰级证据：突变峰占比/信噪比/插入峰强度/poly 峰计数）
homopolymer（同聚物结构识别 + 滑移/压缩判读）
annotator（变体特征注释 + CDS 氨基酸级注释）
可选：tracy 解卷积（疑似混合样品时，检出 tracy 可执行文件才启用）
```

- **比对策略**（aligner.py）：每条 read 正/反两向各比一次局部比对取高分判方向；
  参考 >20 kb 时先用 k-mer 投票定位对角线再窗口延伸（审计 #6），上限 200 kb
  （`MAX_REF_BP`，局部比对 O(参考×read) 的资源约束）。环状参考对
  「参考 + 参考前段」整条比对，跨原点 read 坐标折回。
- **设计哲学**：**保守降级，不冒险断言**。证据不足（弱峰/低Q/可信区外/单 read
  无互检）一律降置信、不进结论判定——把「报一致」的假阴性压到最低，
  代价是少量真变异标为「待人工核对」。

## 2. 能力清单（判定准确的部分）

### 2.1 变体检出与置信分层（Mutation Surveyor 同款评分要素）

| 能力 | 判定依据（代码位置） | 可靠度 |
|---|---|---|
| 替换检出（峰级证据齐全时） | 突变通道占比 ≥80% + 信噪比 ≥10 + Q≥40 → 高置信（`_variant_confidence`）；占比 <70%（双峰形态）或 SNR<5 直接低置信 | 高 |
| 插入检出 | 插入峰/邻峰强度比：≥0.6 且 Q≥25 → 高；<0.3 判 basecaller 伪影；峰压缩区 Q 系统性偏低不一票否决（Q 值已知短板的补偿） | 高 |
| 缺失检出 | 无峰证据退回 Q 口径，单 read 门槛 Q30（indel 假阳性率高于替换的补偿） | 中高 |
| N/模糊碱基 | 恒低置信、不进共识与判定（basecaller 拿不准才给 N） | 高（保守正确） |
| 可信区外差异 | read 首 30bp 硬区 / 31–60bp Q值+峰型判定 / 700–900 灰区 / 900 后硬区；区外单 read 差异直接低置信，多 read 支持才升级（reliable_zone.py） | 高 |
| poly 区 indel | 峰幅比 <0.5（压缩明显）→ 不给高置信；轻微压缩仍走常规口径 | 中高 |

### 2.2 跨引物互检（多 read 交叉验证，本算法的主力机制）

- 单 read 差异 → 其他引物在位点 ±25bp 区域**高质量覆盖**（平均 Q≥30、
  ≥Q20 占 90%+）且读出设计碱基 → 判为该 read 测序误差，排除（`_variant_confidence_pass`）。
- 多引物同报同一参考位点 → 真实变异的最强证据，升级置信。
- 真实数据回归锁定该机制（MX 批次 6653/6666 两处单 read 缺失被互检排除）。

### 2.3 混合样品判读（双峰检测）

- 位点级：次级峰/主峰 >30%（`MIXED_PEAK_RATIO`）计双峰；pull-up（染料拖影）
  按「主峰近饱和 + 次级占比低 + 同期漏峰」三条件抑制；退化窗口（四通道同高）
  护栏防整段误报。
- read 级分级：≥5 处双峰 → widespread「疑似混合样品」；2–4 处 → scattered
  仅提示不升级结论。
- **跨引物互检兜底**：多数双峰位点被其他引物测过且峰形单一 → 倾向该 read
  自身信号问题而非真实混合（真实 MX 批次 22 处双峰 → 互检后仅 4 处待核）。
- 可选 tracy decompose 解卷积（需安装 tracy，未装时自动跳过不报错）。
- **2026-11 起**：「疑似混合」改由 read 级 ReadCall 判定（看整条 read 是否套峰，
  而不是数双峰位点），上面「≥5 处 → widespread」的分级只作为证据保留在
  `mixed_summary` 里；新口径见 §2.7。

### 2.4 同聚物（poly）结构判读

- 三层判读：**计数确证**（峰图独立计数 vs 碱基调用，两 read 互证 accepted）/
  **矛盾**（互证冲突 → 不可信）/ **不可判定**（无可分辨峰证据）。
- 「缺第几个碱基」定位：信息论不可解（KNOWN_ISSUES 挂账 #1），正确输出
  「计数确证但定位存疑」注记而非编造位置。
- 比对端点落在 poly 内部的歧义显式提示（覆盖边界可能有数 bp 偏差）。
- **2026-11 起**：每段 poly 的计数结论由 PolyCall 统一给出（6 种状态：确证计数 /
  疑似缺失 / 各 read 矛盾 / 不可判定 / 只有调用数 / 未覆盖），报告、Excel、
  GenBank/FASTA 导出、批量结论都经 `polycall.poly_calls_of` 读取；
  确证的大量缺失会挡住 ✓（§2.7）。

### 2.5 CDS 蛋白级判定

- 共识重建 CDS 翻译产物与参考**逐位比对**：同义/错义/无义分类、提前终止、
  移码判定（删除按实际落入 CDS 的碱基数取模，防跨边界误判）。
- 特征边界 1–3bp 偏差自动 ORF 校正（手动标注的常见误差）。
- 多段 CDS（GenBank join，含跨原点）按编码方向拼接。
- **目的基因模式**（v2.37.0-alpha.1 新增）：按文件名定位参考内目的 CDS，
  CDS±150bp 侧翼为参考（零侧翼会吞边界变异——真实数据实证并留档
  `postmortems/2026-11-05-cds-mode-coverage-gate.md`）；read↔CDS 覆盖关系
  逐条标注；结论覆盖资格门（目标零覆盖强制 △ 未能验证，不给 ✓）。
- 批量模式另有覆盖下限守门（无完整覆盖 CDS 且共识实测 < 阈值不判合格）。

### 2.6 结论与报告

- ✓/✗/△ 三态结论头 + 分层正文；低置信差异不计入判定但显式列出；
- 覆盖缺口、poly 存疑、端点歧义、混合提示各归其位（2026-11-05 瘦身后
  结论只留判读+汇总，逐条证据进变体表/Read 表）。
- 匿名记录 access_token、批次整理包等报告安全机制另见 KNOWN_ISSUES 审计索引。

### 2.7 二期口径变化（2026-11-17 至 11-18）

结构重构完成后，下列口径做了调整（默认都已打开，多数可用环境变量回旧口径，见 §7）：

| 项 | 以前 | 现在 |
|---|---|---|
| **read 级套峰** | 双峰逐位点统计，≥5 处就算「疑似混合样品」 | ReadCall 先看整条 read：全程套峰、某点后整体套峰、局部双峰集中分开判。套峰段内的差异和 split-read 断点不作确证 |
| **疑似混合的门槛** | 一条 read 双峰多就写「疑似混合样品」 | 只有一条 read 套峰 → 头行「待定」，正文「⚠ 测序套峰：…仅此一条 read，未经其他引物印证」；不同 read 同区段都套峰才写「疑似混合样品」。头行和正文同源（都读 ReadCall） |
| **局部双峰集中** | 混在双峰计数里 | 不判混合，单出一行「△ read 第 x–y 位双峰集中…建议复测」 |
| **零散双峰** | 「↳ 双峰 N 处」子行 | 判定不变（可以是合格），措辞改为「注意：…零散双峰，未形成套峰；若各处次峰比例一致，仍可能是两种克隆混合——建议挑单克隆复测，并核对峰图」 |
| **poly 缺失数** | 各 read 取最大值 | 完整覆盖、且不是 noisy（峰数多于调用数）的 read 之间取最小值；没有这样的 read 时仍取最大值。插入数仍取最大值 |
| **poly 大量缺失报警** | 确证缺失多少都不影响状态 | 确证缺失 ≥5 个碱基 **或** ≥ 该段参考长度的 15%（任一达到）→ 新阻断 `poly_large_deletion`，不能判 ✓。两个阈值各自可停用；优先级：`params.py` 默认 < 环境变量 < 单次请求（分析页可改，结果页可「重新判定」）；所用阈值写进 `verdict.poly_thresholds` |
| **变体重复数行** | 读单条 read 的峰图计数 | PolyCall 已确证计数时，同一行的测得数、峰图计数、indel 长度都取 PolyCall 的综合值；和比对报出的 indel 长度不同时注明「此为综合 N 条 read 的 poly 计数，比对报出的是 Xbp」 |
| **低 AF 双峰（AF<0.15）** | 全部进 verdict 的 info | 只有落在声明范围内的才进 verdict（未声明范围视为范围外）；`result.mixed_variants` 照常全量收录，换范围重算可复现 |
| **位点三态** | CDS、批量、verdict、evidence 各自推导 | 统一走 `sitecall.site_status`（confirmed / unresolved / rejected），旧存档按同一规则推导 |
| **元件补全** | 只认参考里的标注 | 三级补全：参考标注 > 自动识别（图谱元件库，一致度 ≥90% 且覆盖 ≥80%）> 从 CDS 推定（SD/Kozak 窗口、上游 150bp 调控区）；按宿主（T7/lac 用 SD，CMV/EF1a 用 Kozak）取舍；报告注明来源 |
| **影响分级与三层报告** | 范围外关键元件出现差异 → 待定 | 第一层：主结论（只看声明范围 + 接合区），状态只由这一层决定。第二层「附带发现」：范围外异常按 P1/P2/P3 排序，同一元件合并取最高级；确证 P1/P2 单出一行「⚠ 范围外发现 N 处需关注的确证异常」，**不改状态**（前端红字）；其余未决合成一句「另有 N 处范围外未决」。第三层：范围外未完整覆盖、未评估的元件一句话带过 |
| **设计酶** | 无 | 用户填写的酶 + 参考里 `pd_enzyme` 标注的酶；都没有时取声明范围（插入片段）两端 flank 内（边界外 100bp 到内 20bp）、全质粒只切一次的酶；再没有就为空，**不再用全质粒单切酶兜底**。参考 COMMENT「pd_design_enzymes: avoid=…」给出要避开的酶。位点被破坏判 P1，新引入设计酶/避开酶位点判 P2 |
| **酶标注导出** | 无 | 本项目构建导出 GenBank 时，克隆/线性化用酶写 `misc_feature`（`/label=酶名`，`/note="pd_enzyme role=cloning\|linearize"`），避开的酶写文件级 COMMENT；Sanger 分析读回后当作用户指定。单样品与批量入口都接了 `enzymes` 参数 |
| **统一酶库** | 设计酶与图谱各扫各的 | 设计酶改用 `plasmid_map.scan_enzyme` 扫描，与图谱同口径；酶菜单用 `core.enzyme_sites.DEFAULT_ENZYMES` |
| **Excel 新列** | — | 「套峰 read 数」（ReadCall 判全程/某点后套峰的 read 条数）、「零散双峰数」（未判套峰的 read 上的双峰位点数之和）；旧记录无 `read_calls` 时即时推导 |

## 3. 不足之处（按影响排序）

### 3.1 物理极限类（算法层面无解，任何 Sanger 分析共有）

| # | 不足 | 现状处理 |
|---|---|---|
| P1 | **低比例混合不可见**：次级峰占比 <30% 的污染/混合在峰形上无法区分（阈值 0.30 是信噪比极限，调低会淹没在噪声里） | 检出即分级提示；未检出无法负责。需克隆划线纯化后复测 |
| P2 | **优势克隆压制**：一个克隆占比 >80–90% 时峰形与纯克隆无异 | 无法检测；建议测多个克隆 |
| P3 | **poly 内缺失定位**：同聚物内「缺第几个 A」信息论不可解 | 「计数确证、定位存疑」注记（已挂账，正确行为） |
| P4 | **覆盖范围外的事件不可知**：大片段插入/倒位/重复发生在 read 覆盖区外，算法只能通过缺口与「大段未比对」提示存在，无法定性 | 结论/缺口提示；补引物走缺口提示 |

### 3.2 实现取舍类（可改进，有明确代价）

| # | 不足 | 现状与代价 |
|---|---|---|
| P5 | **单条 read 的独立判断力有限**：无互检时，indel 判定退回 Q 值口径（假阳性率高于替换的物理现实）；单 read 报告的高置信变异仍建议人工核对峰图 | 多引物设计可根治；单 read 场景置信分层已保守 |
| P6 | **参考序列错误会传导**：read 与参考的差异全部按「read 侧」解释；参考本身有错（如载体图谱注释错误）时无交叉证据可辨 | 参考来自设计结果/载体库/上传文件，链路有 data_provenance；无法从比对本身发现 |
| P7 | **比对是局部比对，非组装**：多条 read 不做拼接组装（de novo），完全依赖参考；参考缺失区域永远没有「意外发现」能力 | 设计验证场景以参考为锚是正确取舍；探索性测序不适用 |
| P8 | **杂合位点与混合样品不区分**：质粒应无杂合，但若样品实际是二倍体基因组 PCR 产物，双峰解释会错位 | 单质粒平台的场景假设，非目标场景 |
| P9 | **可信区边界是先验参数**：30/60/700/900 是经验默认值，个别 read 信号异常好/坏时边界不准（逐条 Q值+峰型判定缓解，但不消除） | 高级参数可调；边界外判定已保守 |
| P10 | **tracy 依赖可选**：混合样品解卷积需用户自行安装 tracy；未安装时混合判读只到「疑似」级 | 环境变量 TRACY_BIN 可指路；缺省静默跳过 |

### 3.3 已挂账项（KNOWN_ISSUES，与对比算法直接相关）

- poly 内「缺第几个 A」= P3（信息论不可解，维持注记）；
- 单 read indel 降级口径依赖 Q 值（P5 的具体体现）；
- 批量页「疑似混合样品」判定是真实判定（MX 批次实证），但 scattered 级
  （2–4 处双峰）只提示不升级——低置信混合可能被轻描淡写（有意取舍：
  MX 实测 22 处双峰多数为互检可排除的信号噪声）。
- 零散双峰（未形成套峰）不判混合：两个点突变克隆混在一起时峰图恰好也是零散
  双峰，所以正文提示「仍可能是两种克隆混合，建议挑单克隆复测」，但状态不变
  （可能是合格）。这一取舍与旧测试期望不同，相关测试以 xfail 记录口径差。

### 3.4 性能与规模边界

- 参考 ≤200 kb（局部比对资源约束）；单请求 ≤24 个 ab1（批量的 read 上限
  另由 site-config 分档）；计算预算超限按 413 拒绝（估算 Mbp·read）。
- 1 核 2G VPS 单批约 100 组内秒级~分钟级完成（真实 MX 批次 3 read 全管线约 2s）。

## 4. 准确性的实证基线

| 基线 | 内容 |
|---|---|
| 合成数据 | make_ab1 全参数化（碱基/Q/四通道峰形），1850 项测试覆盖各判定分支 |
| **真实数据回归**（唯一权重资产） | `plasmid-designer V1.01/sequencing results` 三批次：MX（pET 环状，双峰 22 处、互检排除 11 处、移码插入）、40001/40008（M13F/R + 基因引物交叉覆盖）；6 项断言锁结论口径与形状，数据不在场自动 skip |
| 专项回归 | CDS 模式 vs 全质粒模式变体集合等价锁（侧翼锚定正确性）；双峰可信区约束（报出的位点必须落在可信区内） |

## 5. 范围与归因（goal v3 scope v3，2026-11-10 收口）

**核心模型**：用户可选「需要测哪一段」（Scope），整条质粒只比对一次，
结论只对声明范围负责。三态范围语义：

| 状态 | 含义 | 判定口径 |
|---|---|---|
| declared（声明） | 用户选了范围（预设/元件/区间/Excel 列） | 范围内逐 bp 负责：✓/✗/△ 都有牙齿 |
| measured（未声明） | 没选范围，只说测了哪里 | 「已测 x–y」如实报告；范围外一律「未评估」，禁整参考一致性主张 |

**范围来源优先级**（`scope.py infer_scope`）：user（显式输入）>
design_insert（设计流插入区间）> cds_target（目的基因模式目标 CDS）>
filename_match（参考文件名命中）> primer_window（引物理论窗）>
measured。同一参考再次分析时按指纹带出上次声明范围（范围记忆，
`sequencing_scope_memory` 表）。

**缺口归因**（`coverage.py` + `primer_check.py`）：范围缺口三分类
`beyond_primer`（引物理论窗够不到→加引物）/ `read_shortfall`（应测
未测→补测/核对）/ `unknown`（归因不明→人工判断）——Excel「缺口原因」
列、前端色带（橙/红/紫）、复核队列入队规则共用同一归因。

**判定内核契约**（`verdict.py build_verdict` 纯函数）：换范围重算
不重跑比对（W11 重算端点毫秒级）；pass ⇒ blocking 为空；三出口
（网页头行/Excel/前端卡片）由 `verdict.status` 经单一映射导出；
人工确认（复核队列一键定夺 artifact/mutation）随 payload 留痕并
进 headline「人工确认 N 处」。

**v3.1 增补（2026-11-10，复审 b39b46a 后的三处含金量修正）**

1. **峰形门控**（`peak_shape.py`）：真实 read 尾端「峰展宽、分辨率下降」
   会产生看似次峰的信号，此前被 af 分带判成混合并建议「重新挑单克隆」
   （会造成真实的实验浪费）。现以 `峰宽 FWHM ÷ 相邻峰间距 > 1.0` 连续
   ≥15bp 判定退化区段，区内双峰一律作「测序未达」。峰间距是仪器固有量
   （实测 10–14 采样点），因此该比值跨数据集可比。实测：合成 ×2.0/×2.5/
   ×3.0 全覆盖，真实 MX 三条 read 零退化段（互检基线与混合计数不变）。
2. **复核队列三级聚合 + 结论行预算**（`review.py` / `conclusion_fold.py`）：
   read 已判 mixed 时位点折成一项、同类相邻 ≤20bp 合并为区段、位点级只收
   确证差异与孤立未决位点。真实子集复核队列 330 → **14** 项、全质粒声明
   393 → **17** 项、MX → **3** 项（仅确证差异）；网页结论正文限 6 行
   （头行 + 5 行要点），明细进折叠组与明细表。
3. **出口逐字同源 + indel 双坐标**：头行唯一来源 `verdict.headline`，
   Excel 结论列 = 去掉符号的 headline（此前三处各自拼接，出现过
   「不合格：与设计一致」的自相矛盾）；indel 同时给质粒左对齐与 HGVS 3′
   坐标（`coords.plasmid` / `coords.plasmid_3p`），并用
   `consequence` 给后果注释。

**实证基线**：场景库 67 个场景定义（88 个测试函数；v3.1 新增 14 个场景），
**87 通过 / 1 xfail（CM3）**；
唯一 xfail 是 CM3——其 r2 构造 `ref[0:300] + ins2 + ref[301:450]` 跳过
`ref[300]`，read 与参考等长，序列上等价于替换而非插入（详见
KNOWN_ISSUES 与 PROGRESS_v3.1「X4」），修它需改 X0 冻结的场景构造，
按 §7 停止条件挂账。真实 AB1 子集入仓（`tests/data/real_ab1/`，
DEMO1 3 处真实确证变异敏感度锁定）。已知的物理边界不变
（低比例混合 <20% 峰形不可见等）——见 §3。

## 6. 一句话结论

**常规点突变/小 indel 验证（覆盖到位、read 质量正常、无非特殊结构干扰）判定
准确且假阴性风险被系统性压低；不准的场景集中在三类：低比例混合（<30% 峰形
不可见）、特殊结构（poly 滑移定位、压缩区计数）、覆盖区外事件——三者都是
Sanger 技术本身的物理边界，算法已做到「如实标出不确定」而非掩盖。**

## 7. 环境变量开关（2026-11-18，默认值以代码为准）

二期口径都已默认打开；下表的开关用于回旧口径对照或排查。改了开关会改变结果，
仅在排查时临时用，不要在生产上长期设成非默认值。

| 环境变量 | 作用 | 默认 | 代码位置 |
|---|---|---|---|
| `SANGER_READ_MIXED_CALL` | read 级套峰判定（ReadCall）；设 `0` 回逐位点双峰口径 | 开（非 `0` 即开） | `readcall.py` |
| `SANGER_MIXED_LINES_READCALL` | 「疑似混合 / 测序套峰」行与头行改读 ReadCall | `1` | `crosscheck.py` |
| `SANGER_POLY_MISSING_MIN` | poly 缺失数在完整覆盖、非 noisy 的 read 之间取最小值 | `1` | `polycall.py` |
| `SANGER_LOW_AF_SCOPE_ONLY` | AF<0.15 的单点双峰只在声明范围内进 verdict | `1` | `verdict.py` |
| `SANGER_REPEAT_FROM_POLYCALL` | 变体重复数行改读 PolyCall 综合计数 | 开（非 `0` 即开） | `render_poly.py` |
| `SANGER_IMPACT` | 元件影响分级与三层报告（附带发现 / 未评估） | `1` | `verdict.py` |
| `SANGER_ELEMENTS_AUTO` | 元件补全时启用图谱元件库自动识别 | `1` | `elements.py` |
| `SANGER_POLY_LARGE_DEL_COUNT` | poly 大量缺失阈值：确证缺失碱基数 | `5`（`params.py`） | `params.py` |
| `SANGER_POLY_LARGE_DEL_PCT` | poly 大量缺失阈值：缺失占该段参考长度百分比 | `15`（`params.py`） | `params.py` |
| `SANGER_POLY_LARGE_DEL_COUNT_ENABLED` / `_PCT_ENABLED` | 单独停用上面某一个阈值（两个都停则不报警） | 都开 | `params.py` |
| `SANGER_STAGE_CACHE` | S1 read 阶段进程内缓存（同 read、同参考、同参数复用） | `0`（关） | `stage_cache.py` |
| `SANGER_STAGE_CACHE_MAX` | 上面缓存的条目上限 | `64` | `stage_cache.py` |
| `SANGER_SITECALL_SHADOW` | 结果里附带 SiteCall 双跑明细（`site_calls` / `site_call_diffs`），排查用，结果体积会翻倍 | 关 | `pipeline.py` |
| `TRACY_BIN` | tracy 可执行文件路径 | `tracy` | `pipeline.py` |

poly 阈值的优先级：`params.py` 默认 < 环境变量 < 单次请求参数 `poly_thresholds`
（分析页设置，或 `POST /sequencing/analyses/{id}/poly-thresholds` 重新判定）。

