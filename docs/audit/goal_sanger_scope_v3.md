# GOAL：Sanger 测序范围与准确报告（v3，承接 24d4d2e 复审，W0–W16）

> **基线**：`experiment/preview-visual-tuning` @ `24d4d2e`（tag `v2.37.0-alpha.2`，已包含 v2 的 T0–T25）。在其上新开工作分支 `feat/sanger-scope`，不直接改 main，也不直接改该分支。
>
> **依据**（W0 中复制进仓库 `docs/audit/`）：
> - `REVIEW_24d4d2e_2026-11-08.md`：复审，含 3 个 P0、可信度和流程问题，下文记作 `RV`；
> - `REVIEW_cds_mode_2026-11-08.md`：目的基因模式审核，下文记作 `CM`；
> - `PLAN_scope_reporting_2026-11-08.md`：综合优化方案，下文记作 `PL`。
>
> 行号只作线索，**一律以函数名定位**。

---

## 0. 目标

用户可以自由选择「需要测哪一段」。系统对整条质粒只分析一次，然后如实报告三件事：

1. **这段测了没有**；
2. **测得对不对**；
3. **没测到的原因**：是引物本来就够不到，还是应该测到却没测到。

人工只看系统确实判断不了的位点。

完成后应满足：

1. **结论只对声明范围负责**。范围外标「未评估」，不降级；未声明范围时只描述实测区段，**任何出口都不得出现「构建与设计一致」**。
2. **不出假 ✓**：修掉 RV 的 P0-1（整质粒模式部分覆盖判 ✓）、P0-2（pass 与 blocking 矛盾）、P0-3（CDS 内 AF 0.12–0.20 静默）。
3. **不出假 ✗**：修掉 CM 的 P0-A（目的基因模式下 read 超出 ±150bp 判 ✗）、P0-B（跨原点 CDS 不加侧翼，导致接合区插入漏检），以及「只测插入片段被 CDS 外包框判待定」。
4. **缺口有归因**：R−E 记为「引物未覆盖」，(R∩E)−V 记为「测序未达」，两者给出不同的动作。
5. **三个出口同源**：网页头行、Excel、前端卡片只读 `verdict.status` 和 `verdict.headline`。
6. **换范围不重跑分析**：`build_verdict` 是纯函数，同一份 result 在不同 scope 下得到的 verdict 可以复现。
7. **复核队列最小化**：只包含范围内「测序未达」、未决位点、确证差异三类，每项自带峰图证据，可一键确认并留痕。

---

## 1. 开工前必读

1. `AGENTS.md`、`CLAUDE.md`：命令、CI 门禁、Git 工作流、尺寸预算。
2. `docs/HANDOVER.md`：真实数据回归纪律、版本常量登记处（`SANGER_ALGO_VERSION` 当前为 `"v7"`，位于 `core/sanger/pipeline.py`）。
3. `docs/KNOWN_ISSUES.md`：防回潮约束、真实数据基线。
4. `docs/audit/PROGRESS.md`、`docs/audit/VERIFY_2026-11-07.md`：上一轮执行记录。
5. `docs/postmortems/2026-11-05-cds-mode-coverage-gate.md`：侧翼与资格门的来历。
6. 本轮依据：`RV`、`CM`、`PL`。
7. 主要代码：
   - 判定：`core/sanger/{verdict,coverage,primer_check,cds_extract,evidence,batch,resolved_regions,mixed_model,reliable_zone,aligner,pipeline}.py`
   - 接入：`app/routes/{sequencing_endpoints,sequencing_routes}.py`、`app/{sequencing_batch,sequencing_store}.py`、`app/database/models.py`、`alembic/`
   - 前端：`api/sequencing.ts`、`components/SequencingPanel.vue`、`components/sequencing/{ConclusionCards,MatchMap,ReadTable,VariantTable,EvidencePackPanel}.vue`、`utils/cdsMode.ts`

---

## 2. 全局硬约束

