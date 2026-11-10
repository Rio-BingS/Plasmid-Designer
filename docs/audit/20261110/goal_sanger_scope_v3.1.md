# GOAL：Sanger 范围报告 v3.1 修复（承接 b39b46a 复审，X0–X10）

> **基线**：`experiment/preview-visual-tuning` @ `b39b46a`。在其上新开分支 `fix/sanger-scope-v3.1`。
> **依据**：`docs/audit/REVIEW_b39b46a_2026-11-10.md`，下文记作 `RB`。行号只作线索，一律以函数名定位。
> **复现探针**：`docs/audit/probe_v3.py`。X0 把它改写成 pytest 场景。

---

## 0. 目标

上一轮 W0–W16 已经落地。本轮只修 RB 中的三个 P0 和 P1/P2，**不扩展新功能**。完成后应满足：

1. **不误判混合**：真实 read 尾端峰展宽、分辨率下降时，判「测序未达」，不判「疑似混合」，也不建议重挑克隆。
2. **复核量可控**：真实子集（MUTPK + S99676，5 条 read）单样品复核队列 ≤ 15 项，结论 ≤ 6 行。
3. **出口全文同源**：网页结论首行、Excel 结论前缀、前端卡片、`verdict.headline` 四者**逐字一致**；不存在自相矛盾的文案；声明范围时不得主张「构建与设计一致」。
4. **变异表示正确**：微同源边界上的 1bp 插入仍报为 insertion，c./p. 后果正确。
5. **上一轮被改的场景恢复原意**：SC6、SC10、CM3 回到原构造或原断言，并且通过。

---

## 1. 开工前必读

1. `AGENTS.md`、`docs/HANDOVER.md`、`docs/KNOWN_ISSUES.md`（防回潮约束）。
2. `docs/audit/REVIEW_b39b46a_2026-11-10.md`（本轮依据）、`docs/audit/PROGRESS_v3.md`（上一轮记录）。
3. 主要代码：
   - 混合与峰形：`core/sanger/{mixed_call,mixed_model,signal,reliable_zone,verdict}.py`
   - 复核：`core/sanger/review.py`
   - 出口：`core/sanger/{evidence,batch,verdict}.py`、`components/sequencing/ConclusionCards.vue`
   - 变异：`core/sanger/{aligner,annotator,pipeline}.py`
   - 范围记忆：`app/sequencing_store.py`、`app/routes/sequencing_endpoints.py`、`app/database/models.py`、`alembic/`
   - 旧路径：`core/sanger/cds_extract.py` 中的 `resolve_reference`

---

## 2. 全局硬约束

- **真值先行且不可改**：
  - X0 写定 §4 的全部场景真值，单独提交；
  - 恢复 SC6、SC10、CM3 的原构造或原断言，同样在 X0 完成；
  - 之后**任何为了让测试通过而修改构造或断言的行为都禁止**；确需修改时，停下汇报。
  - 自检方法：`git log -p` 中，X0 之后的提交不得出现 `tests/sanger_scenarios.py` 的改动。
- **API 契约**：`analyze()` 只允许新增可选参数；不改已有字段语义，只新增字段；改返回结构须同步 `api/sequencing.ts` 和 `_summary` 白名单。
- **数据库**：只做增量迁移，必须走 alembic。不得在运行时用 `__table__.create` 建表。
- **判定不依赖文案**：禁止用字符串匹配做逻辑。
- **依赖**：不新增 Python 运行时依赖，也不新增前端依赖。
- **尺寸预算**：白名单文件只许变小，新文件不超过 900 行。
- **口径版本**：改变结论口径的提交，递增 `SANGER_ALGO_VERSION`（当前 v14）。
- **对照组不得变红**：
  - 上一轮全部 72 个场景（SC、CM、AF、PR、OC 以及 S/F/M 系列）；
  - AF1：CDS 内单 read AF 0.12/0.15/0.18/0.20 至少 pending；
  - AF2；
  - 真实混合 TPR 1.0；
  - 3 个公开真实 AB1 的 FPR 为 0；
  - MX 回归的确证差异数（2）与 status（fail）不变。
- **双平台测试数字**：每次汇报都给出 Linux 和 Windows 的数字。

---

## 3. 设计要点

### 3.1 峰形门控（X1）

