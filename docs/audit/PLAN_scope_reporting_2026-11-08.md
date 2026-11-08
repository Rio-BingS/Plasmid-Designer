# Plasmid-Designer 测序范围与报告：综合优化方案

- 基线：`experiment/preview-visual-tuning` @ `24d4d2e`（v2.37.0-alpha.2）
- 依据：复审 `REVIEW_24d4d2e`、目的基因模式审核 `REVIEW_cds_mode`、部分测序与范围讨论（2026-11-08）

---

## 0. 一句话目标

用户可以自由选择「需要测哪一段」。系统对整条质粒只分析一次，然后报告三件事：这段测了没有、测得对不对、没测到的原因。人只需要看系统确实判断不了的位点。

## 1. 核心模型：一次分析，三个范围，判定用纯函数

```
analyze(全质粒、真实拓扑)  →  result（变异 / 未决位点 / V / 每条 read 的 E / 元件覆盖）
build_verdict(result, L, scope)  →  verdict（纯函数，毫秒级，可随时重算）
```

| 符号 | 含义 | 来源 |
|---|---|---|
| **R** | 需测范围（scope），可多段，可要求双链 | 用户、设计记录、目的基因、文件名、引物 |
| **E** | 引物理论覆盖 | 每条 read 的比对起点和方向，加上 `tail_gray`；有引物序列时用 `locate_binding` 精确定位 |
| **V** | 实际验证覆盖 | `coverage.verified_ranges` |

判定口径：

| 区域 | 结论 | 动作 |
|---|---|---|
| R ⊆ V，且无问题 | ✓ 一致 | 无 |
| R − E | △「引物未覆盖」 | 加引物或移动引物，样品本身没问题 |
| (R ∩ E) − V | △「测序未达」 | 重测或看峰图，**进复核队列** |
| V − R | 附带结果 | 不影响结论；关键元件里的确证差异升为警示 |
| 未声明 R | 只描述实测区段 | **禁止**输出「构建与设计一致」 |

## 2. 现有问题与本方案的对应

| 问题 | 来源 | 本方案中的解决方式 |
|---|---|---|
| 整质粒模式部分覆盖仍判 ✓（whole 豁免） | 复审 P0-1 | 去掉 whole 豁免；未声明 R 时用「已测区段」口径 |
| pass 与 blocking 矛盾 | 复审 P0-2 | 契约：pass ⇒ blocking 为空，verified 口径统一 |
| CDS 内 AF 0.12–0.20 静默 ✓ | 复审 P0-3 | 单独修复（见 §3-G） |
| ROI = 所有 CDS 的外包框，只测插入片段被判待定 | 部分测序分析 | 改为多段 scope，去掉外包框 |
| 目的基因模式：read 超出 ±150bp 判 ✗，跨原点漏检插入 | 目的基因审核 P0-A/B | 不再裁参考，目的基因改为 scope 预设 |
| 局部坐标对不回图谱，三个出口说法不一 | 目的基因审核 P1-D/E | 质粒坐标 + c./p. 双坐标；后端统一生成 headline |
| 引物校验只接受一条引物 | 引物覆盖讨论 | 逐条 read 对应引物 |
| 缺口不区分原因 | 引物覆盖讨论 | 缺口按 R−E 与 (R∩E)−V 归因 |

## 3. 优化项（按模块）

### A. 判定内核：`core/sanger/verdict.py`
1. `build_verdict(result, ref_length, scope=None)` 改为纯函数；`analyze()` 新增可选参数 `scope=None`（只增不改，不破坏现有调用）。
2. 删除 `cds_union` 外包框 ROI 和 `roi_gap` 的 whole 豁免。
3. `roi_uncovered` 拆成两种：
   - `scope_beyond_primer`（action：加引物）
   - `scope_read_shortfall`（action：重测或看峰图）
   E 不可得时退化为 `scope_uncovered`。
4. 新增 `out_of_scope_variant`（info）；范围外关键元件里的确证差异仍走 `critical_element_variant`。
5. 新增 `element_verdicts`：每个 CDS、关键元件、接合区各一条，字段为覆盖、单双链、问题数、是否在范围内。
6. 新增 `headline`（三段式：状态 · 范围 · 证据），作为所有出口唯一的文案来源。
7. 契约断言：`status == "pass"` ⇒ `blocking == []`。
8. 旧字段 `roi` 保留，取 scope 的外包框，兼容历史记录。