- **不发版**：不升版本号，不打 tag。上一轮自行发布了 `v2.37.0-alpha.2`，违反了约束，本轮严禁。提交与推送遵循 AGENTS.md 的 git-commit-style 授权。
- **API 契约**：
  - `analyze()` 只新增可选关键字参数（`scope=None`），**不改必填参数**；
  - 不改已有字段语义，只新增字段；
  - 新字段同步到 `api/sequencing.ts` 和 `sequencing_routes._summary` 的下发白名单；
  - 旧字段 `verdict.roi` 保留，取值为 scope 的外包框。
- **向后兼容**：没有 scope 等新字段的历史记录，读取、重放、前端展示都要正常（`test_sequencing_rehydrate.py` 保持绿）。前端缺字段时降级为旧展示。
- **数据库**：只允许做增量迁移（新表或可空列），并按 `docs/MIGRATIONS.md` 登记。任何非增量变更都要先停下。
- **判定不依赖文案**：禁止用字符串匹配做逻辑。前端在有 `verdict.headline` 时不得自行拼接结论文案。
- **真值先行**：§4 的全部场景在 W0 写定真值，并单独提交。之后**不得为了让测试通过去修改真值**；确实需要改时，停下汇报。上一轮 T24 把 F1/F2/M12 的真值改成跟随新输出，本轮禁止。
- **尺寸预算**：白名单文件只许变小，新文件不超过 900 行。新逻辑放进新模块，例如 `core/sanger/scope.py`、`core/sanger/review.py`。
- **依赖**：不新增 Python 运行时依赖。前端如需新增依赖，先停下。
- **口径版本**：任何改变结论口径的提交，都要递增 `SANGER_ALGO_VERSION`（v7 → v8 → …），并在 HANDOVER 登记。
- **对照组不得变红**：
  - 反向 read 替换高置信检出；
  - 跨原点 read 无假缺失；
  - 大片段 indel 检出；
  - 100% 纯合突变判 ✗；
  - 5′ 错误连接 35–120bp 判 ✗；
  - RV 列出的 13 例干净样品全部判 ✓；
  - 骨架上的 N 判 △；
  - CDS 内混合 AF ≥ 0.23 判 mixed。
- **双平台测试数字**：每次汇报都分别给出 Linux 和 Windows 的测试数字，并注明平台。上一轮 PROGRESS 只有 Windows 数字，这一点要改。
- **私有真实数据回归**：在有 MX 数据的机器上必须跑。改口径前先按旧口径保存输出（`work/baseline_before_Wxx.json`，不入库），改完逐项对比，每条差异都要解释。

---

## 3. 统一数据结构

### 3.1 Scope（新模块 `core/sanger/scope.py`）

```python
@dataclass
class Scope:
    ranges: List[Tuple[int, int]]   # 1-based 闭区间，可多段；环状跨原点用 start > end 表示折回
    source: str                     # user | design_insert | cds_target | filename_match | primer_window | measured
    declared: bool                  # source == "measured" 时为 False
    require_double_strand: bool = False
    elements: List[str] = []        # 范围内的元件名（用于报告和头行）
    reason: str = ""                # 自动推断的依据，例如「样品名 GeneX-3 匹配 CDS GeneX」
```

`Scope.to_dict()` / `Scope.from_dict()` 写入 `result["scope"]` 和 `verdict["scope"]`。

### 3.2 coverage 新增字段

- `expected_ranges`：每条 read 一项，格式为 `{read, primer, ranges, direction, basis: "read_start" | "primer_binding"}`。
  - 理论窗口 = 比对起点沿测序方向，从 `front_gray` 延伸到 `tail_gray`；
  - 有引物序列时，以 `locate_binding` 得到的 3′ 端为起点。
- `gaps`：只计算 R 内的缺口。每项包含：
  `{start, end, length, wraps_origin?, cause: "beyond_primer" | "read_shortfall" | "unknown", detail}`。
  其中 `detail` 要写明衰减位置，例如「GX-F 在 760 处衰减（理论可达 1120）」。

### 3.3 verdict 新增字段

- `scope`：Scope 字典。
- `headline`：结论头行的唯一来源，格式为「符号 · 范围 · 证据」，例如：
  - `✓ GeneX（301–900）+ 接合区：一致 · 双链 100%`
  - `△ GeneX 已验证 82% · 1150–1299 引物未覆盖（需反向引物）`
  - `△ GeneX 已验证 90% · 820–900 测序未达（GX-F 在 760 衰减）`
  - `✓ 已测 201–1050：一致 · 覆盖 GeneX 全长；AmpR、ori 未评估`（未声明范围时）
