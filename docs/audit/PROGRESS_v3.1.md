# goal_sanger_scope_v3.1 执行进度（X0–X7）

> 依据：`docs/audit/20261110/goal_sanger_scope_v3.1.md`（§5 顺序 X0 → X1 →
> X3 → X2 → X4 → X5 → X6 → X7）、`docs/audit/20261110/REVIEW_b39b46a_2026-11-10.md`（RB）。
> 基线 `b39b46a`，分支 `fix/sanger-scope-v3.1`。双平台数字每次记录
> （Linux=VPS `/root/pd-v3`，Windows=本机 `.venv`）。

## X0 真值与复现（单独提交）

**恢复被改的场景**（goal §4 / RB P1-5）：
- **SC6**：恢复 W0 原构造 `make_trace_ab1(ref[200:1101], q=50,
  tail_width=(400, 3.0))`（峰形退化、Q 保持 50），替换 W12 前改成的
  「只降 Q」。原断言（partial + read_shortfall + 进队列）保留并加强为
  「`degraded_ranges` 必须存在」「不得判 mixed」。
- **SC10**：恢复 W0 原构造 `tail_width=(1050, 3.0)`、Q=50；断言
  pending + roi_unverified + 不判 mixed + pass/blocking 不并存。
- **CM3**：构造恢复 W0 原样（`ins = "T" if ref[3699] != "T" else "A"`，
  即「与左邻不同」而非上一轮的双侧都不同；两条 read 各自只带一处插入），
  断言恢复 `len([v for v in variants if type=="insertion"]) >= 2`，
  删除上一轮放宽的「接合区窗口内有确证差异即可」。

**新增 §4 场景**（构造在 `tests/sanger_scenarios.py`，断言在
`tests/test_sanger_scenarios.py`；未实现项标 `XF(strict=True, reason=<X项>)`）：

| ID | 覆盖 | 初始状态 |
|---|---|---|
| SC6b | 峰宽 ×2.0/2.5/3.0 + 未声明组：都不判 mixed，未声明组 pass | xfail(X1) |
| SC6c | 退化区段外 AF0.35 真实次峰 → 判 mixed | **已绿**（门控不得误杀） |
| CM9 | 微同源 1bp 插入 → type=insertion + c. 3′ + 移码 | xfail(X4) |
| RQ1 | 真实子集未声明：队列≤15、结论≤6 行、3 变异全在、fail | xfail(X2) |
| RQ2 | 真实子集全质粒：队列≤25、3 变异全在 | xfail(X2) |
| RQ3 | 单 read 40 双峰 → 只 1 项 read 问题 | xfail(X2) |
| OC3 | SC3/SC5/SC6：网页首行=headline、Excel=去符号 headline（逐字） | xfail(X3) |
| OC4 | 跨原点 Excel 不得「不合格」+「与设计一致」并存 | **已绿** |
| OC5 | 声明范围 pass 不出现「构建与设计一致」 | xfail(X3) |
| HC1 | 接合区插入 headline 写「1 处确证差异」（按位点去重） | xfail(X3) |
| HC2 | 跨原点范围「Cross（3701–300，跨原点）」 | xfail(X3) |
| SM1 | 记忆按用户隔离（A 的范围 B 不得继承） | xfail(X5) |
| SM2 | cds_target 等自动来源不写记忆 | xfail(X5) |
| SM3 | remembered=true + reason=沿用上次范围 + headline 注记 + 可清除 | xfail(X5) |

**复现探针**：`docs/audit/probe_v3.py`（自 `docs/audit/20261110/` 复制；
goal §5 要求放到 `docs/audit/`）。旧口径输出存 `work/probe_v3_before_X1.txt`
（已确认 RB 数字：真实子集队列 **330**（未声明）/ **393**（全质粒）、
峰展宽 ×3.0 判 **mixed**、结论 30 行）。

**X0 基线测试**：`tests/test_sanger_scenarios.py` 71 passed / 17 xfailed
（Windows）。场景库共 34 项（含新增 14 项），§4 全部就位。

**提交**：本段改动单独提交，之后不得修改场景构造或断言（goal §2）。


## X1 峰形门控（RB P0-1）——**触发 §7 停止条件，暂停**

### 已实现（代码在工作树，未提交）