### B. 覆盖与引物覆盖：`coverage.py`、`primer_check.py`
1. `coverage` 新增 `expected_ranges`：每条 read 的理论窗口 = 比对起点沿测序方向 `front_gray` 到 `tail_gray`。
2. `_coverage_gaps` 只计算 R 内的缺口，每个缺口带 `cause`：`beyond_primer`、`read_shortfall` 或 `unknown`；还要带上「哪条 read 在哪里衰减」，例如「GX-F 在 760 衰减」。
3. `primer_check.check_reads` 改为逐条 read 对应引物：`{read 文件名: 引物}`。批量信息表的引物列已经提供 read 与引物名的对应关系。
4. 有引物、却没有对应 read 时，报「引物无信号」，属于 `read_shortfall`。

### C. 范围输入：新建 `core/sanger/scope.py`
1. 数据结构 `Scope{ranges, source, declared, require_double_strand, elements, reason}`。
2. 来源优先级：
   - `user`：用户指定
   - `design_insert`：设计记录的插入区 ±30bp，接合区另算
   - `cds_target`：目的基因
   - `filename_match`：复用 `cds_extract.resolve_auto`
   - `primer_window`：E 的并集
   - `measured`：都没有，视为未声明
3. `parse_scope_cell(text, features)`：解析元件名、`301-900`、多段写法、「插入片段」「全质粒」等关键字；解析失败时回填提示，不阻塞其他样品。
4. 扩展 `POST /sequencing/parse-reference`：返回 `scope_presets` 和默认项，默认项附带 reason。
5. 上传端点新增 Form `scope`；设计流把 `insert_start/insert_end` 传下去；批量信息表新增「验证范围」列。
6. 新增端点 `POST /sequencing/results/{id}/verdict {scope}`：从存档重算并落库，记录 `scope_history`。
7. 范围存在参考指纹（序列哈希）下，同一质粒再次测序时自动带出。

### D. 目的基因模式重构：`cds_extract.py` 与 `analyze_sequencing_upload`
1. `cds_mode=True` 时**不再替换参考**，全质粒按真实拓扑比对，`scope = 目标 CDS + 接合区`。
2. 质粒超过 `MAX_REF_BP` 时才裁剪：
   - 侧翼取 `max(1500, 最长 read + 200)`；
   - 环状质粒绕回取窗口；
   - 跨原点 CDS 先把质粒旋转到使 CDS 连续；
   - split-read 用完整质粒定位。
3. 自动匹配：参考名与 read 名冲突时判歧义；显式选择允许选嵌套特征；重名时报错。
4. CDS 内坐标只做显示变换（`resolved_regions` 的 offset 不再参与判定）。
5. 批量支持目的基因：即 `filename_match` 来源。

### E. 输出：`evidence.py`、`batch.py`、前端
1. 网页头行、Excel 结论、`ConclusionCards` 三处都只读 `verdict.headline` 和 `status`，前端不再拼字符串（顺带解决复审中「前端按字符串分组」的问题）。
2. 结论下方第一屏是元件状态表：范围内的元件逐行列出，范围外折叠成一行。
3. 变异双坐标：质粒坐标 + c./p.；负链基因标出编码方向。
4. Excel 新增列：验证范围、范围来源、范围内验证覆盖、缺口原因、范围外差异、补测建议。原「测序覆盖 x%」移到明细 sheet。
5. 覆盖图画三层色带：R（范围）、E（理论）、V（实测）。缺口按原因着色。
6. annotator 里「低置信度（疑似伪影）」的旧话术，统一改为「未决」口径。

### F. 少复核
1. 复核队列只放「范围内」且属于以下几类的项：`read_shortfall`、未决位点、确证差异。
   - pass 的样品不需要打开；
   - `beyond_primer` 只给动作建议，不需要看峰图。
2. 每个队列项内嵌证据：位点 ±10bp 的峰图（EvidencePackPanel）、各 read 的调用、Q 值、次峰比例。
3. 一键确认（伪影或突变）后重算 verdict 并留痕（`reviewed_by/at`），结论中标注「人工确认 N 处」。
4. 批量页顶部显示「需复核 X 个样品、Y 个位点」，按 fail > read_shortfall > pending 排序。