- `element_verdicts`：每个 CDS、关键元件、接合区一项，格式为
  `{name, type, start, end, in_scope, coverage: "full" | "partial" | "none", double_strand_pct, issues, status}`。
  范围外且未覆盖的，`status` 为 `"not_assessed"`。
- 新增 blocking kind：
  - `scope_beyond_primer`：action 为加引物或移动引物；
  - `scope_read_shortfall`：action 为重测或核对峰图，进入复核队列；
  - `scope_uncovered`：E 不可得时使用。
- 新增 info kind：`out_of_scope_variant`。
- **契约**：`status == "pass"` ⇒ `blocking == []`。

### 3.4 变异的双坐标

每个变异新增 `coords: {plasmid, cds?: {name, c, p?}, strand_note?}`。
`ref_pos` 保持为分析参考上的坐标；本轮改为全质粒比对后，它就等于质粒坐标。

---

## 4. 场景基准（W0 写定真值，加入 `tests/sanger_scenarios.py`）

### 4.1 范围（SC）

| ID | 构造 | 期望 |
|---|---|---|
| SC1 | 4000bp 环状质粒，注释 GeneX CDS（301–900）+ AmpR + AmpR promoter + ori；`scope=GeneX`；正反两条 read 完整覆盖 GeneX | pass；headline 含「GeneX（301–900）」；`element_verdicts` 中 AmpR、ori 为 not_assessed |
| SC2 | 同 SC1，不声明 scope，参考文件名 `GeneX` | `source=filename_match`，pass |
| SC3 | 同 SC1，不声明 scope，文件名也匹配不到 | `declared=false`；headline 写「已测 x–y……未评估」；**任何出口**都不得出现「构建与设计一致」 |
| SC4 | 3000bp、无 CDS 的参考，单条 read 覆盖 1–900，不声明 scope | 同 SC3（RV P0-1 回归） |
| SC5 | `scope=301–1500`，只有正向 read，起点约 280 | partial；缺口 cause 为 `beyond_primer` |
| SC6 | `scope=GeneX`，正向 read 在 read 坐标约 400bp 处质量跌破 Q20 | partial；cause 为 `read_shortfall`；该项进入复核队列 |
| SC7 | `scope=GeneX`，read 伸到 R 外，R 外骨架上有一处确证替换 | pass；`out_of_scope_variant` 单列，不进 blocking |
| SC8 | `scope=GeneX`，R 外的 ori 中有一处确证替换 | pending（critical_element_variant） |
| SC9 | 同一份 result，依次用 `scope=GeneX`、`scope=全质粒`、不声明 调用 `build_verdict` | 三份 verdict 与快照一致；不调用比对 |
| SC10 | CDS 301–1299，verified 99.0%、measured 99.1% | 不得出现 pass 同时带 blocking 的情况（RV P0-2 回归） |
| SC11 | `scope=GeneX`，`require_double_strand=True`，GeneX 后半段只有单链覆盖 | partial；按 E 归因 |

### 4.2 目的基因模式（CM）

| ID | 构造 | 期望 |
|---|---|---|
| CM1 | GeneX 1001–1600，正反 read 覆盖 700–1600 / 1000–1900，序列全对，`cds_mode=True` | pass；无 `unaligned_hq_segment`、`partial_alignment`（P0-A 回归） |
| CM2 | 用载体通用引物，read 起点距 ATG 300bp | pass |
| CM3 | 跨原点 CDS `join(3701..4000,1..300)`，分别构造 ATG 前、终止密码子后的 1bp 插入 | 检出，并判 junction_variant（P0-B 回归） |
| CM4 | 近原点 CDS（31–630），ATG 前 1bp 插入 | 检出；结果与「全质粒 + scope=CDS」一致 |
| CM5 | 同一组 read，分别用 `cds_mode=True` 和「全质粒 + scope=该 CDS」 | `verdict.status`、`headline`、blocking 的 kind 集合逐项一致 |
| CM6 | 参考名 `GFP_backbone`，read 名 `mCherry_F` | 判歧义，返回候选清单 |
| CM7 | 显式选择嵌套的 6xHis | 可以选中；重名时报错 |
| CM8 | 负链基因，质粒 1100 位替换 | `coords.plasmid=1100`，c./p. 正确 |

