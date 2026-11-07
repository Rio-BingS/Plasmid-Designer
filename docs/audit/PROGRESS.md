# Sanger 全量修复阶段汇报（goal_sanger_full_fix_v2）

> 按任务追加滚动记录；每个闸门一节，格式遵 goal §7「阶段汇报」：
> 改动文件 / 新增修改测试 / §4 场景前后对比 / 全量测试数字 / 门禁结果 /
> MX 回归差异 / 性能数据 / 遗留问题。

---

## P0 闸门（T0-T10，2026-11-07，截至 fb1c05a）

### 闸门判定（goal 原文：S1(△)、S2-S16 通过（S17 的注记部分通过）；M6、M10、M13 通过；
### 对照组、全量测试、MX 真实回归（有数据时）绿或差异已逐条解释；CI 门禁本地预演绿；
### KNOWN_ISSUES 更新；SANGER_ALGO_VERSION 已递增）

- **S1(△)**：✅ 实证——`_run("S1")` 当前 verdict.status=pending、blocking=
  unresolved_variant、结论「△ 结论：1 处未决差异，需核对峰图或补测」。变体
  confidence 仍 low（tail_zone 先验未拆，T12 信号驱动化后升 confirmed→✗ 确证），
  场景测试断言 P1 终态，保持 xfail→T12 正确。
- **S2-S16 通过**：✅ 16 项场景测试全绿（xfail 已移除）。
- **S17**：✅ 全绿（test_s17_all_evidence_out_of_zone_noted 通过，reason_codes 含
  all_evidence_out_of_zone）——超出门「注记部分通过」的最低预期。
- **M6、M10、M13**：✅ 全绿（T9 ReadRefMap 统一坐标 / T2 结构化 verdict）。
- **对照组**：✅ 全量 pytest 1907 passed 含全部对照组（反向替换高置信、跨原点无假
  缺失、300bp 缺失/150bp 插入、纯合 ✗、CDS 模式与全质粒模式 MX 一致等）。
- **CI 门禁本地预演**：✅ 四项全绿（见下）。
- **MX 真实回归**：✅ 快照逐字段 diff 无未解释漂移（见「MX 回归差异」）。
- **KNOWN_ISSUES 更新**：✅ 新增 tracy decompose 产物格式挂账（本批次）。
- **SANGER_ALGO_VERSION**：✅ "v2"（T1 递增，v1→v2），写入
  result["algo_version"]，HANDOVER 版本常量表已登记。T2-T10 各提交共享该口径
  版本（P0 期未发版，per-task 不再 bump；P1/P2 闸门如再改口径将递增至 v3/v4）。

### 改动文件（c33e81e → fb1c05a，55 文件 +5517/−795）

- **新模块（全部 ≤900 行）**：`core/sanger/{verdict 398, evidence 332, coverage 208,
  readmap 144, crosscheck 231, mixed_call 65, decompose 136}.py`
- **重写/收缩**：`pipeline.py` 2964→2863（−632/+632 重构，预算顶格）；`homopolymer.py`
  拆出互检簇与坐标映射后 916→656（回默认预算）；`batch.py` Excel 分拣接 verdict；
  `resolved_regions.py` 引物内坐标走 ReadRefMap；`aligner.py` 软剪切窗口 +
  check_unaligned_hq_segment；`signal.py` 滑移间距/链向；`evidence.py`（结论渲染本位）
- **接入层**：`sequencing_routes.py`（decompose_and_annotate 单点入口 + verdict 重建，
  958 顶格）；`sequencing_endpoints.py`、`sequencing_report.py` 小改
- **前端**：`api/sequencing.ts`（verdict/decomposed_alleles/coverage 新字段契约）、
  `ConclusionCards.vue`（结构化 verdict 渲染，去字符串匹配）、`AlleleCard.vue`
  （等位 CDS 判读）、`VariantTable.vue`（reason_codes 徽标 + key 修复）
- **文档/数据**：`docs/audit/{VERIFY_2026-11-07.md, 05-sanger.md, 05-sanger-scripts/}`、
  goal 原文、审计 A 原文入库；`tests/data/sanger_bench/`（3730/3100/310 真实 ab1）；
  `tests/fixtures/tracy/`；`scripts/capture_sanger_baseline.py`；`scripts/size_budgets.json`

### 新增/修改测试

