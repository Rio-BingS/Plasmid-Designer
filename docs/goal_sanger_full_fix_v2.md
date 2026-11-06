# GOAL：Sanger 测序判读链路全量修复 v2（合并两份审计，P0 + P1 + P2，共 25 项）

> 本文档合并了两份独立审计：
> - **审计 A**：《Plasmid-Designer Sanger 测序全链路深度审计报告》（基于 main `bd5dff4`, v2.36.0），下文记作 `A x.y`。
> - **审计 B**：仓库内 `docs/SANGER_AUDIT_2026-11-06.md`，下文记作 `B P0-1`、`B P1-8` 等。
>
> **基线分支**：`experiment/preview-visual-tuning` @ `c33e81e`（2026-11-06 23:28，版本号仍为 v2.37.0-alpha.1），比 main 多 13 个提交。与测序相关的有两批：① 2026-11-06 的「结论重构为区间裁决行」（`99624b5` 后端：互检已裁决差异按「源 read × 位置段」聚合为区间裁决行，pipeline payload 新增结构化 `resolved_regions`，双峰提示瘦身，目的基因模式 ✓ 头行改为「目的基因测序正确」；`bfeb07e` 前端：结论头行独立渲染、高亮裁决行）；② 2026-11-05 的「目的基因模式」：新增 `core/sanger/cds_extract.py`（CDS ± 150bp 侧翼作参考）、`_conclusion_summary` 的目标 CDS 零覆盖资格门、`_build_cds_reports` 的 `analysis_target` 标记、`sequencing_endpoints` 的目的基因模式入口。本修复在该分支上新开工作分支进行（如 `fix/sanger-verdict`），不直接改 main。
>
> 两份审计的行号来自不同版本的代码，**行号只作线索，一律以函数名定位**。T0 第一步是在当前 HEAD 上逐条复核全部发现，不成立的划掉并记录原因（参考 `docs/postmortems/2026-10-31-audit-round3-fixes.md`：上一轮自报 15 项中有 4 项不成立）。

---

## 0. 目标

两份审计指向同一个病根：**证据模型是二元的（确认 / 疑似伪影），缺少第三态“未决”**。N 调用、可信区外差异、低 Q、混合峰、poly 不可判定、dropout、解析失败的文件，都被压成“低置信=伪影”或“未测=按设计对待”，✓ 照发；“已测”口径又不扣可信区、不算坏文件。于是“验证过了 / 没读清 / 根本没测”三种状态得到同一个绿色结论，人工只能全量复核。

完成后应满足：

1. **不出假 ✓ / 假合格**：判定区域内只要有未决证据（未决差异、N、坏文件、未覆盖、互检不足），结论就不是 ✓ / 合格。
2. **口径如实**：覆盖分“已测（measured）”与“已验证（verified = 已测 ∩ 可信区 − N 列）”两种口径，结论、CDS full 判定、批量闸门全部用 verified。
3. **证据标准统一**：变异互检与混合互检同一标准；多 read 证据按全部 read 聚合；坐标换算只有一套实现。
4. **误报可控**：双峰、可信区、SNR、置信度由每条 read 自身的信号统计决定，阈值可配置并经真实数据 ROC 标定。
5. **出口同源**：网页摘要、API 下发、批量 Excel、整理包分拣、Markdown/PDF 报告、前端折叠和峰图配色，全部读同一个结构化 `verdict`；文案只做渲染。
6. **人工只看必须看的**：每个待处理项都有 `P(真实)`、明确行动（看峰图 / 补测 / 重挑克隆）和多 read 峰图证据包。
7. **有回归基准**：仓库内有公开真实 AB1 + 真值 + 合成场景，CI 跑敏感度/特异度门槛；本地私有真实数据（MX 批次）回归按“先取证、再改断言”的纪律更新。

---

## 1. 开工前必读

1. `AGENTS.md`：命令、CI 门禁、Git 工作流、依赖锁、尺寸预算。
2. `docs/HANDOVER.md`：真实数据回归纪律（改变口径时先按旧口径跑一遍取证，再改断言）、缓存/版本常量登记处。
3. `docs/KNOWN_ISSUES.md`：「防回潮约束」全部条目（共识投票“明确碱基优先于 N”、`query_start/query_end` 口径等），“真实数据基线”章节。
4. `docs/postmortems/` 最近几篇，尤其 `2026-11-05-cds-mode-coverage-gate.md`（侧翼是正确性前提；目标零覆盖资格门）与 `2026-10-31-audit-round3-fixes.md`。
5. `docs/ALGORITHM_ROADMAP.md`：已有算法规划，避免冲突；完成后同步更新。
6. 两份审计原文：审计 B 已在仓库 `docs/SANGER_AUDIT_2026-11-06.md`；审计 A 的 PDF 放到 `docs/audit/Sanger审计报告_A.pdf`。另读 `docs/SANGER_ALGORITHM_REPORT.md`（现有算法能力说明）与 `docs/NAMING.md`（命名速查，新模块与字段命名遵循它）。
7. 主要代码：
   - 后端核心：`src/backend/core/sanger/{abif_reader,signal,aligner,pipeline,homopolymer,reliable_zone,annotator,batch,cds_extract,reference_parser}.py`
   - 接入层：`src/backend/app/{sequencing_batch,sequencing_report,sequencing_store}.py`、`src/backend/app/routes/{sequencing_routes,sequencing_endpoints,sequencing_session_routes}.py`
   - 前端：`src/frontend/src/api/sequencing.ts`、`components/SequencingPanel.vue`、`components/sequencing/{ConclusionCards,VariantTable,ReadTable,AlleleCard}.vue`、`composables/useSeqViz.ts`
   - 测试：`tests/abif_utils.py`、`tests/test_sanger_*.py`、`tests/test_sequencing_*.py`、`tests/test_real_data_regression.py`、`tests/test_real_cds_mode_mx*`