### 4.3 混合（MX-AF）

| ID | 构造 | 期望 |
|---|---|---|
| AF1 | CDS 内单 read，AF 分别为 0.12、0.15、0.18、0.20 | 至少 pending，不得 pass（RV P0-3 回归） |
| AF2 | 骨架上的散在双峰，AF 0.15 | 保持现有口径（经 unresolved_sites 判 △），不升级为 mixed |

### 4.4 引物（PR）

| ID | 构造 | 期望 |
|---|---|---|
| PR1 | 正反两条引物各自对应自己的 read | 两条引物校验都为 ok，不报方向不一致 |
| PR2 | 提供了引物但没有对应的 read | 报「引物无信号」，归入 read_shortfall |
| PR3 | 有引物序列，read 起点与引物 3′ 端距离超出窗口 | primer_mismatch（保持 T17 口径） |

### 4.5 出口一致（OC）

| ID | 构造 | 期望 |
|---|---|---|
| OC1 | 对 SC3/SC5/SC6/CM3 以及任意 fail 样品 | 网页头行、Excel 结论前缀、前端卡片三者的符号和状态一致；全部由 `verdict.status` 导出 |
| OC2 | 跨原点探针（CM 审核中三出口不一致的那一例） | 不再出现 fail / △ / 不合格三种说法并存 |

---

## 5. 工作分解

### 阶段 0：基线与取证

#### W0 基线、修红、真值先行
1. 在 Linux 和 Windows 上分别复跑全量测试，记入 `docs/audit/PROGRESS_v3.md`（Linux 当前为 1999 passed / 2 failed / 10 skipped）。
2. 修复 Linux 下的两个红测试：
   - `test_docs_sync`：CHANGELOG [Unreleased] 的 compare 链接改为指向当前最新 tag；
   - `test_filename_safety::…single_implementation`：白名单路径按平台归一化，不用 `chr(92)` 拼接。
3. 把 RV、CM、PL 复制到 `docs/audit/`。同时核对 v2 goal 中审计 A 的引用路径：原文实际在 `docs/SANGER_AUDIT_A_2026-11-06.md`，统一移到 `docs/audit/` 并更新所有引用。
4. 写入 §4 全部场景的真值，未实现的标 xfail，并单独提交（`test(sanger): v3 场景真值先行`）。
5. 按旧口径保存 MX 回归输出。

### 阶段 1：判定内核

#### W1 scope 入参与纯函数化（PL §A1、§A8）
- `build_verdict(result, ref_length, scope=None)`：除 result 外不读任何状态，不调用比对。
- `analyze(..., scope=None)`：只把 scope 透传给 `build_verdict`，并写入 `result["scope"]`。
- 先在 `scope.py` 中建好 `Scope` 数据结构；W10 再补全来源推断。

#### W2 去掉外包框与 whole 豁免（RV P0-1；PL §A2、§A4）
- 删除 `cds_union` 外包框 ROI，删除 `roi_gap` 对 `whole` 的豁免。
- 未传 scope 时：`source="measured"`、`declared=false`，不判 partial；headline 使用「已测区段」口径。
- 范围外的确证差异记为 `out_of_scope_variant`（info）。
- 范围外关键元件里的确证差异仍走 `critical_element_variant`，判 **pending**（见 §8 默认决策 2）。
- `_junction_ranges` 扩展：接受 scope 中的插入区边界（`design_insert`）。

#### W3 verified 口径与 pass 契约（RV P0-2）
- 统一使用 verified 口径判定范围是否完成。
- `roi_unverified` 一类 blocking 不能与 pass 并存。
- 在 `build_verdict` 末尾加断言，同时写成契约测试。