- 新：`tests/sanger_scenarios.py`（456 行场景库，35 项）+ `test_sanger_scenarios.py`
  （34 个测试函数——M8〔A 1.4 PLOC −1〕无独立合成用例，复核表已锚定 T24.1 以真实
  3730 附录 A-PLOC 口径验证）+ `test_sanger_verdict_contract.py`（119 行，四方
  契约：STATUS_CONTRACT/顺序/PENDING_KINDS）+ `test_sanger_tracy_json.py`（3 测试）+
  `frontend_ConclusionCards_verdict.test.ts`（94 行）
- 增：`abif_utils.make_trace_ab1` 高斯峰合成器（secondary/q/n_at/ploc_base）；
  `test_sanger_pipeline.py`（+T7 链向约定等）；`test_batch_sequencing.py`（H1 两用例
  断言升级为 不合格/错误——T6 口径设计内变化，归档不进「正确/」不变）；测试基线
  `test_real_data_regression.py`（T8 待核行按纪律改断言，见 MX 节）
- xfail 注册表：T0 时 33/34（仅 M14 原生绿）→ 现 14 项：S1→T12、F1/F2→T11、
  F3→T24、M1→T25、M2→T14、M3→T21、M4→T16、M5→T17、M7→T24、M9→T25、M11→T18、
  M12→T24、M15→T25

### §4 场景前后对比（旧 = goal「现状」列；新 = 当前 HEAD 实际输出）

| ID | 旧输出 | 新输出 | 状态 |
|---|---|---|---|
| S1 | ✓；共识写参考碱基 | △ pending（unresolved_variant，需核对峰图/补测） | P0 目标达成；✗ 确证待 T12（xfail） |
| S2 | ✓ | △ mixed_variant（AF≈0.35，功能区） | ✅ 绿 |
| S3 | ✓ | △ mixed_variant（AF≈0.5） | ✅ 绿 |
| S4 | ✓（低置信伪影） | △，不得 ✓ | ✅ 绿 |
| S5 | ✓ | ✗ unaligned_hq_segment（5′ 高质量段） | ✅ 绿 |
| S6 | ✓ | ✗ unaligned_hq_segment（3′） | ✅ 绿 |
| S7 | 网页 ✗ / Excel 合格 / 归档「正确」三处不一致 | 三处一致：不合格（critical_element_variant） | ✅ 绿 |
| S8 | ✓；合格 | △ partial（roi_uncovered，部分验证） | ✅ 绿 |
| S9 | ✓（伪影未计入） | △ unresolved（front_hard_zone） | ✅ 绿 |
| S10 | variants=[]、✓ | △ N 位点列出；verified 扣除该列 | ✅ 绿 |
| S11 | ✓ 蛋白一致 | △ n_in_cds，蛋白待定 | ✅ 绿 |
| S12 | 合格；分进「正确/」 | △ read_failed 点名失败文件；不进「正确/」 | ✅ 绿 |
| S13 | coverage 虚高；CDS full | verified 口径 partial / roi_unverified | ✅ 绿 |
| S14 | 6/6 clean → ✓ 合格 | △ 互检 read 未达 _region_ok → 不算 clean | ✅ 绿 |
| S15 | 被「疑似伪影」吸收 | △ junction_variant | ✅ 绿 |
| S16 | 混合位点被滑移剔除 | 混合位点保留并检出（SLIP_ECHO_GAP=3） | ✅ 绿 |
| S17 | ✗ 无注记 | ✗ + all_evidence_out_of_zone 明示 | ✅ 绿（超预期） |
| F1 | △ 疑似混合 | 仍 △（3730 染料肩峰） | ⬜ xfail→T11 |
| F2 | △ 疑似混合 | 仍 △（3100 衰减段） | ⬜ xfail→T11 |
| F3 | 拒绝分析（无 PCON） | 仍拒绝（310.ab1） | ⬜ xfail→T24 |
| M1 | 同密码子逐位注释 | 仍逐位 | ⬜ xfail→T25 |
| M2 | 证据随上传顺序 | 待 evidence_by_read | ⬜ xfail→T14 |
| M3 | 重叠区分歧无报告 | 待 overlap_discordance | ⬜ xfail→T21 |
| M4 | ORF 校正无界改标注 | 待 orf_annotation_suspect | ⬜ xfail→T16 |
| M5 | 无引物校验 | 待 primer_mismatch | ⬜ xfail→T17 |
| M6 | 三处窗口不一致 | 信号/poly/映射三处一致 | ✅ 绿（T9） |
| M7 | secondary_base=None | 待 T24.4 | ⬜ xfail→T24 |
| M8 | （无独立场景测试） | 复核表 A 1.4 成立 | ⬜ 锚定 T24.1 |
| M9 | analyze() 静默线性 | 待 T25.2 | ⬜ xfail→T25 |
| M10 | 引物内坐标错位 | ReadRefMap.primer_coord 统一 | ✅ 绿（T9） |
| M11 | precheck 对坏参考静默 | 待 T18 | ⬜ xfail→T18 |
| M12 | PLOC 短于 PBAS 静默截短 | 待 T24 | ⬜ xfail→T24 |
| M13 | 前端匹配「差异区间…无需核对」文案 | 结构化 verdict 字段渲染 | ✅ 绿（T2） |
| M14 | — | 单 low 一票不定插（原生绿） | ✅ 绿 |
| M15 | 酶切位点无待定提示 | 待 T25 | ⬜ xfail→T25 |

