# 密码子优化与合成 oligo 算法路线图

> **状态（截至 v2.9.0）**：「已实现」项均在主流程生效；「暂缓项」为数据/依赖门槛
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
| DNAWorks 式 Tm 均一化分片（v2.8.0 起均一化的是相邻片**重叠双链**的 Tm，边界位置与重叠长度 18-25nt 共同作为自由变量；1.2 kb 基因实测极差中位数 1.8-2.6°C，代价是多 2-4 条 oligo） | `core/primer_designer.py`（_tm_homogenize_boundaries） | Hoover & Lubkowski 2002 (NAR, e43) |
| 交叉杂交审查（3' 端 12mer 与其他 oligo 互补匹配计数，检出告警） | `core/primer_designer.py`（cross_hybridization_count） | Thachuk & Condon 2007 (BIBE) |
| 综合评分 score (0-100)：CAI 45% + GC 25% + 5' 结构 20% + motif 10% | `core/codon_optimizer.py`（_optimization_score） | — |
| 排除限制酶位点（密码子优化 avoid_motifs 避让，缓存键纳入） | `app/design_service.py` | — |
| 引物 Tm 最近邻法（SantaLucia DNA_NN4 + 盐校正，v2.8.0 收敛为单一实现） | `core/primer_designer.py`（Bio.SeqUtils.MeltingTemp.Tm_NN） | SantaLucia 1998 (PNAS) |
| 限制酶参数统一取自 Bio.Restriction（REBASE），v2.8.0 起为唯一来源 | `core/enzyme_sites.py` | REBASE |
| Golden Gate overhang 连接保真度筛查（v2.8.0） | `core/primer_designer.py`（screen_gg_overhang_pair） | Potapov 2018 (ACS Synth Biol) |

## 暂缓项（短期不做，按需启动）

### 1. CodonTransformer / 深度学习优化后端

- **价值**：164 物种训练的 BigBird transformer，生成「自然样」密码子分布，
  多项基准表现 SOTA（Fallahpour 2025，PMCID: PMC11968976）
- **门槛**：torch 级重依赖，与本项目轻量部署路线（1 核 2G VPS、依赖全部为预编译 wheel）冲突
- **建议方案**：作为可选依赖接入（`pip install codontransformer`），提供
  species→model 的后端接口，未安装时自动降级到现有 v2 确定性算法；
  也可参考同类产品把模型封装为独立微服务
- **核查（2026-10）**：包仍为 Apache-2.0，最新版 1.6.7（2025-04 发布，此后无新版）；
  模型 89.6M 参数 F32（权重约 360MB），依赖链含 torch / transformers /
  pytorch_lightning / scikit-learn。1 核 2G VPS 上与后端同机常驻不可行；
  上游 pyproject 已带 onnxruntime，真要落地应走 ONNX 导出 + 独立微服务

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
- **核查（2026-10）**：PyPI `viennarna` 有预编译 wheel、无运行时依赖，
  上游活跃（2.7.x，2025-12 仍有发布）；但许可为**自定义非标准条款**——
  允许研究/教育/商业使用与修改，但禁止收费再分发、嵌入商业产品需联系作者，
  与本仓 MIT 不兼容。因此只能作为用户自行安装的可选依赖，
  不写进 `requirements.txt` 与镜像

### 4. 密码子对偏好（CPB）/ 密码子上下文（CC）

- **价值**：2025 对比综述（J Microbiol Biotechnol, PMCID: PMC12010093）
  强调的多准则框架成员；GenSmart 声称已整合 codon context
- **门槛**：需要物种级 **codon pair 统计表**（现有数据不含）
- **建议方案**：数据格式同 2；评分用负曼哈顿距离并入综合 score；
  综述观察到 CC 与 CAI 中度正相关，可作为 CAI 之外的独立维度

### 5. 合成 oligo 交叉杂交完全规避

- **现状（v2.8.0 后）**：仍只检测告警；`_tm_homogenize_boundaries` 已在 v2.8.0 重写为
  「边界 + 重叠长度」坐标下降，惩罚项要加在这里（接口未变，建议方案依然成立）
- **价值**：当前只检测告警；Thachuk & Condon 2007 给出 DP 形式化
  （最小 oligo 数 + Tm 均一 + 交叉杂交最小化），DFS 2022 实现了
  <1°C 偏差的搜索剪枝
- **门槛**：回溯/DP 搜索的实现与耗时中等
- **建议方案**：在 `_tm_homogenize_boundaries` 的边界移动评分中
  加入交叉杂交惩罚项，将「告警」升级为「主动规避」

### 6. 密码子使用数据源更新

- 现状：`data/codon_tables/*.yaml`（Ecoli K12 / Human / CHO / Yeast）已于 2026-10
  按 Kazusa 原始 per-thousand 数据重建（终审 A-04，文件内 `data_provenance` 记录来源）
- 更正（2026-10 核查）：真正滞后的是 **Kazusa**——数据仍停在 GenBank release 160
  (2007-06)，许多物种条目 CDS 数过少（BMC Bioinformatics 2017, PMCID: PMC5581930）；
  FDA/HIVE 的 **HIVE-CUTs（CoCoPUTs 系列）持续更新**，应作为校准基准
  （https://dnahive.fda.gov/dna.cgi?cmd=cuts_main）
- 2026-02 新增 **Cell Lines CoCoPUTs**（J Mol Biol, PMID 41707750），覆盖
  HPA/CCLE/COSMIC 细胞系的密码子与密码子对频率——对本项目的 CHO / 人源表达场景
  比物种级表更贴近实际
- 可对照 CoCoPUTs 重新导出校准，并考虑补充「基因组级 vs 高表达基因级」两套频率
  （综述显示两者 CAI 差异显著）

