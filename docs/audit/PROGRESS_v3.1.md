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
