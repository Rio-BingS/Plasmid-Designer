# goal_sanger_scope_v3 执行进度（W0–W16）

> 依据：`docs/audit/goal_sanger_scope_v3.md`（§7 顺序执行，§8 默认决策）。
> 本文件按阶段闸门累积汇报；双平台测试数字每次记录（Linux=VPS ~/pd-v3，
> Windows=本机 .venv）。约定：pytest 须在 `tests/` 下带
> `SECRET_KEY=test-secret-key-for-pytest-0123456789abcdef` 运行。

## W0 真值先行（commit 64dcee4）

- §4 全部 24 项场景入册 `tests/sanger_scenarios.py`（SC1-11/CM1-8/AF1-2/PR1-3/OC1-2）；
  30 项 xfail(strict) + PR3/AF2 直接真值锁定；OC 场景经 status_symbol/status_excel_word
  三出口契约表达。
- v2 真值与 v3 冲突处预重编码（S8/S13/M9/M12/pipeline×3/rereview×2/batch×1），
  逐处留痕 goal §8 决策 1；W0 基线 Windows 1988 passed / 13 failed（全部为
  v3 待落地项）/ 46 skipped / 30 xfailed。
- MX 旧口径基线快照 `work/baseline_before_W0.json`（gitignored）。

## W1 scope 入参与纯函数化（commit 8ddae0f）

- 新建 `core/sanger/scope.py`：Scope dataclass（ranges/source/declared/…）、
  parse_scope_cell、coerce_scope、junction_ranges、measured 兜底。
- `build_verdict(result, ref_length, scope=None)` 纯函数化；`analyze(..., scope=None)`
  keyword-only 新参（goal 硬约束：只加可选参数）。
- 三出口契约测试 V3_CONTRACT_XFAIL 清空（W1 接线后 9 例 TypeError 全部转一致）。

## W2 去掉外包框与 whole 豁免（本次提交）

- verdict：未声明范围 → scope=measured ranges（source=measured, declared=false）；
  范围外确证差异 → out_of_scope_variant（info，不阻断）；关键元件未声明范围按
  「完整落入实测才负责」包含闸，范围外 not_assessed（SC1）；覆盖闸只对声明范围
  （roi_uncovered→partial / roi_unverified→pending，if/elif 先 uncovered）。
- 三出口措辞（goal §0.1）：未声明范围 ✓ 一律「已测 X–Y 与设计一致；范围外未评估」，
  Excel「合格：已测…」，禁整参考主张「构建与设计一致」（声明范围路径的措辞
  随 W5 三段式重构）。
- v2 真值重编码留痕：truth.yaml S8/M9/M12（+W0 的 S13）→ pass；rereview
  P0-1 重编码（1-900 声明→pending/roi_unverified（端部软剪切）、1-2200→partial/
  roi_uncovered）；batch/sequencing 集成断言改「合格：已测」口径。
- `SANGER_ALGO_VERSION` v8→v9（HANDOVER §2 登记）。
- MX 回归（真实数据 7039bp，实测 5012–6931 未声明）：status 仍 fail（3 确证差异
  在范围内）；blocking 变化 critical_element_unverified 7→0（ori×2/KanR/lacI/T7
  promoter/lac operator/T7 terminator 均在实测窗外 → not_assessed，范围内 RBS/
  6xHis×2 verified 100% → info）、roi_uncovered 1→0（未声明范围不再按整参考
  判缺口）——与 goal §3.4/§8 决策 1 预期完全一致，无回归。
- 测试数字：Windows 全量 **2002 passed / 0 failed / 46 skipped / 29 xfailed**；
  Linux（VPS，W0 树 64dcee4 基线）2021 passed / 16 failed / 10 skipped / 30 xfailed
  （16 失败=W1/W2 待落地契约分叉，W2 推送后 VPS 重跑回填）。
