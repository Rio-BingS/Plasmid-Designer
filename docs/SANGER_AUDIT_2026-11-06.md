# Sanger 测序全链路深度审计（2026-11-06）

> **范围**：`core/sanger/` 全部模块（pipeline / aligner / homopolymer / reliable_zone /
> signal / abif_reader / cds_extract / annotator / batch）+ 接入层（sequencing_routes /
> sequencing_endpoints / sequencing_session_routes / sequencing_batch / sequencing_report）
> + 前端（SequencingPanel / ConclusionCards / VariantTable / useSeqViz / api 契约）。
> **方法**：逐层读码 + 关键疑点用合成 ab1 与真实 MX 批次（V1.01）走完整管线复现。
> 审计全程只读；基线：现有 Sanger 测试 175 项全过（`tests/test_sanger_pipeline.py`
> + `test_real_data_regression.py` + `test_sanger_n_and_merged_zone.py`）。
> **标注**：【已复现】= 有可复跑探针（探针在 /tmp 未入库，固化进 tests 见 §实施顺序）；
> 【静态确认】= 代码路径确凿但未跑端到端。
> **状态**：本文件是发现清单，**尚未修复**——修复前逐条复核（先取证再动手，
> 教训见 postmortems/2026-10-31-audit-round3-fixes.md：自报 15 项有 4 项不成立）。

---

## 0. 总评：一个系统性病根

架构里有真东西（不用推倒）：种子延伸比对的下界数学与差分测试、环状跨原点整条比对+折回、
反向 read 坐标镜像、indel 最左归一化、逐列 Q 加权投票、poly 五态判读、FWO_1 权威通道映射。

**病根：证据模型是二元的（「确认」 vs 「疑似伪影」），缺第三态「未决」。**
所有无法确证的信号（N 调用、可信区外差异、低 Q、混合峰、poly 不可判定、dropout、
解析失败文件）都被坍缩成「低置信=伪影」或「未测=按设计对待」，✓ 头行照发；
「已测」口径又不扣可信区、不含错误文件。三类完全不同的状态——**验证过了 /
没读清 / 根本没测**——被压成同一个绿色结论。人工被迫全量肉眼复核，
正是因为头行不可信。

---

## 1. P0 —— 直接造成漏报（均已复现）

### P0-1 「低置信 = 测序伪影」的预判决，✓ 头行无资格门
- 位置：`pipeline.py` `_conclusion_summary`（403-418）、low_keys 排除（2900-2902）；
  一票降 low 在 `_variant_confidence`（195：zone 检查仅 `support < 2` 时生效）。
- 复现：单 read、变异落在首端 30bp 硬不可信区（Q40、峰证据齐全）→
  结论头 **「✓ 构建与设计一致（1 处低置信差异疑似测序伪影，未计入判定）」**。
- 为什么重要：首端 30bp 恰是 PCR 产物真实突变高发位（酶切位点残留、引物 3' 端）；
  单引物测到这里的真变异**永远**进不了判定。CDS 重建只取 confirmed（`_build_cds_reports`
  1442-1444），蛋白判定同样被绕开。
- ✓ 翻 △ 的资格门只存在于目的基因模式「零覆盖」一种情形（398-402）；
  全质粒模式的「已测区域内存在未决证据」没有任何资格门。
- **改法**：引入第三态 `unresolved`（N 调用、区外差异、poly 不可判定、dropout 区段、
  无法互检的双峰、互检矛盾未定案）；✓ 资格 = 判定区域内无未决项；
  措辞从「疑似测序伪影」改为「N 处未决差异，需人工核对峰图」。

### P0-2 N/模糊碱基全面隐身
- 链路：`aligner.py` 371-375（N 列不出变体、不计 identity）→ `pipeline.py` 共识投票
  （1030-1046，N 不进变体）→ `_build_cds_reports`（重建只走 variants，N 不可见）。
- 复现：read 中段 1 个 N（Q40）→ `variants=[]`、`identity=1.0`、
  **「✓ 构建与设计一致，未检出差异」**；N 静默留在共识序列，无任何提示行。
- 更深的洞：CDS 内的 N 对蛋白判定**完全不可见**——`_rebuild_from_variants` 从变体重建，
  N 不在其中 → alt=参考 → 「蛋白一致」。真实错义突变恰被 N 遮住时系统报一致性。