---

## 2. 全局硬约束

- **测试基线**：HANDOVER 记录的是 2026-11-05 的后端 1850 passed / 43 skipped、前端 686 passed；`99624b5`/`bfeb07e` 改过测试，数字可能已变。T0 先复跑，以复跑结果为基线写进复核文档。
- **已知门禁问题（开工前先处理）**：在 `c33e81e` 上 Linux 预演 `python scripts/check_file_size.py` 报 `pipeline.py: 2964 行 > 预算 2934 行`。T0 先在本机复现：若确实超预算，第一个提交就是把 pipeline.py 中一块内聚逻辑（建议区间裁决行的构建，`_build_conclusion` 中生成 `resolved_regions` 的部分）迁出到新模块使其回到预算内，不得用 `--refresh` 调高预算；若本机不报，记录差异原因（行尾/统计口径）。
- **尺寸预算**：`scripts/size_budgets.json` 白名单文件（`core/sanger/pipeline.py`、`app/routes/sequencing_routes.py`、`composables/useSeqViz.ts` 等）只许变小；新文件 ≤900 行。新逻辑放新模块，并把 pipeline.py 中被替换的逻辑迁出，每次迁出后下调预算数字。
  建议新模块：`core/sanger/{verdict,coverage,readmap,mixed_call,mixed_model,zone_signal,noise,evidence,scoring,primer_check,orf_guard,overlap,artifacts,evidence_pack,trace_align,split_read}.py`。
- **向后兼容**：旧分析记录没有新字段，读取/重放/前端展示必须正常（`tests/test_sequencing_rehydrate.py` 保持绿）。新字段全部可选，前端缺字段时降级为旧展示。
- **API 契约**：不改路径、不改已有字段语义，只新增字段；改返回结构必须同步 `src/frontend/src/api/sequencing.ts`，并在 `sequencing_routes._summary` 的下发白名单中显式加入。
- **判定不依赖文案**：禁止用字符串匹配做逻辑（如 `"疑似混合样品" in x`、`startswith("合格")`、前端 `includes('低置信（')`、`startsWith('差异区间') && includes('无需核对')`）。
- **依赖**：不新增 Python 运行时依赖（可用 biopython 自带的 numpy；不引 scipy/sklearn/matplotlib）。tracy 为镜像中可选外部二进制，缺失时必须降级并在结果中标注 `engine`。确需新增依赖先停下汇报。
- **结果口径版本**：新增 `SANGER_ALGO_VERSION`（若已有类似常量则沿用），写入每次分析结果 `result["algo_version"]`；任何改变结论口径的提交都要递增，并在 HANDOVER 的版本常量处登记。
- **对照组不得变红**：反向 read 替换高置信检出；跨原点 read（circular=True）无假缺失；300bp 缺失 / 150bp 插入（两侧 flank ≥450bp）高置信检出；100% 纯合突变 ✗；CDS 模式与全质粒模式在 MX 数据上判定一致（`test_real_cds_mode_mx`）；B 审计 §4 列出的“已核对无问题”的机制（种子延伸比对、环状折回、通道映射、基线校正、run length 估计、`jumpToVariant` 主链路、分块会话状态机）行为不变。
- **私有真实数据回归**：`test_real_data_regression.py` 依赖仓库外的 MX 数据，在有数据的机器上必须跑。改变口径前先按旧口径跑一遍保存输出（`work/baseline_before_Txx.json`，不入库），改完逐项对比，断言变化必须逐条解释。
- **不发版**：不升版本号、不打 tag。提交与推送遵循 AGENTS.md 的 git-commit-style 授权。

---

## 3. 统一数据结构

```text
Variant（已有，新增字段）
  status: "confirmed" | "rejected" | "unresolved"
  p_real: float | null                  # T15 之后有值
  action: "accept" | "exclude" | "review_trace" | "resequence"
  evidence_by_read: [EvidenceItem]      # T14
  covering_reads_not_supporting: [{read, read_pos, q}]
  reason_codes: [str]                   # 机器可读，如 "front_hard_zone", "tail_zone", "low_snr", "n_call", "mixed_bg_high"

EvidenceItem
  read, read_pos, strand, q, mutant_pct, snr, mixed_ratio, mixed_z, in_zone, called_base

UnresolvedSite（T1）
  ref_pos, kind: "n_call" | "iupac" | "out_of_zone_diff" | "poly_undetermined" | "dropout" | "mixed_uncorroborated" | "corroboration_conflict"
  reads[], detail

MixedVariant（T7）
  ref_pos, ref_base, alleles, af, reads[], ratio, z_score

Coverage（T3）
  measured_ranges, verified_ranges, measured_percent, verified_percent,
  verified_bp, n_columns: [ref_pos...]

Verdict（T2）
  status: "pass" | "fail" | "pending" | "mixed" | "partial" | "insufficient" | "failed_reads"
  blocking: [{kind, ref_start, ref_end, element, detail, action, refs: [id...]}]
  info: [同结构]
  roi: {start, end, source, verified_fraction, measured_fraction, double_strand_fraction}
  reads: {total, analyzed, failed: [{file, error}]}
  algo_version: str

ReadRefMap（T9）
  read_to_ref(i) / ref_to_read(p) / ref_range_to_read_window(a, b) / primer_coord(i)
```

`blocking.kind` 枚举：`confirmed_variant`、`unresolved_variant`、`unresolved_site`、`n_in_cds`、`mixed_variant`、`mixed_sample`、`unaligned_hq_segment`、`structural_breakpoint`、`critical_element_variant`、`junction_variant`、`roi_uncovered`、`roi_unverified`、`poly_unreliable`、`dropout`、`partial_alignment`、`read_failed`、`primer_mismatch`、`orf_annotation_suspect`、`overlap_discordance`、`allele_mismatch`。

