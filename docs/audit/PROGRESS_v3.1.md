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
| MX（b39b46a → 当前） | 11 行 | **7 行** |

**MX 停在 7 行是合规下限，不是漏折**：余下 7 行全是「永不折叠」的判读要点
（头行 + 差异摘要 + 2 条确证变异行 + ↳ 酶切位点注记 + 2 条 CDS 结论）。被折走的
是 4 行核对素材（区间裁决 1 行、↳ 逐 read 双峰明细 2 行、「注意：」缺口说明 1 行），
分别进 `resolved` / `mixed_detail` / `notes` 组，前端可展开。MX 不是 §3.2 的
「单样品 6 行」样本，不按 6 行验收；§6 第 5 条对 MX 的验收项是「确证差异 2 处、
status=fail 不变」，这两项逐字节一致。

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

---

## 门禁调整：解除单文件行数限制（2026-11-10，所有者决定）

**决定**：项目处于**推进期**而非维护期。为过「单文件 ≤900 行」门禁而先拆分，
把大量资源花在结构调整上（本轮 X2/X4/X5 各有一半时间在处理行数），功能推进
被显著拖慢。大文件的重构放到维护期集中做效果更好、总资源更少。

**改动**：
- `scripts/check_file_size.py`：改为**只统计、只提示**，永远退出码 0；
  新增 `--top N` 直接列出最大的 N 个文件（作为后期重构的输入）；
- `.github/workflows/ci.yml`：`Check file size budgets`（阻断）→
  `Report file sizes`（只打印清单）；
- `AGENTS.md` / `docs/HANDOVER.md` / `README.md` / `docs/KNOWN_ISSUES.md`：
  同步口径，KNOWN_ISSUES 新增「大文件重构」待办清单（pipeline.py ~2870、
  design_service.py ~2330、BatchDesignView.vue ~1320 等，含建议切分线）。

**已做的拆分不回退**：`conclusion_fold.py` / `indel_norm.py` /
`mutual_support.py` 是按职责边界切的，本身是合理结构，留着比合回去更好
（只是不再为「再涨 3 行」去做无意义压缩——本轮为过门禁压缩过的排版已还原）。

---

## X5 范围记忆（RB P1-3 / P2-3）— 完成

### 键从「参考哈希」改为「用户 + 参考哈希」

| 项 | 旧 | X5 后 |
|---|---|---|
| 存储键 | `reference_hash` 单主键 | `(owner_id, reference_hash)` 复合主键 |
| 匿名/单用户 | — | owner 归一为 `ANONYMOUS_OWNER`，行为与旧版一致 |
| 多用户 | 会串味 | 各记各的（SM1） |
| 迁移 | 无 | `b7c8d9e0f1a2`（`batch_alter_table`，SQLite 重建表搬数据） |
| 运行时建表 | `__table__.create` ×2 | **已删除**（只走 alembic） |

### 只记忆显式声明

`REMEMBERED_SOURCES = {"user"}`：`save_scope_memory` 对 `cds_target` /
`filename_match` / `primer_window` 等自动来源直接返回（SM2）。否则下次换
目标 CDS 时会被上一次的自动选择污染。

### 套用记忆时的标记

- `scope.source` 保持原来源；
- `scope.reason = "沿用上次范围"`；
- **新增 `scope.remembered = true`**；
- headline 末尾追加「（沿用上次范围）」——范围是记忆带出的而不是这次声明的，
  不标注会让用户以为真的检查了这个范围；
- 前端范围条显示「沿用上次范围（元件）· 清除」按钮。

### 批量路径不套用记忆

`sequencing_batch.py` 原有的「留空则按参考带出上次范围」已删除（§3.5）：
信息表一行一个构建，若某行留空就套记忆，整批会被上一次的单一范围污染。

### 新增 DELETE 端点

`DELETE /api/sequencing/scope-memory?reference_hash=<sha256>`：只删本人那一行，
幂等（无记录返回 `deleted=false`）。`verdict.scope` 新增 `reference_hash` 字段
供前端直接回传，不必重传整条参考序列。

### 测试

