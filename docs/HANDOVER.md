# 交接文档

> **谁该读**：接手本项目的工程师、下一次 AI 会话、以及过一段时间回来的自己。
> **更新**：2026-11-10（`v2.37.0-alpha.4`：**Sanger scope v3 W0–W16 收口**——
> 三态范围模型/范围重算端点与记忆/三出口统一/Excel 新六列/范围条与三层
> 色带/复核队列与一键确认/p_real 三档/真实 AB1 子集基准，变更明细见
> CHANGELOG 2.37.0-alpha.4；上一版 2026-11-08 `v2.37.0-alpha.3` 复审修复）
> 
> 假 ✓ 三处——整质粒部分覆盖/verified 口径/低 AF 混合静默，加首端硬区
> 连接段上报、p_real 展示改评分、结论分组结构化、基准集独立正例与诚实
> 声明、五条防回潮约束登记），变更明细见 CHANGELOG 2.37.0-alpha.3；
> alpha.2 全量修复史见同文件与 docs/audit/PROGRESS.md）。
>
> 本文只回答「现在在哪、怎么接、下一步做什么」。功能清单、API、环境变量看
> [README.md](../README.md)；工作规范与代码地图看 [AGENTS.md](../AGENTS.md)；
> 约束、挂账与历次审计索引看 [KNOWN_ISSUES.md](KNOWN_ISSUES.md)；
> 逐版本变化看 [CHANGELOG.md](../CHANGELOG.md)；图谱契约看 [MAP.md](MAP.md)。
> 下面凡与这些文档冲突处，以代码与实测为准，并顺手修正对应文档。

## 1. 一句话现状

单用户 / 小团队**自托管**的质粒构建设计与 Sanger 测序验证平台，当前分支
`experiment/preview-visual-tuning` 上 **v2.37.0-alpha.4**（2026-11-10 scope v3 收口；
alpha.2 = 2026-11-07 全量修复收口）：
**Sanger 判读链路全量修复收口**（goal_sanger_full_fix_v2 T0-T25）——证据三态
（rejected/confirmed/unresolved）+ 结构化 verdict 统一四出口、verified 覆盖
口径、可信区信号驱动、校准 P(真实) 四档行动、证据包、引物校验、混合判定
重构（染料肩峰不再误升「疑似混合」）、解析层口径修正（PLOC 峰心原值/峰型
估 Q）与基准集 CI 断言（33/33 对角、FPR=0）；「待定」结论明显增多是预期
（诚实呈现证据不足）。上一版 v2.37.0-alpha.1（2026-11-05：目的基因模式 +
结论覆盖资格门）；**main 仍停在 v2.35.0**，alpha 验收后合回 main 发正式版。
2026-11-08（alpha.3 时点）实测：后端 1975 passed / 46 skipped / 0 failed / 0 xfailed
（约 152 s；46 skipped = 42 deploy 需 bash/sh + 1 snapgene-reader 分支不可
触发 + 3 契约测试对不经 analyze 场景的显式登记），
前端 693 passed，`vue-tsc --noEmit` **0 error**，build/尺寸预算/文档同步
门禁全绿。**当前基线见下方 v3.1 段落。**
线上为 Docker Compose 部署（VPS，ssh 别名 `vps-direct`（直连 23.95.78.18；旧别名 myvps 已不存在；ControlMaster 残留时加 `-o ControlMaster=no -o ControlPath=none`），
`/opt/plasmid-designer`），发版即 `git fetch --tags && git reset --hard <tag>` + 重建
backend/frontend 容器（流程见 §2「线上部署」行）。

`feature/snapgene-primer-view` 已快进合入 `main` 并随 v2.34.0 发版部署（序列视图引物
按 SnapGene 截图复刻、`full_sequence`、`MAP_ALGO_VERSION=8`、审计修复批次全量上线）。
v2.35.0（2026-10-31）= 限制性克隆预览三图分工（v2.34.1 后 main 累积的一批前端）
+ 第三轮全量审计修复（17 项，含 REDIS_ENABLED 缺省语义变化，见 CHANGELOG 升级注意）。

**当前最大缺口是批量页体量**：`src/frontend/src/views/BatchDesignView.vue` 已 **1318 行**
（预算顶格，本轮靠收敛 localStorage 读写到 batchHistory.ts 才保住）、连续两轮触发 CI
尺寸预算记账——预览表/载体池/参数区应拆成独立组件。见 §8 P1。