---

## 4. 场景基准（T0 建立，全程使用）

### 4.1 假 ✓ / 假合格场景（漏报）

| ID | 来源 | 场景 | 现状 | 最终期望 |
|---|---|---|---|---|
| S1 | A 1.1 | 单 read，原始第 780bp 错义替换，Q50、突变峰 100%、SNR 高 | ✓；共识写参考碱基 | P0 后 △；P1（T12）后 ✗ 确证 |
| S2 | A 3.2 | CDS 内单点 35% 混合 | ✓ | △ 疑似混合（AF≈0.35） |
| S3 | A 3.2 | 同上 50:50 | ✓ | △ 疑似混合（AF≈0.5） |
| S4 | A 3.2 | 同上 65% 突变为主峰 | ✓（低置信伪影） | △，不得 ✓ |
| S5 | A 2.1 | read 5′ 端 175bp 高质量与设计不同 | ✓ | ✗ unaligned_hq_segment（P2 后命中别处则 structural_breakpoint） |
| S6 | A 2.1 | read 3′ 端 188bp 不同 | ✓ | 同 S5 |
| S7 | A 4.1 | CMV promoter 内高置信替换 | 网页 ✗；Excel 合格；归档“正确” | 三处一致：不合格 |
| S8 | A 4.2 | 唯一 CDS 仅覆盖 34.5% | ✓；合格 | 部分验证，不得合格 |
| S9 | B P0-1 | 单 read，变异落在首端 30bp 硬区，Q40、峰证据齐全 | ✓（1 处疑似伪影未计入） | △ 未决差异，需核对峰图/补测 |
| S10 | B P0-2 | read 中段 1 个 N（Q40） | variants=[]、identity=1.0、✓ | △，列出 N 位点；verified 覆盖扣除该列 |
| S11 | B P0-2 | CDS 内错义突变位点被调用为 N | ✓ 蛋白一致 | △ n_in_cds，蛋白判定为“待定” |
| S12 | B P0-3 | 3 条 read，2 条解析失败，剩 1 条覆盖 30% 无差异 | 合格；整理包分进“正确/” | △ read_failed，点名失败文件；不进“正确/” |
| S13 | B P0-4 | 差异全部位于首端硬区与尾端 >900bp，覆盖率靠不可信碱基凑满 | coverage 虚高；CDS full；批量闸门放行 | verified 口径下 partial 或 roi_unverified |
| S14 | B P0-5 | read1 6 位点 45% 次级通道；read2 同区纯主克隆 Q25 | 6/6 clean → ✓ 合格 | △ 疑似混合（互检 read 未达 `_region_ok` 标准 → 不算 clean） |
| S15 | B P1-11 | CDS 模式，载体-基因接合区 ±30bp 内非 low 差异 | 被“疑似伪影”吸收 | △ junction_variant（confirmed 时 ✗） |
| S16 | B P2-20 | poly 后两个相距 20+ 碱基的弱峰被连成滑移序列，吞掉真实混合双峰 | 混合位点被剔除 | 混合位点保留并检出 |
| S17 | B P1-7 | 两条 read 都在尾端灰区报同一变异 | high、✗，无注记 | 仍可 ✗，但 reason_codes 含 `all_evidence_out_of_zone`，网页明示 |

### 4.2 误报场景

| ID | 来源 | 场景 | 现状 | 最终期望 |
|---|---|---|---|---|
| F1 | A 3.1 | Biopython `3730.ab1`（6 处 0.30-0.39 次峰，集中修剪后 50-125bp，5/6 次峰为 A） | △ 疑似混合 | 非混合；最多 info |
| F2 | A 3.1 | Biopython `3100.ab1`（A 级，61 处次峰集中在 470-667bp） | △ 疑似混合 | 非混合；该段标“信号衰减” |
| F3 | A 1.3 | Biopython `310.ab1`（无 PCON） | 拒绝分析 | 可分析，注明 `quality_source` |

### 4.3 其他问题场景

| ID | 来源 | 场景 | 期望 |
|---|---|---|---|
| M1 | A 4.5 | 同一密码子相邻两处替换（AAA→TGA） | 变体表合并注释 K2*，与 CDS 结论一致 |
| M2 | A 3.4 / B P1-6 | 两条 read 支持同一变体，上传顺序颠倒 | status、p_real、峰证据不随顺序变化 |
| M3 | A 4.4 | 两条 read 重叠区高质量分歧 | overlap_discordance，共识 IUPAC |
| M4 | A 4.3 | CDS 标注偏移 128bp / 被 ORF 校正改成 516bp | 不改标注，orf_annotation_suspect |
| M5 | A 1.5 | 指定正向引物，read 以反向高分比对 / 起点偏离引物 3′ 下游 >100bp | primer_mismatch |
| M6 | A 2.2 | 首端软剪切 ≥50bp + 参考含 ≥20bp polyA，正/反向 | 信号层、poly 计数、`_read_ref_maps` 三处窗口一致 |
| M7 | A 3.7 | 3100.ab1 第 609 位（调用碱基为第二高通道） | secondary_base 非 None + `call_disagrees_with_trace` |
| M8 | A 1.4 | PLOC 偏移统计 | 峰窗中心偏移 0 的比例 ≥ 现状 |
| M9 | A 2.4 | 直接调核心 `analyze()` 不传拓扑 | 报错或从参考读取，不得静默线性 |
| M10 | B P1-12 | 反向 read 有软剪切时，区间裁决行的“引物内坐标” | 与变体表 read_pos 口径一致 |
| M11 | B P1-13 | 分块会话 `_precheck` 收到 `b"\0"` 参考 | precheck 阶段即报不可解析 |
| M12 | B P2-18 | PLOC 短于 PBAS | 明确告警/报错，不静默截短 |
| M13 | B P1-8 | 改写“差异区间…无需核对”文案 | 前端裁决展示不变（读结构化字段） |
| M14 | B P2-16 | 参考末端插入仅一票且该票为 low | 不定插，或作为 unresolved |
| M15 | B P2-17 | 用户计划使用的酶切位点附近有未决 indel | 提示“位点归属待定” |