| 场景 | 结果 |
|---|---|
| SM1 记忆按用户隔离 | 通过 |
| SM2 自动来源不记忆 | 通过 |
| SM3 remembered/reason/headline 注记 + 可清除 | 通过 |
| 后端（Windows） | **2091 passed / 0 failed** / 46 skipped / **1 xfailed（CM3）** |
| 前端 `test:run` | 693 passed（90 files） |
| 前端 `type-check` | clean |
| `test_migrations.py` | 通过（升级后 model 与迁移一致） |
| `check_docs_sync.py` | 通过 |
| `check_file_size.py` | 只提示（11 个文件超参考线，退出码 0） |

---

## X6 清理（RB P2-1 / P2-2）— 完成

### 删除误入库的重复目录

`docs/sanger_scope_v3_docs/`（4 个文件）是压缩包解压位置不对造成的重复目录，
内容与 `docs/audit/` 下的文件重复，已 `git rm -r` 删除。

### `resolve_reference` 守卫

按 §5 给的第二个选项（保留函数 + 禁止生产调用），不删函数——它定义了
「CDS ± FLANK_BP 侧翼」的口径，那组提取测试（正链/负链/多段/报错）验证的
正是这个口径，删掉会丢掉回归覆盖。

- 函数 docstring 标为「**仅测试与回退对照使用**，生产代码不得调用」；
- 新增 `tests/test_scope_mode_guard.py`：扫 `src/backend/**/*.py` 源码文本，
  出现 `resolve_reference(` 调用形态即红（注释与 `def` 行豁免，
  文档字符串里不带括号的提及不算调用——用正则而不是子串匹配）；
- 同时断言 `prepare_scope_mode` 仍是 `cds_extract` 的公开入口。

实测：生产代码已无任何调用点（只有 `cds_extract.py` 自身的定义与注释）。

### 测试

| 套件 | 结果 |
|---|---|
| 后端（Windows） | **2093 passed / 0 failed** / 46 skipped / 1 xfailed（CM3） |
| 新增 | `test_scope_mode_guard.py` 2 项 |
| `check_docs_sync.py` | 通过 |

---

## X7 防回潮与文档 — 完成

### KNOWN_ISSUES 新增四条约束

1. **峰形退化区段内的双峰不得判混**：`peak_shape.split_span_by_degraded` 先切退化
   区段，区段内双峰只归因 `noise`/未达，不得升级「疑似混合」、不得建议重挑克隆。
   依据：MX 的 37 个噪声双峰曾把 read 尾端降分辨率误读成混合。
2. **头行唯一来源 `verdict.headline`**：`excel_conclusion` 是 headline 去掉符号的
   派生值；前端、Excel、批量三处都不得自行拼接头行。守卫在
   `tests/test_three_exits.py`（逐字比对四个出口）。
3. **复核队列按 read / 区段 / 位点三级聚合，未声明范围不收位点项**：
   `minor_af_site` 只计数不入队；`unresolved_site` 在 scope 未声明时跳过。
   阈值 `READ_FOLD_SHARE=0.30`，区段合并 ≤20bp。
4. **X0 之后不得修改场景构造或断言**：`tests/sanger_scenarios.py` 与
   `tests/test_sanger_scenarios.py` 中 X0 提交（`628e3b1`）写定的构造/断言为本轮
   真值基线。实现做不到时按 goal v3.1 的停止条件停下汇报，不得改断言凑绿。

### SANGER_ALGORITHM_REPORT §5 增补

「v3.1 增补」小节记录三处含金量修正（峰形门控、队列三级聚合+行预算、
三出口同源+indel 双坐标），并把基线数字改正为「67 个场景定义（88 个测试函数），
87 通过 / 1 xfail（CM3）」。

### CHANGELOG [Unreleased]

`### Sanger scope v3.1` 分「升级注意 / 修复 / 已知未修 / 工程」四块。

### HANDOVER

- §1 现状新增 v3.1 段落（三处核心修复 + CM3 说明 + 门禁调整）；
- §2 缓存版本常量：`SANGER_ALGO_VERSION` v14→**v15** 并登记递增理由；
- 事实清单「后端测试」行更新为 **2093 passed / 46 skipped / 1 xfailed**。

### 测试

| 套件 | 结果 |
|---|---|
| 后端（Windows，全量） | **2093 passed / 46 skipped / 1 xfailed**，0 failed，247s |
| `check_docs_sync.py` | 通过（最新 tag v2.37.0-alpha.4） |
| `check_file_size.py` | 只提示，退出码 0 |

### 刻意未做

`CHANGELOG` 未升版本号、未打 tag：本轮是分支上的修复收口，发版留给你决定。