**汇总**：34 项测试 20 绿 / 14 xfail（+M8 锚定 T24.1）。P0 范围内应绿的场景全部绿。

### 全量测试数字

- 后端：**1907 passed / 47 skipped / 14 xfailed**（T0 基线 1850/43/33 xfail→净增
  57 通过；xfail 33→14，19 项转绿）
- 前端：**vitest 690 passed（89 文件）**；`vue-tsc --noEmit` 0 error
- 契约护栏：`test_sanger_verdict_contract` 四方一致（§3 数据结构 ↔ verdict.py ↔
  前端 TS 类型 ↔ 契约测试），新增 partial 行

### 门禁结果（CI 本地预演）

| 门禁 | 结果 |
|---|---|
| `scripts/check_file_size.py` | ✅ 215 个源码文件，白名单 10 个（pipeline 2863 顶格、routes 958 顶格，homopolymer 拆分后 656 回默认） |
| `scripts/check_docs_sync.py` | ✅（最新 tag v2.37.0-alpha.1） |
| `npx vitest run` | ✅ 690 passed |
| `npx vue-tsc --noEmit` | ✅ 0 error |

### MX 回归差异（私有真实数据，纪律：先取证再改断言）

快照链 `work/baseline_before_T0.json → after_T1…after_T8、after_T10`（探针
`work/mx_probe_t5.py`，MX 批次全部质粒 × 全部 read，字段含结论/双峰/变体/裁决行/
CDS 报告/verdict/coverage）：

- **T1-T7、T9：共享维零漂移**——逐字段 diff 仅新增维（verdict/coverage/
  unaligned_hq_segments/mixed_variants 等），既有结论、待核数、变体列表不变。
- **T8：设计内变化 1 处**——混合互检改用与变异互检同一 `_region_ok` 标准（±25bp
  区域质量门）后，旧口径过宽的 clean 判定收紧：seqF「9 处中 8 处…」（5709、
  5735-5741 两段不再互认 clean）、pET-Up「10 处中 9 处…」（5075-5079、5082-5085）。
  结论保持 △ 待核不变，仅待核行文案更精确；已按纪律更新
  `test_real_data_regression.py` 断言并在提交 2edf6cf 记录取证。
- **T10：零漂移**——`after_T10.json` 与 `after_T8.json` 全质粒全字段一致（decompose
  仅在 tracy 可用且判混合时触发；本机无 tracy，走降级路径）。
- 快照时序说明：after_T9.json 抓取于 T6-T8 之前（执行序 T5→T9→T6→T7→T8），仅作
  T9 当时验证；T10 的正确基线是 after_T8。

### 性能数据

- 全量 pytest 129 s（T0 基线约 105 s；新增 57+ 测试与合成器，无异常劣化）
- MX 探针全批次分析耗时与 T0 同量级（分钟内完成，无正式基准；性能基准随 T23）

### 提交清单（T0-T10，一任务至少一提交）

