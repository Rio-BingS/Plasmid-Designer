# 代码审查报告 05 —— Sanger 测序分析模块

- 仓库：`/workspace/work/Plasmid-Designer`（HEAD `8a95a3c`）
- 范围：`src/backend/core/sanger/`（abif_reader / aligner / annotator / pipeline / batch / signal / reference_parser 相关部分）、`src/backend/app/sequencing_report.py`、`sequencing_store.py`、`routes/sequencing_routes.py`、`scripts/batch_sequencing_report.py`、`scripts/poly_*.py`、前端 `src/frontend/src/components/sequencing/*`（及其依赖 `utils/seqPanelModel.ts`、`composables/useSeqViz.ts`）
- 环境：Python 3.12 + Biopython 1.88；现有 `test_sanger_pipeline.py / test_batch_sequencing.py / test_sequencing_batch.py` 共 163 例全部通过（说明下列问题均为现有测试未覆盖的盲区）
- 验证脚本：`/workspace/work/review/05-sanger-scripts/t1_basic.py … t7_revclip.py`（在仓库根目录用 `python3 <脚本>` 运行，均为只读实验，不修改仓库）

## 总览

| # | 标题 | 严重度 | 已脚本复现 |
|---|------|--------|-----------|
| 1 | 等高双峰（≈1:1 杂合/混合）被"退化窗口护栏"整体屏蔽，混合样品可被判"✓ 一致" | **严重** | ✅ t2 |
| 2 | 局部比对软剪切偏移未计入：read↔参考坐标映射整体错位（后端互检/poly 窗口 + 前端峰图） | **严重** | ✅ t1、t7 |
| 3 | 蛋白比对遇空序列抛 `ValueError`，整份分析 500（起始即终止 / CDS 被删光） | 高 | ✅ t4c |
| 4 | tracy 交叉 basecall 修剪后为空时 `align_read("")` 抛异常，整份分析崩溃 | 高 | ✅ t4d |
| 5 | 替换变异无跨 read 反证：一条 read 报、另一条高 Q 覆盖为参考，仍判"高置信/✗ 确认差异"，与共识/CDS 结论自相矛盾 | 高 | ✅ t4b |
| 6 | 参考序列长度无上限：比对内存 ≈3.6 KB/参考bp/read，300 kb 参考单条 read 1.1 GB/11 s | 高 | ✅ t1 |
| 7 | 批量接口无总字节上限（400×20 MB=8 GB 全量进内存）；`_BATCHES` 缓存条数无上限 | 高 | 代码审查 |
| 8 | 进程内存储跨线程无锁读写：`_register_analysis` 在线程池中运行，可抛 `dictionary changed size during iteration`；`_pack` 直接 `_ANALYSES[...]` 可 KeyError | 高 | 代码审查 |
| 9 | 同名/重复上传的 read 被当作独立证据：`support_reads` 虚增、峰证据取错 read | 中 | ✅ t4a |
| 10 | 合并变体只用"首个 read"的峰证据/混合位点/压缩区判定置信度 | 中 | 代码审查 |
| 11 | 跨原点（环形）特征被解析为整条质粒；跨原点 read 被局部比对截断 | 中 | ✅ |
| 12 | 酶切位点注释窗口固定 ±15bp，indel 把位点推出窗口 → 误报"破坏酶切位点" | 中 | ✅ t3 |
| 13 | 同一密码子内多个替换逐个注释，`aa_change` 错误（实为终止却报两处错义） | 中 | ✅ t3 |
| 14 | 基线校正 `_fill_invalid` 每个窗口边界重复 1 点，本底曲线逐窗累积错位 | 中 | ✅ t1 |
| 15 | 峰图原始数据驻留内存：最多 40 条 × 24 read × ~2.4 MB ≈ 2.3 GB | 中 | ✅ 估算 |
| 16 | 共识导出 `covered_only` 用参考坐标切共识序列，共识含 indel 时片段错位 | 中 | 代码审查 |
| 17 | 批量整理包下载无属主校验；匿名分析记录对所有用户公开可列 | 中 | 代码审查 |
| 18 | 数据库落库（zlib+JSON+count）在事件循环中同步执行；`persist_record` 未建表 | 中 | 代码审查 |
| 19 | 比对实现：`len(alignments)` 计数开销、正反向都做完整回溯 | 低-中 | ✅ t6 |
| 20 | ABIF 解析健壮性（Biopython 用 assert 校验、内置回退解析器类型码错误/截断静默接受） | 低-中 | ✅ t5 |
| 21 | 共识投票：插入与替换落同一 cell 互斥 | 低 | 代码审查 |
| 22 | 批量一句话结论：CDS 内同义突变被写成"编码区外 N 处确证差异" | 低 | 代码审查 |
| 23 | 离线脚本：质粒名未净化即拼路径（路径穿越/Windows 非法字符）；原 Excel 被原地覆盖 | 低-中 | 代码审查 |
| 24 | 其他低优先级（死代码、前端 key 冲突、poly_signal_probe 小样本分位、无 PCON 报错误导等） | 低 | — |

