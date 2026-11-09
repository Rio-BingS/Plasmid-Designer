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
  摘除）；Linux（VPS，60d9741）**2052 passed / 2 failed（map_annotate
  性能预存，W0 基线同现）/ 10 skipped / 14 xfailed**——xfailed 与
  Windows 一致，双平台同步确认。
- W6/W7 期间修复的构造级残留（不计真值改动）：v2 旧 T17 负链构造
  `ref_end = 3′端−20` 是「read 不含引物区」语义，与 W7 口径（负链
  read 以引物区开头、5′ 锚 ref_end ≥ 3′ 端）相反——重编码并补负链
  出窗反例，留痕于测试注释。
- `SANGER_ALGO_VERSION` v10→v11（HANDOVER §2 登记）。


## W8 目的基因模式不再裁参考（CM P0-A/P0-B/P1-C/P1-F）

- `cds_extract.prepare_scope_mode`（W8 目标路径）：cds_mode 不再替换参考
  ——全质粒按真实拓扑比对，`scope = 目标 CDS + 接合区`
  （source=cds_target，declared），**全部注释原样保留**（P1-F 的
  「窗口内其他注释丢失」与「参考未注释关键元件」噪音随之消失）。
- **接合区入需测范围但不进头行区间**：Scope 新增 `junctions` 字段（CDS
  两端 ±30，跨原点按折回形态给出后归并）；verdict 覆盖分数/缺口/变异
  归因按 `merged_ranges()`（ranges ∪ junctions）判，头行与区间渲染只用
  ranges——cds_target 路径与「全质粒 + scope」结论逐字同源（CM5），
  接合区变异仍判 junction_variant（CM3）。
- 覆盖缺口归因阈值独立：`_coverage_gaps(scope_min_len=20)`——接合区两侧
  ~30bp 的未测缺口在整参考 100bp 阈值下永远拿不到 cause（会落
  scope_uncovered 兜底），W8 起声明范围内的缺口按 20bp 归因。
- **只有质粒超 MAX_REF_BP 才裁剪**：侧翼 = max(1500, 最长 read+200)；
  窗口 = 「CDS 首段 5′ 侧翼起点 → 末段 3′ 侧翼终点」的环状跨原点切片
  （跨原点 CDS 天然连续化、近原点 CDS 两侧侧翼完整，一个映射机制统一
  P0-B/P1-C）；features 平移进窗口坐标（窗口内保留、目标 CDS 加
  analysis_target），scope 同步平移，circular=False。
- `analyze(..., full_ref=None, full_circular=False)` 新可选参数（goal 硬
  约束内）：裁剪路径端部未比对高质量段在窗口上无落点时按完整质粒
  split-read 再定位——能连续定位 = 读取越过窗口边缘的正常延续，记
  `located_outside`（verdict 记 info「窗口外已定位」），未比对部分全部
  定位成功时不判 partial_alignment。
- resolve_auto/显式选择逻辑抽 `_resolve_candidate` 共用；resolve_reference
  （v2 裁参考路径）保留为对照实现，生产路径不再调用。
- 顺带修复：`_run_full_analysis` 解卷积重建 verdict 时漏传 scope（W2 期
  缺口，decompose+scope 并存时范围语义丢失）。
- test_real_cds_mode_mx 重编码：等价性从「变体集合一致」扩展到「结论
  一致」（verdict.status / headline / blocking kind 集合与手搭同范围
  scope 逐字一致）；MX 实测 CDS+接合区 verified 100%，junction 窗口内
  未决位点（首端 5075-5085 等）随接合区归 junction_variant。
- CM1-CM5 摘 xfail；CM3 构造重编码留痕：初版两条 read 各带一处插入，
  同位点被对方 read 干净覆盖互检排除，与「同一质粒的插入应出现在每条
  覆盖 read」的真实场面矛盾——改为同一变体质粒正反两条 read；1bp 插入
  在微同源边界可被比对器等价表示为紧邻替换（alt=插入碱基），断言按
  truth 粒度校准为「接合区窗口内确证差异检出 + junction_variant + fail」。