| 提交 | 任务 |
|---|---|
| 0194703 | T0 复核表 + 审计 A 原文/goal 入库 |
| e5e7d3c | T0 合成器 make_trace_ab1 + MX 取证脚本 |
| 609645a | T0 场景库 35 项 xfail 红测 |
| b6e47ba | T1 证据三态与 ✓ 资格门 |
| 84e54c2 | T2 结构化 verdict 统一三出口 |
| c5364d8 | T3 measured/verified 双覆盖口径 |
| e536bfd | T4 解析失败 read 进结论 |
| 9879026 | T5 ROI 覆盖分级、关键元件与接合区判读 |
| b075564 | 前端 v-for key 修复（T5 CI 预演发现） |
| 99bd50e | T9 ReadRefMap 统一坐标映射 |
| 04d36eb | T6 端部软剪切高质量段进判定 |
| aa60717 | T7 单点双峰次等位 MixedVariant |
| 2edf6cf | T8 混合互检与变异互检同一标准 |
| b3a5709 | T10 tracy decompose 产物解析与等位判读 |
| fb1c05a | 预算拆分重构（crosscheck/readmap 独立模块） |

### 口径偏差记录（对照 goal 原文，均已写入对应提交说明）

1. **T3 verified 切换范围**：单 read 首尾不可信区使 verified<100% 在 REF_MX（123bp
   实测）几乎恒真，全量切 verified 会令对照组必红。P0 期 verified 作为新口径输出
   （coverage.verified_percent），结论/CDS full/批量闸门仍用 measured 并按 T5 分级
   兜底；**语义切换随 T12 复评**，T3 场景（S8/S13）已绿。
2. **T5 partial 判据**：按 ROI **measured** 覆盖分级（0%→insufficient、<99%→partial），
   优于 goal 的纯 verified 口径（同偏差 1 的过渡设计）；ROI 取 CDS 并集外包框
   （cds_union），多分散 CDS 偏保守，T12 复评。
3. **T6 对照组两用例口径升级**：错质粒 H1 污染 read 的高 Q 端部剪切段按新口径判
   不合格/错误——属 A 2.1 修复的设计内变化，回归核心「不进 正确/」不变，
   `test_batch_sequencing.py` 断言随提交 04d36eb 更新。
4. **T7 混合升级范围收窄**：mixed_variant 只在功能区（CDS∪关键元件∪接合区）升级
   blocking；骨架/无注释参考的散在双峰不升级「疑似混合」，经 unresolved_sites 走
   △待定（2026-11-06 用户定稿：个别双峰位点不升级）。S16 断言同步修正作者笔误
   （140→构造值 150）。
5. **T8 MX 待核基线**：互检收紧后 MX 基线断言变化（上文），逐条已解释。
6. **T10 挂账**：本机无 tracy，产物格式按官方文档（v0.9.1）解析（JSON 优先 →
   .align1/.align2），fixtures 按文档格式构造；**生产镜像内实测 tracy --version 与
   真实运行产物格式**记入 KNOWN_ISSUES 挂账。

### 遗留问题（进 P1）

- P1 范围内 xfail 14 项由 T11-T18/T24/T25 依次移除（对应表见上）。
- T3 的 verified 语义切换、T5 的 ROI 外包框精确化 → T12 复评。
- 生产镜像 tracy 实测挂账（KNOWN_ISSUES 未解决 #5）。

## P1 闸门（T11-T18，2026-11-07，截至 03e6dea）

### 闸门判定（goal 原文：S1 为 ✗；F1、F2 不判混合；S2-S17、M2、M4、M5、M11 通过；
### 对照组、全量测试、MX 真实回归绿或差异已解释；评分系数与拟合脚本已提交）

- **S1 为 ✗**：✅ 实证——`_run("S1")` verdict.status=**fail**、blocking=
  [confirmed_variant]；变体 780 confirmed high、p_real=0.9975、action=accept
  （T12 可信区信号驱动逐窗 + T15 校准评分）。
- **F1、F2 不判混合**：✅ 两场景 verdict=pending，blocking 全部为
  unresolved_site（6/7 处），无任何 mixed 升级项（T11 双峰不升级口径 +
  T14 多 read 聚合；F1/F2 场景测试已随 T11 转绿）。
- **S2-S17 通过**：✅ 16 项场景测试全绿。
- **M2、M4、M5、M11 通过**：✅ M2→T14（顺序无关聚合）、M4→T16（ORF 校正
  护栏）、M5→T17（引物方向/距离校验）、M11→T18（预检与 finalize 口径一致）。
  另 M1 意外随 T16 转绿（护栏拦住伪校正掩盖 K2* 的病根）、F1/F2/M7 随 T11
  转绿（M7 的 secondary_base 部分由自适应背景模型覆盖）。
- **对照组**：✅ 全量 pytest 1938 passed 含全部对照组（反向替换高置信、跨原点、
  纯合 ✗、CDS 模式与全质粒模式 MX 一致等）。