goal v3（scope 报告）**W0–W16 全部收口**（2026-11-10）：范围三态模型
（declared/measured，范围外「未评估」）、范围重算端点与范围记忆表
（`sequencing_scope_memory`，alembic a1b2c3d4e5f6）、三出口统一（网页头行/
Excel/前端卡片全部由 verdict.status 导出）、Excel 回填新六列（验证范围/
范围来源/范围内验证覆盖/缺口原因/范围外差异/补测建议）、范围预设条与
MatchMap 三层色带（W13 前端）、复核队列三类收口与一键确认留痕（W14，
headline「人工确认 N 处」）、p_real 三档展示（W15）、KNOWN_ISSUES 防回潮
约束 15 条（W15）、真实 AB1 子集入仓（W16，`tests/data/real_ab1/`，MUTPK
3 处真实确证变异敏感度锁定）。场景库 72 场景全绿（xfail 清零）；Windows
全量 2070 passed / 0 failed / 46 skipped；SANGER_ALGO_VERSION 保持 v14
（W11–W16 未改判定阈值/真值）。逐阶段数字与刻意未做事项见
docs/audit/PROGRESS_v3.md 与 goal 终版汇报。

**v3.1（scope 复审修复，2026-11-10，分支 `fix/sanger-scope-v3.1`）**：
复审 b39b46a 的 3 个 P0 + 7 个 P1 + 3 个 P2 逐条落地（X0–X7）。核心三处：
①**峰形门控**（`core/sanger/peak_shape.py` + `params/peak_shape.json`）——
尾端峰展宽不再误判混合（判据 `FWHM ÷ 相邻峰间距 > 1.0` 连续 ≥15bp）；
②**复核队列三级聚合 + 结论行预算**（`review.py` / `conclusion_fold.py`）——
真实子集队列 330→14 项、MX→3 项，网页结论限 6 行；
③**头行唯一来源 `verdict.headline`**——Excel 结论列 = 去符号的 headline，
三出口逐字同源。另有 indel 双坐标（左对齐 + HGVS 3′，`indel_norm.py`）、
范围记忆按用户隔离 + DELETE 端点（`mutual_support.py` 处理接合区双侧
独立 indel 互证）。**SANGER_ALGO_VERSION v14→v15**。
实测（两平台同分支同用例集）：
- **Windows** 后端 **2093 passed / 0 failed / 46 skipped / 1 xfailed**（约 247 s，
  46 skipped = 42 deploy 需 bash/sh + 1 snapgene-reader 分支 + 3 契约测试显式登记）
- **Linux**（VPS，`/tmp/pdtest/repo`，含真实 V1.01 数据）后端 **2135 passed /
  0 failed / 4 skipped / 1 xfailed**（约 521 s；多出的 42 项是部署 shell 测试与
  真实数据回归在 Linux 上真正执行）

场景库 67 个场景（88 个测试函数）**87 passed / 1 xfailed**，两平台一致；唯一
xfail 是 CM3——其 r2 读段构造 `ref[0:300] + ins2 + ref[301:450]` 跳过 `ref[300]`，
read 与参考等长，序列上等价于替换而非插入；修它必须改 X0 冻结的场景构造
（goal 的停止条件），**待所有者裁决**。逐项取证见 docs/audit/PROGRESS_v3.1.md。

**门禁调整（2026-11-10）**：解除「单文件 ≤900 行」限制。推进期不为行数
强制拆分——`scripts/check_file_size.py` 改为只提示不阻断（`--top N` 看
最大的几个），CI 只打印清单。大文件重构清单见 KNOWN_ISSUES「未解决」第 7 条。

## 2. 事实清单（2026-10-26/27 实测，可直接引用）