- `core/sanger/peak_shape.py`：逐碱基峰宽（`signal.peak_widths`）滑窗中位数 →
  全 read 基准 ×1.6 判退化 → 连续 ≥15bp 成段；`expand_runs` 供 mixed 剔除
  外扩 10bp。阈值 `params/peak_shape.json`（含 `_doc` 推导依据）。
- `pipeline`：read_results 新增 `degraded_ranges`；`detect_and_classify`
  的 `exclude` 并入退化段（read 坐标）；read 级退化占比 ≥30% 时从
  `coverage_ranges` 与 consensus `covered_ranges` 扣除（只扣可信区内）。
- `coverage._verified_positions`：退化段内的碱基不计 verified。
- 场景结果：**SC6 / SC6b / SC6c / SC10 全部转绿**（SC6 → partial +
  scope_read_shortfall，measured 0.52；SC10 → pending + roi_unverified，
  measured 1.0 / verified 0.989）；场景库 76 passed / 12 xfailed
  （剩 X2/X3/X4/X5）。AF1/AF2/F1/F2/F3 8 passed 不变。

### 停止条件（goal §7 第二项）

**「峰形门控导致 AF1、真实混合 TPR 或公开 AB1 FPR 任一变差」——已触发。**

MX 真实回归（`test_real_data_regression::test_real_analysis_mixed_baseline_and_corroboration`）
从 `sorted(counts) == [15, 25, 35]` 变为 **`[12, 25, 25]`**：

| read | 门控前 | 门控后 | 被剔除位点（read 坐标） | 退化段 | 段内占比 |
|---|---|---|---|---|---|
| SZ15674-10570-2-seqF | 35 | 25 | 911, 914, 915, 917, 920, 925, 926, 935, 941, 947 | [921,937]、[1009,1024] | 3% |
| SZ15674-pET-Up | 25 | 25 | — | [964,999] | 4% |
| SZ15674-T7-Term | 15 | 12 | 923, 926, 927 | [1,16]、[926,954]、[997,1018] | 7% |

**误杀证据**：seqF 被剔除的 10 个位点里有 **6 个（911/914/915/917/920/925）
落在检测段 [921,937] 之外**——它们是被 `expand_runs` 的 10bp pad 扩进来
的。MX 是真实的双峰样品（15/25/35 是 T12/T24/R3 三轮取证锁定的基线），
这些位点是**真实混合信号**，不是峰形退化产物。

### 根因

MX 的真实混合区与峰展宽区**空间相邻**：峰宽检测器把「相邻双峰造成的
复合峰」也判为「峰宽增大」，于是把真实混合区的一部分划进了退化段。
goal 设计假设「退化段 = 纯测序未达」在这一数据上不成立。

### 待决策（需你确认，我不自行绕过）

1. **取消 pad**（`pad_bp=0`）：可救回 911–920 这 6 个，但 926/927（在段内）
   与 935/941/947 仍会被剔除，计数仍不到 35；
2. **退化段内「次峰独立」例外**：goal §3.1 本就要求「次峰是独立峰（次通道
   在主峰心 ±1 采样点内有局部极大值）」。把这条实现成退化段内的放行条件，
   可保留真实双峰而挡掉拖尾肩峰——但需新增次峰独立性判定，工作量与
   风险都大于当前门控；
3. **接受基线变化**：把 `[12, 25, 25]` 记为新的 MX 真值并说明理由——但
   goal §2 明确「对照组不得变红」「MX 确证差异数与 status 不变」（status
   与 2 处确证差异确实没变），而 mixed 计数属于 TPR 侧，属停止条件范围；
4. **提高 ratio 阈值**（1.6 → 2.0）：让 seqF 的 [921,937] 段不再被判退化。
   但 REVIEW 实测 tail_width=2.0 档的峰宽比是 1.85 < 2.0，SC6b 的 2.0 档
   会漏检——需要 SC6b 也用独立阈值，违背 goal 的单一阈值设计。

**建议**：方案 2（次峰独立性判定）。它同时满足 goal §3.1 原文的两条
（「峰形正常」+「次峰独立」），且不靠调阈值绕过数据。但它会让 X1 的
实现量翻倍，且需要单独验证「独立峰判定」在合成/真实两侧的分布。

请确认走哪一条，或给出其他口径。