- **MX 真实回归**：✅ 共享字段零漂移，3 处探针投影差异已解释（见下）。
- **评分系数与拟合脚本已提交**：✅ `core/sanger/params/variant_score.json` +
  `scripts/fit_variant_score.py`（27440 网格点、锚点等价一致率 1.0000，
  T15 7ad0a7a；`--check` 模式可复验）。
- **SANGER_ALGO_VERSION**：v4（T12）→ v5（T15），写入 result["algo_version"]，
  HANDOVER 登记链完整（v1→v2 T1、v2→v3 T11、v3→v4 T12、v4→v5 T15）。

### 改动文件（fb1c05a → 03e6dea，42 文件 +2927/−758）

- **新模块（全部 ≤900 行）**：`core/sanger/{noise 91, scoring 255, orf_guard 113,
  primer_check 150}.py`；`core/sanger/params/variant_score.json`（评分锚点+权重+
  _doc）；`data/primers/common.yaml`（通用引物库 8 条，注明来源）
- **扩张/收缩**：`evidence.py` 332→541（per-read 证据项/代表 read 择强/覆盖不支
  持口径 + T13 变体峰证据迁入）；`pipeline.py` 2863→2544（T14 合并环聚合、T15
  attach_score 替换 _variant_status、T16 find_guarded_orf 委托、T17 primer 透传，
  白名单持续收缩）；`sequencing_routes.py` 958 顶格（primer 透传 + 注释压缩对冲）
- **接入层**：`sequencing_endpoints.py`（primer 表单字段 + JSON 解析失败 400）、
  `sequencing_session_routes.py`（_precheck 重写）、`verdict.py`（orf_annotation_
  suspect/primer_mismatch 入 _SITE_KIND_MAP 与 PENDING_KINDS）
- **前端**：`useVariantColors.ts`（新，三态色板 confirmed 红/unresolved 琥珀/
  rejected 灰绿，旧记录按 confidence 回退）、`useSeqViz.ts` 962 行（flashRef/
  ensureVisible 抽出 + jumpToVariant 全 evidence_by_read 支持）、`VariantTable.vue`
  （P(真实)/行动徽标列、pendingKey 排序）、`api/sequencing.ts`（evidence_by_read/
  covering_reads_not_supporting/p_real/action 契约）
- **拟合脚本**：`scripts/fit_variant_score.py`（网格枚举 + 校准分桶 + --check）

### 新增/修改测试

- 新：`test_sanger_noise.py`（8）、`test_sanger_scoring.py`（4）、
  `test_sanger_primer_check.py`（5）、orf_guard 三测试（并入 test_sanger_pipeline）
- 增：`test_real_data_regression.py`（T12 重基线断言：zone 975/965/940、counts
  [9,18,19]、全 noise low_site_q、seqF 待核行消失取证、T7-Term 新增待核）；
  `test_batch_sequencing.py`（信噪比 96.0）；`test_sequencing_upload_sessions.py`
  （T18 fixture 占位 size 对齐真实下限）；`sanger_scenarios.py`（_s8 真实 ORF 窗口）
- xfail 注册表：P0 末 14 项 → 现 **5 项**（F3→T24、M3→T21、M9→T25、M12→T24、
  M15→T25）；S1/F1/F2/M1/M2/M4/M5/M7/M11 九项转绿

### §4 场景前后对比（旧 = P0 闸门时点；新 = 当前 HEAD 实际输出）

| ID | P0 末输出 | P1 末输出 | 转绿任务 |
|---|---|---|---|
| S1 | △ pending（可信区先验 low） | ✗ fail（confirmed_variant，p_real=0.9975） | T12 |
| F1/F2 | ✗ 混合升级 | △ pending 仅 unresolved_site（不判混合） | T11 |
| M1 | 伪校正掩盖 K2* | ✗ 确证（护栏拦伪校正） | T16（意外） |
| M2 | 依赖 read 顺序 | 顺序无关（多 read 聚合+代表择强） | T14 |
| M4 | CDS 边界随标错框 | ±3bp 护栏 + suspect 待核 | T16 |
| M5 | 引物错配无信号 | primer_mismatch（3′ 端 220bp>150） | T17 |
| M7 | 次级峰无依据 | secondary_base（自适应背景模型） | T11 |
| M11 | 3 字节 .gb 可建会话 | 建会话即 400（大小下限预检） | T18 |

### 全量测试数字