**新增 `core/sanger/peak_shape.py`**：
- 每条 read 计算逐碱基的局部峰宽（主通道半高宽，滑窗中位数）和相邻峰重叠度。
- 由此得到「峰形退化区段」：局部峰宽 > 全 read 中位数 × 1.6，或重叠度超过阈值，连续 ≥ 15bp。
- 阈值放进 `params/`，并写明来源。

**退化区段的处理**：
- 区段内的双峰**不进入** `mixed_positions`、widespread 分级、af 分带（mixed_variant / minor_af_site / 软带）和互检分母；
- 区段计入可信区外，与 Q 衰减同等对待，使 verified 收缩；
- 声明范围内的缺口，由 W6 的逻辑自然归为 `read_shortfall`。

**mixed 判定的资格**：位点所在区段峰形正常，并且次峰是独立峰（次通道在主峰心 ±1 采样点内有局部极大值，而不是主峰拖尾的肩）。

**read 级标记**：每条 read 写入 `reads[i].degraded_ranges`。前端在峰图上灰显这些区段；evidence 对这条 read 给一句话：「GX-F 自 760 起峰形退化，已按测序未达处理」。

### 3.2 复核队列聚合（X2）

`review.build_queue` 改为按以下三级组织：

1. **read 级**：read 已判 mixed、整体低质量，或退化区段占其范围内 ≥ 30% 时，生成一项「read 问题」。这条 read 上的位点项全部折叠到这一项下，不再单独入队。
2. **区段级**：其余位点中，同类且相邻 ≤ 20bp 的合并为一项，显示区间、位点数、最大 AF 和最低 Q。
3. **位点级**：只保留确证差异和范围内的孤立未决位点。

其他规则：
- **未声明范围**：`unresolved_site`、`minor_af_site` 只计数，不入队；info 中给出汇总一行。
- **排序**：confirmed > junction > read 问题 > 区段 > 位点。
- 前端每个样品默认展开前 10 项，其余折叠为「其余 N 项」。
- **结论文本**：网页结论只写头行加最多 5 行要点，明细全部放进复核队列和明细表。

### 3.3 出口全文同源（X3）

- `verdict.headline` 是唯一的头行来源，格式为「符号 · 范围 · 证据」。
- `evidence.py` 的结论首行等于 headline。附加说明另起行，只能来自 `verdict` 的结构化字段，不得重新判断状态或区间。
- `batch.excel_conclusion` 的结论列等于去掉符号后的 headline。明细列（缺口原因、范围外差异、补测建议）从结构化字段填写。
- 判断「是否在范围内」一律用 `scope.merged_ranges()`（ranges ∪ junctions）。
- 区间统一使用 verified 区间，measured 只在明细列出现。
- **声明范围 pass 时的措辞**：「合格 · 范围：GeneX（1001–1600）+ 接合区 · 一致」。禁止「构建与设计一致」「与设计一致」这类不带范围的整体主张。
- **证据计数按位点去重**：同一位点的 confirmed_variant、junction_variant、移码项合计 1 处。
- **跨原点范围显示**：带名称，写成一段，例如「Cross（3701–300，跨原点）」。多段非跨原点时写「名称（a–b、c–d）」。

### 3.4 indel 规范化（X4）

在变异层统一做 indel 规范化：
- 按 HGVS 3′ 规则右移，用于 c. 表示；质粒坐标用左对齐形式，并在 `coords` 中记录两者；
- 规范化前，若「1bp gap + 邻位替换」可以等价改写为「1bp 插入 + 0 替换」且 read 证据支持，优先采用 indel 表示（替换得分与 gap 得分打平时，不得归一成替换）；
- 后果注释基于规范化后的结果。

### 3.5 范围记忆（X5）

- 存储键改为「用户 ID + 参考哈希」。匿名或单用户部署时，键为参考哈希 + 固定 owner。
- 只记忆用户**显式声明**的范围，不记 cds_target、filename_match 等自动来源。
- 套用记忆时：
  - `scope.source` 保持原来源，`scope.reason="沿用上次范围"`；
  - 新增 `scope.remembered=true`；
  - 前端范围条显示「沿用上次范围 · 清除」；
  - headline 末尾标注「（沿用上次范围）」。
- 批量路径不套用记忆。
- 新增端点 `DELETE /sequencing/scope-memory`（参数：参考哈希），用于清除记忆。
- 建表改为 alembic 增量迁移，删除运行时 `__table__.create`。

---

## 4. 场景基准（X0 写定，加入 `tests/sanger_scenarios.py`）