- **改法**：N 列计入未决区段并进 ✓ 资格门；CDS 覆盖把 N 列从「已验证」扣除；
  结论给 N 位点列表（单 read 覆盖保留 N / 多 read 投票压制 N 两种情形都要呈现）。

### P0-3 批量结论无视解析失败的 read
- 位置：`core/sanger/batch.py` `excel_conclusion`（614-685；624 只在**全部** read
  失败时才报「分析失败」，`res["errors"]` 不参与任何结论分支）；
  分拣在 `app/sequencing_report.py` 148-153（合格→「正确/」目录）。
- 复现：3 条 read、2 条解析失败（corrupt/过短）、剩 1 条覆盖 30% 无差异 →
  **「合格：与设计一致；测序覆盖 30%」**，整理包把损坏文件与报告分进「正确/」文件夹。
- 单样品网页端同样：errors 只在 `ReadTable.vue` 94-95 显示，不进结论头。
- 这是交付文件夹场景最危险的形态：测序公司一批 96 个 ab1 坏几十个，剩余照常「合格」。
- **改法**：任何 read 失败 → 结论至少 △ 并点名（「3 条 read 中 2 条解析失败，未验证」）；
  整理包分拣覆盖此形态。

### P0-4 覆盖口径不扣可信区，「已测」系统性虚高
- 位置：`pipeline.py` covered_ranges 直接取比对区段（2906-2910）；
  `zone["ref_trusted"]` 已算出但只进报告表格（2822-2828）。
- 后果链：首端硬区、尾端 900bp 后全部计入覆盖 → ① `coverage_percent` 虚高；
  ② CDS `full`（≥99%，`_build_cds_reports` 1406）可由不可信碱基凑满；
  ③ 批量覆盖闸门 `_covered_bp`（`batch.py` 577-585）用同一口径，
  `MIN_VERDICT_COVERED_BP=100` 名存实亡；④ 共识导出把不可信碱基当实测序列交付。
- **改法**：`verified_ranges = covered ∩ Σ(ref_trusted)`；measured / verified 双口径，
  结论、CDS full 判定、批量闸门全部切到 verified。

### P0-5 混合互检标准远低于变异互检，真混合被翻成 ✓
- 位置：`homopolymer.py` `_corroborate_mixed`（552-651，clean 判定）vs
  `_region_ok`（400-437，变异互检标准）。同一系统两套标准：

  | | 变异互检 `_region_ok` | 混合互检 clean 判定 |
  |---|---|---|
  | 验证 read 可信区 | 必须在区内（core 列区外即 None） | **无 zone 检查** |
  | 区域质量 | ±25bp 均值 Q≥30、≥Q20 占 90%、core 最低 Q≥30 | 单列 Q≥20 |
  | 自身健康 | 该区未报其他差异 | 只看自己没报双峰 |

- 复现：read1 注入 6 位点 45% 次级通道（真实混合形态）+ read2 同区纯主克隆 Q25 →
  6/6 clean → **「✓ 构建与设计一致，未检出差异」**，混合行降为「更倾向该 read
  自身信号问题」；批量端同口径降级（`batch.py` 552-558）→ 一句话结论「合格」。
- 逻辑要害：「验证引物没看到第二个克隆」≠「源 read 双峰是噪声」——次要克隆与主克隆
  差异集中在验证引物覆盖之外时，验证 read 单峰是必然的；这与变异互检「同 junction
  被独立读出」的证据强度完全不同。
- **改法**：clean 判定复用 `_region_ok`（zone + 区域质量）；验证覆盖之外的差异位
  永远归 uncovered。⚠️ MX 真实回归基线（22 处双峰→互检后 4 处待核）会变，
  先按旧口径跑一遍取证再改断言（HANDOVER §6.5 纪律）。

---

## 2. P1 —— 系统性弱点