- 后端：**1938 passed / 47 skipped / 5 xfailed**（P0 末 1907/47/14 → 净增 31
  通过；xfail 14→5，9 项转绿）
- 前端：**vitest 690 passed（89 文件）**；`vue-tsc --noEmit` 0 error
- 拟合复验：`scripts/fit_variant_score.py --check` 网格 27440 点一致率 1.0000

### 门禁结果（CI 本地预演）

| 门禁 | 结果 |
|---|---|
| `scripts/check_file_size.py` | ✅ 222 个源码文件，白名单 10 个（pipeline 2544、routes 958 顶格） |
| `scripts/check_docs_sync.py` | ✅（最新 tag v2.37.0-alpha.1） |
| `npx vitest run` | ✅ 690 passed |
| `npx vue-tsc --noEmit` | ✅ 0 error |

### MX 回归差异（私有真实数据，纪律：先取证再改断言）

快照链 `after_T12 → after_T14 → after_T15 → after_T18`（work/，gitignore）：

- **工具口径说明**：after_T12/T14/T15 为当时 ad-hoc 紧凑探针（结论/变体/CDS
  判读文本，无 verdict/coverage 键）；after_T18 起恢复全字段探针
  `work/mx_probe_t5.py`。两代探针字段集不同，对比一律以**共享字段**为准。
- **T13/T14/T15**：conclusion 逐字节一致（当时取证）；T13 3730.ab1 T 通道
  SNR 由 None 达成非 None（本底确定化），MX 结论不受影响。
- **T16-T18（after_T15 vs after_T18 共享字段全对比）**：conclusion、variants
  （17 共享键 × 4 变体）、cds_reports（8 共享键 × 5 CDS）、consensus_len/
  consensus_diffs/coverage_percent/resolved_regions/mixed_corroboration/
  ref_len/n_features/errors **全部零漂移**。
- 3 处探针投影差异已解释（`git diff 7ad0a7a..HEAD` 未触碰相关产物路径）：
  ① reads[].direction——全字段探针读顶层键（当前为 None，方向在 alignment 内），
  紧凑探针读 alignment.direction（'+'/'-'）；② mixed_detail——紧凑探针取
  含 mixed_z 的明细字典，全字段探针取 positions 整数列表（与 mixed_positions
  同源）；③ homopolymer_runs——全字段探针只留 tier=="poly"，紧凑探针含
  observed 档（6884-6901 caller_only 仍在，仅投影过滤不同）。
- 每任务全量 `test_real_data_regression.py`（T12 重基线断言）绿。

### 提交清单（T11-T18，一任务至少一提交）

| 提交 | 任务 |
|---|---|
| 991ef59 | T11 双峰自适应背景模型与证据形态学分级 |
| 08d6a7c | T12 可信区信号驱动逐窗判定（S1 ✗ 确证） |
| d8d4513 | T13 局部噪声本底与 SNR 确定化 |
| aae3414 | T14 多 read 证据聚合与代表 read 择强 |
| 7ad0a7a | T15 校准 P(真实) 四档行动与峰图三色（v5） |
| 6c3bc03 | T16 ORF 校正护栏与宿主起始密码子 |
| 095aaae | T17 引物库与方向校验 |
| 03e6dea | T18 会话预检与 finalize 口径一致 |

### 口径偏差记录（对照 goal 原文，均已写入对应提交说明）

1. **T12 MX 重基线**：可信区从位置先验改为信号驱动后 zone 延伸（975/965/940）、
   变体计数 [9,18,19]、待核行文案变化——逐条取证并更新
   test_real_data_regression.py 断言（08d6a7c 提交说明）。
2. **T15 评分外皮等价性**：hard tier 保留原 if 树逐位迁移，logistic 锚点层内
   修正不跨层 → status/confidence 与旧规则逐位等价（网格 1.0000 验证），
   非口径变化；action 四档为新增展示维。
3. **T16 M1 意外转绿**：护栏拦住「自由挑最长 ORF 掩盖 K2*」的病根——M1 的
   goal 归属 T25（MNV 合并展示）不变，场景测试注释已记录此语义。
4. **T17 引物校验默认 fail**：mismatch → fail（fail_impact），goal 允许 pending
   可配置；收窄时机待 T22 对接设计记录（goal 原文同此表述）。
5. **T18 会话测试 fixture**：占位 size（10 字节 .ab1 / 3 字节 .fasta）在
   T18 语义下本就应拒——测试意图是「上限检查」与「excel 校验」，fixture 对齐
   真实下限（200/30 字节），非放宽断言。