---

## 5. 工作分解

### 阶段 P0：止住所有漏报出口（T0-T10）

#### T0 复核 + 回归夹具（先取证、先写红测）

1. **复核表**：在当前 HEAD 上逐条复核两份审计全部发现（A 1.1-5.6，B P0-1 至 P2-21），产出 `docs/audit/VERIFY_<date>.md`：每条写“成立 / 不成立 / 部分成立”、当前代码位置（函数名 + 行号）、复现方式。不成立的条目对应任务直接跳过并在最终汇报说明。
2. **合成器**：`tests/abif_utils.py` 增加高斯峰合成器 `make_trace_ab1(seq, *, spacing=12, amp_jitter=(0.8,1.2), noise=(0,3), secondary={pos:(base,frac)}, q=50|list, n_at=[...], ploc_base=0)`，FWO_1 = "GATC"。
3. **场景库**：`tests/sanger_scenarios.py` 生成 §4 全部合成场景（参考、read、期望）；把 B 审计 /tmp 下的探针思路固化进来。
4. **场景测试**：`tests/test_sanger_scenarios.py` 逐项断言最终期望；未修的用 `pytest.mark.xfail(strict=True, reason="Txx")`，由对应任务移除。
5. **旧口径取证**：有 MX 数据的机器上，先跑一次 `test_real_data_regression.py` 与批量整理脚本，保存结论/双峰/待核数等到 `work/baseline_before.json`。
6. 对照组保持绿。

#### T1 证据三态 + ✓ 资格门（A 3.3；B P0-1、P0-2）

- 变体新增 `status`：`rejected` 仅当有正面反证（`cross_read_resolved=True` 且互检达标、或后续 T15 p_real<0.01 且有反证）；`confirmed` 为现 high/medium；其余为 `unresolved`。`confidence` 字段保留。可信区外、首端硬区的差异一律 unresolved，不再以“疑似测序伪影”吸收。
- 新增 `unresolved_sites`：N/IUPAC 调用列（单 read 保留 N、多 read 投票压制 N 两种情形都记录）、poly 不可判定、dropout 区段、无法互检的双峰、互检矛盾未定案。
- `aligner` 与共识投票：N 列不再只是“不出变体”，而是写入 `unresolved_sites` 并从 identity/verified 中扣除。
- **目的基因模式现有资格门并入**：`_conclusion_summary` 里按 `analysis_target` 拼接的覆盖提示（零覆盖 △、部分覆盖追加文案）改为 verdict 规则——目标 CDS 即 ROI，uncovered → insufficient，partial → partial；全质粒模式与目的基因模式走同一套 ✓ 资格门，不再各自拼字符串。`cds_extract.FLANK_BP` 侧翼逻辑不动。
- `_build_cds_reports`：蛋白判定用 confirmed；CDS 内有 unresolved 变体（would_change）或 N 列 → `pending_impact: true` / `n_in_cds`，该 CDS 不得出“一致”终态；`_rebuild_from_variants` 遇 N 列时 alt 不得回落为参考。
- `_build_consensus`：`skip_keys` 只放 rejected；unresolved 替换输出 IUPAC；unresolved indel 在 `diffs` 标 `unresolved: true`；禁止回填参考碱基；保留“明确碱基优先于 N”。
- **✓ 资格门**：判定区域（T5 的 ROI）内无 unresolved 变体、无 unresolved_sites，才有资格 ✓。全质粒模式与目的基因模式同一规则（目前只有目的基因模式“零覆盖”一种资格门）。
- 措辞：“N 处低置信差异疑似测序伪影，未计入判定”改为“N 处未决差异，需核对峰图或补测”。
- 期望：S1（△）、S9、S10、S11 通过。

#### T2 结构化 verdict + API 下发 + 前端结构化渲染（A 5.1/5.2/5.5；B P1-8、P1-9）

- 新模块 `core/sanger/verdict.py`：`build_verdict(result)`，挂 `result["verdict"]`。
- 状态优先级：`fail` > `failed_reads` > `mixed` > `pending` > `partial` > `insufficient` > `pass`（failed_reads 在有 confirmed 差异时让位给 fail，但 blocking 中仍列出）。
  - fail：ROI/CDS/关键元件/接合区内 confirmed 差异、蛋白不一致、unaligned_hq_segment、structural_breakpoint、primer_mismatch（T17 后）。
  - failed_reads：任何 read 解析失败（T4）。
  - mixed：mixed_sample 或 ROI 内 mixed_variant。
  - pending：ROI 内 unresolved 变体或 unresolved_sites、n_in_cds、poly_unreliable、dropout、overlap_discordance、orf_annotation_suspect、allele_mismatch。
  - partial：ROI verified 覆盖 <100%。
  - insufficient：现 `_undetermined_reason` 条件，及 verified_bp 低于闸门。