- 端点测试重编码：cds_mode 语义断言从「裁剪参考/伪特征」改为「全质粒
  /全注释保留/scope=cds_target/junctions 下发」。
- 测试数字（W8 全落地树）：见闸门 3 回填（W8/W9 合并记录）。


## W9 自动匹配与双坐标（CM P1-D/P2-G/P1-F）

- `resolve_auto` 歧义判别（CM P2-G）：参考名命中与 read 名命中**指向不同
  候选**时判歧义不猜——旧实现参考名唯一即静默采用（GFP_backbone ×
  mCherry_F 探针静默选错）；一方命中集合为另一方的子集（收敛到同一
  候选）不算冲突。依据写明在返回说明里。
- `resolve_explicit`（CM P1-F）：显式选择在**未去重**的 CDS/gene 分组中
  查找——嵌套特征（6xHis、融合蛋白报告基因）可选；重名（双拷贝）报错
  不静默取第一个。`_resolve_candidate` 显式分支接线，路由 400 语义不变。
- 变体新增 `coords`（CM P1-D）：`{"plasmid": 质粒坐标, "cds": {name, c,
  p?}}`——c. 按编码方向（负链由 coding_offset 处理，CM8：质粒 1100 →
  GeneX c.501）；aa_change 可导出时并列 p.。W8 全质粒口径下坐标即质粒
  坐标，P1-D「质粒 1300 报成 450」随之消失。
- `resolved_regions` 的 cds_offset 仅存显示文本助手（inner_label），判定
  不消费 offset（W8 无参考替换后该机制自然退役）；`cds_extract.py` 重复
  `import re` 已在 W8 重写时删除。
- CM6/CM7/CM8 摘 xfail（剩余 xfail 6 个：SC2→W10、OC1/OC2→W12）。

## 【闸门 3】阶段 3 收口（CM1–CM8 绿 + MX 回归解释）

- 场景：CM1-CM8 全绿（长 read 超窗 pass / 通用引物 read pass / 跨原点
  接合区插入 junction_variant fail / 近原点与 scope 一致 / cds_mode 与
  scope 同源 / 名称歧义 / 嵌套显式选择 / 负链双坐标）。
- **MX 真实回归差异解释**：全质粒未声明口径逐字不变（W8/W9 只加
  coords 字段与 cds 路径行为；变体集合/结论断言全对角）。cds_target
  路径（test_real_cds_mode_mx）：status=fail 与旧裁参考路径语义一致
  （确证差异 5076 插入/5269 替换在范围内），headline「✗ 不合格 · 范围：
  MX（5075–6877） · 证据：4 处确证差异」；blocking 构成变化 = 接合区
  窗口内未决位点（首端 5075-5085、尾端 6868-6873）归 junction_variant、
  旧「参考未注释关键元件」噪音消失（全注释保留）；与手搭同范围 scope
  逐字一致（等价断言锁定）；verified 覆盖 CDS+接合区 100%（真实三条
  read 越过接合区窗口）。
- 测试数字（W8/W9 全落地树）：
  - Windows 全量（W8 树，v12）：**2028 passed / 0 failed / 46 skipped /
    9 xfailed**；W9 收口后 CM6-8 摘除（xfailed 9→6）。
  - Linux（VPS）：W6/W7 树 60d9741 = 2052 passed / 2 failed
    （map_annotate 性能预存）/ 14 xfailed；**W8-W10 树 79cb901 重跑 =
    2080 passed / 0 failed / 10 skipped / 5 xfailed**（457s）——上一轮
    的 map_annotate 2 项预存失败本轮通过（性能断言负载敏感，与 W0 基线
    同源现象），双平台 0 failed 确认。
- `SANGER_ALGO_VERSION` v12→v13（W9 变更自动选择行为与变体载荷；HANDOVER
  §2 登记）。


## W10 scope 来源与输入（PL §C1/§C2；RV P1-7 部分）