### 性能（新增 `docs/audit/bench_v31.py`）

同一台 Windows 机器、同一 venv、同参数各跑 7 次取中位数，只测
`analyze()` 纯计算（不含 tracy 外部进程）。基线用 `git worktree` 检出
`b39b46a`，`PD_REAL_DIR` 指向真实数据目录。

| 用例 | b39b46a | v3.1 | 比值 |
|---|---|---|---|
| synth（900bp 单 read + 2 替换） | 277.3 ms | 292.0 ms | 1.053× |
| long（1600bp 单 read + 1 替换） | 666.3 ms | 694.1 ms | 1.042× |
| real_mx（真实 MX 3 条 read，7039bp 参考） | 1348.3 ms | 1397.9 ms | 1.037× |
| **合计** | **2291.9 ms** | **2384.1 ms** | **1.040×** |

goal §6 第 7 条限 1.15×，**达标**。新增开销来自逐碱基峰宽计算
（`signal.peak_widths` + 滑窗中位数），随 read 长度近似线性。

### Linux 实测（VPS）

Windows 的 46 个 skip 里有 42 个是「需要 bash/sh」的部署测试，在 Linux 上会
真正执行；真实数据回归也依赖本机 V1.01 目录。为了拿到可对比的两套数字，
把分支打成 bundle 传到 VPS（Python 3.12 venv + 真实数据目录），全量跑：

| 平台 | 结果 | 耗时 |
|---|---|---|
| Windows（本机） | **2093 passed / 46 skipped / 0 failed / 1 xfailed** | 250 s |
| Linux（VPS） | **2135 passed / 4 skipped / 0 failed / 1 xfailed** | 521 s |

差 42 项正是那 42 个部署 shell 测试 + 真实数据回归（Linux 上执行、Windows 上 skip）。
两个平台的 xfail 都只有 CM3 一项，0 failed。

首次在 Linux 跑时 `test_docs_sync` 报了「仓库没有任何 v* tag」——bundle 克隆默认
不打包 tag。补一次 `git fetch <bundle> "refs/tags/*:refs/tags/*"` 后通过，与代码无关。

### 前端（本轮改了 `SequencingPanel.vue` + `api/sequencing.ts`）

| 检查 | 结果 |
|---|---|
| `vitest run` | **693 passed**（90 files） |
| `vue-tsc --noEmit` | clean（exit 0） |
| `npm run build` | 通过（979 ms） |

### 验收标准逐条

| goal §6 | 结果 |
|---|---|
| 1. §4 全部场景通过，无 xfail/skip | **不完全**：CM3 保持 xfail（§7 停止条件，见 X4） |
| 2. X0 后未改场景构造/断言 | 满足（CM3 只改了 xfail 的 reason 文案） |
| 3. 双平台 0 failed、通过数只增不减 | 满足：Windows 2093、Linux 2135 |
| 4. 门禁全绿 | 满足（尺寸门禁已按所有者决定改为只提示） |
| 5. MX 确证 2 处 / fail 不变；队列明显下降 | 满足：330→3，2 处确证差异与 status=fail 逐字节不变 |
| 6. 真实子集队列 ≤15，3 变异全在 | 满足：14 项，3 处确证全在 |
| 7. 性能 ≤1.15× | 满足：**1.040×** |
| 8. 「构建与设计一致」只在降级分支 | 满足（仅 `evidence.py:617/621` 的无 verdict 分支） |

---

## 最终汇报（goal v3.1 §7）

### 一、X0–X7 完成情况

| 项 | 状态 | 提交 | 要点 |
|---|---|---|---|
| X0 真值与复现 | 完成 | `628e3b1` | 恢复 SC6/SC10/CM3 原构造；新增 14 个 §4 场景；71 passed / 17 xfailed |
| X1 峰形门控 | 完成 | `9a265df` `46d4277` | `FWHM/峰间距 > 1.0` 判退化（≥15bp）；MX 零误判 |
| X3 出口全文同源 | 完成 | `695b55f` | `verdict.headline` 唯一来源；`excel_conclusion` = 去符号派生 |
| X2 队列三级聚合 | 完成 | `aa48d79` | read→区段→位点；`minor_af_site` 只计数；结论 6 行预算 |
| X4 indel 规范化 | 完成（CM3 除外） | `473b6f0` | 左对齐 + HGVS 3′ 双坐标 + consequence；接合区互证豁免 |
| X5 范围记忆 | 完成 | `5275751` | `(owner_id, reference_hash)` 复合键；只记忆 user 来源 |
| X6 清理 | 完成 | `5b29575` | 删重复文档目录；`resolve_reference` 生产调用守卫 |
| X7 防回潮与文档 | 完成 | `aa6c304` `f866e1d` | KNOWN_ISSUES 四条约束；REPORT/CHANGELOG/HANDOVER；v15 |

