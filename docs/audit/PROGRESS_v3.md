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
- Linux 回填（VPS，W2 树 1b5dfd4，2026-11-09）：**2035 passed / 3 failed /
  10 skipped / 29 xfailed**。3 failed = test_plasmid_map_annotate 3 项性能
  断言（1.33s/1.44s>1.0s 预算等），**W0 基线日志中同样存在**——VPS CPU
  慢于预算的预存平台差异，与 sanger 无关。
- Linux 回填（VPS，阶段 1 树 332579d，2026-11-09）：**2044 passed / 3 failed
  （同上 map_annotate 预存）/ 10 skipped / 21 xfailed**——xfailed 21 与
  Windows 一致，双平台同步确认。

## W6 理论覆盖 E 与缺口归因（PL §B1/§B2）

- `coverage.expected_ranges`：每条 read 的理论窗口 = 比对 5′ 锚沿测序方向
  按原始 basecall 长度（raw_bases/raw_length）延伸、首端 front_gray=30 不
  构成理论覆盖；basis="read_start"（引物结合位点 basis 随引物映射接入），
  负链从 ref_end 端向低坐标延伸。随 result["coverage"] 下发（W13 画理论窗）。
- `_coverage_gaps` 新增 expected/scope_ranges 参数：声明范围时缺口只保留
  R 内部分（跨原点缺口逐段求交）；cause 归因——理论窗够不到 →
  beyond_primer、可达而未达 → read_shortfall（detail 带「F.ab1 在 400 处
  衰减（理论可达 1071）」式衰减位置）、E 不可得 → unknown。既有跨原点
  合并逻辑原样保留。
- verdict：`roi_uncovered` 拆 `scope_beyond_primer`（action 加引物）/
  `scope_read_shortfall`（action 看峰图/重测）；E 不可得或无 ≥100bp 缺口
  可归因 → `scope_uncovered` 兜底；R 内实测 0% → `scope_uncovered`。
  batch/evidence 的 insufficient 分支 kind 表同步扩充。
- 双链要求（SC11）：`scope.require_double_strand` 生效 →
  `scope_single_strand` 阻断（策略性 partial，不依赖声明范围）；全局
  REQUIRE_DOUBLE_STRAND 默认仍关。
- SC10/SC6 构造重建（峰形退化 tail_width 会在段内造出强次峰判 mixed，
  偏离「纯 verified 缺口/纯测序未达」场面）→ 均改 Q 衰减（SC10 尾端
  Q=24、SC6 400bp 后 Q=15）；SC5/SC11/SC6 摘 xfail（SC6 经 W14 提前
  接线的 build_queue，见下）。

## W7 引物逐 read 对应（PL §B3/§B4）

- `check_reads` 三形态入参：None → 全 unverified；单引物 dict（键
  name/seq/direction）→ 所有 read 同引物（旧入参兼容）；{read 文件名:
  引物} 映射（键含非引物属性键即判映射）→ 逐 read 校验，映射未覆盖的
  read 记 unverified。
- **引物无信号**（PL B4）：映射里提供了引物但没有对应 read → status=
  no_signal；verdict 出 `scope_read_shortfall` 阻断（「引物 X 无对应
  read——引物无信号」），策略性 partial（PR2 真值）。
- 负链距离口径修正（PR1 构造暴露）：负链 read 的 5′ 锚是 ref_end，距离
  统一按「5′ 锚 − 引物 3′ 端」计；旧式 (three_end − read_start) 把负链
  锚在结合区远端出负距离，正常 read 误判 mismatch。