- 所有出口由 `verdict.status` 映射：`_conclusion_summary` 首行符号、`batch.excel_conclusion` 前缀、`sequencing_report._conclusion_bucket` 分拣（新增“待定/”“混合/”“读取失败/”目录，CHANGELOG 说明）、Markdown 报告。
- `has_mixed` 改读 `mixed_profile["class"]` 与互检结论。
- **API**：pipeline 已产出 `resolved_regions`，但 `sequencing_routes._summary` 的下发白名单仍没有它（`c33e81e` 核对）。白名单补齐 `resolved_regions`、`verdict`、`coverage`、`unresolved_sites`、`resolved_regions`、`mixed_corroboration`、变体新字段；同步 `api/sequencing.ts` 类型；修正 tooltip 中指向“导出 JSON 的 resolved_regions”的说明。
- **前端**：`ConclusionCards.vue` 徽标读 `verdict.status`，阻断项按 `verdict.blocking` 列出，裁决行读 `resolved_regions` 结构化渲染（`bfeb07e` 后 `ConclusionCards.vue` 仍用 `l.startsWith('差异区间') && l.includes('无需核对')` 判定裁决行，tooltip 仍指向“导出 JSON 的 resolved_regions”，均需改掉）；无 verdict 时退回旧逻辑。低置信折叠默认值保持现状，T15 后改为只折叠 exclude。
- 契约测试 `tests/test_sanger_verdict_contract.py`：所有场景下网页符号 / Excel 前缀 / 分拣目录 / API 下发的 `verdict.status` 一致；“文案改写不改 status”；前端 vitest 覆盖 M13。

#### T3 measured / verified 双覆盖口径（B P0-4、P1-7）

- 新模块 `core/sanger/coverage.py`：`verified_ranges = measured_ranges ∩ Σ(ref_trusted) − N 列`；输出 §3 的 Coverage。
- 结论、CDS `full`（≥99%）判定、批量闸门 `_covered_bp` / `MIN_VERDICT_COVERED_BP`、ROI 覆盖全部切到 verified；`coverage_percent` 保留旧语义（measured）以兼容，新增 `verified_percent`。
- 共识导出：不可信区碱基以小写或单独标注导出，不再与实测序列无差别交付（新增导出选项，默认标注，不改已有字段）。
- 所有支持证据都来自可信区外的变体，reason_codes 加 `all_evidence_out_of_zone`，网页明示（S17）。
- 期望：S13、S17 通过；S8 在 verified 口径下仍为 partial。

#### T4 解析失败的 read 进入结论（B P0-3）

- `batch.excel_conclusion`：`res["errors"]` 非空即至少 △，前缀“待定·读取失败”，点名“N 条 read 中 M 条解析失败（文件名…）”；全部失败保持“分析失败”。
- 单样品：errors 进入 `verdict.reads.failed` 与 blocking，结论头显示，不再只在 `ReadTable.vue` 中出现。
- 整理包分拣：含失败 read 的样品不得进入“正确/”。
- 期望：S12 通过。

#### T5 ROI、关键元件与接合区（A 4.1/4.2；B P1-10、P1-11）

- ROI 来源：设计记录的插入区间（两侧各外扩 50bp）> 参考 CDS 特征并集 > 整条参考（`source="whole"` 时 partial 只记 info）。
- 关键元件表放 `verdict.py`：feature type ∈ {promoter, RBS, rep_origin, polyA_signal, terminator, protein_bind, misc_recomb}，或名称匹配 `Kozak|loxP|att[BPLR]|FRT|AmpR|KanR|CmR|NeoR|His|FLAG|HA|Myc|Strep|GST|MBP`（不区分大小写），可配置覆盖，注释写明依据。对每个已注释关键元件输出 full/partial/uncovered 元件级结论（复用 CDS 报告框架）。
- 关键元件内 confirmed → critical_element_variant（fail）；unresolved → pending。
- **接合区**：CDS 模式下载体-基因接合区 ±30bp（`cds_extract` 侧翼伪特征）内任何非 rejected 差异或 unresolved_site → junction_variant（confirmed 为 fail，否则 pending）；全质粒模式对设计记录的插入边界同样适用。
- 删除 `batch.excel_conclusion` 中“合格：编码区外 N 处确证差异”分支；同义突变单列。
- 未注释抗性基因的盲区：本任务只在 info 中提示“参考未注释抗性/ori 元件，未做元件级判读”；自动注释不在范围内。
- 期望：S7、S8、S15 通过。

#### T6 端部软剪切高质量碱基（A 2.1）

- `aligner._align_impl` 输出 `clipped_5p/clipped_3p`（原始电泳坐标）。
- 可信区内、Q≥20 占比 ≥80%、长度 ≥15bp 的软剪切段 → `unaligned_hq_segment`（记录相邻比对块的参考坐标与方向）；首尾低质量正常剪切不触发；`PARTIAL_ALIGN_RATIO` 保留但不再是唯一兜底。
- 期望：S5、S6 通过。

#### T7 单点双峰次等位调用 + 滑移序列约束（A 3.2；B P2-20、P2-21）

- 新模块 `core/sanger/mixed_call.py`：每个双峰位点（已排除 pull-up、真实滑移、合并压缩区、可信区外）经 ReadRefMap 映射到参考；`{called_base, secondary_base}` 含非参考碱基 → MixedVariant（反向 read 取互补）。AF ≥0.15 且在 ROI/CDS/关键元件/接合区 → verdict mixed；其余 info。与同位点 unresolved 替换合并为一条 blocking。
- `signal.detect_phase_shift`：首个 echo 之后相邻 echo 间距 ≤3 碱基（STR stutter 物理上紧邻），超过即中断序列，不再 `continue` 跳过本底级弱峰；保证被剔除出 mixed 的只有真实滑移位点。
- 反向 poly slip 的 `run_base` 链向约定加断言/类型（如 `Strand` 枚举 + 单测覆盖反向场景），不再只靠注释。
- P0 阶段沿用现有 30% 双峰判定；T11 后改用自适应模型输出。
- 期望：S2、S3、S4、S16 通过。

#### T8 混合互检与变异互检同一标准（B P0-5）

- `homopolymer._corroborate_mixed` 的 clean 判定复用 `_region_ok`：验证 read 的对应列必须在其可信区内，±25bp 区域质量达标（均值 Q≥30、≥Q20 占 90%、core 最低 Q≥30），且该区未报其他差异；达不到即 `uncovered`，不算 clean。
- 源 read 双峰位落在验证 read 覆盖之外的，永远归 uncovered，不能据此把源 read 双峰判为噪声。
- `batch.py` 中对应的降级口径同步修改。
- 改动前后对比 MX 真实回归（B 审计记录的基线：22 处双峰 → 互检后 4 处待核），按“先取证再改断言”纪律更新断言并逐条解释。
- 期望：S14 通过。