| ID | 构造 | 期望 |
|---|---|---|
| SC6（恢复原构造） | scope=GeneX，`make_trace_ab1(ref[200:1101], q=50, tail_width=(400, 3.0))` | partial；缺口 cause=read_shortfall；不是 mixed；进入复核队列；read 带 `degraded_ranges` |
| SC6b | 同 SC6，tail_width 倍数取 2.0、2.5、3.0；再加一组不声明范围 | 四组都不判 mixed；blocking 中无 mixed_variant、minor_af_site；未声明组 status 为 pass，headline 不含「混合」 |
| SC6c | 同 SC6b 的 3.0 档，但在退化区段之前（read 坐标 150 处）叠加一个真实次峰（AF 0.35） | 判 mixed（门控不能误杀退化区段之外的真实混合） |
| SC10（恢复原构造） | `make_trace_ab1(ref[240:1320], q=50, tail_width=(1050, 3.0))` | pending；有 roi_unverified；不是 mixed；pass 与 blocking 不并存 |
| CM3（恢复原断言） | 上一轮的变体质粒构造保留，断言恢复为两处 `type == "insertion"` | 两处插入都检出；有 junction_variant；判 fail |
| CM9 | CDS 内部 1bp 插入，插入碱基与左邻或右邻相同（微同源），分别取 3 个位置 | type 为 insertion；c. 符合 3′ 规则；后果为移码 |
| RQ1 | 仓库真实子集（MUTPK + 5 条 read），不声明范围 | 复核队列 ≤ 15 项；结论 ≤ 6 行；3 处确证变异（335 ins、440 ins、4081 del）全部在队列中；status 为 fail |
| RQ2 | RQ1 声明全质粒范围 | 复核队列 ≤ 25 项；3 处确证变异全部在队列中 |
| RQ3 | 合成数据：单 read 判 mixed，且有 40 个双峰位点 | 队列中只有 1 项「read 问题」，位点全部折叠在下面 |
| OC3 | 对 CM1、CM3、跨原点干净样品、SC3、SC5、SC6、移码样品，以及 RQ1 | 网页首行等于 headline；Excel 结论等于去掉符号的 headline；前端卡片显示 headline；全文逐字比较 |
| OC4 | 跨原点 ATG 前插入 | Excel 不得同时出现「不合格」和「与设计一致」；不得出现「范围外 1 处差异」 |
| OC5 | 声明范围 pass（CM1 干净样品） | 任何出口都不得出现「构建与设计一致」或不带范围的「与设计一致」 |
| HC1 | 一处接合区插入（同时触发 confirmed 和 junction） | headline 证据写「1 处确证差异」 |
| HC2 | 跨原点 CDS Cross | headline 范围写「Cross（3701–300，跨原点）」 |
| SM1 | 用户 A 以 scope=GeneX 分析质粒 P；用户 B 不声明范围分析 P | B 不套用 A 的范围 |
| SM2 | 同一用户先以 GeneX 分析，再以 cds_mode 选择 AmpR 分析 | 第二次不套用记忆；cds_target 为 AmpR |
| SM3 | 记忆生效时 | headline 带「（沿用上次范围）」；`scope.remembered=true`；清除后再分析，回到未声明口径 |

**另外修正**：`test_real_ab1_subset.py` 的头注释写「无 mixed 判定」，与实际输出不符。X1 完成后重新运行，按实际结果写真值（依据门控后的结果），并在提交说明中写明。这是唯一允许在 X0 之后修改真值的地方，前提是门控逻辑已落地。

---

## 5. 工作分解

- **X0 真值与复现**
  - 写入 §4 全部场景，未实现的标 xfail；
  - 把 `probe_v3.py` 复制到 `docs/audit/`；
  - 单独提交；
  - 保存 MX 和真实子集的旧口径输出。
- **X1 峰形门控**（RB P0-1）：按 §3.1 实现。
  - 验收：SC6、SC6b、SC6c、SC10 通过；AF1、AF2 和真实混合 TPR/FPR 不变。
  - 改动后复跑 3 个公开 AB1 和 MX，记录 mixed_variant 与 minor_af_site 的计数变化，逐条解释。