---

## 1. 等高双峰被"退化窗口护栏"屏蔽 —— 1:1 混合/杂合完全漏检【严重】

- **位置**：`src/backend/core/sanger/pipeline.py:327-333`（`_detect_mixed_detail`）
- **证据**：
  ```python
  if sorted_a[1] >= 0.9 * sorted_a[0]:   # "四通道面积接近相等"护栏
      continue
  ...
  if ratio > MIXED_PEAK_RATIO and areas[base] == sorted_a[0]:
  ```
  护栏注释说的是"四通道接近相等"，实际只比较**前两名**。真实 1:1 双峰（次峰/主峰 ≥0.9）正好落进这个护栏被丢弃；另外要求 called 碱基必须是面积最大者，basecaller 叫了略小的那个峰时同样漏检。
  `t2_mixed.py`（每碱基 10 采样点，5 个位点注入次级峰）：
  ```
  secondary/main=0.89  检出 5/5
  secondary/main=0.92  检出 0/5
  secondary/main=0.95  检出 0/5
  secondary/main=1.00  检出 0/5
  ```
- **影响**：最典型的混合（两个克隆约 50/50、或杂合位点）反而检测不到；若 basecaller 在这些位点恰好叫的是参考碱基，则无变体、无双峰，结论直接给出"✓ 构建与设计一致"，批量模式归入「正确」文件夹。这是对用户最危险的假阴性。
- **修改建议**：
  1. 护栏改为真正检查"四通道接近相等"：`sorted_a[3] >= 0.8 * sorted_a[0]`（第四名也接近才算无信号），或以窗口外本底为参照判断"无主导通道"；
  2. 去掉 `areas[base] == sorted_a[0]` 的硬要求，改为"called 碱基属于前两名"；`secondary_base` 取前两名中非 called 的那个；
  3. 退化窗口的本意（called 碱基无信号）用 `areas[base] < main_floor` 单独判断；
  4. 增加 ratio ∈ {0.9, 1.0} 与"basecaller 叫了次峰"的单测。

## 2. 局部比对软剪切偏移未计入 —— 坐标映射整体错位【严重】

- **位置**：
  - `core/sanger/aligner.py:129-155`：只输出 `ref_start` 与 `aligned_read_len`，没有输出 query 起点 `coords[1][0]`；
  - `pipeline.py:751-756` `_read_ref_maps`：`orig = (n - qi) if direction == "-" else (qi + 1)`，`qi` 从比对起点算 0；
  - `pipeline.py:686-707` `_aligned_run_window`：`idx` 从 0 起、`total` 取比对内碱基数；
  - `pipeline.py:1857-1862`（poly 滑移/骤降/压缩窗口）：`i0 = run["start"] - aln["ref_start"]` / `aln["ref_end"] - run["end"]`，线性映射，同时忽略软剪切和上游 indel；
  - 前端 `src/frontend/src/utils/seqPanelModel.ts:59`：`origIdx = direction === '-' ? L - 1 - qi : qi`，同样问题。
- **证据**：aligner 自己的 `variants[].read_pos` 用的是 query 绝对坐标（正确），但上面这些映射用的是"比对内相对坐标"，两套坐标只有在 read 两端都没被剪切时才一致。
  `t1_basic.py`（正向 read 前 30 bp 不匹配）：
  ```
  variant [(200, 130)] true read_pos 130
  read2ref[130] -> 230  (应为 200)
  ref2call[200] read_pos 100 (应为 130)
  ```
  `t7_revclip.py`（反向 read 的 3' 末端只要多 1 个不匹配碱基）：
  ```
  3'端多 1 个非参考碱基: read2ref[1]=None (应为600)
  ```
  打分是 match +2 / mismatch −4，末端 1-2 个错误碱基就会被局部比对剪掉。修剪后 read 末端仍常有零星错误，反向 read 的 3' 端尤甚，所以这在真实数据里是常态，不是边角情况。