### G. 一并处理的复审遗留
1. **混合检测（P0-3）**：`mixed_model` 的 ratio_floor 从 0.30 改为 0.20（与校准文档的并列最优一致）；让 `MINOR_AF_MIN=0.15` 真正生效，或者删掉它。范围内 AF 0.12–0.20 的位点至少落入 pending。
2. **p_real**：不再显示为百分比，改为等级（高、中、低）。在完成真实数据校准之前，不当作概率使用。
3. Linux 下的两个红测试：
   - CHANGELOG 的 compare 链接改指向 alpha.2；
   - `test_filename_safety` 的 Windows 路径白名单按平台归一化。
4. goal §6 的 5 条防回潮约束写入 KNOWN_ISSUES。
5. 基准集：新增场景的真值必须在实现之前写定，不能跟随输出改真值。补一份脱敏的真实 AB1 子集（含真实变异）进仓库，让 CI 真正跑起来。

## 4. 阶段与验收

| 阶段 | 内容 | 验收 |
|---|---|---|
| 1 判定内核 | A1–A8、G1、G3 | P0-1、P0-2、P0-3 回归场景全过；pass ⇒ blocking 为空 |
| 2 引物覆盖与归因 | B1–B4 | 缺口原因场景全过 |
| 3 目的基因重构 | D1–D5 | 目的基因模式与「全质粒 + scope=CDS」的 verdict、headline 逐字段一致 |
| 4 范围输入 | C1–C7 | 预设和默认项正确；改范围不重跑分析，verdict 快照一致 |
| 5 输出统一 | E1–E6 | 三个出口的 status 和符号一致；双坐标正确 |
| 6 少复核 | F1–F4、G2、G4、G5 | 队列只含应复核项；确认后留痕 |

每个阶段结束时写入 PROGRESS 并继续，不停下。**不升版本，不打 tag。** `analyze()` 只新增可选参数；如果需要改动必填参数或返回结构，先停下来询问。

## 5. 场景清单（真值先行）

| ID | 构造 | 期望 |
|---|---|---|
| R1 | 全质粒（GeneX + AmpR 注释），`scope=GeneX`，正反 read 全覆盖 | ✓，头行带范围 |
| R2 | 同上，不声明范围，样品名为 GeneX | `filename_match` → ✓ |
| R3 | 不声明范围，匹配不到 | 「已测区段……其余未评估」，不得出现「构建与设计一致」 |
| R4 | 无 CDS 的参考，3000bp 只测 900bp | 同 R3（P0-1 回归） |
| R5 | `scope` 尾部超出正向 read 的 700bp，且没有反向 read | △，原因为引物未覆盖 |
| R6 | read 在 400bp 衰减 | △，原因为测序未达，进入复核队列 |
| R7 | read 伸到 R 外，R 外骨架有差异 | ✓，附带结果中单列 |
| R8 | R 外的 ori 中有确证差异 | 警示（pending） |
| R9 | 同一份 result 换三种 scope | verdict 与快照一致，不重跑分析 |
| C1 | 目的基因模式 + 900bp 干净 read 超出 CDS | ✓（P0-A 回归） |
| C2 | 跨原点 CDS，ATG 前 / 终止后 1bp 插入 | 检出，判 junction_variant（P0-B 回归） |
| C3 | 近原点 CDS | 与全质粒模式结果一致 |
| C4 | 参考名为 GFP，read 名为 mCherry | 判歧义 |
| C5 | 负链基因替换 | 双坐标正确 |
| M1 | CDS 内 AF 0.15 / 0.18 | 至少 pending（P0-3 回归） |
| P1 | 一对正反引物各自对应自己的 read | 两条引物校验都为 ok |
| P2 | 提供了引物但没有对应的 read | 报「引物无信号」 |
| O1 | 任意 fail / partial 样品 | 网页、Excel、前端的 status 和符号一致 |

## 6. 默认决策（建议值，待你确认）

1. 未声明范围时：沿用 `pass`，加 `scope.declared=false`，不新增状态；头行和 Excel 用「已测区段」口径。
2. 范围外关键元件里的确证差异：判 pending，不判 fail。
3. 同义突变：做成可配置，默认「合格（注明同义）」。
4. 目的基因模式的裁参考：只在质粒超过 `MAX_REF_BP` 时自动启用，不再作为用户开关。