顺序按 §5：X0 → X1 → X3 → X2 → X4 → X5 → X6 → X7。

### 二、§4 场景前后对比

旧输出取 `work/probe_v3_before_X1.txt`（b39b46a），新输出取当前分支
`docs/audit/probe_v3.py` 实测。

| ID | 旧输出 | 新输出 |
|---|---|---|
| SC6（峰宽×3.0，声明 GeneX） | `△ 疑似混合`，结论「建议重新挑单克隆复测」，blocking 15 项 `unresolved_site` | `△ 部分验证`，`scope_read_shortfall`，read 带 `degraded_ranges`，不进混合 |
| SC6b（×2.0 / ×2.5 / ×3.0） | 三档全部 `mixed`，双峰判混 6–7 处 | 三档 `partial`/`pending`，**都不判 mixed** |
| SC6b（未声明档） | `△ 疑似混合` | `✓ 合格 · 已测 …；范围外未评估`，headline 不含「混合」 |
| SC10（峰宽×3.0，尾端） | （旧探针未覆盖；W12 曾改成只降 Q） | `△ 待定` + `roi_unverified`，不判 mixed，pass/blocking 不并存 |
| CM3（跨原点双插入） | 2 处 insertion 检出 | **仍 xfail**——构造 off-by-one，见下文 |
| CM9（微同源 1bp 插入） | 未覆盖 | `type=insertion`，c. 按 3′ 规则，后果「移码」 |
| RQ1（真实子集未声明） | 队列 **330**，结论 **30 行** | 队列 **14**，结论 **6 行** |
| RQ2（真实子集全质粒） | 队列 **393** | 队列 **17** |
| RQ3（单 read 40 双峰） | 40 项位点 | **1** 项 read 问题 |
| OC3 / OC4 / OC5 | 三出口不一致（`✓ 结论：构建与设计一致` vs Excel `合格：与设计一致`） | 网页/Excel/卡片逐字同源；跨原点不自相矛盾；声明 pass 不主张整体一致 |
| HC1 | 接合区插入报 2 处 | 按位点去重报「1 处确证差异」 |
| HC2 | 范围写 `1–300、3701–4000` | `Cross（3701–300，跨原点）` |
| SM1 / SM2 / SM3 | 范围记忆跨用户串味、自动来源也记忆、无清除 | 按用户隔离；只记 user 来源；`remembered` + headline 注记 + DELETE 端点 |

**唯一未过项：CM3。** 它的 r2 读段构造
`ref[3299:4000] + ref[0:300] + ins2 + ref[301:450]` 跳过 `ref[300]`（G），
read 长度 1151 == 参考长度，序列上等价于 `301 G>T` 替换而非插入——场景要断言的
「两处 insertion」在数据里客观不存在。r1 是真插入（1152 bp）且已被 X4 正确检出。
修它必须改 X0 冻结的构造，属 goal v3.1 的停止条件，**待裁决**。我尝试过按
「1bp gap + 邻位替换改写为插入」处理，导致 34 个测试变红（S1 的 780 真实
missense 被改判插入），已整体回退——那个 `G>T` 确实是替换。

### 三、MX 与真实子集复核队列前后计数

| 数据集 | 旧口径 | 新口径 | 构成（新） |
|---|---|---|---|
| MX（真实交付批次） | **330 量级** | **3** | 仅 3 项确证差异；50 个噪声双峰只计数 |
| RQ1 真实子集·未声明 | **330** | **14** | confirmed 3 / read 2 / segment 1 / site 8（只计数 60） |
| RQ2 真实子集·全质粒 | **393** | **17** | confirmed 3 / read 2 / segment 2 / site 10（只计数 118） |

MX 的 `blocking` 明细仍是 262 `unresolved_site` + 55 `minor_af_site` + 48
`mixed_variant`（完整清单不删，只是不再全部进队列）；确证差异 2 处（5076 ins /
5269 C>T）、`status=fail`、三种 read 的双峰数 [15, 25, 35] **逐字节不变**。