| 项 | 值 |
|---|---|
| 版本 / 标签 | 分支 `experiment/preview-visual-tuning`：`v2.37.0-alpha.3`（2026-11-08：复审修复 8 条——覆盖闸收紧/双峰地板 0.18/首端连接段上报/结论分组结构化/基准集诚实化）。此前 `v2.37.0-alpha.2`（2026-11-07：Sanger 判读链路全量修复 T0-T25）。此前 v2.37.0-alpha.1（2026-11-05：目的基因模式 + 结论覆盖资格门）、v2.36.1-alpha.1（2026-11-04：轨道宽度两轮弹性求解）、v2.36.0（2026-11-04：预览三图分工）。**main 停在 v2.35.0**（2026-10-31：预览三图分工 + 第三轮审计修复。此前 v2.34.1：v2.34.0 引物视图+审计批次、v2.34.1 回滚健康检查误改。更早的 v2.33.x 引物名牌复刻系列见 CHANGELOG） |
| 后端测试 | **Windows 2093 passed / 46 skipped / 0 failed / 1 xfailed**（约 247 秒）；**Linux 2135 passed / 4 skipped / 0 failed / 1 xfailed**（约 521 秒，VPS，含真实数据）。2026-11-10 分支 `fix/sanger-scope-v3.1` 实测；Windows 的 46 skipped = 42 deploy 需 bash/sh + 1 snapgene-reader 分支不可触发 + 3 契约测试显式登记，Linux 上这 42 项真正执行。场景库 67 个场景/88 个测试函数 = 87 passed + 1 xfailed（CM3，构造 off-by-one 待裁决），两平台一致 |
| 前端测试 | **693 passed**；`vue-tsc --noEmit` **0 error**；`npm run build` 通过（2026-11-08；npm audit 2026-10-29 实测 0 漏洞） |
| 运行时 | Python **3.12**（仓库根 `.venv`）+ Node 22+；FastAPI + Vue3/Vite |
| 本机后端启动前提 | **没有 `src/backend/.env`**：起服务前必须自己在环境变量里给 `SECRET_KEY`（`openssl rand -hex 32`），低熵值会被拒绝启动 |
| 依赖 | 只改 `requirements*.in`，再 `bash scripts/lock_requirements.sh` 生成 `--require-hashes` 锁文件 |
| 缓存版本常量 | `CODON_OPT_ALGO="v2.3"`、`ANALYSIS_ENZYMES_VERSION="v3"`（`app/cache.py`）、`MAP_ALGO_VERSION="8"`（`app/routes/map_routes.py`）、`SANGER_ALGO_VERSION="v15"`（`core/sanger/pipeline.py`，T1 起 v1→v2、T11 v2→v3、T12 v3→v4、T15 v4→v5、T24 v5→v6、T25 v6→v7、复审 R1-R4 v7→v8、goal v3 W1/W2 v8→v9、W3-W5 v9→v10（pass 契约、af 分带、同义可配、headline/element_verdicts）、W6/W7 v10→v11（缺口 cause 归因、scope_* 拆分、引物逐 read 映射）、W8 v11→v12（cds_mode 全质粒+scope、junctions 入需测范围）、W9 v12→v13（resolve_auto 歧义、显式选择未去重、变体 coords 双坐标）、W10 v13→v14（scope 来源推断 infer_scope、parse-reference 预设、Form scope、批量验证范围列）、v3.1 v14→v15（峰形门控、队列三级聚合、头行唯一来源、indel 双坐标、范围记忆按用户隔离）写入 result `algo_version`，口径变更递增）；与代码的一致性由 `scripts/check_docs_sync.py` 门禁校验 |
| 业务数据 | `data/vectors/` 载体 YAML（带 `data_provenance`）、`data/codon_tables/` 4 物种、`data/features/common_features.json`（图谱元件库） |
| 真实测序数据 | 仓库外 `..\plasmid-designer V1.01\sequencing results`（不入库；本机在场 → 5 项回归真跑） |
| CI | `.github/workflows/ci.yml`（push main / PR 触发）：文件尺寸**统计**（`scripts/check_file_size.py`，2026-11-10 起只提示不阻断）→ `pytest tests -q` → pip-audit；前端 `vue-tsc` + `vitest` + **build** + `npm audit` ×2 全阻断。**本地只跑测试不够，动代码后要把门禁本地预演一遍**（[ci-red-chain 留档](postmortems/2026-10-26-ci-red-chain-and-backup.md) 教训） |
| 线上部署 | **Docker Compose on VPS**（`ssh vps-direct`，`/opt/plasmid-designer`）：`git fetch --tags && git reset --hard <tag>` → `cd deploy/docker && docker compose build backend frontend` → `up -d --force-recreate backend frontend`（前端必须 force-recreate）；四容器 plasmid-backend(127.0.0.1:18000)/frontend(80)/db/redis，版本横幅 `🧬 Plasmid Designer API vX.Y.Z`（`docker logs plasmid-backend`）。cron 备份 `deploy/scripts/backup.sh`（v2.29.4 修复后恢复正常退出）。**有迁移的版本要先迁移再重建容器**，见下方「数据库迁移」。**当前线上代码是 `fix/sanger-scope-v3.1` 分支（2026-11-10 v3.1 部署，所有者决定保持），不是常驻分支——下次按 tag 重置前先确认这个分支是否已合回 main/实验分支** |
| 远程仓库 | `https://github.com/Rio-BingS/Plasmid-Designer.git`（2026-10 用户改了 GitHub 用户名，旧地址 `gui123a1/...` 已失效，勿再引用） |

