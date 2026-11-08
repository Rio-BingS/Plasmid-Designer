# Sanger 判读校准报告（goal T23；A 5.6）

基准集：`tests/data/sanger_bench/`（35 项合成场景 + 3 个 Biopython 真实
AB1，真值见 [truth.yaml](../tests/data/sanger_bench/truth.yaml)）；评测脚本
`scripts/sanger_benchmark.py`（CI 断言在 `tests/test_sanger_benchmark.py`，
不 skip）。本文档记录 2026-11-07（P2 闸门时点）的指标、拟合结果与阈值依据。

## 1. 基准指标（scripts/sanger_benchmark.py 实测）

### verdict 混淆矩阵（33 项经 analyze 的场景，行=真值，列=实际；T24 后实测）

| 真值 \ 实际 | fail | failed_reads | mixed | partial | pass | pending |
|---|---|---|---|---|---|---|
| fail（12） | 12 | | | | | |
| failed_reads（1） | | 1 | | | | |
| mixed（4） | | | 4 | | | |
| partial（1） | | | | 1 | | |
| pass（5） | | | | | 5 | |
| pending（10） | | | | | | 10 |

**33/33 全对角**（S/M 场景全对）。raw 场景 M8（abif_reader 峰窗）、
M11（会话预检）不经 analyze，不参与矩阵。T24.1（PLOC 取 0 基峰心原值）
后：F3 由 failed_reads 转 pending（无 PCON 按峰型估 Q 恢复分析）、
F1/F2/M12 由 pending 转 pass（滞后主峰的染料 blob 肩峰占比实测下降，
贴 0.30 地板的伪影条目不再检出）。

### 位点级敏感度/特异度

- 敏感度 **1.0000**（TP=14 / FN=0）：真值位点全部检出（同位同型且非 rejected）；
- 特异度 **1.0000**（TN=29378 / FP=0）：覆盖区内无任何真值外的确证变异
  （TN 较 T23 的 28935 增加：F3 恢复分析后纳入覆盖计算）。
- CI 阈值定为 ≥0.95 / ≥0.99（truth.yaml `thresholds`）：留出真实数据替换
  合成场景后的正常波动空间；当时实测 1.0/1.0。

### 混合判定 TPR/FPR（status=mixed 为「判混合」）

- TPR **1.0000**（真值混合 S2/S3/S4/S14 全部判「疑似混合」）；
- FPR **0.0000**（染料肩峰 F1/F2、poly 滑移 S13/S17、衰减段 M7、PLOC 截短
  M12 一律不升级——2026-11-06 定稿口径：个别双峰位点不升级「疑似混合」；
  T24.1 后 F1/F2/M12 更进一步连待核位点都不产生）。
- CI 阈值：FPR = 0（硬约束，F1/F2 是本链路的历史病根）。

## 2. p_real 阈值扫描 ROC 与形态说明

真值位点变体（正例 14）vs 非真值位点变体（负例：M3 分歧降级 ×2、
M10 互检排除 ×2 等）的 p_real 分布呈**双峰**：确证档聚在 0.99+（锚点
0.993+）、未决档聚在 0.005（锚点 -5.3），中间几乎无样本——ROC 曲线在
τ∈[0.01,0.91] 区间为平线（TPR 0.643 = 9/14，其中 2 条插入变异与 3 条
未决位点的低 p_real 拉低分母）。两点结论：

1. p_real 是**证据质量分**：它的判别力体现在两极分明的分档（confirmed
   ≥0.5 与 unresolved <0.5 之间无重叠），不适合作为唯一裁决线；
2. 位置级上下文（重叠分歧、互检排除）按设计**覆盖**证据分——M3 的两条
   降级变体与 M10 的两条 rejected 变体 p_real 都很高，最终裁决由 T21
   分歧与互检链路给出。这正是 T15「hard tier + 层内修正」的结构意图。

## 3. T15 评分系数真拟合（scripts/fit_variant_score.py --benchmark）