#### W4 混合低 AF（RV P0-3）
- `mixed_model` 的 `ratio_floor` 从 0.30 改为 0.20（依据：`docs/SANGER_CALIBRATION.md` 显示 0.20 与 0.30 并列最优）。
- `MINOR_AF_MIN=0.15` 要么真正生效（范围内 AF 0.15–0.20 进入 pending），要么删除。不得保留不可达的死代码。
- AF2（骨架）口径保持不变。改动后复跑 3 个真实 AB1 的混合 FPR，并记录结果。

#### W5 element_verdicts 与 headline（PL §A5、§A6）
- 后端生成 `element_verdicts` 和 `headline`。
- 同义突变做成可配置项（见 §8 默认决策 3）：在 `config` 或 `analyze` 的可选参数中设置，默认「合格（注明同义）」。

**阶段 1 闸门**：SC1–SC4、SC7–SC10、AF1–AF2 全部变绿；对照组不变红；写入 PROGRESS 后继续。

### 阶段 2：引物覆盖与缺口归因

#### W6 E 与缺口原因（PL §B1、§B2）
- 实现 `coverage.expected_ranges`，以 read 起点为默认依据；有引物时改用引物结合位点。
- `_coverage_gaps` 只计算 R 内的缺口，并给出 `cause` 和衰减位置。保留现有的跨原点合并逻辑。
- verdict 中 `roi_uncovered` 按 cause 拆成 `scope_beyond_primer` 和 `scope_read_shortfall`；E 不可得时用 `scope_uncovered`。

#### W7 引物逐 read 对应（PL §B3、§B4）
- `primer_check.check_reads` 接受 `{read 文件名: 引物}`。旧的单引物入参继续兼容，解释为「对所有 read 使用同一条引物」。
- 批量路径：由信息表的引物列生成映射。
- 提供了引物但没有对应 read 时，报「引物无信号」。

**阶段 2 闸门**：SC5、SC6、SC11、PR1–PR3 全部变绿。

### 阶段 3：目的基因模式重构

#### W8 不再裁参考（CM P0-A、P0-B、P1-C、P1-F）
- `cds_mode=True` 时，按真实拓扑做全质粒比对，`scope = 目标 CDS + 接合区`，`source=cds_target`。保留全部注释。
- 只有质粒超过 `MAX_REF_BP` 时才裁剪：
  - 侧翼取 `max(1500, 最长 read 长度 + 200)`；
  - 环状质粒绕回取窗口；
  - 跨原点 CDS 先把质粒旋转到使 CDS 连续；
  - `classify_segment` 改用完整质粒定位，能连续定位到窗口外的段不算异常。
- 更新 `test_real_cds_mode_mx`：增加 `verdict.status` 和 headline 断言（等价性从「变异集合一致」扩展到「结论一致」）。

#### W9 自动匹配与双坐标（CM P1-D、P2-G）
- `resolve_auto`：参考名命中与 read 名命中指向不同候选时判歧义。
- 显式选择在**未去重**的候选中查找，允许选中嵌套特征；重名时报错。
- 变异新增 `coords`。`resolved_regions` 的 offset 只用于显示，不参与判定。
- 删除 `cds_extract.py` 中重复的 `import re`。

**阶段 3 闸门**：CM1–CM8 全部变绿，MX 真实回归通过并解释差异。

### 阶段 4：范围输入

#### W10 scope 来源与输入（PL §C1–§C5）
- `scope.py`：
  - 按 §3.1 的来源优先级推断 scope；
  - 实现 `parse_scope_cell(text, features)`，支持元件名、`301-900`、多段写法（`;` 分隔）、「插入片段」「全质粒」「接合区」；解析失败时返回错误说明，不抛异常。
- `POST /sequencing/parse-reference` 新增 `scope_presets` 和 `default_preset`（附带 reason）。
- `analyze_sequencing_upload` 新增 Form 字段 `scope`（字符串，按 `parse_scope_cell` 解析）。
- 设计流 `analyze_design_sequencing` 把 `insert_start/insert_end` 作为 `design_insert` 传入。
- 批量：
  - 信息表新增「验证范围」列；留空时走 `filename_match`；
  - 解析失败的行在结论中回填「范围无法识别：xxx」，不阻塞其他样品。