## 3. 版本主线（v2.12 之后）

| 版本 | 主题 | 关键点 |
|---|---|---|
| v2.12.0 | 在线序列来源导入 | SnapGene 在线库 / Ensembl / UniProt 反查 / Addgene；结构化出处、拓扑保真、写入口限管理员 |
| v2.13.0 | v2.12 审查修复 + 能力增强 | 在线来源 Cookie/重定向加固、`SNAPGENE_ONLINE_ENABLED` 独立开关、落盘撞车 409、前端**路由级拆包**（主包 481→192 kB） |
| v2.14.0 / 2.14.1 | SnapGene 集合选择体验 | 集合改为可输入组合框；开关**改回默认开启**（仍仅登录可用、落库管理员） |
| v2.15.0 | 跨集合按质粒名搜索 | 19 个集合按质粒名检索（首查 ~20 s，之后 1 小时缓存）；未知集合名不再误报 502，Cloudflare 520–527 统一中文提示 |
| v2.15.1 | 测序结论与图谱显示修复 | 结论写清差异类型、互检改看 ±25 bp 区域质量、单 read 差异可判为测序误差直接排除、拼接只显示实测片段（参考填充灰显）、环形图谱放大不再被裁 |
| v2.16.0 | read 首尾可信区逐条判定 | 首 30 bp 恒不可信、31–60 bp 按 Q 值/峰型判定、700 bp 后默认不可信（701–900 灰区）；「高级参数」可调四个边界（`reliable_zone`），read 概况表新增「可信区」列 |
| v2.16.1 | 测序结论坐标口径统一 | 混合双峰详情行（修剪后 read 坐标）与结论尾部（原始 read 坐标）不一致，同一条结论里两处「可信区」自相矛盾；不同时一并写出原始区间（`可信区 13–682（原始 31–700）`） |
| v2.16.2 | SnapGene 在线库「搜得到、导不进」 | 序列 ID 校验是字符白名单，而索引里 18%（488/2822）的 `url` 带 `( ) + ' &`；搜索不校验 ID、导入才校验，症状就是「列表里有、点了 422」。改为排除法，编码与主机白名单不变（详见 [snapgene-search-import-mismatch 留档](postmortems/2026-10-18-snapgene-search-import-mismatch.md)） |

> **v2.17–v2.30 不在本表**（逐版变化见 [CHANGELOG.md](../CHANGELOG.md)）：批量设计
> 大升级（FASTA/Excel 上传、合并引物订购表、简单/复杂模式、载体池与行级参数）、
> 合成分子取构建体段落根治、CI 红灯链清理（[ci-red-chain 留档](postmortems/2026-10-26-ci-red-chain-and-backup.md)）。本表的「主线规律」总结写于
> v2.16 时代，v2.27 起批量功能线是「用户工作流驱动的新增」，规律已不适用。

主线规律：**v2.13 之后几乎全是「真实数据驱动的精修」**——测序口径、图谱边界、SnapGene
操作体验。新增功能少、改行为多，所以**每次动口径都要同时处理缓存版本与回归断言**；
而「搜得到、导不进」这类问题的共性是**两条路径的校验口径不一致**（[snapgene 留档](postmortems/2026-10-18-snapgene-search-import-mismatch.md)）。

## 4. 代码从哪看起（按任务选入口）