- **X2 复核队列聚合**（RB P0-2）：按 §3.2 实现，覆盖后端和前端。验收：RQ1–RQ3 通过。
- **X3 出口全文同源**（RB P0-3、P1-1、P1-2）：按 §3.3 实现。
  - 验收：OC3–OC5、HC1、HC2 通过；上一轮 OC1、OC2 不变红。
  - 删除 evidence 和 batch 中自行生成头行的分支；没有 verdict 的旧记录走降级分支，降级分支保留旧文案。
- **X4 indel 规范化**（RB P1-4）：按 §3.4 实现。
  - 验收：CM3（恢复断言）和 CM9 通过；S 系列大片段 indel 场景不变红；真实子集 335 ins、440 ins、4081 del 的位置和类型不变（规范化后坐标如有位移，须说明）。
- **X5 范围记忆**（RB P1-3、P2-3）：按 §3.5 实现。验收：SM1–SM3 通过；迁移在 MIGRATIONS.md 登记。
- **X6 清理**（RB P2-1、P2-2）：
  - 删除 `docs/sanger_scope_v3_docs/`；
  - 删除 `cds_extract.resolve_reference`，或在 tests 中加守卫，禁止 `src/` 中出现它的调用；
  - 相关测试改为调用 `prepare_scope_mode`。
- **X7 防回潮与文档**：
  - KNOWN_ISSUES 新增约束：
    - 峰形退化区段内的双峰不得判混；
    - 头行唯一来源为 verdict.headline，出口不得自行生成；
    - 复核队列按 read、区段、位点三级聚合，未声明范围不收位点项；
    - X0 之后不得修改场景构造或断言。
  - 更新 SANGER_ALGORITHM_REPORT §5、CHANGELOG [Unreleased]、HANDOVER（algo_version）。

**顺序**：X0 → X1 → X3 → X2 → X4 → X5 → X6 → X7。
- X3 在 X2 之前，因为队列的展示依赖统一后的 headline 和结构化字段；
- X1 在 X2 之前，因为门控会减少队列项，聚合阈值要在门控之后调整。

---

## 6. 最终验收标准

1. §4 全部场景通过，无 xfail、无 skip；上一轮 72 个场景不变红。
2. X0 之后，`tests/sanger_scenarios.py` 中已有场景没有被修改（仅允许 §4 末尾说明的真实子集注释与真值修正，且在门控落地之后）。
3. 后端全量测试：Linux 和 Windows 都 0 failed，通过数只增不减，skip 不增。前端 `test:run`、`type-check`、`build` 全绿。
4. 门禁 `check_file_size.py`、`check_docs_sync.py` 全绿。
5. MX 回归：确证差异 2 处、status=fail 不变；minor_af_site 与 unresolved_site 的入队数量明显下降，前后数字写进 PROGRESS。
6. 真实子集：复核队列 ≤ 15 项，3 处真实变异全部在队列中。
7. 性能：单样品耗时不超过 b39b46a 的 1.15 倍（峰形计算是新增开销）。
8. `grep -rn "构建与设计一致" src/backend src/frontend/src` 只出现在「无 verdict 旧记录」的降级分支中，或完全不出现。

---

## 7. 执行方式、停止条件与汇报

- **全量执行**：X0–X7 全部完成。每完成一项都写入 `docs/audit/PROGRESS_v3.1.md`，然后继续，不停下等确认。
- **每个任务的流程**：
  1. 对应场景先红；
  2. 实现；
  3. 跑相关测试、全量 pytest（Linux 和 Windows）、前端测试（有前端改动时），再预演门禁；
  4. 改口径时跑 MX 和真实子集对比；
  5. 按 Conventional Commits 提交，提交说明写 RB 条目和场景 ID。
- **立即停下汇报、不得自行绕过**：
  - 需要修改 X0 之后的场景构造或断言；
  - 峰形门控导致 AF1、真实混合 TPR 或公开 AB1 FPR 任一变差；
  - 退化区段检测在真实子集或 MX 上把超过 30% 的 read 范围判为退化（阈值可能过严，附分布数据）；
  - indel 规范化改变了真实子集 3 处变异的类型；
  - 需要非增量迁移、新依赖，或修改 `analyze()` 必填参数；
  - 任何对照组必须变红才能让目标场景变绿。
- **最终汇报**：
  - X0–X7 完成情况；
  - §4 场景前后对比表（旧输出 / 新输出）；
  - Linux 和 Windows 的测试数字；
  - MX 和真实子集的队列计数前后对比；
  - 峰形阈值来源与分布；
  - 性能数据；
  - 刻意未做的事项及原因。