| # | 问题 | 位置 | 说明 |
|---|---|---|---|
| P1-6 | 多 read 合并的证据归属错位【静态确认】 | `pipeline.py` 2837-2845 | 合并只更新 support/max-Q；`read`/`read_pos`/峰证据/zone 全保留第一条 read 的——`support=2` 的 "high" 可能是「A 区内 Q40 + B 尾端 Q25」。改法：证据取最高质量 read + per-read 判定明细 |
| P1-7 | 尾端双证直接升 high 无注记【已复现】 | `pipeline.py` 195 | 两条 read 都在尾端灰区同报同一变异 → support=2 → high → ✗ 确认。大概率真变异可接受，但「证据全部来自不可信区」零注记——随 P0-4 verified 口径一并解决 |
| P1-8 | `resolved_regions`/`mixed_corroboration` 没进 API 下发，前端靠字符串反解析【静态确认】 | `sequencing_routes.py` `_summary` 826-847 白名单缺键；`ConclusionCards.vue` 58 | 前端用 `startsWith('差异区间') && includes('无需核对')` 从**结论文本**重新解析裁决行；tooltip 还指向「导出 JSON 的 resolved_regions」。措辞改一字前端静默断裂——与 KNOWN_ISSUES「搜得到、导不进」同型（两条路径口径不一致）。改法：`_summary` 补字段、前端结构化渲染 |
| P1-9 | 低置信行默认折叠 | `ConclusionCards.vue` 22-23 | `showLowConf` 默认 false。配合 P0-1 的绿 ✓，复核待办被藏进绿色结论。资格门修好后折叠才合理 |
| P1-10 | 骨架/非编码区/未注释关键元件审计盲区【静态确认】 | `batch.py` 630-631 等 | 抗性基因若参考未注释则完全盲（批量结论只看 CDS 蛋白）；总长偏差、拷贝数变化、重排只有 partial_alignment（<80%，`pipeline.py` 422）和覆盖缺口两道粗网。改法：对已注释 ori/抗性/promoter 出元件级 full/partial/uncovered 结论（CDS 报告框架可复用） |
| P1-11 | CDS 模式侧翼（载体-基因接合区）差异无资格门【静态确认】 | `cds_extract.py` 202-224；`pipeline.py` 369-402 | 侧翼伪特征标「非分析目标」，落在侧翼的未决差异被「疑似伪影」话术吸收——而接合区（±30bp）恰是 Gibson 臂错位/酶切残留等**构建错误**最常出现的位置。改法：接合区 ±30bp 内任何非 low 变异/未决项 → 结论 △ |
| P1-12 | 反向 read 区间裁决行的引物内坐标未扣软剪切偏移【静态确认】 | `pipeline.py` 2542-2551 | `trim_len - read_pos + 1` 换算没用 `query_start/query_end`（变体表与 `_read_ref_maps` 都正确用了）——反向 read 有软剪切时裁决行坐标整体偏移，按坐标看峰图会看错位 |
| P1-13 | 分块会话 `_precheck` 用假字节分组，可解析性不一致【静态确认】 | `sequencing_session_routes.py` 112-122 | `b"\0"` 会被当有效参考，参考损坏到 finalize 才爆整批失败；与 docstring「与 finalize 判定一致」不符（归组一致、可解析性不一致） |

---

## 3. P2 —— 改进项

| # | 问题 | 位置 |
|---|---|---|
| P2-14 | 混合检测阈值不可配不自适应（`MIXED_PEAK_RATIO=0.30`、`MIXED_WIDESPREAD_MIN=5`、`MIXED_STRETCH_MIN=8` 硬编码），与 `reliable_zone` 可调待遇不对等 | `pipeline.py` 61-65 |
| P2-15 | 峰图条带不区分置信：确证与低置信差异同为红色系，已排除项无视觉区分；修 P1-8 后按 confidence/resolved 三色渲染 | `useSeqViz.ts` 783-795, 867-874 |
| P2-16 | `end_insert_votes` 单票定插：参考末端插入按权重取最大，无 low 排除路径，与 skip_keys 口径不一致（罕见，低危） | `pipeline.py` 1106-1111 |
| P2-17 | 酶位提示不含未决 indel：用户计划使用的位点附近有未决 indel 时无「位点归属待定」提示 | `annotator.py` 135-165 |
| P2-18 | PLOC 短于 PBAS 时 peak_indices 静默截短，下游证据静默缺失而非报错（真实文件 PLOC≈PBAS，防御深度） | `abif_reader.py` 286, 339；`pipeline.py` `_variant_peak_evidence` 160 |
| P2-19 | 通道检测采样偏置：均匀 150 位含首端爬升区，与「首端不可信」口径不一致（`_MIN_MAP_SCORE=0.5` 容错大，风险低） | `abif_reader.py` 149-156 |
| P2-20 | **`detect_phase_shift` echo 序列中间跳过无上限**：`SLIP_MIN_RATIO` 之下 `continue` 不中断，两个相距 20+ 碱基的弱峰会连成「滑移序列」→ slip 位点从 mixed 剔除 → **漏报真实混合双峰**。改法：首 echo 后相邻间距 ≤3 碱基（STR stutter 物理上紧邻） | `signal.py` 512-524；剔除点 `pipeline.py` 1872-1883 |
| P2-21 | 反向 poly slip 的链向约定（`run_base` 取互补）靠注释维系，无类型/断言防护；反向场景仅 MX 批次间接覆盖 | `signal.py` 181-192 vs `pipeline.py` 1841-1844 |