1. **设计流水线**：`app/design_service.py`（单条与批量共用：密码子优化 → 引物/oligo → 组装 → 存储）
2. **密码子与引物算法**：`core/codon_optimizer.py`、`core/primer_designer.py`（口径与暂缓项见 `docs/ALGORITHM_ROADMAP.md`）
3. **质粒图谱**：后端 `core/plasmid_map/` + `app/routes/map_routes.py`（缓存键在 `map_routes.py`），前端 `src/map/`（契约 `plasmid-map/1`，见 `docs/MAP.md`）
4. **Sanger 测序**：`core/sanger/`（`pipeline` 主流程、`reliable_zone` 可信区、`aligner` 双向判向与环状折回、`annotator`、`batch` 归组），网页端点与离线脚本共用
5. **载体库与外部导入**：`core/vector_library.py` + `app/vector_import.py`（落盘形状**唯一入口**）+ `app/online_import.py`（来源清单/许可/缓存）+ 两个路由模块（载体库侧 `online_source_routes`、图谱侧 `map_online_routes`）
6. **在线来源取字节层**：`core/online_sources.py`（限速 + 体积上限 + https 主机白名单 + 逐跳校验；ID 校验在这里，见 [snapgene 留档](postmortems/2026-10-18-snapgene-search-import-mismatch.md)）
7. **横切**：`app/gating.py`（功能开关）、`app/rate_limit.py`（按「方法 + 路由模板」分档）、`app/cache.py`（内存/Redis 自动降级）、`app/auth/`（httpOnly Cookie + CSRF）

## 5. 怎么跑 / 怎么验 / 怎么发

```bash
# 后端（仓库根 .venv；改 app/core 代码必须重启——没开 --reload）
# 本机没有 .env，SECRET_KEY 自己给（低熵值会被拒绝启动）
cd src/backend && ../../.venv/Scripts/python -m uvicorn app.main:app --port 8000

# 测试
cd tests && ../.venv/Scripts/python -m pytest -q                  # 后端：约 100 s
cd src/frontend && npx vitest run && npx vue-tsc --noEmit         # 前端：约 15 s + 类型检查

# 冒烟（需后端在跑；20 项，覆盖注册/登录/设计/图谱/测序主链路）
powershell -ExecutionPolicy Bypass -File smoke_test.ps1

# 联网冒烟（默认不跑；真打 SnapGene/Ensembl/UniProt/Addgene，只读不写）
.venv/Scripts/python scripts/smoke_online_sources.py --run
```

**发布**（**用户明确说「发版」才做**；日常提交不碰版本号）：

1. 升版本号：`src/backend/core/_version.py`、`src/frontend/package.json`、
   `package-lock.json`（2 处）——共 4 个字符串，全部要改
2. 补 `CHANGELOG.md` 的 `## [x.y.z] - 日期` 与链接引用（`[Unreleased]` 的 compare 目标一并改）
   - **同步外围文档，防再挂账**：CHANGELOG 新增的链接引用行（缺了整条 compare 链会静默断链，
     v2.18–v2.30 曾断 13 个版本）；改了测试基线要 HANDOVER §1 与 AGENTS.md 两处同改；
     改缓存常量要同步 §2「缓存版本常量」行。**一致性由 `scripts/check_docs_sync.py`
     门禁机器校验（发版前必跑，红灯照提示修）**；工作区外壳 `../AGENTS.md`
     不维护版本号，无需改动（版本事实只收敛在 HANDOVER §1/§2）
3. `git commit -m "chore: 发布 vX.Y.Z"` → `git tag -a vX.Y.Z -F <说明文件>` → `git push --follow-tags`
4. `tests/test_version_sync.py` 会校验三处版本号一致，漏改必红

**推送**（这台机器上的真实情况，2026-10-19 验证）：

- 直连 `github.com` 的 TLS 是通的，但 git 全局配置 `http.proxy=127.0.0.1:7890`，而该代理的
  GitHub 路由经常不可用 → 表现为 `schannel: failed to receive handshake`；绕开代理又会撞上
  Windows 离线取不到 CRL 的 `0x80092013`。实测可用的组合：

```bash
git -c http.proxy= -c https.proxy= -c http.schannelCheckRevoke=false push --follow-tags origin main
```

- 只用 `http.schannelCheckRevoke=false` 关掉吊销查询，证书链校验仍在；不要改用
  `http.sslVerify=false`。推送前照例 `git pull --rebase` 并先看 `git log --oneline origin/main..HEAD`。

**部署**（细节见 `deploy/*/README.md`；线上现状见 §2「线上部署」行）：

- 线上是 **Docker Compose on VPS**（`ssh vps-direct`，`/opt/plasmid-designer`）。发版：
  `git fetch --tags && git reset --hard <tag>` → `cd deploy/docker && docker compose
  build backend frontend` → `up -d --force-recreate backend frontend`（前端必须
  force-recreate）；验 `docker logs plasmid-backend | grep 'Plasmid Designer API'`。
- 裸机 systemd（`deploy/bare/`）只是备选路线，线上未采用；旧流程「本地 build 传
  dist/ + systemctl restart」已废弃。