### 更新（X1 已解开，停止条件撤回）

上面「停止条件」的结论**基于错误的判据实现**，不是数据本身的问题。修正过程：

**第一步：撤掉自己加的两个非物理外挂**

| 参数 | 初版 | 修正 | 依据 |
|---|---|---|---|
| `pad_bp` | 10 | 1 | 10bp 外扩把峰宽**正常**的位点也剔除：MX seqF 被误杀的 10 个里有 6 个 `width/base` 仅 1.14–1.43（阈值 1.6）。只留 1bp——相邻峰窗必然重叠，退化段前一碱基的次峰确实来自宽峰 |
| `smooth_window` | 9 | 1 | 9 点中位数平滑把**孤立宽峰抹成连续段**：MX 原始标记**零个** ≥15bp 连续段（宽峰全是孤立单点），平滑后虚构出 `[921,937]` 等 3 段 |

pad 归零后 MX 恢复 `[13, 25, 32]`（原 `[12, 25, 25]`），平滑归零后**完全回到 `[15, 25, 35]`**。

**第二步：换判据为物理量**

原判据 `width / 全 read 中位宽 > 1.6` 隐含「退化段自身的宽度会污染中位数基准」的
假设；实测显示它在真实数据上不稳定。改用**分辨率倒数**：

```
退化 ⟺ FWHM / 相邻峰间距 > 1.0   （连续 ≥15bp）
```

FWHM 达到峰间距即相邻两峰在半高处无法分开——这是「测序未达」的物理定义。
峰间距是仪器固有量（实测 10–14 采样点），跨数据集可比，与样品/信号强度无关。

**阈值分布（决定 1.0 的依据）**

| 数据集 | `width/spacing` 中位 | 判据 1.0 的结果 |
|---|---|---|
| 合成 SC6b ×2.0 | 2.00 | 全覆盖 [400,901] |
| 合成 SC6b ×2.5 / ×3.0 | 4.83 / 5.00 | 全覆盖 [399,901] |
| 合成 SC10（×3.0） | — | [1049,1080]（read 尾 32bp） |
| 真实 MX seqF / pET-Up / T7-Term | 0.58 / 0.62 / 0.62 | **零退化段**（p90 仅 1.54–1.67） |

判据在 0.85–1.3 区间内结论一致（合成全检出、MX 零误判），取 1.0 是取其物理
含义本身，不是拟合出来的数。

**停止条件逐项复核**

| §7 停止条件 | 修正后 |
|---|---|
| AF1、AF2 | 10 passed（`-k "af1 or af2"`） |
| 真实混合 TPR | `mixed_tpr == 1.0` 不变 |
| 公开 AB1 FPR | `mixed_fpr == 0.0` 不变 |
| MX 确证差异数与 status | 2 处 / `fail` 不变；`mixed_detected` 回到 [15,25,35] |
| 退化段 >30% read 范围 | MX 三条 read **零退化段**（0%），合成退化场景 22–100% |

**同时纠正一个我读错的基线**：先前把 MX 的 15/25/35 称为「真实混合信号」是错的。
`test_real_data_regression` 的 docstring 写明这三条 read 的双峰由模型全部归因
`noise`（低 Q 衰减噪声），MX 从未升级「疑似混合样品」。因此这 37 个位点本身属于
**应当被门控压制的噪声双峰**——但 MX 是 v3 三轮取证锁定的基线，压低它就是回归，
所以判据必须做到「真实数据零退化段」，而不是「少剔除几个」。

---

## X2 复核队列三级聚合（RB P0-2）— 完成

### 口径裁定（用户裁决：方案 3）

§3.2 第 1 条「read 已判 mixed → 位点全部折叠」中的 `mixed_profile.class` 有四档
（`widespread`/`local`/`noise`/`scattered`）。折叠哪些档决定所有计数，两种极端都不满足
§5 验收：

| read 折叠口径 | RQ1 ≤15 | RQ2 ≤25 | RQ3 ≤2 | MX |
|---|---|---|---|---|
| 只折 `widespread`+`local` | 14 ✅ | 30 ❌ | 1 ✅ | 3 |
| 折全部 mixed 分级 | 17 ❌ | 17 ✅ | 1 ✅ | 6 |