- **影响**：
  - 跨引物双峰互检（`_corroborate_mixed`）把双峰映射到错位的参考位置，"同报双峰 / 被覆盖且峰形单一"分类出错，直接左右"疑似混合 vs 倾向噪声"的结论措辞；
  - indel 跨 read 反证（`_clean_cover_reads`）用到的 `read_pos` 与 END_MARGIN 判定错位；
  - poly 结构的滑移 echo、骤降、合并压缩区窗口落在错误碱基上（`merged_zones` 抑制、`slippage_artifact` 标记跟着错）；
  - `_anchor_verdict_for_read` 与 poly 峰计数的 read 窗口错位；
  - **前端峰图融合视图**画出的峰与上方参考列错开 N 个碱基，人工核对看到的是错误位置的峰。
- **修改建议**：
  1. `align_read` 返回 `query_start = int(coords[1][0])`、`query_end = int(coords[1][-1])`，并写入 `aligned` 视图；
  2. 统一换算：正向 `orig = query_start + qi + 1`；反向 `orig = n - (query_start + qi)`（n = 修剪后长度）；
  3. `_read_ref_maps`、`_aligned_run_window`、前端 `buildSeqCols` 都改用这一公式；
  4. `analyze` 的 poly 窗口循环（1856 行起）不要再做线性推算，改用 `_aligned_run_window`（修好后）或 `_read_ref_maps` 的 ref→read 映射；
  5. 增加"read 两端带不匹配尾巴"的正/反向回归测试。

## 3. 蛋白比对遇空序列抛异常，整份分析 500【高】

- **位置**：`pipeline.py:1507-1508`、`1550`；`_protein_alignment` 位于 `1396`（`aligner.align(ref_prot, alt_prot)[0]`）
- **证据**：确证变体让 CDS 第一个密码子变成终止（如 ATG→TAG，或缺失吃掉起始区）时，`stop_idx == 0` → `alt_prot == ""`。`t4_pipeline.py` 场景 c：
  ```
  File ".../pipeline.py", line 1550, in _build_cds_reports
  ValueError: sequence has zero length
  ```
  `ref_prot` 为空（CDS < 3 bp 或 ORF 退化）时同理。
- **影响**：单样品接口直接 500（`_run_full_analysis` 没有兜底）；批量模式下该质粒变成"分析失败"。偏偏最严重的突变（起始即终止）拿不到任何结论。
- **修改建议**：`_protein_alignment` 开头处理空串：任一为空时直接返回 `identical=0, first_diff=0, subs=[], gap_residues=len(ref)+len(alt)`；`_build_cds_reports` 整体包 try/except，失败时降级成单条"CDS 判读失败"报告，不要拖垮整个 analyze。

## 4. tracy 交叉 basecall 修剪后为空时崩溃【高】

- **位置**：`pipeline.py:1760-1767`
- **证据**：
  ```python
  if len(cb) >= MIN_WINDOW:
      cs, ce = _trim_by_quality(cb, cq, min_q)
      caln = align_read(cb[cs:ce], ref, cq[cs:ce])   # 未检查修剪后长度
  ```
  主路径在 1736 行专门防过"空序列送进 PairwiseAligner → ValueError → 500"，这条分支漏了。`t4` 场景 d（把 tracy 返回值模拟为全 Q5）：`CRASH ValueError sequence has zero length`。另外 `len(cq[cs:ce]) - 1 == -1` 时 `qi=-1` 会取到最后一个元素。
- **影响**：生产镜像内置 tracy（注释如是说），一条低质量 ab1 就能让整份多 read 分析失败。
- **修改建议**：`if ce - cs >= MIN_WINDOW:` 再比对；整个 tracy 交叉段用 `try/except Exception` 包住，失败就按"未运行 tracy"降级。

## 5. 替换变异缺少跨 read 反证，结论与共识矛盾【高】