- nginx：`deploy/*/nginx.conf` 有 `location ^~ /api/map/`（请求体 17 m）等更新，改过
  就要一起上。新增环境变量要同步 `docker-compose.yml` 透传。

**数据库迁移（有 alembic 版本时必读）**

生产的表由 `init_db()` 的 `create_all` 建立，`alembic_version` 长期停在基线
`35a49b81767c`——**老库不重放建表迁移，只对齐版本号**。因此带新迁移的版本要按
下面的顺序走，否则会撞「relation already exists」或漏掉主键变更：

```bash
cd /opt/plasmid-designer/deploy/docker
# .env 里 MAIL_FROM 这类值带空格和尖括号，`source .env` 会语法错——用 compose 解析
DB=$(docker compose config --format json | python3 -c "import json,sys;print(json.load(sys.stdin)['services']['backend']['environment']['DATABASE_URL'])" | sed 's/@db:/@127.0.0.1:/')
SK=$(docker compose config --format json | python3 -c "import json,sys;print(json.load(sys.stdin)['services']['backend']['environment']['SECRET_KEY'])")
run() { docker run --rm --network host -e ALEMBIC_DATABASE_URL="$DB" -e SECRET_KEY="$SK" \
  --workdir /app/backend -v /opt/plasmid-designer/src/backend:/app/backend:ro \
  --entrypoint alembic docker-backend "$@"; }

pg_dump -U plasmid plasmid_designer > /root/pd-backup/pre-$(date +%Y%m%d).sql   # 先备份
run stamp <上一个 head>   # 老库：把新迁移的父版本标记为已应用（表已存在）
run upgrade head          # 只跑新增的那一条
```

- `stamp` 用**新迁移的父 revision**，不是 `head`。当前链：
  `35a49b81767c`（基线）→ `a1b2c3d4e5f6`（scope_memory 建表）→
  `b7c8d9e0f1a2`（加 owner_id）。v3.1 部署时是
  `stamp a1b2c3d4e5f6` 再 `upgrade head`。
- `--network host` + 把 `@db:` 换成 `@127.0.0.1:`：临时容器不在 plasmid-network 上，
  `--link` 对先有的旧容器已不可靠。
- 迁移在 PG 上必须显式 drop/create 主键：`batch_alter_table` 的 `copy_from` 只在
  SQLite 重建表时生效，PG 上走原生 ALTER——只写 `alter_column` 会「列加上了、
  主键没变」，多用户范围记忆互相覆盖。
- 迁移做完整轮 up→down→up 演练（v3.1 已在真实备份副本上跑过 3 轮）。

## 6. 一碰就容易出事的约束（Top 9）

1. **改结果口径必须递增缓存版本**：`ANALYSIS_ENZYMES_VERSION`（分析/酶表，现 `v3`）、
   `MAP_ALGO_VERSION`（图谱，现 `8`，在 `map_routes.py`）、`CODON_OPT_ALGO`（现 `v2.3`）。
   否则用户命中旧缓存、以为改动没生效。只改 `data/codon_tables/*.yaml` 时内容摘要自动变化。
2. **载体 YAML 契约**：`features[]` 必须带 `name`；出处写结构化 `data_provenance`
   （描述里那句可读标注不算）；拓扑按真实值落 `topology`；落盘撞车 409，同 ID 重导入才覆盖。
   形状统一由 `app/vector_import.py` 决定。
3. **外部数据源合规边界**：SnapGene（默认开启、只读须登录、落库须管理员、只低频单次读取）、
   Addgene（无凭据不绕登录，凭据仅单用户自托管、仅 https 发往 addgene.org、禁自动重定向）、
   UniProt 只做「反查核酸入口」。**不要为了"顺手"改成匿名/批量/定时同步**。
4. **在线来源的 ID 校验用排除法，不要写字符白名单**：索引里的 `url` 与集合 `path` 真实取
   值带 `( ) + ' &`（`pET-28a(+)` 等 18%）；只禁 `/ \ ? # %` 与控制字符 + 限长。
   搜索/列表路径与导入路径的口径必须一致，否则又是「搜得到、点不开」（[snapgene 留档](postmortems/2026-10-18-snapgene-search-import-mismatch.md)）。
5. **测序口径不可随手改**：可靠区默认 `30/60/700/900`、次要克隆 = r/(1+r)、共识只取实测
   覆盖区、单 read indel 降级、MX 批次「疑似混合样品」是真实判定。改口径必须同时更新
   `tests/test_real_data_regression.py` 的断言（先按旧口径跑一遍取证，再改）。