根因是真实子集里 118 个 `minor_af_site`（次等位丰度不足、判不了混）被切成 13 个区段，
全折则 RQ1 的孤立未决位点一起被折走（涨到 17），不折则 RQ2 停在 30。

**取方案 3**：`minor_af_site` 在任何范围下只计数不入队，依据是 §3.2 第 3 条
「位点级只保留确证差异和范围内的孤立**未决**位点」——`minor_af_site` 是有明确丰度
判据的结论，不是「读不清」的未决位点。完整清单仍在 `verdict.blocking` 与明细表。

read 折叠只收 `widespread`/`local`：这两档是「该 read 有真实双峰问题」；`noise`/`scattered`
是信号噪声，X1 峰形门控与 Q 比判别器已挡下降级，其位点仍需逐个核对（MX 正是靠这条
判据不升级「疑似混合样品」，压掉它等于让 MX 的 50 个真实双峰位点静默消失）。

### 队列前后对比

| 数据集 | 旧口径 | X2 后 | 构成（后） |
|---|---|---|---|
| RQ1 真实子集·未声明 | **330** | **14** | confirmed 3 / read 2 / segment 1 / site 8；只计数 60 |
| RQ2 真实子集·全质粒 | **393** | **17** | confirmed 3 / read 2 / segment 2 / site 10；只计数 118 |
| RQ3 单 read 40 双峰 | 40 项位点 | **1** | 1 项 read 问题，37 处位点全折叠 |
| MX 真实回归 | **330 量级** | **3** | 仅 3 处确证差异；只计数 50 |

MX 的 3 项即 3 处确证差异（5076 ins / 5269 C>T / MX 蛋白不一致），无位点级噪声项。

### 结论行预算

`conclusion_fold.apply_budget`：头行 + 最多 5 行要点 = 6 行（含头行）。折叠优先级
（低→高）低置信未决行 → ↳ 证据锚点 → 区间裁决 → 「注意：」→ ⚠ 混合汇总。
**永不折叠**：确证差异行、差异摘要、变异注记（↳ 酶切位点）、CDS 结论。

| 数据集 | 折叠前 | 折叠后 |
|---|---|---|
| RQ1 | 28 行 | **6 行** |
| RQ2 | 28 行 | **6 行** |
| MX | 10 行 → 折叠后 6 行（预算内不动） | **6 行**（逐字与 b39b46a 基线一致） |

预算判断放在**加入 head 之后**（此前错放在之前，导致 6 行预算实际放行 7 行）。
移行走后按非空行收口，避免尾部空行把行数算多。

### 前端

- `ReviewQueuePanel.vue`：默认展开前 10 项，其余折叠为「其余 N 项」；
- 每项级别徽标：整条 read / 区段 · N 处 / 位点 / 确证 / 接合区；
- `api/sequencing.ts`：`level`/`sites`/扩展 `evidence`（max_af/min_q/read/reason/positions/zone）。

### 为过尺寸门禁做的拆分

`pipeline.py` 已用满 2863 行预算，X2/X1 逻辑不得让它增长。拆出两个模块（净减 130 行）：

- `core/sanger/conclusion_fold.py`（新，约 125 行）：`CONCLUSION_BUDGET`、`fold_tier`、
  `apply_budget`——折叠判定按构建端的结构化归属，不靠文案前缀匹配；
- `peak_shape.py` 增收 `degraded_share()` 与 `split_span_by_degraded(span, r, to_ref)`：
  退化区段占比与覆盖段切分本来就属该模块职责（`to_ref` 回调，换算仍只有
  `ReadRefMap` 一处，符合防回潮约束 9）。

### 测试

- 删除 X2 的三处 `@XF(strict=True, reason="X2")`（RQ1/RQ2/RQ3 转正）；
- `test_review_queue`：组内排序补 prio 级（缺口先于孤立未决位点）；`scope` 缺失时
  按已声明处理（缺信息 ≠ 未声明，否则未决位点静默从队列消失）；
- `test_real_data_regression`：MX 逐读待核行与区间裁决行的逐字断言改到
  `conclusion_detail`（全量正文）与折叠组上——判定词一个不少，正文按预算让位。