### 遗留问题（进 P2）

- xfail 5 项：F3/M12→T24、M3→T21、M9/M15→T25。
- T24 PLOC −1 口径（M8 无独立合成用例，复核表锚定真实 3730 附录 A-PLOC）。
- 生产镜像 tracy 实测挂账（KNOWN_ISSUES 未解决 #5，P0 遗留）。

---

## P2 闸门（T19-T25，2026-11-07，截至 9d0d948）

### 闸门判定（goal 原文：§4 全部场景无 xfail 通过；基准测试在 CI 运行且达标；
### 对照组、全量测试、MX 真实回归绿）

- **§4 全部场景无 xfail**：✅ 实证——`test_sanger_scenarios.py` **36/36 通过、
  0 xfail**（XFAIL_BY_TASK 注册表清空并留档各场景转绿任务号）；全量
  **1965 passed / 46 skipped / 0 xfailed**。T0 注册的 4 项红测全部转绿：
  F3/M12→T24（峰型估 Q / PLOC 截短告警）、M9→T25.2（circular 必填关键字）、
  M15→T25.4（酶位 ±10bp 未决提示）；M1 的 MNV 联合注释随 T25.1 落地
  （两成员均报 AAA>TAG / GeneX:K2*，旧「104 单边 TAA 巧合终止」已消除）。
- **基准测试在 CI 运行且达标**：✅ `test_sanger_benchmark.py` 在全量内运行
  （不 skip）：verdict 混淆矩阵 **33/33 对角**（T24 后 fail 12 / failed_reads 1 /
  mixed 4 / partial 1 / pass 5 / pending 10）；位点敏感度 1.0（TP14/FN0）、
  特异度 1.0（TN29378/FP0）≥ 阈值 0.95/0.99；混合 TPR 1.0、**FPR 0.0**
  （硬约束）；truth.yaml 契约自洽断言（confirmed≥0.5 / unresolved<0.5）绿。
  指标与拟合报告见 docs/SANGER_CALIBRATION.md（T24 后实测同步）。
- **对照组**：✅ 全量 1965 passed（对照场景：反向替换高置信、跨原点折回、
  纯合 ✗、CDS/全质粒模式一致、Excel/分拣契约等全部在内）。
- **全量测试**：✅ 1965 passed / 46 skipped / 0 xfailed（skip 构成：42 deploy
  需 bash/sh + 1 snapgene-reader ImportError 分支不可触发 = T0 基线 43；
  +3 为契约测试对 M5/M8/M11「不经 analyze」的显式登记，goal §4 允许，
  test_sanger_scenarios.py 自身 0 skip 0 xfail）。
- **MX 真实回归**：✅ work/after_T25.json 对 after_T24.json 逐字段 diff：仅
  2 处——①跨原点缺口「2 段（1-5011、6932-7039）」合并为「1 段（6932-7039、
  1-5011，5119bp）」：T25.2 探针改用参考真实拓扑（GenBank 环状）后，环上
  连续的跨原点缺口正确并段；read 不跨原点时环状与线性比对等价，生产
  batch 路径本就走 reference_circular（探针此前 linear 是偏离生产）；②
  algo_version v6→v7。核心判定（2 确证差异、CDS 不一致、mixed 计数、
  待核行）逐字节一致。
- **前端门禁**：✅ `vue-tsc --noEmit` 0 error、`vitest run` 90 文件 / 693 tests
  全绿（T22 证据包面板 + T24/T25 类型同步后复验）。
- **尺寸/文档门禁**：✅ check_file_size（白名单 10 个不超预算，
  sequencing_routes.py 用行合并对冲 T24 新增字段）、check_docs_sync 绿。
- **SANGER_ALGO_VERSION**：v5（T15）→ v6（T24 PLOC 口径）→ v7（T25 MNV
  联合注释），写入 result["algo_version"]，HANDOVER 登记链完整。

### 改动文件（03e6dea → 9d0d948，42 文件 +2804/−213）

- **新模块（全部 ≤900 行）**：`core/sanger/{split_read 68, artifacts 179,
  overlap 154, evidence_pack 241}.py`；`scripts/{sanger_benchmark,
  fit_mixed_params}.py`（拟合脚本，不改自动参数）；`tests/data/sanger_bench/
  truth.yaml`（35 场景真值 + 3 真实 AB1 + 阈值）