- **位置**：`pipeline.py:1987-1992`（`cross_read_conflict` 只对 insertion/deletion 计算）；`_variant_confidence` 145-210；`_build_consensus` 1194-1220；`_conclusion_summary` 244/252；`_build_cds_reports` 1505（用 confirmed 变体重建，而不是用共识）
- **证据**：`t4` 场景 b：read A 在 401 位报 C>A（Q45），反向 read B 以 Q45 覆盖同位、读到的是参考 C。
  ```
  401 substitution C > A support 1 conf high cross_conflict None
  consensus@401: C   diffs: []
  conclusion head: ✗ 结论：检出 1 处确认差异
  ```
  共识说"无差异"，头部结论却说"✗ 确认差异"；CDS 报告会按这个 confirmed 变体判"蛋白不一致"。
- **影响**：单 read 的替换伪影（染料团、峰压缩）会被判成不合格克隆，批量模式归入「错误」文件夹；同一份报告里共识、CDS、头部结论三者口径不一。
- **修改建议**：
  1. 把 `_clean_cover_reads` 推广到替换：其他 read 在该参考位有比对列、碱基 = 参考、Q ≥ CROSS_CHECK_MIN_Q、且不在 END_MARGIN 内 → 记 `cross_read_conflict`，置信度降为 low，或至少不允许 high；
  2. CDS 重建与头部结论要么统一以共识为准，要么统一以 confirmed 变体为准（建议：只有共识也支持的变体才算 confirmed）。

## 6. 参考序列长度无上限 —— 比对内存/CPU DoS【高】

- **位置**：`routes/sequencing_routes.py:70`（只限文件 20 MB）、`239`、`277`（只检查 ≥50 bp）；`aligner.py:46-53`（正、反向各做一次带回溯的完整局部比对）
- **证据**：`t1` 第 4 部分（900 bp read）：
  ```
  10000  bp → 0.38 s, 峰值 36 MB
  100000 bp → 3.83 s, 峰值 364 MB
  300000 bp → 11.1 s, 峰值 1092 MB
  ```
  20 MB 的 FASTA 可以塞进约 2000 万 bp 参考。单请求最多 24 条 read，每条比两次。
- **影响**：一个请求就能让后端 OOM 或长时间占满线程池（单样品、批量、设计/载体三个入口都受影响）。
- **修改建议**：
  1. 在 `_analyze_endpoint` / `_run_batch` 里加 `MAX_REF_LEN`（质粒场景 50–100 kb 足够），超限返回 400；
  2. 比对先用 `aligner.score()`（线性内存）比正反两个方向，只对胜出方向调用 `align()`；
  3. 长参考可先用 k-mer 种子定位，再截取 ±(read 长 + 余量) 窗口做局部比对（同时也能顺带解决第 11 条的环形问题）。

## 7. 批量接口无总字节上限；整理包缓存条数无上限【高】

- **位置**：`routes/sequencing_routes.py:483-491`（只限文件数 400、单文件 20 MB，逐个读满）；`556`（`_BATCHES[...] = {"zip": ...}`）；`79`（`MAX_BATCH_CACHE_BYTES` 只限单批）
- **证据**：400 × 20 MB = 8 GB 会先全部读进 `bytes`，之后才分组、才分析；整理包又保留一份原始字节（zip 内副本），每批最多 256 MB，15 分钟内批次数不受限。
- **影响**：单个请求或几个并发请求即可耗尽内存。
- **修改建议**：增加请求级累计字节上限（如 500 MB，读取过程中累计超限就 400）；`_BATCHES` 加全局字节上限（LRU 淘汰）或把整理包落到临时文件；最好在反向代理层也限 body 大小。

## 8. 进程内存储跨线程无锁读写【高】