### 四、峰形阈值来源与分布

判据：`FWHM ÷ 相邻峰间距 > 1.0`，连续 ≥15 bp 才算退化段。

物理含义：FWHM 达到峰间距时，相邻两峰在半高处无法分开——这是「测序未达」的
定义，不是拟合出来的数。峰间距是仪器固有量（实测 10–14 采样点），跨数据集可比，
与样品/信号强度无关。阈值文件 `params/peak_shape.json` 的 `_doc` 记录了推导。

| 数据集 | `width/spacing` 中位 | 判据 1.0 的结果 |
|---|---|---|
| 合成 SC6b ×2.0 | 2.00 | 全覆盖 [400, 901]（100%） |
| 合成 SC6b ×2.5 / ×3.0 | 4.83 / 5.00 | 全覆盖 [399, 901] |
| 合成 SC10（×3.0） | — | [1049, 1080]（read 尾 32bp，22%） |
| 真实 MX seqF / pET-Up / T7-Term | 0.58 / 0.62 / 0.62 | **零退化段**（p90 仅 1.54–1.67） |

在 0.85–1.3 区间内结论一致（合成全检出、MX 零误判）。§7 的「退化段 >30% read
范围」停止条件：MX 三条 read **0%**，合成退化场景 22–100%（构造本意就是退化）。

另需说明：初版判据是 `width/全 read 中位宽 > 1.6`，外加 10bp 外扩与 9 点平滑，
曾把 MX 的 10 个峰宽正常位点误杀、并虚构出 3 段连续退化区。撤掉这两个非物理
外挂（pad 归零、不做平滑）、换成分辨率倒数后 MX 才回到零退化段。初版曾因此
触发 §7 停止条件，撤掉后条件解除。

### 五、性能

同机同 venv，`analyze()` 纯计算，各跑 7 次取中位数（`docs/audit/bench_v31.py`）：

| 用例 | b39b46a | v3.1 | 比值 |
|---|---|---|---|
| synth（900bp 单 read + 2 替换） | 277.3 ms | 292.0 ms | 1.053× |
| long（1600bp 单 read + 1 替换） | 666.3 ms | 694.1 ms | 1.042× |
| real_mx（真实 3 条 read + 7039bp 参考） | 1348.3 ms | 1397.9 ms | 1.037× |
| **合计** | **2291.9 ms** | **2384.1 ms** | **1.040×** |

§6 第 7 条限 1.15×，达标。新增开销来自逐碱基峰宽（`signal.peak_widths` +
滑窗中位数），随 read 长度近似线性。真实场景含 tracy 外部进程时，这部分占比
会更低。

### 六、刻意未做的事项及原因

| 事项 | 原因 |
|---|---|
| **CM3 转绿** | 构造 off-by-one（读段跳过 `ref[300]`，等长即等价替换）。修它必须改 X0 冻结的构造 → goal 停止条件，按规矩停下汇报而非自行绕过。尝试过的「gap+邻位替换改写为插入」使 34 个测试变红，已回退 |
| **按文件行数强制拆分** | 所有者 2026-11-10 明确决定：推进期不做强制拆分，`check_file_size.py` 改为只提示，CI 不再阻断；大文件重构清单进 KNOWN_ISSUES「未解决」第 7 条 |
| **`analyze()` 增必填参数** | 全局硬约束只允许新增可选参数。X5 的 owner 由端点层传入，未动签名 |
| **非增量迁移 / 新依赖** | X5 的 owner 列走 alembic `batch_alter_table`（加列→回填→收窄），无新依赖 |
| **改判定口径以压 MX 队列到 0** | MX 的 3 项就是 3 处确证差异，压掉等于藏证据。§6 对 MX 的要求是「确证 2 处、fail 不变」，已满足 |
| **`minor_af_site` 入队** | 用户裁决方案 3：它是有明确丰度判据的结论，不是「读不清」的未决位点；入队会让 RQ1/RQ2 无法同时达标 |
| **打 tag / 升版本号** | 本轮是分支上的修复收口，发版由所有者决定 |
| **`resolve_reference` 删除** | 它定义了「CDS ± 侧翼」口径且有一组回归测试验证；改为保留函数 + 生产调用守卫（`tests/test_scope_mode_guard.py`） |
| **`test_real_ab1_subset.py` 头注释修正** | 门控落地后重跑，实际输出与注释一致（无 mixed 判定），无需改 |