## 优先级评估（2026-10）

评估口径：部署目标为 1 核 2G VPS（见 `docs/KNOWN_ISSUES.md`「部署备忘」，瓶颈是 CPU），
依赖要求为预编译 wheel + 许可可与本仓 MIT 共存。工作量 S ≈ 1 天内，M ≈ 2-4 天，
L ≈ 1 周以上。

| # | 项 | 用户价值 | 工作量（触达） | 依赖 / 许可 | 运行时风险 | 测试策略 |
|---|---|---|---|---|---|---|
| 5 | oligo 交叉杂交主动规避 | 高：直接影响全基因合成组装成功率，当前只给告警、用户无法自行消解 | **S-M**：`core/primer_designer.py`（`_tm_homogenize_boundaries` + `cross_hybridization_count`）、`app/design_service.py` 告警文案、`tests/test_primer_designer.py` | 无新依赖 | 中：v2.8.0 的坐标下降已同时搜索边界与重叠长度，再内嵌交叉杂交评分会进一步放大搜索开销，需对 12mer 建索引并缓存，必要时设迭代上限 | 构造已知冲突序列断言计数降为 0；保留现有重叠 Tm 极差 ≤5°C 与「片数恒偶、无完全互补对」回归断言 |
| 6 | 密码子使用数据源更新 | 高：CAI/评分的输入质量，影响全部优化结果；Kazusa 数据停在 2007 | **S**：`data/codon_tables/*.yaml`、`tests/test_codon_tables_data.py`、导出脚本 | 仅数据（CoCoPUTs / HIVE-CUTs 公开下载）；需在 `data_provenance` 注明来源与抓取日期 | 低：纯数据替换，无运行时开销 | 频率和归一化校验（每个氨基酸家族和为 1）、与现表的差异报告、`tests/test_real_data_regression.py` 固定序列 CAI 基线更新 |
| 3 | 完整 mRNA 折叠（MFE） | 中-高：把 5' 发夹计数近似升级为全序列能量，报告可信度明显提升 | **M**：新增 `core/rna_fold.py` 可选封装、`core/codon_optimizer.py::_optimization_score`、报告/导出层 | ViennaRNA（自定义许可，禁收费再分发）→ 只能可选安装，不进 `requirements.txt` | 中：O(n³) 时间 / O(n²) 内存，1-2 kb 量级在单核上约亚秒到数秒、内存十 MB 级可接受；必须只在终评调用一次，禁止进迭代热路径，并对超长序列设长度上限 | 未安装 ViennaRNA 时走降级分支的用例（CI 默认环境）；安装后对已知序列断言 MFE 落在参考区间 |
| 4 | 密码子对偏好 CPB / 上下文 CC | 中：独立于 CAI 的评分维度，但对普通用户是「分数多一项」，不改变主序列质量感知 | **M**：新增 `data/codon_pairs/<species>.yaml`（3721 对/物种）、`core/codon_optimizer.py`（`_window_score` / `_optimization_score`）、新测试 | 仅数据（CoCoPUTs 提供 codon pair 表） | 中：滑窗精修已是 CPU 热点，窗口评分再加一项需保持 O(1) 查表，评分权重重标定会牵动既有基线 | 人工构造高/低 CPS 序列断言排序正确；综合 score 权重变更需同步刷新回归基线 |
| 2 | Codon harmonization / harmony index | 中：对难表达蛋白（共翻译折叠敏感）有价值，但收益需湿实验才能验证，离线无法证伪 | **L**：`data/codon_tables` schema 扩列 + tRNA GCN 导入、`core/codon_optimizer.py` 优化目标重写（`_initial_optimization` / `_iterative_optimization` / 评分） | GtRNAdb（Release 22, 2024-09）tRNA 基因拷贝数，免费可下载 | 中-高：目标函数从「最大 CAI」改为「曲线拟合」，会推翻现有全部分数基线与缓存键语义 | 先做零数据近似（滑窗 CAI 平滑）并以「局部速度曲线方差下降」为可测指标；全量回归 + 缓存失效验证 |
| 1 | CodonTransformer / 深度学习后端 | 中：论文基准 SOTA，但评估为 in silico，且用户拿到的是「黑盒序列」，与本项目可解释评分路线冲突 | **L**：新增后端适配层 + 降级逻辑；若走微服务还需部署/运维 | torch + transformers（Apache-2.0，许可无碍），模型 89.6M 参数约 360MB | **高**：1 核 2G VPS 无法与后端同机常驻；即使 ONNX 单条推理也占满单核数秒，批量任务串行会直接拖垮现有排队 | 只能对适配层做 mock 单测 + 「未安装自动降级」用例；模型本身无法在 CI 内跑 |

### 推荐顺序

1. **#5 交叉杂交主动规避（S-M）**——唯一「纯本地实现、无依赖、直接提升产出可用性」的项，
   把现有告警变成用户不必自己处理的结果。
2. **#6 数据源更新（S）**——成本最低、收益面最广；Kazusa 停更在 2007 是已证实的事实，
   换 CoCoPUTs 能同时为 #4 准备好同源数据。
3. **#3 MFE（M）**——技术上最成熟，受限只在许可，按可选依赖做即可；放在 #6 之后
   以免评分基线连续变动两次。
4. **#4 CPB/CC（M）**——数据随 #6 一并取得后再做，边际成本最低，但属锦上添花。
5. **#2 harmonization（L）**——先只做零数据近似版本验证方向，确认有效再引入 tRNA 数据。
6. **#1 深度学习后端（L）**——与当前部署目标硬冲突，继续挂起；若确有需求，
   走独立 GPU/大内存微服务，而非塞进本服务依赖。

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