- **位置**：`routes/sequencing_routes.py:415`（`_run_batch` 在 `run_in_threadpool` 中调用 `_register_analysis`）；`198-226`（对 `_ANALYSES` 做 sorted/迭代/删除）；`82-90`（`_sweep_expired` 遍历两个 dict）；`551`（`_pack` 线程内 `_ANALYSES[it["analysis_id"]]`）；`555`（`_pack` 线程内 `_sweep_expired()`）
- **证据**：事件循环线程（单样品分析、列表、删除）和线程池线程（批量、打包）同时增删、遍历同一个 dict。CPython 下"迭代期间 dict 被另一线程改变大小"会抛 `RuntimeError: dictionary changed size during iteration`。另外 `MAX_STORED=200`，而一批最多 120 个分组：两个并发批量，或批量本身超过 `ANALYSIS_TTL`=15 分钟（400 文件 × ~1 s/read 已接近这个量级），都会让 `_pack` 取记录时 KeyError → 跑完全部分析后才 500。
- **影响**：并发下偶发 500；大批量分析结束才失败，前面的计算全部白费。
- **修改建议**：用 `threading.RLock` 包住 `_ANALYSES/_BATCHES` 的读写（或改成线程安全的 LRU 类）；`_run_batch` 内收集 records 直接传给 `_pack`，不要事后回查全局 dict；批次内的记录在打包完成前不参与淘汰（pin）。

## 9. 同名/重复上传 read 被当作独立证据【中】

- **位置**：`pipeline.py:1806-1813`（按 key 计 `support_reads`）、`1926`/`1927`/`1929`（`by_read`、`mixed_by_read`、`ref2call_by_read` 都以 filename 为键）；`scripts/batch_sequencing_report.py:109`（以 `path.name` 为名，递归扫描时不同子目录可能同名）
- **证据**：`t4` 场景 a：同一个文件上传两次 → `support 2`。在 `_variant_confidence` 中 `support >= 2 and q >= 30` 即判 high，单 read Q30-39 的变体就这样从 medium 升到 high。两个**不同内容但同名**的文件，后一个会覆盖 `by_read[filename]`，峰证据可能取自另一条 read。
- **修改建议**：analyze 入口按内容 MD5 去重（重复的记入 errors 并提示）；内部以 read 索引而非文件名作键；同名不同内容时自动加后缀。

## 10. 合并变体只用首个 read 的证据定置信度【中】

- **位置**：`pipeline.py:1806-1813`（merged 保留第一个 read 的 `read`/`read_pos`）、`1935-1996`（`src = by_read[v["read"]]`）
- **影响**：两条 read 都报的变体，若先遍历到的那条在该位峰形差（如位于其末端），整个变体会因这条 read 的 `mutant_pct<70`、`mixed_positions` 或 `poly_merged_zones` 被判 low，另一条 read 的干净证据被忽略；反过来也会被最好的 read 掩盖矛盾。
- **修改建议**：merged 时保存每条支持 read 的 `(read, read_pos, quality)` 列表；逐 read 计算证据后聚合（如取最佳峰证据、任一 read 混合则标注），`support_reads` 只统计各自证据合格的 read。

## 11. 环形质粒：跨原点特征与跨原点 read【中】

- **位置**：`core/sanger/reference_parser.py:64-71`（GenBank 复合位置取 `location.start..end` 整体跨度）、`139-140`（SnapGene start>end 直接交换）；`pipeline.py:1445`（`feat_start > feat_end` 直接 continue）；`aligner.py`（参考按线性处理，见 `pipeline.py:1699` 注释）
- **证据**：`join(901..1000,1..50)` 的 CDS 解析结果为 `{'start': 1, 'end': 1000}`（脚本输出），即整条质粒；SnapGene 的跨原点特征被交换成互补区间（错误的区域）。
- **影响**：跨原点 CDS 的 CDS 报告会在整条质粒里找最长 ORF、给出完全无关的蛋白结论，所有变体都被注释到这个"CDS"里；跨原点 read 被局部比对截成一段，造成虚假覆盖缺口和末端伪差异。
- **修改建议**：解析时保留分段（`parts`）或用 `start>end` 表示跨原点并全链路支持；对环形参考比对时把参考拼成 `ref + ref[:read_len]`，比对坐标再取模回 1..L。

## 12. 酶切位点注释窗口固定，indel 误报"破坏位点"【中】

- **位置**：`core/sanger/annotator.py:252-276`
- **证据**：`alt_sites` 用的是 `alt_seq[lo:hi]`，与参考相同的窗口坐标；插入后右侧序列整体右移，原本在窗口右缘的位点被推出窗口。`t3_annot.py`：5 bp 插入（与位点无关，位点在其右侧约 10 bp）→ `enzyme_sites_lost = ['EcoRI', 'MluCI']`。反方向，缺失会把窗口外的位点拉进来，可能误报"新增"。
- **修改建议**：突变侧窗口取 `alt_seq[lo : hi + delta]`；只统计**跨越变异 junction** 的位点（`site_start <= junction < site_end`）。这样既不受窗口边缘影响，也不再需要 `_preserved` 的平移推断。