### 测试数字（两平台）

| 平台 | 结果 | 耗时 |
|---|---|---|
| Windows（本机） | **2093 passed / 46 skipped / 0 failed / 1 xfailed** | 250 s |
| Linux（VPS，Python 3.12 + 真实数据） | **2135 passed / 4 skipped / 0 failed / 1 xfailed** | 521 s |
| 前端 vitest / vue-tsc / build | 693 passed / 0 error / 通过 | 19 s / — / 1 s |

两平台差 42 项 = Windows 上 skip 的 42 个部署 shell 测试（Linux 真跑）+ 真实数据
回归。场景库 67 个场景（88 个测试函数）两平台都是 87 passed + 1 xfailed（CM3）。

---

## 部署（2026-11-10，生产 VPS）

分支 `fix/sanger-scope-v3.1` 已推到 origin 并部署到 `/opt/plasmid-designer`。

### 部署中发现并修掉的真 bug：迁移没改主键

原迁移只 `add_column` + `batch.alter_column(nullable=False)`，`copy_from` 里的
复合主键只在 SQLite 重建表时生效；**PostgreSQL 上走原生 ALTER，主键仍是单列
`reference_hash`**。在真实备份副本上实测：

```
INSERT ... ON CONFLICT (owner_id, reference_hash) DO UPDATE ...
→ ERROR: there is no unique or exclusion constraint matching the ON CONFLICT specification
```

即 SM1（按用户隔离）在生产上根本不会生效——两个用户对同一质粒写范围记忆会
互相覆盖。单元测试没抓到，是因为 `test_migrations.py` 只跑 SQLite 并比对
autogenerate 差异，不覆盖「PG 上主键是否真的换了」。

修法（`e6180df`…`f1ec184` 共 6 个提交，全是同一处迁移的迭代）：

1. 显式 `drop_constraint` + `create_primary_key`（PG），SQLite 仍走 batch 重建；
2. 主键名按 `pg_constraint` 目录查真名——PG 在原名被占用时会自动改名成
   `..._pkey1`，硬编码让第二次 upgrade 报 UndefinedObject；
3. `text()` 里 `:t::regclass` 的 `::` 会被当参数名分隔（`KeyError: 't'`），改 `CAST(:t AS regclass)`；
4. 降级里 `updated_at` 是 datetime 且可 NULL，不能与空串混比；
5. 降级必须显式重建单列主键——否则表变成无主键，再升级无从下手；
6. 两侧都改成原生命令 + 按方言分支：`batch_alter_table` 进入即反射旧主键，
   与后续换主键/删列打架（实测 `column owner_id is in a primary key`）。

### 部署步骤与实测结果

1. 备份：`pg_dump` → 53 MB，存 `/root/pd-backup/pre-v31-20261110-101426.sql`
2. 演练：把备份灌进临时 PG，`stamp a1b2c3d4e5f6` → `upgrade head`，跑
   **3 轮 up→down→up** 全部通过，主键在 `reference_hash` 与
   `(owner_id, reference_hash)` 间正确来回切，数据行数不变
3. 生产迁移：`stamp a1b2c3d4e5f6` → `upgrade head`，主键变为复合键
4. 构建 + `up -d --force-recreate backend frontend`
5. 验证：
   - 后端横幅 `🧬 Plasmid Designer API v2.37.0-alpha.4`，`/health` = 200，`db:"ok"`
   - 容器内 `SANGER_ALGO_VERSION = v15`，四个新模块均已加载
   - **真实 MX 数据端到端**：`status=fail`、确证 [5076, 5269]、队列 3、结论 7 行、
     双峰 [35,15,25]、零退化段——与本地逐项一致
   - **跨用户隔离实测**：A 存 `[[1,200]]`、B 存 `[[9,9]]` 各自读回；清除 A 不影响 B
   - 新端点 `DELETE /api/sequencing/scope-memory`：带 hash 200、无参 422
   - 前端容器 healthy，首页 200

### 部署路径已写进 HANDOVER

`.env` 里 `MAIL_FROM` 等值带空格与尖括号，`source .env` 会语法错——改用
`docker compose config --format json` 取解析后的值；临时容器用 `--network host`
并把 `@db:` 换 `@127.0.0.1:`（`--link` 对已存在的旧容器不可靠）。完整命令见
HANDOVER §5「数据库迁移」。