真值行 12 条（真变体 9：S1@780、S7@180、S13@950、S15@140、S17@800、
M1@104/106、M2@300、M13@780；构造伪影 3：S4@450、S9@20、S13@15——
后三者证据特征本身很强（mp 93-97、snr 104-109），判未决靠的是**可信区
位置特征**，不是证据不足）。对 [截距, (Q-40)/10, (mp-80)/20, (snr-10)/40]
做纯 Python logistic 梯度下降（4000 轮）：

| 方案 | 真值行 AUC |
|---|---|
| 现行 params/variant_score.json（hard tier 含位置特征） | **1.0000** |
| 证据-only logistic 真拟合 | 0.8148 |

**结论：保持现行系数，不采纳拟合结果。** 证据-only 拟合在真值行上反而
劣化（区外伪影的证据特征与真变体几乎不可分，判别力来自位置/可信区
特征——它们在 hard tier 里，不进层内 logistic）。现有手设系数已与规则
标签逐位等价（27440 网格点一致率 1.0000，`--check` 可复验）且在真值行
上完全分离；小语料（12 行）拟合无增益、有过拟合风险。语料扩充后
（尤其带设计参考的真实数据）重跑同一脚本复核。

## 4. T11 双峰模型参数拟合（scripts/fit_mixed_params.py）

对 MixedParams 核心参数网格（ratio_floor × adaptive_k）在混合语料
（S2/S3/S4/S14 + 伪影 F1/F2/S13/S17/M7/M12）上以 FPR=0 硬约束择优。

**2026-11-08 复审 R3 修订**：T23 的网格下限是 0.20，从未覆盖 0.15/0.18——
而 af = ratio/(1+ratio)（mixed_call 口径），旧地板 0.30 卡的是 **AF 0.231**，
MINOR_AF_MIN=0.15 对应比值 0.176，AF 15-22% 带是死代码（CDS 内静默 ✓，
复审指出的 P0-3）。补测低地板（FPR=0 硬约束下）：

| 参数组 | status TPR | status FPR | 位点检出率 |
|---|---|---|---|
| 旧现行（ratio_floor=0.30, adaptive_k=4.0） | 1.0 | 0.0 | 1.0 |
| **新现行（ratio_floor=0.15, adaptive_k=4.0）** | 1.0 | 0.0 | 1.0 |
| 0.15 / 0.176 / 0.18 × k∈{3.0, 4.0} 全部 | 1.0 | 0.0 | 1.0 |

**结论：0.15 在 FPR=0 约束下与 0.30 等价，取最敏感值。** 噪声仍由自适应
MAD 项（med + k·mad_scale·MAD）抬升，地板只管干净区低带。语义归一：

- 检测带（af ≥ 0.130）进表；verdict 端 af < MINOR_AF_MIN(0.15) 只记
  info（info 分支自此有真实输入），af ≥ 0.15 且在判定范围（CDS/关键
  元件/接合区）→ mixed_variant 升级（goal T7 设计意图恢复）；
- 互检端（crosscheck）同步 af < 0.15 不进未决位点；
- 散在双峰（af ≥ 0.15、范围外、单 read 无法互检）仍按 2026-11-06 定稿
  走 unresolved_site △待定——基准集 F1/F2 由此 pass→partial→pending
  （3730：4 处 af 0.155-0.193；3100：7 处 af 0.153-0.208，见 truth.yaml
  行注释）；MX 真实批次快照（work/after_R3.json vs after_T25.json）
  **逐字段零 diff**——真实数据在自适应阈值下无 0.15-0.30 比值带的
  边界检出，旧地板在本语料上一直是冗余闸门。

## 5. 复验方式

```
.venv/Scripts/python.exe scripts/sanger_benchmark.py            # 指标报告
.venv/Scripts/python.exe scripts/fit_variant_score.py --check   # 规则等价率
.venv/Scripts/python.exe scripts/fit_variant_score.py --benchmark
.venv/Scripts/python.exe scripts/fit_mixed_params.py
cd tests && python -m pytest test_sanger_benchmark.py -q        # CI 断言
```

真实 AB1（3730/3100/310）无设计参考，位点级真值不可用（F1/F2/F3 场景
以 read 自身 basecall 拼参考，只参与混合 FPR）；真实数据带设计参考的
扩充项见 KNOWN_ISSUES「真实数据基线」。