6. **测试隔离**：`tests/test_config_cors.py` 会 `reload(app.config)` → 打桩要同时打
   `app.config.settings` 与 `app.design_service.settings`，并清 `cache.backend.clear_pattern("vector:*")`；
   改 `site_settings` 必须用真实 `app.database.SessionLocal` 并在结束时恢复。
7. **匿名测序记录的 `access_token` 契约**：创建响应下发、前端 sessionStorage 带头、
   列表永不返回匿名 ID、遗留无 token 记录仅管理员可见。不要改回公开。
8. **前端 API 层即契约**：后端字段/路径变化要同步 `src/frontend/src/api/index.ts`（以及
   `src/map/api.ts`），否则表现为「点了没反应」。
9. **Windows 上的测试红先怀疑测试自己**：权限位断言（POSIX 才有）、用 `/` 拆路径、
   `--reload` 没开导致「改了没生效」。三条都已踩过，见 [windows-test-reds 留档](postmortems/2026-10-18-windows-test-reds.md) 与 AGENTS「已知坑」。

## 7. 已修问题留档（索引）

逐篇留档（现象/根因/修法/验证/教训）在 [postmortems/](postmortems/) 目录，2026-10-27
从本节拆出，本节只留索引；新留档直接在该目录建文件（命名 `YYYY-MM-DD-主题.md`），这里加一行。

| 日期 | 留档 | 一句话教训 |
|---|---|---|
| 2026-10-18 | [windows-test-reds](postmortems/2026-10-18-windows-test-reds.md) | POSIX 权限位断言等平台相关测试，写入时就该在目标平台跑一次 |
| 2026-10-18 | [real-data-baseline-shift](postmortems/2026-10-18-real-data-baseline-shift.md) | 回归基线要随口径更新钉住新口径本身，别锁旧口径 |
| 2026-10-18 | [snapgene-search-import-mismatch](postmortems/2026-10-18-snapgene-search-import-mismatch.md) | 「列出来」和「打开」两条路径的校验口径必须一致；ID 校验拿真实索引全量回归 |
| 2026-10-26 | [ci-red-chain-and-backup](postmortems/2026-10-26-ci-red-chain-and-backup.md) | 本地绿 ≠ CI 绿；被 skip 的测试等于不存在的测试；连续红灯要有「推完看一眼 CI」的收尾 |
| 2026-10-26 | [synthetic-oligo-orientation](postmortems/2026-10-26-synthetic-oligo-orientation.md) | 凡是「从既成结果反推」的场景，直接取既成结果的段落，别重建——簿记迟早左右互换 |
| 2026-10-29 | [audit-round2-fixes](postmortems/2026-10-29-audit-round2-fixes.md) | 可控字符串进日志/头/路径前必须过白名单（4 处同型）；双层读取 `in`+`[]` 非原子一律 `get()`；静默降级必须留 warning |
| 2026-10-31 | [audit-round3-fixes](postmortems/2026-10-31-audit-round3-fixes.md) | 审计报告逐条复核再动手（agent 自报 15 项有 4 项不成立）；死代码要查「有没有人用」不只「有没有错」；re-export 块是死导入重灾区 |
| 2026-11-05 | [cds-mode-coverage-gate](postmortems/2026-11-05-cds-mode-coverage-gate.md) | 截段当参考必须带边界上下文锚定（零侧翼吞边界变异）；结论头 ✓ 是资格判定不是默认值（目标没测到不给 ✓） |
| 2026-11-06 | [SANGER_AUDIT_2026-11-06.md](SANGER_AUDIT_2026-11-06.md) | Sanger 全链路只读审计（P0×5 复现 + P1×8 + P2×8）：证据模型缺「未决」第三态导致系统性漏报——修复清单与实施顺序在该文件，**尚未动手** |

## 8. 建议的下一步（按优先级）