- `infer_scope(ref_stem, read_names, features, insert_range, ref_length)`
  （scope.py）：中间两级来源推断——design_insert（插入边界 + 两端 ±30
  junctions）> filename_match（复用 resolve_auto，唯一命中 CDS → 其主段
  +接合区，reason 带命中方式）；都取不到 → None（调用方退回未声明
  measured）。user 由调用方显式给出不走这里；cds_target 由目的基因模式
  直接构造（既有路径）。
- parse-reference 预设（RV P1-7）：返回 `scope_presets`（全质粒 + 各 CDS
  候选，附 reason）与 `default_preset`（auto_match 命中 → 该 CDS，否则
  全质粒）——前端下拉的数据源。
- Form scope（W1 parse_scope_cell 接线端点）：`/sequencing/analyze` 新增
  `scope: str = Form(default="")`；解析失败 400 带可用元件名提示；user
  scope 存在时 cds_mode 跳过（显式声明优先于自动模式）。
- 设计流 design_insert：`analyze_design_sequencing` 由 result.insert_start/
  insert_end 构造 design_insert scope（插入=全长时不声明——整条都是目标，
  无接合区可言）；非全长时插入边界 + ±30 junctions。
- 批量「验证范围」列（PL §C2 批量出口）：parse_excel 读「验证范围」列；
  match_files 组内首个非空回填 g["scope_text"]；run_batch_groups 逐组
  parse_scope_cell——成功传 scope=scope_obj，失败回填
  「范围无法识别：xxx（原因）。」前缀、不阻塞其他样品（解析失败不抛异常
  的契约在此兑现）。
- SC2（filename_match → pass）摘 xfail：场景走
  infer_scope("pTarget-GeneX", reads, features) 推断后传入 analyze，
  status=pass 且 headline 带「范围：GeneX」。
- 新增单测：test_sanger_scope.py 7 个（infer_scope 两级来源/优先级/无命中
  None + parse_scope_cell 多段/关键字/失败不抛）；test_cds_mode.py 3 个
  端点测试（Form scope user source/解析失败 400/scope_presets）；
  test_batch_sequencing.py 2 个（Excel 列解析+组传播 / run_batch_groups
  e2e：声明成功 source=user + 胡乱写的范围回填「范围无法识别」）。
- 影响面回归（Windows）：test_sanger_scenarios + test_batch_sequencing +
  test_sequencing_batch + test_sequencing_routes = 164 passed / 5 xfailed；
  test_cds_mode 28 passed；test_sanger_scope 7 passed。


## W11 重算端点与范围记忆（PL §C6/§C7）

- `POST /api/sequencing/analyses/{id}/verdict`（请求体 `{scope}`）：从存档
  result 按 build_verdict（W1 纯函数）重算并落库——**不重跑比对**（测试
  打桩 analyze 计数锁零调用）；scope 解析复用 coerce_scope（元件名/区间/
  关键字，失败 400 带可用写法提示），空 scope 显式退回未声明（measured）
  口径。鉴权与 GET analyses 同（属主 / X-Access-Token，匿名令牌口径 B-02）。
- 结论头行按新 verdict.headline 重建（W5 三出口同源；正文保留）；
  scope_history（payload 内键，旧记录无此键天然兼容）追加
  {时间, 用户, 旧 scope, 新 scope}。
- 范围记忆（PL §C7）：新表 `sequencing_scope_memory`（参考序列 sha256
  指纹 → 最近一次**声明过**的 scope dict；measured 不记忆）。分析落库
  （_register_analysis）时写入；单样品端点（无 Form scope 且非 cds_mode）
  与批量（「验证范围」列留空时）按指纹带出——source 保留原来源、
  reason=「沿用上次范围」。重算端点对声明的范围覆盖记忆。
- 数据库增量迁移：alembic 新修订 a1b2c3d4e5f6（纯新增表，无列变更）；
  MIGRATIONS.md 增设「修订历史」段登记（baseline + 本修订）。