- **扩张**：`pipeline.py`（T19 split-read 接线、T20 伪迹、T21 分歧/IUPAC、
  T24 解析层质量来源/warnings、T25 端插加固 + circular 必填）；`annotator.py`
  （T25.1 MNV 聚合、T25.4 enzyme_sites_pending）；`abif_reader.py`（T24.1
  PLOC 原值 + 峰型估 Q + 通道采样跳首端）；`verdict.py`/`evidence.py`（T19/
  T20/T21 判定接入）；`sequencing_endpoints.py`（T22 证据包端点、T25.2
  设计流拓扑读载体记录）；`sequencing_session_routes.py`（T18 预检）
- **前端**：`sequencing.ts`（evidence-pack/quality_source/warnings/mnv/
  enzyme_sites_pending 类型）、`EvidencePackPanel.vue`（新）、`VariantTable.vue`
  （unresolved/mixed 行 + MNV 徽标 + 酶位待定徽标）、`SequencingPanel.vue`
  （证据包跳转）
- **测试**：新增 `test_sanger_{split_read, artifacts, overlap, evidence_pack,
  benchmark}.py` + `test_real_data_regression` 重基线；场景 36 项全绿

### 提交清单（T19-T25，一任务至少一提交）

| 提交 | 任务 |
|---|---|
| 3b4cad8 | T19 split-read 端部剪切段二次定位 |
| 6f4c54b | T20 通用伪迹检测——压缩/dye blob/短滑移 |
| e0d844d | T21 重叠区分歧与双链覆盖 |
| 9a59126 | T22 证据包与多 read 同屏 |
| f51dd3d | T23 基准集与 ROC 回归、T11/T15 真拟合 |
| 6dd42b4 | T24 解析层：PLOC 峰心原值与无 PCON 峰型估 Q |
| 9d0d948 | T25 判读注释层：MNV 联合注释/拓扑必填/酶位待定提示 |

### 口径偏差记录（对照 goal 原文，均已写入对应提交说明）

1. **T24.1 PLOC 基准**：实测 3730/3100/310 原值即峰心（减 1 系统性偏左），
   解析端取 0 基原值；tracy basecallPos 同口径统一但本地无 tracy 无法实测，
   基准核对并入 KNOWN_ISSUES 挂账 #5。
2. **T24 真实数据基线更新**：峰心对称窗下滞后主峰的染料 blob 肩峰占比
   实测下降（0.62→0.45 等），贴 0.30 地板条目不再检出——F1/F2/M12
   pending→pass、MX 混合计数 9/18/19→8/16/17、待核 6873 掉档；
   truth.yaml/test_real_data_regression.py 断言逐条随实测更新（6dd42b4）。
3. **T24 M12 边界伪位点消失**：旧 pending 由错位窗（12i−1）边界伪双峰
   @92 驱动，修正后不再产生；M12 场景章程（PLOC<PBAS 告警）由
   res["warnings"] 承接。
4. **T25.2 拓扑显式化**：circular 为必填关键字是核心 API 契约变更——
   测试/脚本 107 处调用点 codemod 显式传参；M9 直接调用保持无参断言
   TypeError；设计流启发式（insert_start != 1）改为读载体记录 topology，
   载体记录缺失时保留旧启发式兜底（线性骨架载体语义更正确）。
5. **T25.3 end_insert_votes 静态加固**：idx==L 路径经局部比对不可达
   （B P2-16），skip_keys 同口径/低票不定插/单票 unresolved 按规格落地，
   现无场景可触发（M14 走主循环路径，保持绿）。
6. **T25.4 酶位提示范围**：核心层无「用户计划酶位」入参，按参考上的
   酶识别位点（find_enzyme_sites，与 enzyme_sites_lost 同源）±10bp 判定；
   设计流用户的克隆酶提示可后续在端点层叠加。

### 遗留问题（进最终验收）

- 生产镜像 tracy 实测（decompose 产物格式 + basecallPos 基准）挂账
  KNOWN_ISSUES #5——goal §7 停止条件不触发（本机无 tracy 属预期环境）。
- MX 真实数据无设计参考 → 位点级真值不可用（KNOWN_ISSUES #6 阈值
  标定口径）；带设计参考的真实数据扩充项留档。
- 46 skip 中 42 项 deploy 环境性 skip（需 bash/sh）为 Windows 本机常态，
  CI Linux 环境实跑。