#### T9 统一 read↔参考坐标映射（A 2.2；B P1-12）

- 新模块 `core/sanger/readmap.py`：`ReadRefMap.from_alignment(read_result)`，正确处理 `query_start/query_end`、反向 read、插入/缺失列、环状折回；口径以 `homopolymer._read_ref_maps` 为准；提供 `primer_coord(i)`（引物内坐标）。
- 替换以下线性推算：`pipeline._signal_analysis_pass` 的 read 窗口、`homopolymer._aligned_run_window`、区间裁决行的反向 read 引物内坐标（`trim_len - read_pos + 1` 未扣软剪切）。后续任务的坐标换算一律走 ReadRefMap。
- 期望：M6、M10 通过。

#### T10 tracy decompose 输出解析（A 3.6）

- `try_tracy_decompose` 按实际产物解析：优先 JSON，退回 `outprefix.align1/.align2`；先在镜像内 `tracy --version` + 真实运行核对格式，样例存 `tests/fixtures/tracy/decompose_*`。
- 两条等位分别走比对 + `_build_cds_reports`，写入 `decomposed_alleles[file][k].cds_reports`；任一等位与设计不符 → allele_mismatch。`AlleleCard.vue` 显示每条等位的 CDS 结论与差异。

**P0 阶段闸门**：S1(△)、S2-S16 通过（S17 的注记部分通过）；M6、M10、M13 通过；对照组、全量测试、MX 真实回归（有数据时）绿或差异已逐条解释；CI 门禁本地预演绿；KNOWN_ISSUES 更新；`SANGER_ALGO_VERSION` 已递增。

---

### 阶段 P1：降低误报与复核量（T11-T18）

#### T11 双峰自适应背景模型 + 可配置（A 3.1；B P2-14）

- 新模块 `core/sanger/mixed_model.py`：
  1. 每条 read ±25 峰滑窗内按通道对（主→次，如 G→A）估计次峰比的局部中位数与 MAD。
  2. 位点双峰：`ratio > max(0.20, local_median + k·1.4826·MAD)`，k 默认 4，输出 `mixed_z`。
  3. read 级 mixed_sample：可信区内双峰密度（每 100bp）显著高于背景，**且**次要组分比值变异系数 <0.35，**且**前/中/后三段至少两段有分布；集中于单一区段 → `local_signal_issue`（info）。
  4. 参数表 `core/sanger/params/mixed_<instrument>.json`，按 ABIF `MODL`/`MCHN` 选择，与 `reliable_zone` 一样可通过配置调整。
- `MIXED_WIDESPREAD_MIN/SCATTERED_MIN/STRETCH_MIN` 的绝对计数不再决定结论（常量保留兼容，旧测试按新口径更新并逐条说明）。
- 期望：F1、F2 不判混合；S2-S4、S14 仍检出；合成均匀 30% 混合 read 判 mixed_sample。

#### T12 可信区信号驱动（A 1.1）

- 新模块 `core/sanger/zone_signal.py`：20bp 滑窗 mean Q、峰距 CV、半高宽比、主次峰比，满足即可信；取消 700bp 硬顶，`tail_hard` 只在无峰图时兜底；`front_hard` 保留。
- 位置不再直接把变体判 low，只作为 T15 的特征。`reliable_zone.py` 保留接口，内部调用新实现。
- T3 的 verified 口径随之更新。
- 期望：S1 变为 ✗（确证错义）。

#### T13 局部噪声 SNR（A 1.2）

- 新模块 `core/sanger/noise.py`：`noise = max(1, 1.4826 × MAD(±10 峰内非调用通道峰窗面积))`。
- `snr is None` 视为不确定（不再当作响亮），reason_codes 记 `snr_unavailable`。
- 期望：3730.ab1 中 T 通道位点 SNR 非 None。

#### T14 多 read 证据聚合（A 3.4；B P1-6）

- 新模块 `core/sanger/evidence.py`：合并变体时保留 `evidence_by_read` 与 `covering_reads_not_supporting`；展示用的“代表 read”取证据最强者，而不是文件顺序第一条；互检扩展到所有变体。
- 前端 `jumpToVariant` 同时勾选全部支持 read（现有机制保留，只扩展 read 集合）。
- 期望：M2 通过。

#### T15 校准的 P(真实)、四档行动与峰图三色（A 5.3；B P1-9、P2-15）

- 新模块 `core/sanger/scoring.py`：逻辑回归（纯 numpy 推理）。特征：Q、mutant_pct、局部 SNR、mixed_z、峰距异常分、read 位置分位、可信区内外、支持 read 数、正反链是否都支持、互检结果、是否位于 poly/压缩/dye blob 区（T20 后）、`call_disagrees_with_trace`。
- 系数存 `core/sanger/params/variant_score.json`，由 `scripts/fit_variant_score.py` 用 T23 基准集拟合并输出校准曲线；T23 完成前先用与现有规则等价的手设系数，行为不倒退。
- 行动：accept（P>0.99）、exclude（P<0.01 且有反证）、review_trace、resequence。`status` 由此导出。`_variant_confidence` 的 if 分支迁出 pipeline.py 或删除；`confidence` 由 p_real 分档回填。
- `VariantTable.vue`：按待处理优先级排序，显示 p_real 与 action；只折叠 exclude（此时折叠才合理）。
- `useSeqViz` 峰图条带按 confirmed / unresolved / rejected 三色渲染（新逻辑放新 composable，遵守尺寸预算）。

#### T16 ORF 校正加护栏（A 4.3）