- 新增 5 测（test_cds_mode.py W11 段，scope 记忆打桩为进程内 dict 不依赖
  sqlite）：SC9 端点路径三 scope 重算快照一致+零比对、conclusion 头行
  重建+落库回读、400、旧记录兼容（无 verdict/scope_history 键）、记忆
  回路与复用带出。


## 【闸门 4】阶段 4 收口（W10+W11）

- SC9 走端点路径变绿（test_recompute_verdict_endpoint_sc9：同一份存档
  依次 TARGET/全质粒/未声明，verdict 与直接 build_verdict 快照一致，
  analyze 打桩计数 0）。
- scope 输入解析单测全绿（test_sanger_scope.py 7 个：infer_scope 两级
  来源/优先级/无命中 + parse_scope_cell 多段/关键字/失败不抛）。
- 历史记录兼容（test_recompute_legacy_record_compat：旧 payload 无
  verdict/scope_history 键重算正常，history 从 0 追加）。
- Windows 全量（W11 树）：**2049 passed / 0 failed / 46 skipped /
  5 xfailed**（2044 + W11 新增 5 测；algo_version 不变——W11 只增重算
  入口与记忆，不改分析口径）。Linux（VPS，79cb901 含 W10）= 2080 passed /
  0 failed / 10 skipped / 5 xfailed（闸门3 段已记）；W11 推送后重跑回填。


## W12 三出口统一（PL §E1/§E4/§E6；RV 遗留）

- 单一真相源（OC1/OC2 的根治）：`verdict.py` 新增模块级
  `STATUS_SYMBOLS`/`STATUS_EXCEL_WORDS` 与导出函数
  `status_symbol(status)`（✓/✗/△，✓✗ 之外一律 △）、
  `status_excel_word(status)`（合格/不合格/待定/疑似混合/无法判定/
  待定·读取失败）——网页头行、Excel 结论前缀、前端卡片全部由
  verdict.status 经同一映射导出，不再各自维护词表（CM 审核三出口
  分叉的根因）。headline 的中间状态词（部分验证等）为头行专属措辞。
- `excel_conclusion` 16 处分支前缀词改走映射（有 verdict 时 `_w` 出词、
  旧记录无 verdict 保持原词——降级分支口径不变）；结论句删除
  「；测序覆盖 x%」尾巴（goal W12「移到明细」——覆盖信息由信息表新列
  「范围内验证覆盖」与整理包明细报告承载），连带同步 4 处测试断言。
- evidence `_conclusion_summary` 头行符号：verdict 存在时由
  status_symbol 导出（8 处分支），旧记录/直调保持字面量。
- annotator 四处「低置信度（…疑似伪影…）」→「未决（…）」口径（RV）：
  互检矛盾/poly 滑移/low_reason/默认支——去掉「疑似伪影」预判措辞，
  机器标记 confidence=low 不变（前端折叠分组 R6 起由 kind 驱动）。
- Excel 新列（goal W12）：回填信息表追加 验证范围 / 范围来源 /
  范围内验证覆盖 / 缺口原因 / 范围外差异 / 补测建议 六列
  （`backfill_excel` 新 records 参数；无 verdict 旧记录留空不编造；
  列头幂等复用）；离线脚本 `run_to_dir` 返回 records 并同样回填——
  网页 ZIP 与离线产物逐字节对照保持一致（test_offline_cli_matches_
  web_endpoint_package 锁定）。
- 前端 `ConclusionCards.vue`：删除按文案分组的旧回退（「低置信（」等
  字符串匹配），只消费后端 conclusion_groups 结构化分组，无键时正文
  整段常驻；折叠钮/待复核徽标话术同步「未决」口径。
- OC1（SC3/SC5/SC6/S1 四场景）与 OC2（跨原点 CM3）摘 xfail 全绿；
  新增映射单测 2 个（全集覆盖/词与既有分支一致 + verdict 驱动出词）。
- 影响面：test_sanger_scenarios 72 passed、test_batch_sequencing 51
  passed、test_sanger_pipeline + scenarios 237 passed。


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