| 套件 | 结果 |
|---|---|
| 后端（Windows） | **2087 passed / 0 failed / 46 skipped / 5 xfailed** |
| 前端 `test:run` | **693 passed**（90 files） |
| 前端 `type-check` | clean |
| `check_file_size.py` | 通过（235 文件，白名单 10 个） |
| `check_docs_sync.py` | 通过 |

---

## X4 indel 规范化（RB P1-4）— 完成（CM3 触发 §7 停止条件）

### 已落地

**1. HGVS 3′ 与质粒左对齐双坐标**（§3.4 第 1 条），`coords` 同时给两者：

| 字段 | 含义 | 例（CM9 的 1bp 插入） |
|---|---|---|
| `coords.plasmid` | 质粒左对齐（规范表示，数据库用） | `1499` |
| `coords.plasmid_3p` | HGVS 3′ 形式（写构建记录用） | `1500` |
| `coords.cds.c` | c. 左对齐 | `c.499` |
| `coords.cds.c_3p` | c. 按 3′ 规则（HGVS 惯例） | `c.500` |

左对齐稳定可比、3′ 与 SnapGene/送检单一致；只留一种会在对端产生无法解释的
1–3bp 位移。

**2. 后果注释**（§3.4 第 3 条）：新增 `variant["consequence"]`，基于**规范化后**
的结果，取值顺序 移码 > 氨基酸改变 > 同义 > 编码区内 > 非编码区。CM9 实测
`插入 1bp 导致移码`。

**3. 双侧独立 indel 互证**（新模块 `mutual_support.py`）：跨原点 CDS 的
「F read 带 ATG 前插入 + R read 带终止后插入」形态下，两处各由不同 read 报出、
分布在对侧，逐位互检的前提（clonal 变异必被每条覆盖 read 独立复现）不成立——
两条 read 并不冗余覆盖同一事件。判据：都是 indel、都单 read 支持、read 不同、
间距 ≥ 跨度一半、**且两处都处于互检矛盾状态**。

最后一条是关键：真实变异（如 MX 的 5076，被其他 read 支持、无矛盾）不得给
伪影背书。**MX 的 6653/6666 仍被互检排除、5076 仍 confirmed**，真实基线守住。

### CM3 触发 §7 停止条件：场景构造 off-by-one

**证据**（可复现）：

```
场景构造（tests/sanger_scenarios.py:663）
  r2 = ref[3299:4000] + ref[0:300] + ins2 + ref[301:450]
                                          ^^^^^^^^^^ 跳过 ref[300]（G）

实测
  CG_R read 长度 1151  ==  不含插入的参考片段长度 1151   → 没有多出 1bp
  CG_F read 长度 1152  >   参考片段 1151                 → 真正的插入 ✓
```

r2 在序列上等价于 `301 G>T` 替换而非「终止后插入」。任何「把替换读成插入」的
规范化都会把真实错义突变一起误报——实测按此实现后 **34 个场景变红**（含 S1 的
780 错义），已回退。

要修只能改场景构造（`ref[301:450]` → `ref[300:450]`），属 §7「需要修改 X0 之后
的场景构造」，因此 **CM3 保持 xfail 并在最终汇报中列明**；r1 的插入已被 X4 正确
检出为 insertion，CM9 全绿。

### 拆分（尺寸门禁）

`pipeline.py` 已用满 2863 行预算，X4 逻辑净增不得让它增长。拆出：

- `core/sanger/indel_norm.py`（新，约 130 行）：`normalize_left`（Tan 2015）、
  `normalize_right`（HGVS 3′）、`indel_coords`、`consequence_text`；
- `core/sanger/mutual_support.py`（新，约 70 行）：`opposite_side_single_indels`；
- `pipeline.py` 保留 `_normalize_indel` / `_normalize_indel_all` 兼容别名
  （`decompose.py` 与既有测试按此导入），实际实现只在 `indel_norm` 一处。

### 测试

| 套件 | 结果 |
|---|---|
| 后端（Windows） | **2088 passed / 0 failed** / 46 skipped / 4 xfailed |
| 前端 `test:run` | 693 passed（本轮无前端改动） |
| `check_file_size.py` | 通过（2862 ≤ 2863） |
| `check_docs_sync.py` | 通过 |

CM3 由 xfail 原因 `X4` 改为 `CM3 构造 off-by-one（§7 停止条件，已汇报）`，
docstring 写明证据链与不可自修的理由。