- 新模块 `core/sanger/orf_guard.py`：只允许标注起点 ±3bp、同读框内调整；起始密码子仅 ATG（原核额外允许 GTG/TTG）；超出保留原标注并给 orf_annotation_suspect。
- 读取 GenBank `/codon_start`、`/transl_table`，未给出时按宿主选择密码子表。
- 期望：M4 通过。

#### T17 引物库与方向校验（A 1.5）

- 新模块 `core/sanger/primer_check.py`：引物来源为批量信息表引物列 + 可选 `data/primers/*.yaml`（常用通用引物 T7、T7term、SP6、M13F/R、CMV-F、BGH-R 等，注明来源）。
- 能确定引物时：定位结合位点，校验方向一致、比对起点位于引物 3′ 下游 20-150bp（可配置）；否则 primer_mismatch（默认 fail，可配置 pending）。无法确定时只记 info“未校验引物”。
- 单样品上传接口新增可选 `primer` 字段。
- 期望：M5 通过。

#### T18 分块会话预检一致性（B P1-13）

- `sequencing_session_routes._precheck` 对参考与 read 做与 finalize 相同的可解析性检查（复用同一解析函数，或轻量解析头部），不再用占位字节分组；docstring 与行为一致。
- 期望：M11 通过；现有会话状态机测试保持绿。

**P1 阶段闸门**：S1 为 ✗；F1、F2 不判混合；S2-S17、M2、M4、M5、M11 通过；对照组、全量测试、MX 真实回归绿或差异已解释；评分系数与拟合脚本已提交。

---

### 阶段 P2：结构性升级与收尾（T19-T25）

#### T19 质量感知比对与 split-read（A 2.1/2.3；B §4）

- B 审计认为比对层没有“假错配雪崩”。本任务**先测量再改**：在合成压缩/低 Q 场景与基准集上统计错位 indel+错配簇的发生率；若可忽略，只做 split-read，把 Q 加权打分记为“已评估无需改动”。
- 若有问题：Q 加权打分 `mismatch = −4 × min(1, Q/30)`，N 与 Q<10 近似通配；性能不超过现状 2 倍（单 read 1000bp × 10kb 参考）。tracy 可用时 `core/sanger/trace_align.py` 封装 `tracy align` 互为校验，不一致区段进 unresolved。
- split-read：`core/sanger/split_read.py` 对 unaligned_hq_segment 在参考（含环状延长与反向互补）上二次局部比对；命中 → structural_breakpoint，未命中 → `detail="未知序列"`。

#### T20 通用压缩与 dye blob 检测（A 3.5）

- 新模块 `core/sanger/artifacts.py`：压缩（峰距 < 0.6 × 局部中位，连续 ≥2）、dye blob（read 前 150bp 四通道同时宽峰且与调用无关）、短同聚物（3-7bp）滑移。只作为 T15 特征与 info，不新增 if 分支直接改结论。

#### T21 重叠区分歧与双链覆盖（A 4.4）

- 新模块 `core/sanger/overlap.py`：输出 overlap_discordance（参考位、各 read 调用/Q/峰比、加权票差）；票差低且双方非 exclude → 共识 IUPAC + unresolved + pending。
- ROI 计算 `double_strand_fraction` 写入 `verdict.roi`；“ROI 必须双链覆盖”策略可配置（默认关闭）。
- 期望：M3 通过。

#### T22 证据包与多 read 同屏（A 5.4）

- 新模块 `core/sanger/evidence_pack.py`：对每个 blocking 项与 review_trace/resequence 变体，生成全部覆盖 read 在该位 ±10 峰的四通道小图（纯 Python 生成 SVG），附调用/Q/峰比/p_real 表与判定理由。
- 整理包 Markdown 报告嵌入证据包；单样品提供下载（新增端点或复用导出端点，同步前端类型）。
- 前端：点击变体/阻断项时多 read 堆叠对齐显示；MixedVariant 与 unresolved_sites 也出现在列表中可跳转。

#### T23 公开真实数据基准集与 ROC 回归（A 5.6）

- `tests/data/sanger_bench/`：Biopython 测试 AB1（3730、3100、310 等，注明许可）、tracy 示例数据（许可允许时）、合成场景导出；每个样本配 `truth.yaml`。大文件用 Git LFS 或只放必要样本，README 注明来源与许可。
- `scripts/sanger_benchmark.py`：位点级敏感度/特异度、read 级混合 TPR/FPR、verdict 混淆矩阵、阈值扫描 ROC。
- `tests/test_sanger_benchmark.py`（CI 运行，不 skip）：S/M 场景全对；F1/F2 混合误报为 0；位点级敏感度 ≥0.95、特异度 ≥0.99（以基准集实际情况标定，写进测试并在 KNOWN_ISSUES 说明）。
- 用基准集拟合 T15 系数与 T11 参数（`scripts/fit_variant_score.py`、`scripts/fit_mixed_params.py`），结果与校准曲线写入 `docs/SANGER_CALIBRATION.md`（注意 `check_docs_sync.py` 的文档清单与链接检查）。

#### T24 小修集合 1：解析层（A 1.3/1.4/3.7；B P2-18、P2-19）

1. PLOC −1（`abif_reader` 两处 `int(p) - 1`）：删除；tracy JSON `basecallPos` 按 tracy 文档确认基准后统一。M8 通过。
2. PLOC 短于 PBAS：明确告警并在结果中标注受影响区段，下游峰证据缺失时进 unresolved，不静默截短。M12 通过。
3. 通道检测采样：跳过首端不可信区（与 `front_hard` 同口径）再均匀采样。
4. 次峰碱基 None：取前两名通道中不等于调用碱基者；调用碱基不在前两名时记 `call_disagrees_with_trace`。M7 通过。
5. 无 PCON：先 `_try_tracy_basecall`，再按峰型估 Q；标注 `quality_source`；修剪改为错误概率口径，中段低质量凹陷做掩蔽而非截断。F3 通过。