## 13. 同一密码子多处替换逐个注释，aa_change 错误【中】

- **位置**：`annotator.py:229-243`
- **证据**：GCA→TGA（两处相邻替换，合起来是终止）逐个注释为 `g:A2S`、`g:A2G`（`t3`）。CDS 报告用全部 confirmed 变体重建，是对的；但变体表和 `summarize_severity` 的结论行显示的是这些逐位错误结果。
- **修改建议**：注释前按 (CDS, 密码子) 把同一 read（或同一共识）的替换分组，一起应用后再翻译；变体行标注"与 X 位同密码子（MNV）"。

## 14. 基线校正插值逐窗口累积错位【中】

- **位置**：`core/sanger/signal.py:99-106`（`_fill_invalid`）
- **证据**：每段 `range(c, nxt + 1)` 都包含右端点，下一段又从 `nxt` 开始，每个窗口边界重复写入 1 个点，最后再 `[:n]` 截断。`t1`：本底设为 y=x、n=1000、step=50 时，`x=999` 处输出 980，误差 19 个采样点。真实 ab1 约 1.2–2 万采样点，末段错位 240–400 个采样点（约 20–30 个碱基）。
- **影响**：本底漂移明显时，read 后段扣除的是错位位置的本底；峰面积、mutant_pct、poly 压缩比等后段定量都会系统性偏差。
- **修改建议**：除最后一段外用 `range(c, min(nxt, n))`，最后一段用 `range(c, n)`；或者直接用 `numpy.interp(np.arange(n), centers, filled)`。补一个 `len(out)==n` 且线性本底误差≈0 的单测。

## 15. 峰图原始数据驻留内存过大【中】

- **位置**：`routes/sequencing_routes.py:73`（`MAX_TRACE_STORED = 40`）、`211`（`_trace_data` 为 Python int 列表）
- **证据**：4 通道 × 15000 采样点的 Python list 实测 2.41 MB / read；40 条记录 × 24 read ≈ 2.3 GB。批量模式还会额外持有 `res["traces"]` 直到注册完成。
- **修改建议**：存储前转成 `array('H')` / numpy uint16（约 120 KB / read），或者按字节总量淘汰而不是按记录条数；数据库模式下峰图只落库，内存不保留。

## 16. 共识导出 covered_only 坐标错位【中】

- **位置**：`routes/sequencing_routes.py:711-781`
- **证据**：`segs` 是参考坐标，但 `seq = record["consensus"]["sequence"]` 在含插入/缺失时长度与参考不同；FASTA 用 `seq[s-1:e]` 切片，GenBank 用 `i+1` 做 N 屏蔽，都把参考坐标当成共识坐标。
- **影响**：共识上游一旦有 indel，导出的"实测片段"会整体错位；GenBank 的 misc_feature 坐标也跟着错。
- **修改建议**：`_build_consensus` 同时输出 `ref_to_cons` 映射（每个参考位在共识中的起始索引），导出时用映射切片；或者导出前只按参考坐标做 N 屏蔽，再把共识差异逐个应用上去。

## 17. 访问控制缺口【中】

- **位置**：`routes/sequencing_routes.py:566-587`（`download_batch_report` 没有 user 依赖，也没有属主校验）；`93-99`（`owner_id is None` 即公开）；`656`（列表把所有匿名记录返回给任何人）
- **影响**：拿到 batch_id 的任何人（日志、浏览器历史、分享截图）都能下载整理包，里面有原始 ab1、参考图谱、信息表；匿名用户的分析（包括参考序列）对所有登录用户可见、可列出。
- **修改建议**：`_BATCHES` 记录 `owner_id`，下载时复用 `_can_access`；匿名记录默认只对创建会话可见（可用随机 token），不进公共列表。

## 18. 数据库落库阻塞事件循环；persist 未建表【中】