#### W11 重算端点与记忆（PL §C6、§C7）
- 新增 `POST /sequencing/results/{id}/verdict`，请求体 `{scope}`：从存档 result 重算并落库，追加 `scope_history`（时间、用户、新旧 scope）。
- 按参考序列哈希保存最近一次 scope；同一质粒再次分析时作为默认值，`source` 记为原来源，`reason` 写「沿用上次范围」。
- 数据库只做增量迁移，并在 MIGRATIONS.md 登记。

**阶段 4 闸门**：SC9 走端点路径变绿；scope 输入解析的单元测试全绿；历史记录兼容。

### 阶段 5：输出统一与前端

#### W12 三出口统一（PL §E1、§E4、§E6）
- `evidence.py` 的结论头行、`batch.excel_conclusion`、`ConclusionCards.vue` 只读 `verdict.status` 和 `verdict.headline`。缺少 verdict 的旧记录走降级分支。
- Excel 新增列：验证范围、范围来源、范围内验证覆盖、缺口原因、范围外差异、补测建议。原「测序覆盖 x%」移到明细 sheet。
- annotator 中四处「低置信度（…疑似伪影…）」话术改为「未决」口径（RV 遗留）。
- 清除前端按字符串分组的逻辑（RV 遗留）。

#### W13 前端范围与覆盖可视化（PL §E2、§E3、§E5；PL 产品方案 §2.2）
- `SequencingPanel`：上传参考后显示范围选择条，包括预设按钮和「自定义」，默认项附带 reason。
- `MatchMap`：
  - 点击元件可加入或移出范围；
  - 在坐标轴上拖拽可框选区间；
  - 范围外区域置灰；
  - 分三层色带显示 R / E / V，缺口按原因着色。
- 结论下方显示元件状态表：范围内的元件逐行列出，范围外的折叠成一行。
- 变异表显示双坐标。
- 修改范围后调用 W11 的端点重算；在等待返回期间，可以先用 `element_verdicts` 在本地给出预览。
- 目的基因模式开关并入范围预设，不再作为单独开关（见 §8 默认决策 4）。

**阶段 5 闸门**：OC1、OC2 全部变绿；前端测试、type-check、build 全绿。

### 阶段 6：少复核与收尾

#### W14 复核队列（PL §F）
- 新建 `core/sanger/review.py`，从 verdict 生成复核队列：只收范围内的 `scope_read_shortfall`、未决位点、确证差异。`beyond_primer` 只给出动作建议，不入队。
- 每个队列项内嵌证据：位点 ±10bp 的峰图（复用 EvidencePackPanel）、各 read 的调用、Q 值、次峰比例。
- 一键确认（伪影或突变）：写入 `reviewed_by/at` 和决定，重算 verdict；headline 标注「人工确认 N 处」。
- 批量页顶部显示「需复核 X 个样品、Y 个位点」，按 fail > read_shortfall > pending 排序。

#### W15 可信度与防回潮
- `p_real` 不再显示为百分比，改为高 / 中 / 低三档。在完成真实数据校准之前，不当作概率使用。
- KNOWN_ISSUES「防回潮约束」补齐 v2 §6 的 5 条，再新增以下 5 条：
  1. 判定只对 scope 负责，范围外为「未评估」；
  2. 未声明范围时不得输出「构建与设计一致」；
  3. 目的基因模式不得以裁剪后的参考做未比对判定；
  4. 缺口必须归因（beyond_primer / read_shortfall / unknown）；
  5. 修改 scope 不得触发重新比对。

#### W16 基准与文档收尾
- 补一份脱敏的真实 AB1 子集进仓库，**必须包含真实变异**，用来测敏感度。许可不明确时停下。
- 更新：`docs/ALGORITHM_ROADMAP.md`、`docs/SANGER_ALGORITHM_REPORT.md`（新增范围与归因章节）、CHANGELOG [Unreleased]、HANDOVER（`SANGER_ALGO_VERSION` 登记）。
- CHANGELOG 中写明用户可见的变化：
  - 只测插入片段不再判待定；
  - 未声明范围时的头行口径变化；
  - 目的基因模式不再因 read 过长判不合格；
  - 低 AF 混合会更多地判待定（这是预期行为）。

---

## 6. 最终验收标准