| 优先 | 事项 | 为什么 |
|---|---|---|
| **P2** | **拆分 BatchDesignView.vue**（1318 行）：预览表/载体池/参数区各自成组件 | 体积已大但不是痛点；推进期不做（2026-11-10 解除行数门禁），留到维护期与 pipeline.py/design_service.py 一起集中重构 |
| **P0** | **Sanger 审计修复**（[SANGER_AUDIT_2026-11-06.md](SANGER_AUDIT_2026-11-06.md)）：P0×5 全部已复现——证据三态化+✓资格门 → verified 覆盖口径 → errors 进批量结论 → 混合互检对齐 `_region_ok` → `resolved_regions` 进 API 下发 | 5 项 P0 都是直接漏报（N 隐身、坏 read 判合格、真混合翻 ✓、低置信=伪影预判决、覆盖虚高），先固化探针为回归用例再修；改口径按 §6 约束 5 的纪律：先按旧口径取证、再改断言并递增结果版本 |
| **已解除** | 尺寸预算纪律（2026-11-10） | 项目处于**推进期**：为过行数门禁而先拆分，把资源花在结构调整上，反而拖慢功能推进。门禁改为**只提示不阻断**（`check_file_size.py` 永远退出码 0，`--top N` 看最大的几个）。大文件（pipeline.py 2871、design_service.py 2328、BatchDesignView.vue 1318 等）重构留到维护期集中做 |
| P2 | **目的基因模式推广到批量测序页**（讨论已定：缺图谱兜底优先，按名从参考/设计拉 CDS；`core/sanger/cds_extract.py` 的候选/匹配/提取可直接复用） | 单样品已随 v2.37.0-alpha.1 上线，批量工作流是自然延伸 |
| P3 | 多段 CDS（GenBank join）在目的基因模式下暂不加侧翼（纯编码序列拼接），接缝边界效应留观察 | 真实多段 CDS 数据出现后再议 |
| P2 | 批量二期候选：每条序列更多参数覆盖、服务端批量历史列表 API（现为 localStorage 本地恢复，换设备不可见） | 用户工作流可能需要 |
| P2 | 白名单历史大文件拆分收敛（design_service.py 2235、primer_designer 1048 等） | 与 P1 同一轮重构做，白名单只减不增 |
| P3 | 批量执行并发度（现为串行逐条，100 条上限） | 常规基因每条 ~1s，暂够用 |
| P3 | 第三轮审计遗留观察项（2026-10-31，见[留档](postmortems/2026-10-31-audit-round3-fixes.md)末节）：设计缓存 `design:{id}` 键无算法版本号（口径漂移靠 model_validate 兜底）；`adhoc_vector.overhang_bases` 的 cut_fwd 越界需属性测试验证；`run_design_task` 的兜底 FAILED 不含 plasmid_name 等展示字段 | 静态无法确认真 bug，留观察；动到相关代码时顺手验证 |

**明确不要主动做的事**（用户已表态或已定案）：扩展酶表（「不需要，已有在线导入」）、
SnapGene 批量镜像/定时同步、Addgene 代登录、iGEM Registry 接入（用户未表态）、
把仓库外侧的 `docs/` 与 `seqdemo_tmp/` 归档清理（用户要求保持原样）。

**挂账但非 bug**：同聚物内部「缺第几个 A」信息论不可解，维持「计数确证、定位存疑」；
Gibson 单位点恒正链组装（模型限制）、custom_arms 负链融合检测不覆盖、Golden Gate 合成
oligo 未含末端。

## 9. 接手第一天 checklist

1. 读 `AGENTS.md`（规范与坑）→ 本文 → `KNOWN_ISSUES.md` 的「防回潮约束」与「已接受风险」。
2. 配 `SECRET_KEY`，起后端与前端，登录后点一遍：设计 → 图谱 → 载体库 → 测序。
3. 跑三套测试并**记录本机基线**；与 §2 不一致就先查原因，别直接改断言。
4. 跑 `smoke_test.ps1`（后端需在跑）与（可选）`scripts/smoke_online_sources.py --run`。
5. 向所有者确认 §10 的问题（尤其线上主机与 `dist/` 由谁构建）。
6. 动手前看 `git log --oneline -20`：近两周的提交标题基本就是当前工作重心。

## 10. 需要向项目所有者确认的事

- 线上主机、SSH 方式、Cloudflare 配置（仓库里没有，也不该有）。
- 线上实例现在跑的是哪个版本？`dist/` 由谁构建、何时上传（决定 §8 P1 怎么做）。
- SnapGene 在线库是否保持默认开启（涉及网站条款，见 KNOWN_ISSUES 决策记录）。
- 线上实例是不是**单用户**自托管（若是多用户，应清空 `ADDGENE_SESSION_COOKIE`）。
- 下一次发版的节奏：日常修复先在 `main` 累积，还是每次修复都出补丁版。
