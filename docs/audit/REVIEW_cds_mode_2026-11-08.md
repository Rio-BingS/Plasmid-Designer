# 目的基因模式审核（`24d4d2e`）

- 范围：`core/sanger/cds_extract.py`、`analyze_sequencing_upload` 中 `cds_mode` 分支、`verdict` / `evidence` / `batch` 对 `analysis_target` 的处理、`frontend/src/utils/cdsMode.ts`，以及相关测试。
- 方法：读代码，加合成 AB1 探针（`work/probe_cds.py`、`work/probe_cds2.py`）。参考均为 4000bp 质粒，read 为 Q45 的干净 read。

## 结论

这个模式的出发点是对的：聚焦目的基因、按文件名自动识别、给出蛋白级结论。**问题在实现方式：把参考裁成「CDS ±150bp」**。真实 read 通常有 700–1000bp，必然超出这个窗口。T6/T19 之后，超出部分会被判成「高质量序列未比对上 → 连接/结构异常」。结果是：**干净样品在目的基因模式下会被判 ✗ 不合格**。

建议换掉实现：**不再裁参考，改为全质粒比对，目标 CDS 只作为判定范围**。坐标换算为 CDS 内坐标只放在显示层。这和上一份「可选测序范围」方案是同一套机制，目的基因模式就成了其中一个预设。

## 问题清单

### P0-A：read 超出 ±150bp 侧翼，干净样品判 ✗

| 探针 | 输出 |
|---|---|
| GeneX 1001–1600；正反两条 read 覆盖 700–1600 / 1000–1900，序列全对 | **✗ 不合格**：GeneX_F.ab1 5′端 151bp、GeneX_R.ab1 5′端 150bp 高质量序列未比对上参考（连接/结构异常） |
| 同一基因，read 800–1700，含 1 处替换 | 替换正确检出，但 blocking 里多出一条「5′端 51bp 未比对」 |
| 对照组：read 刚好落在 851–1750（窗口内） | ✓ 目的基因测序正确 |
| 对照组：同一条长 read 用全质粒模式 | ✓（这里 blocking 还带着 roi_unverified，即复审 P0-2） |

- **根因**：`FLANK_BP = 150`，远小于 read 长度。`pipeline` 里 `check_unaligned_hq_segment` 和 `_check_partial_alignment` 用的是裁剪后的参考，split-read 也只在裁剪后的参考里重定位（`classify_segment(..., ref, ...)`），所以超出部分只能报成「未知序列」。
- **影响**：只要 read 起点离 CDS 超过 150bp，就会误报不合格。用载体通用引物（T7/T7-Term/pET-Up、M13）测时，这几乎总会发生。真实 MX 批次的 T7-Term、pET-Up 两条 read 大概率也会触发（推断：真实数据不在仓库里，未实跑）。
- **为什么测试没拦住**：
  - `test_cds_mode.py` 的合成 read 只有 102bp，永远不会超出侧翼；
  - `test_real_cds_mode_mx` 只断言变体集合和蛋白判定相同，**不断言 verdict 和结论**；
  - 而且真实数据缺失时整个用例被 skip，Linux/CI 上根本不跑。

### P0-B：跨原点的 CDS 不加侧翼，接合区插入被吞

- 环状质粒上跨原点的 CDS（`join(3701..4000,1..300)`）会被 `cds_candidates` 归为「多段 CDS」，走 `len(parts) > 1` 分支：拼接成纯编码序列，**不加侧翼**。
- 探针：ATG 前 1bp 插入、终止密码子后 1bp 插入，**都没有检出**（variants 为空），被吞进未比对段。干净样品同样判不合格（原因同 P0-A）。
- 这正是 docstring 里点名要防的「MX 首端插入被软剪切吞掉」，在跨原点场景下又出现了。
- docstring 说「多段 CDS 接缝留待真实数据出现后再议」，但跨原点不是真正的 join，只是坐标绕回。

### P1-C：靠近原点的 CDS，侧翼被截断

`span_start = max(1, s - FLANK_BP)` 没有考虑环状绕回。CDS 从 31 开始时，5′侧翼只剩 30bp（探针实测），锚定上下文不足，超出窗口的部分也更长。

### P1-D：坐标只有局部坐标，无法对回图谱

- 结果用的是裁剪后片段的坐标：
  - 质粒 1300 位的替换报为 **450**；
  - 负链基因中质粒 1100 位的替换报为 **651**（方向也反了）；
  - ATG 前的插入报为「150 insertion（5′侧翼）」。
- 用户在 SnapGene 或图谱上找不到对应位置。CDS 内坐标对蛋白分析有用，但应该**双坐标并列**：质粒坐标 + CDS 内 nt/aa 位置。