- 批量路径：match_files/group_rows 归组时按信息表引物列回填
  read→引物名（`primer_name_for`：精确 > 分隔符后缀 > 整段包含，唯一
  最长命中才回填）；`run_batch_groups` 经引物库（data/primers/*.yaml）
  解析序列后以映射传 analyze，库缺失时该 read 不进映射（unverified 口径
  不变）。
- SC9 直接重算修复：analyze 结果载荷补 `features` 键——build_verdict 以
  result 直接重算 scope（元件名解析）不再依赖调用方另传注释（W11 重算
  端点同源依赖）；PR1/SC7/SC9/PR2 摘 xfail。
- 复核队列最小接线（W14 提前）：新增 `core/sanger/review.py::build_queue`
  ——pending 级阻断条目入队并带 evidence（缺口带 E 归因与理论窗；双峰带
  af；位点带 reads）；W14 扩展一键确认留痕与批量页汇总。

## 【闸门 2】阶段 2 收口（SC5/SC6/SC11、PR1–PR3）

- 场景：SC5（beyond_primer）、SC6（read_shortfall+复核队列+evidence）、
  SC11（单链归因 partial）、PR1（逐 read 映射 ok）、PR2（引物无信号
  partial）、PR3（距离出窗 fail 保持 T17 口径）全绿。
- 测试数字（W6/W7 全落地树）：Windows 全量 **2018 passed / 0 failed /
  46 skipped / 14 xfailed**（xfailed 21→14：SC5/SC11/SC6/SC9/PR1/PR2
  摘除）；Linux（VPS）W3-W5 树 2044 passed / 3 failed（map_annotate
  预存）/ 21 xfailed——W6/W7 推送后重跑回填。
- W6/W7 期间修复的构造级残留（不计真值改动）：v2 旧 T17 负链构造
  `ref_end = 3′端−20` 是「read 不含引物区」语义，与 W7 口径（负链
  read 以引物区开头、5′ 锚 ref_end ≥ 3′ 端）相反——重编码并补负链
  出窗反例，留痕于测试注释。
- `SANGER_ALGO_VERSION` v10→v11（HANDOVER §2 登记）。


## W3 verified 口径与 pass 契约（RV P0-2）

- `build_verdict` 末尾新增真断言：status==pass ⇒ blocking==[]（契约违反
  直接抛 AssertionError 列出冲突 kind）；契约测试 `test_pass_never_carries_
  blocking` 对全部 26 个经 analyze 场景扫零（与断言双保险）。
- SC10 构造复核（alpha.3 覆盖条目逐条做实）：原 tail_width=3.0 尾端退化
  会在段内造出密集次峰触发 widespread 判 mixed，偏离 §4「纯 verified 缺口」
  场面——改为尾端 50bp Q=24（高于修剪阈值不缩 measured、低于可信窗均值
  TRUST_MEAN_Q=25 使 verified 止于 ~1289）；断言改按 ROI 分数（measured_
  fraction==1.0、0.98≤verified_fraction<1.0），W3「统一 verified 口径」落地。
- 连带修复（SC10 构造暴露的 W2 漏网）：未决位点循环补范围闸——范围外
  未决位点（如声明 301-1299 时 1301+ 的双峰）不再误入 blocking 驱 pending，
  记 out_of_scope_unresolved info；ref_pos 缺失无法归因的保留旧行为。

## W4 混合低 AF（RV P0-3；alpha.3 复核后偏离 W4 原案）

- **floor 不取 0.20、取 0.18 对齐**：运行时真值在 params/mixed_default.json
  （R3 实测标定 0.18，F1/F2 真值钉死 0.153+ 检出带；0.20 会让 af 0.1525-0.167
  重新变死代码）。W4 原案消灭的是「dataclass 死默认 0.30」——已将
  `MixedParams.ratio_floor` 缺省 0.30→0.18 与 JSON 对齐，并留注说明。
- `MINOR_AF_MIN=0.15` 真正生效（不再死代码）：verdict af 分带——功能区
  （CDS/关键元件/接合区）内 **AF≥0.23 → mixed_variant（mixed）**；
  **0.15–0.23 → minor_af_site（pending 复核项，入 PENDING_KINDS）**；
  骨架散在双峰一律 info（2026-11-06 定稿不变）。
- 新增软检出带（soft_ratio_floor=0.13，af≈0.115 起）：模型地板 0.18 之下
  的双峰以 soft 标记出表，**不进** mixed_positions/互检分母/widespread 分级
  （FPR 口径不变，MX 真实批次计数基线逐字不变）；verdict 在**声明范围×
  功能区**将 af 0.115-0.15 出 minor_af_site pending——AF1 的 0.12 档不再
  静默。未声明范围软带只记 info（噪声带不进复核）。
- AF1 四档全绿（pending+留痕）、rereview P0-3 重编码（ratio 0.22→AF 0.18 →
  pending 不判混）、AF2 真值锁定不变。
- FPR 复测（3 个真实 AB1：3730/3100/310）：mixed TPR 1.0（4 项）、
  **FPR 0.0（误判 []）**；verdict 混淆矩阵 35 项全对角（14/1/4/4/12）；
  位点级敏感度 1.0 / 特异度 1.0（fp=0）。

## W5 element_verdicts 与 headline（PL §A5/§A6）

- verdict 新增 `element_verdicts`：CDS/关键元件/接合区各一条，字段
  name/type/start/end/status（ok|issue|not_assessed）/coverage（verified %）/
  double_strand/issues（相交 blocking 数）/in_scope。范围闸与关键元件闸
  同款：声明范围相交才判、未声明范围须完整落入实测（SC1：AmpR/ori
  not_assessed）。
- verdict 新增 `headline` 三段式「符号 状态 · 范围 · 证据」：声明范围
  「范围：GeneX（301–900）」；未声明范围「已测 x–y；范围外未评估」
  （goal §0.1 禁整参考主张）。W12 起三出口统一只读 headline（本阶段
  随 verdict 下发）。
- 同义突变可配置（§8 决策 3）：`analyze(..., synonymous_action="ok")`
  默认合格口径——CDS 内确证同义替换（蛋白一致）出 synonymous_variant
  info 不阻断，Excel 合格注明「X 处同义突变（蛋白不变）」；"fail" 沿旧
  口径按确证差异阻断（双向锁定测试入册）。
- SC1/SC3/SC4 摘 xfail 转绿。

## 【闸门 1】阶段 1 收口（SC1-4/7-10、AF1-2、对照组）

- 场景：SC1/SC2*（W10 范围来源，构造预留）/SC3/SC4/SC7-SC10、AF1/AF2 全绿；
  对照组 benchmark verdict 混淆矩阵 35 项全对角、mixed FPR 0.0 不变红。
- 测试数字（W3/W4/W5 全落地树）：
  - Windows 全量：**2011 passed / 0 failed / 46 skipped / 21 xfailed**
    （xfailed 29→21：SC1/SC3/SC4/SC10/AF1×4 摘除）；
  - Linux（VPS）：W2 树 2035 passed / 3 failed（map_annotate 预存）——
    W3-W5 推送后重跑回填。
- `SANGER_ALGO_VERSION` v9→v10（HANDOVER §2 登记）。

