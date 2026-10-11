# 常见质粒元件库（图谱自动注释）

`common_features.json` 是图谱自动注释（`core/plasmid_map/annotate.py`）比对用的
元件库，由 `scripts/build_feature_library.py` **生成，勿手改**。

## 收录原则

- 只收录**可核验来源**的序列，不手写任何序列：
  1. 仓库自带载体库 `data/vectors/*.yaml`：按 YAML 特征坐标从载体序列截取，
     上游来源见各文件的 `data_provenance`（SnapGene 质粒库公开页面 / NCBI）；
  2. NCBI GenBank 公开记录：记录 `accession.version` 与坐标，可在
     `https://www.ncbi.nlm.nih.gov/nuccore/<accession>` 复核。
- 每条记录的 `sources` 列出全部来源；同一序列出现在多个来源时只保留一条。
- 构建时逐条校验，任何一项不符脚本直接失败：
  - 蛋白编码元件（`check: orf`）必须是完整 ORF（ATG/GTG/TTG 起始、末尾终止、
    无内部终止、长度为 3 的倍数）；融合标签（GST/MBP，`fusion`）须 ATG 起始且无终止；
  - 坐标核对（`seq:`）：如 T7 promoter 截取结果必须等于 `TAATACGACTCACTATAGG`；
  - 肽标签 / 蛋白酶位点（`match: peptide`）的肽段必须原样出现在来源区段的翻译里，
    库中保存编码该肽段的来源 DNA。

## 字段

| 字段 | 说明 |
|------|------|
| `id` | 稳定 id（名称 slug，重名加序号） |
| `name` / `type` | 显示名 / 契约 FeatureType（`docs/MAP.md`） |
| `match` | `dna`：DNA 比对（≥40 bp 走 k-mer 种子 + 局部比对，更短的精确查找）；`peptide`：六框翻译后查找 `peptide` |
| `sequence` | 元件 DNA（元件自身方向 5'→3'） |
| `peptide` | 仅 `match=peptide` |
| `notes` | 简短说明（随自动注释特征下发） |
| `sources` | `vector_library`（file/vector/location/upstream）或 `ncbi_genbank`（accession/location/url） |

库文件内容的 sha256 前 12 位即**库版本**，进入图谱结果缓存键：重建库后旧缓存
自动失效，无需手动递增版本。

## 当前内容（63 条）

- 载体库截取 DNA（40）：CMV enhancer/promoter、T7 promoter/terminator、bGH / SV40 poly(A)
  （两个变体）、f1 ori、SV40 promoter/ori、ori（pUC/ColE1）、AmpR 及其启动子、
  NeoR/KanR、KanR、lacI、lacI/lacIq 启动子、rop、lac operator（25 bp）、tac promoter、
  GST、Tn7L/Tn7R、GmR、Pc promoter、polyhedrin promoter、5'/3' LTR、HIV-1 Psi、RRE、
  cPPT/CTS、EMCV IRES、PuroR、WPRE、GAL1 promoter、CYC1 terminator、URA3 及启动子、2μ ori
- NCBI GenBank（DNA 14 + 肽段 6）：EGFP（U55762）、mCherry（AY678264）、BSD（D83710）、HygR（K01193）、
  ZeoR（X52869）、CmR（V00622）、TcR（J01749）、lacZα（L09137）、lac promoter /
  CAP binding site / lac operator 17 bp（OM304286）、attB1/attB2（PV983739）、
  MBP（MZ215989）、PreScission site（U78872）、TEV site（MK533800）、FLAG（PQ065984）、
  HA（OR519721）、Myc（MZ215986）、V5（OQ547858）
- 载体库肽段（3）：6xHis、T7 tag、thrombin site（pET-28a）

未收录（找不到可核验的公开记录）：AcV5 tag、RBS（7 bp 太短，随机序列中误报过多）、
p15A ori（公开记录只有比常用注释大得多的区段）。

## 扩展

1. 在 `scripts/build_feature_library.py` 的 `VECTOR_SPECS`（载体库特征）、
   `NCBI_SPECS`（GenBank 区段，1-based 闭区间 + 链）或 `PEPTIDE_SPECS`（肽段）
   末尾追加一行，填名称、契约类型、校验方式与一句说明；
2. 仓库根目录运行（需联网访问 NCBI E-utilities，下载会缓存到 `--cache` 目录）：

   ```bash
   python scripts/build_feature_library.py            # 重写 common_features.json
   python scripts/build_feature_library.py --check    # 只校验，不写文件
   ```

3. 跑 `tests/test_plasmid_map_annotate.py`，确认既有检出未回归后一起提交脚本与 JSON。
