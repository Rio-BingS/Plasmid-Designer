# 密码子优化与合成 oligo 算法路线图

> **状态（2026-08）**：「已实现」项均在主流程生效；「暂缓项」为数据/依赖门槛
> 较高的可选增强——**短期无实施计划，按需启动**。启动任何一项前请先阅读
> 对应的「门槛与建议方案」。

## 已实现（主流程）

| 策略 | 位置 | 依据 |
|---|---|---|
| translational ramp：5' 前 20 密码子使用中等频率密码子 | `core/codon_optimizer.py` | Verma 2019 (Nat Commun)；Tuller 2013 (Mol Syst Biol) |
| 5' 端 mRNA 发夹削减（60nt 窗口发夹计数 + 同义替换削弱） | `core/codon_optimizer.py` | Mauro 2014 (PMCID: PMC4253638) |
| 隐蔽调控 motif 审查：polyA 信号 AATAAA/ATTAAA、TATA box、细菌 SD 序列 AGGAGG、poly-T（按宿主分类） | `core/codon_optimizer.py`（CENSOR_MOTIFS） | GenScript / Synbio Technologies 设计指南 |
| GC 效率平滑（单位 CAI 损失换取 GC 调整量，单点贪心） | `core/codon_optimizer.py` | — |
| 变窗多参数精修（滑窗枚举同义变体，窗口 CAI + GC 贴近度择优，跳过 5' 区） | `core/codon_optimizer.py`（_sliding_window_refinement） | GeneOptimizer，Raab 2010 (PMCID: PMC2955205) |
| 合成 oligo 错位交替排列（无完全互补对、片数恒偶） | `core/primer_designer.py` | — |
| DNAWorks 式 Tm 均一化分片（边界微调使相邻片 Tm 差最小，实测极差 ~1.1°C） | `core/primer_designer.py`（_tm_homogenize_boundaries） | Hoover & Lubkowski 2002 (NAR, e43) |
| 交叉杂交审查（3' 端 12mer 与其他 oligo 互补匹配计数，检出告警） | `core/primer_designer.py`（cross_hybridization_count） | Thachuk & Condon 2007 (BIBE) |
| 综合评分 score (0-100)：CAI 45% + GC 25% + 5' 结构 20% + motif 10% | `core/codon_optimizer.py`（_optimization_score） | — |
| 排除限制酶位点（密码子优化 avoid_motifs 避让，缓存键纳入） | `app/design_service.py` | — |

## 暂缓项（短期不做，按需启动）

### 1. CodonTransformer / 深度学习优化后端

- **价值**：164 物种训练的 BigBird transformer，生成「自然样」密码子分布，
  多项基准表现 SOTA（Fallahpour 2025，PMCID: PMC11968976）
- **门槛**：torch 级重依赖，与本项目轻量部署路线（HF Spaces 免编译）冲突
- **建议方案**：作为可选依赖接入（`pip install codontransformer`），提供
  species→model 的后端接口，未安装时自动降级到现有 v2 确定性算法；
  也可参考同类产品把模型封装为独立微服务

### 2. Codon harmonization / harmony index（GenSmart 专利核心路线）

- **价值**：沿基因匹配宿主 tRNA 丰度曲线（局部翻译速度协调），
  减少共翻译折叠冲突；GenSmart™ 专利 WO2020024917A1 的多目标之一
- **门槛**：需要宿主 **tRNA 基因丰度**或分区密码子用法数据——现有
  `data/codon_tables/*.yaml` 只有单一密码子频率表
- **建议方案**：扩展 YAML 格式支持 tRNA 丰度列（或引入 tRNAscan-SE 扫描结果）；
  优化目标从「全局最大 CAI」改为「局部 ramp 拟合 + harmony 打分」；
  起步可用滑动窗口 CAI 平滑作为 harmonization 的零数据近似

### 3. 完整 mRNA 折叠评估（MFE）

- **价值**：全序列最小自由能精确评估（当前仅 5' 窗口发夹计数近似）
- **门槛**：ViennaRNA Python 绑定，或自实现 Nussinov/Zuker（工作量大）
- **建议方案**：可选 ViennaRNA 绑定，仅用于最终评分与报告展示，
  不进入迭代热路径；未安装时沿用现有近似

### 4. 密码子对偏好（CPB）/ 密码子上下文（CC）

- **价值**：2025 对比综述（J Microbiol Biotechnol, PMCID: PMC12010093）
  强调的多准则框架成员；GenSmart 声称已整合 codon context
- **门槛**：需要物种级 **codon pair 统计表**（现有数据不含）
- **建议方案**：数据格式同 2；评分用负曼哈顿距离并入综合 score；
  综述观察到 CC 与 CAI 中度正相关，可作为 CAI 之外的独立维度

### 5. 合成 oligo 交叉杂交完全规避

- **价值**：当前只检测告警；Thachuk & Condon 2007 给出 DP 形式化
  （最小 oligo 数 + Tm 均一 + 交叉杂交最小化），DFS 2022 实现了
  <1°C 偏差的搜索剪枝
- **门槛**：回溯/DP 搜索的实现与耗时中等
- **建议方案**：在 `_tm_homogenize_boundaries` 的边界移动评分中
  加入交叉杂交惩罚项，将「告警」升级为「主动规避」

### 6. 密码子使用数据源更新

- 2025 综述指出 HIVE-CUT、KAZUSA 等经典数据源更新滞后；
  `data/codon_tables/*.yaml`（Ecoli K12 / Human / CHO / Yeast）
  可对照 HIVE-CUT 最新版重新导出校准，并考虑补充高频密码子
  「基因组级 vs 高表达基因级」两套频率（综述显示两者 CAI 差异显著）

## 参考资料

- GenSmart™ 专利：WO2020024917A1（多目标遗传算法：harmony index + CAI + mRNA 稳定性）
- OptimumGene™：PSO 粒子群（GenScript）
- GeneOptimizer：Raab 2010, Bioinformatics（PMCID: PMC2955205）——滑窗多参数
- DNAWorks：Hoover & Lubkowski 2002, NAR 30:e43——Tm 均一性评分；GitHub: davidhoover/DNAWorks
- DNA Chisel：Edinburgh Genome Foundry, Bioinformatics 2020——spec 组合优化框架（MIT）
- Thachuk & Condon 2007, IEEE BIBE——oligo 设计 DP 形式化
- CodonTransformer：Fallahpour 2025（PMCID: PMC11968976）；GitHub: adibvafa/CodonTransformer
- 对比综述：J Microbiol Biotechnol 2025（PMCID: PMC12010093）——10 工具多准则对比
- Mauro 2014（PMCID: PMC4253638）——治疗性基因密码子优化的批判性分析
- Verma 2019（Nat Commun）——translational ramp
- Twist 白皮书——LLM harmonization 基准；ATUM GeneGPS——实验数据驱动 ML