#### T25 小修集合 2：判读与注释层（A 2.4/4.5；B P2-16、P2-17）

1. MNV 注释：同一读框同一密码子内的替换先聚合再注释。M1 通过。
2. 核心层拓扑：`analyze()` 的 `circular` 无默认值或从参考对象读取；参考解析器统一给出拓扑（GenBank LOCUS / SnapGene / FASTA 默认环状并声明）；`sequencing_endpoints` 中以 `insert_start != 1` 推断拓扑的启发式改为读参考拓扑。M9 通过。
3. `end_insert_votes`：与 `skip_keys` 同口径，low/rejected 票不定插；单票时为 unresolved。M14 通过。
4. 酶位提示：用户计划使用的位点附近（±10bp）有未决 indel 时提示“位点归属待定”。M15 通过。

**P2 阶段闸门**：§4 全部场景无 xfail 通过；基准测试在 CI 运行且达标；对照组、全量测试、MX 真实回归绿。

---

## 6. 最终验收标准

1. `tests/test_sanger_scenarios.py` 全部通过，无 xfail/skip；`tests/test_sanger_benchmark.py` 在 CI 运行并达标。
2. 契约测试：网页 / API 下发 / Excel / 分拣目录 / Markdown 报告的结论都由同一 `verdict.status` 导出；文案改写不改 status；前端在有 verdict 时不读文案。
3. `grep -rnE '"疑似混合样品" in|startswith\("(合格|不合格)"\)' src/backend` 为空；前端 `grep -rn "startsWith('差异区间')\|includes('低置信（')" src/frontend/src` 为空（或仅在无 verdict 的降级分支中）。
4. 后端 `cd tests && ../.venv/Scripts/python -m pytest -q` 全绿（基线 1850 passed / 43 skipped，以 T0 复跑结果为准，通过数只增不减、skip 数不增；按新口径更新的旧断言在提交说明中逐条解释）。前端（基线 686 passed）`npm run test:run`、`npm run type-check`、`npm run build`、`npm audit` 全绿。
5. CI 门禁本地预演全绿：`python scripts/check_file_size.py`（白名单文件不超原预算，迁出后已下调）、`python scripts/check_docs_sync.py`、pip-audit 不新增告警、依赖锁同步测试绿。
6. MX 私有真实回归：有数据的机器上通过；与 `work/baseline_before.json` 的差异逐条记录在 KNOWN_ISSUES“真实数据基线”章节。
7. 旧记录兼容：`tests/test_sequencing_rehydrate.py` 绿；无新字段的历史记录在前端正常显示。
8. 性能：单样品（3 条 read、10kb 参考）耗时不超过改造前 2 倍；`test_sequencing_batch_budget.py` 绿。
9. 文档：
   - `docs/audit/VERIFY_<date>.md`：两份审计逐条复核结果。
   - `docs/KNOWN_ISSUES.md`：修复历史按 T0-T25 记录；「防回潮约束」新增至少五条（unresolved/N/坏文件不得出 ✓；覆盖判定只用 verified；判定只读 verdict；坐标换算只走 ReadRefMap；混合互检与变异互检同一标准）；真实数据基线更新。
   - `docs/ALGORITHM_ROADMAP.md`：标记已完成项与遗留项。
   - `CHANGELOG.md` [Unreleased]：用户可见变化（新增待定/读取失败/疑似混合/部分验证状态与分拣目录、verified 覆盖率、证据包、引物校验、混合判定口径变化；“待定”会明显增多，这是预期）。
   - 如改测试基线，AGENTS.md 与 HANDOVER 同步；`SANGER_ALGO_VERSION` 登记在 HANDOVER 版本常量处并纳入 `check_docs_sync.py`。

---

## 7. 执行方式、停止条件与汇报

- **顺序**：T0 → T1 → T2 → T3 → T4 → T5 → T9 → T6 → T7 → T8 → T10 → P0 闸门 → T11-T18 → P1 闸门 → T19-T25 → P2 闸门。约束：T9 在 T7、T11、T14 之前；T14 在 T15 之前；T23 基准集骨架在 T0 后即可搭建，T11/T15 的正式参数在 T23 后拟合；T3 在 T12 后需复跑。
- **每个任务**：对应场景先红（或已 xfail）→ 实现 → 相关测试 + 全量 pytest + 前端测试（有前端改动时）+ 门禁预演 →（改口径时）MX 回归对比 → 全绿后按 git-commit-style 提交（Conventional Commits，如 `fix(sanger): …`），一任务至少一提交，提交说明写明对应审计条目（A x.y / B Px-n）与场景 ID。
- **立即停下汇报、不得自行绕过**：
  - T0 复核发现某条审计结论不成立，而它是某个 P0 任务的唯一依据（汇报后由人决定是否保留该任务）；
  - 需要修改既有 API 路径或已有字段语义；
  - 任何对照组必须变红才能让目标场景变绿；
  - 白名单文件无法在预算内完成改动；
  - 需要新增 Python 运行时依赖；
  - tracy 实际输出格式与文档都对不上；
  - 基准集许可不明确；
  - MX 真实回归出现无法解释的结论变化；
  - 标定后敏感度与特异度目标无法同时满足（附 ROC 数据，由人取舍）。
- **阶段汇报**（每个闸门一次）：改动文件、新增/修改测试、§4 场景前后对比表（每项旧输出 / 新输出）、全量测试数字、门禁结果、MX 回归差异、性能数据、遗留问题。
- **最终汇报**：25 项完成情况（含复核后跳过的条目与理由）、基准集指标（敏感度/特异度/混合 FPR、校准曲线摘要）、所有参数表位置与来源、刻意未做的事项及原因。