### P1-E：同一份结果，三个出口说法不一

跨原点探针中：
- verdict `status = fail`；
- 网页头行是「**△** 已比对区域与设计一致，但 1 条 read 有大段未比对……」；
- Excel 是「**不合格**：……」。

这违反了 T2「同一 status 驱动所有出口」的约定。`evidence` 头行在 `partial_alignment` 分支里自行选了 △。

### P1-F：窗口内其他注释全部丢失

- 输出的特征只有目标 CDS 和两段侧翼。窗口里的 T7 promoter、RBS、标签都没了，这些元件里的差异不会被标注。
- verdict 还会因为看不到抗性和 ori，额外报一条 info「参考未注释抗性/复制原点等关键元件」，这是噪音。
- 嵌套目标选不了：显式指定 `6xHis`，或融合蛋白里嵌套的 luciferase，会报「参考文件内没有名为……的 CDS」，原因是 `cds_candidates` 做了嵌套去重，显式选择也只在去重后的列表里查。

### P2-G：自动匹配会静默选错

- `resolve_auto` 中，参考文件名命中唯一候选就直接返回，**不检查 read 名是否指向别的基因**。
  - 探针：参考 `GFP_backbone`，read `mCherry_F` → 选中 GFP，没有任何提示。
- 显式名称有重名时（两个特征都叫 `CDS`，或同名两拷贝），按 `norm_stem` 取第一个，同样静默。
- 建议：参考名和 read 名冲突时判为歧义；所有自动选择都在结论里写明依据，现在依据只写在特征 description 里，界面上不显眼。

### P2-H：覆盖面

- 批量路径（`run_batch_groups`）不支持目的基因模式。
- 接合区按 ATG 和终止密码子 ±30bp 计算，不是真实的插入边界（酶切位点、RBS/Kozak 往往在 ATG 上游更远处）。
- 同义突变判「不合格」（策略问题，前面已列为待决）。
- `cds_extract.py` 里有两行重复的 `import re`（小事）。

## 已确认没问题

- 负链 CDS：反向互补和 CDS 区间换算正确，替换能检出。
- CDS 内替换、ATG 前插入（非跨原点）都能正确检出并标为 junction_variant。
- 自动匹配在歧义时不猜（`S1-AmpR-F` + `S1-GeneX-R` → 判歧义）；未命中时给出候选清单。
- 前端 `cdsMode.ts` 的 read 与 CDS 交集计算支持 `ref_segments`。

## 修复建议

### 根本方案（推荐，与「可选测序范围」合并）
1. 目的基因模式不再替换参考：**全质粒、按真实拓扑比对**，`scope = 目标 CDS + 接合区`，`source = cds_target`。
2. P0-A、P0-B、P1-C、P1-F 随之消失：read 的每一段都能在全质粒上定位，跨原点由环状比对处理，其他注释保留。
3. 显示层做坐标变换：变异同时给「质粒 1300 / GeneX c.300 / p.K100」。`resolved_regions` 现有的 offset 逻辑改成只做显示，不参与判定。
4. 只在质粒超过 `MAX_REF_BP` 时才裁剪：
   - 侧翼取 `max(1500, 最长 read 长度 + 200)`；
   - 环状质粒绕回取窗口；
   - split-read 和 unaligned 判定都用完整质粒；
   - 落在质粒窗口外但能连续定位的段，记为「窗口外，已定位」，不算异常。

### 短期补丁（如果暂时保留裁参考）
- `FLANK_BP` 改为 1000 以上，或随 read 长度调整；
- 环状质粒按绕回取侧翼；
- 跨原点 CDS 先把质粒旋转到使 CDS 连续，再按单段处理；
- `classify_segment` 传入完整质粒。

### 测试要补
| 场景 | 期望 |
|---|---|
| 目的基因模式 + 900bp 干净 read，超出窗口 | pass（锁 P0-A） |
| 载体通用引物 read（起点离 ATG 300bp） | pass |
| 跨原点 CDS，ATG 前 / 终止后 1bp 插入 | 检出，判 junction_variant |
| 近原点 CDS | 侧翼完整，结果与全质粒模式一致 |
| 同一组 read，目的基因模式与全质粒 + scope=CDS | **verdict 与 headline 一致**（不只是变体集合一致） |
| 参考名与 read 名指向不同基因 | 判歧义 |
| 三个出口 | 同一 status，同一符号 |
| `test_real_cds_mode_mx` | 增加 verdict.status 断言；真实数据补一份脱敏子集进仓库，否则 CI 永远不跑 |