1. §4 全部场景通过，无 xfail、无 skip；真值自 W0 提交后未被修改（用 `git log -p tests/sanger_scenarios.py` 核对）。
2. 契约测试：
   - 三个出口由同一个 `verdict.status` 导出；
   - `status == "pass"` ⇒ `blocking == []`；
   - 未声明范围时，全仓库任何出口都不得输出「构建与设计一致」；
   - 同一份 result 在不同 scope 下重算，verdict 可复现，且不调用比对。
3. 后端全量测试：Linux、Windows 均全绿，通过数只增不减，skip 不增。前端 `test:run`、`type-check`、`build`、`npm audit` 全绿。
4. 门禁：`check_file_size.py`（白名单只减不增）、`check_docs_sync.py`、依赖锁、pip-audit 全绿。
5. MX 真实回归：在有数据的机器上通过；目的基因模式与「全质粒 + scope」结论一致；与基线的差异逐条记录。
6. 旧记录兼容：`test_sequencing_rehydrate.py` 绿；无 scope 的历史记录在前端正常显示。
7. 性能：单样品（3 条 read、10kb 参考）耗时不超过 v2 的 1.2 倍；scope 重算端点 P95 < 200ms；`test_sequencing_batch_budget.py` 绿。
8. 未升版本，未打 tag。

---

## 7. 执行方式、停止条件与汇报

- **顺序**：W0 → W1 → W2 → W3 → W4 → W5 →【闸门 1】→ W6 → W7 →【闸门 2】→ W8 → W9 →【闸门 3】→ W10 → W11 →【闸门 4】→ W12 → W13 →【闸门 5】→ W14 → W15 → W16 →【最终】。
  - 依赖关系：W1 在其他所有任务之前；W6 在 W12 之前（Excel 需要缺口原因）；W8 在 W10 之前（`cds_target` 来源依赖重构后的模式）；W11 在 W13 之前。
- **全量执行**：16 项全部完成。阶段闸门只写 PROGRESS，然后继续，**不停下等待确认**。
- **每个任务的流程**：
  1. 对应场景先红（或保持 xfail）；
  2. 实现；
  3. 跑相关测试、全量 pytest（Linux 和 Windows）、有前端改动时跑前端测试，再预演门禁；
  4. 改口径时跑 MX 回归对比；
  5. 全绿后按 Conventional Commits 提交（如 `fix(sanger): …`、`feat(sanger): …`）。
  - 每个任务至少一个提交，提交说明写明对应条目（RV / CM / PL）和场景 ID。
- **立即停下汇报、不得自行绕过**：
  - 需要修改 `analyze()` 的必填参数，或已有字段的语义；
  - 需要修改 §4 的真值；
  - 任何对照组必须变红才能让目标场景变绿；
  - 需要非增量的数据库迁移；
  - 白名单文件无法在预算内完成改动；
  - 需要新增 Python 运行时依赖，或新增前端依赖；
  - W4 降低阈值后，3 个真实 AB1 的混合 FPR 明显上升（附数据，由人取舍）；
  - W8 改为全质粒比对后，MX 真实回归出现无法解释的结论变化；
  - 真实 AB1 子集的许可不明确；
  - 任何需要升版本或打 tag 的情况。
- **阶段汇报**（每个闸门一次，写入 `docs/audit/PROGRESS_v3.md`）：
  - 改动文件；
  - 新增或修改的测试；
  - 场景前后对比表（每项列出旧输出和新输出）；
  - Linux 和 Windows 的测试数字；
  - 门禁结果；
  - MX 回归差异；
  - 性能数据；
  - 遗留问题。
- **最终汇报**：16 项完成情况、全部场景结果、混合 FPR、性能、刻意未做的事项及原因。

---

## 8. 已采用的默认决策（可改）

1. **未声明范围的状态**：沿用 `pass`，加 `scope.declared=false`，不新增状态。headline 和 Excel 使用「已测区段」口径。
2. **范围外关键元件里的确证差异**：判 pending，不判 fail。
3. **同义突变**：做成可配置项，默认「合格（注明同义）」。原口径（判不合格）作为可选值保留。
4. **目的基因模式的裁参考**：只在质粒超过 `MAX_REF_BP` 时自动启用，不再作为用户开关。前端的「目的基因模式」并入范围预设。
