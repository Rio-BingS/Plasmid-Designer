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