- **位置**：`routes/sequencing_routes.py:247`（异步函数里同步调用 `_register_analysis` → `sequencing_store.persist_record`）；`sequencing_store.py:62-95`（JSON 序列化 + zlib 压缩数 MB 峰图 + 同步 DB 提交 + `_prune` 每次 `count()`）；`sequencing_store.py:62` 没有调用 `_ensure_table()`（load/list/delete 都有）
- **影响**：每次单样品分析结束都会阻塞事件循环数百毫秒到数秒，所有其他请求一起卡住；启动时没跑 `init_db` 的环境里，持久化静默失败，只留一条日志。另外路由 docstring 写"15 分钟后自动删除"，数据库行却永久保留（最多 2000 条），对用户的数据保留说明不一致。
- **修改建议**：`_register_analysis` 用 `run_in_threadpool` 包裹（批量路径已在线程中）；`persist_record` 开头调用 `_ensure_table()`；`_prune` 改成每 N 次或定时执行；同步更新文档中的数据保留说明。

## 19. 比对实现的性能细节【低-中】

- **位置**：`aligner.py:47-50`
- **证据**：`len(alignments) == 0` 会触发 Biopython 统计全部最优路径数。cProfile（`t6_perf.py`，6 条 1 kb read、6 kb 参考）中 `__len__` 占 0.52 s / 5.75 s；正反向都做完整 `align()` 占 2.2 s。（Biopython 1.88 下 polyA/AT 重复未出现溢出，`t1` 第 3 部分。）
- **修改建议**：用 `aligner.score()` 判向，再对胜出方向 `next(iter(aligner.align(...)), None)`；不要调用 `len()`。

## 20. ABIF 解析健壮性【低-中】

- **位置**：`abif_reader.py:264-267`、`275`；内置回退解析器 `51-65`、`117-130`、`316-322`、`344-349`
- **证据（`t5_fuzz.py`，400 个截断/随机损坏样本）**：
  - Biopython 主路径：`AssertionError` 152 次、`UnicodeDecodeError` 15 次、成功 233 次，单例最慢 0.06 s；没有发现挂起或内存爆炸，异常都被 `analyze` 的宽泛 except 接住。但 Biopython 用 `assert` 做格式校验，在 `python -O` 下会失效，损坏文件将以未定义状态继续解析。
  - 内置回退解析器：399/400 "成功"，截断文件被静默接受；把 PBAS 的 data_size 改成 0xFFFFFFF0 也"成功"，`bases` 变成文件剩余全部字节（含 `\ufffd` 等非碱基字符）。
  - 内置解析器的类型码与 ABIF 规范不符：规范里 pString=18、cString=19、10/11 是 date/time、5 是有符号 int32、1 是无符号 byte；代码把 10/11 当字符串解码、18/19 未识别（`SPNM1` 返回原始 bytes，`str()` 后变成 `"b'\\x05…'"`），`("DLAB1", 1)` 的 tag 写错（应为 `("DLAB", 1)`）。
  - PLOC 越界值（10^9）与 0（→ −1）均被下游正确防护（`t5`），无崩溃。
- **影响**：主路径基本可用；回退路径（Biopython 导入失败时）会把损坏文件当成正常 read 分析，给出看似可信的错误结论。
- **修改建议**：`extract_read` 把所有解析异常统一转成 `AbiParseError`，并给出可读的"文件已损坏/截断"提示；内置解析器在 `data_offset + data_size > len(data)` 时报错而不是截断，类型码按规范修正，`bases` 校验只含 IUPAC 字符；提取后检查 `len(quality)`、`len(peak_indices)` 与 `len(bases)` 一致，不一致时写入 errors 或警告。

## 21. 共识投票：插入与替换落在同一 cell 互斥【低】

- **位置**：`pipeline.py:1214-1239`
- **证据**：插入投在 `votes[ref_pos]`（插入点右侧那一位），与该位的碱基票在同一字典里竞争；插入胜出时 1239 行追加的是**参考碱基**，该位上的替换票被丢弃（单 read 同时报"插入 + 紧随其后的替换"时，两票都是 weight+5，平票取先插入的键）。
- **修改建议**：插入票单独放在 `ins_votes[pos]`，与碱基票分开决胜。

## 22. 批量一句话结论把 CDS 内同义突变写成"编码区外"【低】