---

## 4. 已核对、明确无问题（不再挂账）

- **比对层无「假错配雪崩」**：mismatch -4 / gap -6/-2 使 1-2bp 漂移以局部 gap 消化
  而非连锁错配；软剪切 + 最左归一化 + 跨 read key 聚合收敛 anchor 漂移；
  种子延伸（`_align_seeded`）有下界证明与差分测试。
- **环状跨原点**：延长比对 + 折回（`align_read_circular` 双份拷贝去重）+
  junction 折回互检（`_clean_cover_reads` 环状 fold）+ 跨原点缺口合并（`_coverage_gaps`），
  实现完备。
- **abif_reader 通道映射**：FWO_1 权威优先 + 内容检测交叉校验 + 兜底修正
  （旧 A/T/G/C 兜底错位已修），警告不静默。真实 MX read 验证 no-op 正常。
- **baseline_correct 双护栏**：P90 封顶 + 跨通道共模（平台护栏），迭代收敛有界。
- **estimate_run_length 宽度法**：峰数/宽度/二阶导三种方法取整规则与文献口径一致，
  Δt 退化有守卫。
- **cds_extract 匹配与侧翼**：多级匹配 + token 化 + 歧义不猜；FLANK_BP=150 有真实数据
  实证（MX 探针：CDS 模式与全质粒模式变体/蛋白判定逐项一致，回归锁
  `test_real_cds_mode_mx`）。
- **峰图跳转主链路**：`jumpToVariant`（`useSeqViz.ts` 247-261）「点变体 → 自动勾选
  支持 read → 拉 trace → 选中条带 → 居中 + 1.6s 黄闪」完整可用；断点在 P1-8/P2-15
  的契约与配色，不在跳转机制。
- **分块会话状态机**：`store.locked` 双重提交 409、chunk 幂等去重、finalize 走
  `_heavy_slot` 带超时。

---

## 5. 按审核维度归位

| 维度 | 结论 |
|---|---|
| 1 信号/预处理 | 解析、通道映射、基线校正无硬伤；问题在消费端（P0-1/P0-4）与 slip 判定过宽（P2-20） |
| 2 比对算法 | 对齐本身健全；环状/反向/indel 归一实现正确；真问题在证据消费（P0-1/P0-4/P1-6/P1-12） |
| 3 变异检出/过滤 | 分层要素齐全但出口坍缩（P0-1）、互检不对称（P0-5）、合并归属（P1-6）、阈值刚性（P2-14） |
| 4 结构审计 | CDS 层扎实；骨架/未注释关键元件盲区（P1-10）、覆盖口径失真（P0-4）、接合区（P1-11） |
| 5 输出/人机 | **根源不是信息过载而是结论过度乐观**：✓ 无资格门、N 隐身、坏文件合格、契约断裂（P1-8）。修好资格门+未决态+结构化联动，复核才能收敛到真未决项 |

---

## 6. 实施顺序建议

1. **证据三态化 + 资格门**（P0-1 / P0-2 / P1-9 / P1-11）：一次改变所有漏报出口。
   动口径须递增结果版本并同步真实数据回归断言（HANDOVER §6.5 纪律）。
2. **verified 覆盖口径**（P0-4 / P1-7）：`ref_trusted` 已有，只差接线。
3. **errors 进结论**（P0-3）：batch / web 各一小分支。
4. **互检对齐**（P0-5 / P2-20）：复用 `_region_ok` + slip 间距约束；MX 基线先取证再改。
5. **契约与 UI**（P1-8 / P1-12 / P2-15）：`_summary` 补字段、前端结构化渲染、峰图三色。
6. 其余（P1-6 / P1-10 / P1-13 / P2-14 / P2-16~21）按触碰相关代码时顺手修。

修复前把 P0-1/2/3/5 的探针固化成 `tests/` 回归用例（先锁「现状能复现」，修复后翻断言）。