- **位置**：`core/sanger/batch.py:509-511`
- **证据**：`bad` 为空（蛋白一致）且存在 confirmed 变体时，统一写"编码区外 N 处确证差异"，但其中可能有 CDS 内的同义突变；非编码区的启动子/RBS/ori 变体也直接给"合格"。
- **修改建议**：按 `v["features"]` 区分 CDS 内同义与编码区外；对启动子/RBS/ori 等关键调控元件内的确证变体，至少降为"需复核"而不是"合格"（这一条属于产品口径，需要与使用方确认）。

## 23. 离线批处理脚本的路径与文件安全【低-中】

- **位置**：`scripts/batch_sequencing_report.py:77`、`299`（`out_dir / plasmid`，质粒名来自 Excel，未净化）；`341`（`wb.save(excel_path)` 原地覆盖原表）
- **影响**：质粒名含 `/`、`..` 会写到输出目录之外或建出意外子目录；含 `:` `*` `?` 时在 Windows 上直接异常。文档承诺"原始文件一律不移动、不删除、不覆盖"，实际原 Excel 被覆盖（虽然有备份）；Excel 正在 WPS/Excel 中打开时，全部分析跑完才在最后一步抛 PermissionError。同名质粒的多行会被重复复制、重复分析。
- **修改建议**：复用 `sequencing_report._safe_component` 净化目录名；默认把回填写到 `out_dir` 下的新文件，原表只读；开始前先探测 Excel 是否可写；按质粒名去重后再遍历。

## 24. 其他低优先级

| 位置 | 问题 | 建议 |
|------|------|------|
| `pipeline.py:2530-2532` | 空循环（`continue` 后无内容）死代码 | 删除 |
| `pipeline.py:1731-1741` | 文件缺 PCON（质量）时报"Q≥20 的碱基不足 50bp（修剪后仅 0bp）"，掩盖真正原因 | `quality` 为空时单独报"缺少质量值" |
| `pipeline.py:2595` | `has_mixed` 靠在结论文本里查子串"疑似混合样品"来判定 | 改用结构化标志 |
| `pipeline.py:1156-1161` | 每条 read 都同步跑 tracy basecall（超时 60 s）；24 条最坏 24 分钟 | 并行或限总时长，或仅对存疑 read 运行 |
| `aligner.py:140` | 反向 read 的 Q 映射假设 `len(quality) == len(read)` | 在 analyze 入口统一截齐 bases/quality/peaks |
| 前端 `MatchMap.vue:302` | `:key="'v'+ref_pos+type"` 在同位不同 alt/长度时重复 | key 加入 alt_base 与 length |
| 前端 `PolyCard.vue:153/179` | `:key="h.start"`、`:key="rc.filename"` 可能重复 | key 加入 period/unit、read index |
| 前端 `useSeqViz.ts:607-611` | 两端"不可信区"按参考坐标画 20 bp，而后端 END_MARGIN 是 read（修剪后）坐标，软剪切时不一致 | 修好第 2 条后按 read 坐标换算 |
| 前端 `useSeqViz.ts:616`、`765` | 每帧对每个双峰位点做 `cols.find`（O(n·m)） | 预建 `origIdx → col` 的 Map |
| `scripts/poly_signal_probe.py:62-64` | `pairs` 少于 4 个时 `pairs[:0]` 为空 → 中位数 0；`pairs[-0:]` 取到全表 | 样本不足时跳过分位统计 |
| `scripts/poly_baseline.py` | `_detect_trace_mapping` 用列表相等反查通道号，通道数据恰好相同时会错配 | 直接返回通道号映射 |
| `sequencing_report.py` 整理清单 CSV | 文件名/质粒名原样写入 CSV，以 `=`/`+`/`-`/`@` 开头时在 Excel 中可触发公式注入 | 对这些前缀加 `'` 转义 |

---

## 修复优先级建议

1. **立即**：#1（1:1 双峰漏检）、#2（软剪切坐标错位，前后端一起修）、#3/#4（两处崩溃，改动很小）、#6/#7（参考长度与批量总字节上限）。
2. **短期**：#5（替换互检，统一结论口径）、#8（存储加锁、批量记录 pin 住）、#9/#10（证据按 read 维度聚合）、#17（访问控制）。
3. **中期**：#11（环形支持）、#12/#13（注释精度）、#14/#15/#18（信号与资源）、其余低优先级项。

每项修复都建议补充对应的回归用例；`/workspace/work/review/05-sanger-scripts/` 下的脚本可以直接改写成 pytest。
