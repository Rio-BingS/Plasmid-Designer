# 交接文档

> **谁该读**：接手本项目的工程师、下一次 AI 会话、以及过一段时间回来的自己。
> **更新**：2026-10-31（`v2.35.0` 发版：限制性克隆预览三图分工 + 第三轮全量审计修复
> 批次（6 bug + 3 加固 + 2 配置 + 8 死代码，[留档](postmortems/2026-10-31-audit-round3-fixes.md)）。
>
> 本文只回答「现在在哪、怎么接、下一步做什么」。功能清单、API、环境变量看
> [README.md](../README.md)；工作规范与代码地图看 [AGENTS.md](../AGENTS.md)；
> 约束、挂账与历次审计索引看 [KNOWN_ISSUES.md](KNOWN_ISSUES.md)；
> 逐版本变化看 [CHANGELOG.md](../CHANGELOG.md)；图谱契约看 [MAP.md](MAP.md)。
> 下面凡与这些文档冲突处，以代码与实测为准，并顺手修正对应文档。

## 1. 一句话现状

单用户 / 小团队**自托管**的质粒构建设计与 Sanger 测序验证平台，当前 **v2.35.0**
（2026-10-29 发版并已部署上线）+ **main 上未发版的两个批次**：10-30/31 限制性克隆
预览三图分工（纯前端），10-31 第三轮审计修复（6 bug + 3 加固 + 2 配置 + 8 死代码，
见 [留档](postmortems/2026-10-31-audit-round3-fixes.md)）。功能完整可用、有持续的真实数据回归。
2026-10-31 复核实测（第三轮审计修复完成后）：后端 **1828 passed / 43 skipped / 0 failed**，
前端 **666 passed**，`vue-tsc --noEmit` **0 error**，build/尺寸预算门禁全绿。线上为 Docker Compose 部署（VPS，ssh 别名 `myvps`，
`/opt/plasmid-designer`），发版即 `git fetch --tags && git reset --hard <tag>` + 重建
backend/frontend 容器（流程见 §2「线上部署」行）。

`feature/snapgene-primer-view` 已快进合入 `main` 并随 v2.34.0 发版部署（序列视图引物
按 SnapGene 截图复刻、`full_sequence`、`MAP_ALGO_VERSION=8`、审计修复批次全量上线）。
v2.35.0（2026-10-31）= 限制性克隆预览三图分工（v2.34.1 后 main 累积的一批前端）
+ 第三轮全量审计修复（17 项，含 REDIS_ENABLED 缺省语义变化，见 CHANGELOG 升级注意）。

**当前最大缺口是批量页体量**：`src/frontend/src/views/BatchDesignView.vue` 已 **1318 行**
（预算顶格，本轮靠收敛 localStorage 读写到 batchHistory.ts 才保住）、连续两轮触发 CI
尺寸预算记账——预览表/载体池/参数区应拆成独立组件。见 §8 P1。

## 2. 事实清单（2026-10-26/27 实测，可直接引用）

| 项 | 值 |
|---|---|
| 版本 / 标签 | `v2.35.0`（2026-10-31：预览三图分工 + 第三轮审计修复。此前 v2.34.1（2026-10-29）：v2.34.0 引物视图+审计批次、v2.34.1 回滚健康检查误改。此前 v2.33.6：环形图引物按用户提供的 SnapGene 截图复刻——蓝底白字名牌 + 旁侧坐标、22 条全部标出且每条带坐标；含引物时标签栏宽度上限放宽到 42%；名牌渲染抽到 `PrimerLabel.vue`。v2.33.3–2.33.5 的纯文字/整体省略/洋红版均已废弃，复盘见 CHANGELOG；切点数徽标见 v2.32.0/1、上传解析链见 v2.32.2–4、克隆酶高亮见 v2.31.5/6、MAP_ALGO_VERSION=8；酶名大小写归一见 v2.31.3） |
| 后端测试 | **1828 passed / 43 skipped / 0 failed**（约 110 秒，2026-10-31；43 skipped 含本机 Windows 平台跳过的 deploy_backup 与真实数据回归的条件跳过） |
| 前端测试 | **666 passed**；`vue-tsc --noEmit` **0 error**；`npm run build` 通过（2026-10-31；npm audit 2026-10-29 实测 0 漏洞） |
| 运行时 | Python **3.12**（仓库根 `.venv`）+ Node 22+；FastAPI + Vue3/Vite |
| 本机后端启动前提 | **没有 `src/backend/.env`**：起服务前必须自己在环境变量里给 `SECRET_KEY`（`openssl rand -hex 32`），低熵值会被拒绝启动 |
| 依赖 | 只改 `requirements*.in`，再 `bash scripts/lock_requirements.sh` 生成 `--require-hashes` 锁文件 |
| 缓存版本常量 | `CODON_OPT_ALGO="v2.3"`、`ANALYSIS_ENZYMES_VERSION="v3"`（`app/cache.py`）、`MAP_ALGO_VERSION="8"`（`app/routes/map_routes.py`）；与代码的一致性由 `scripts/check_docs_sync.py` 门禁校验 |
| 业务数据 | `data/vectors/` 载体 YAML（带 `data_provenance`）、`data/codon_tables/` 4 物种、`data/features/common_features.json`（图谱元件库） |
| 真实测序数据 | 仓库外 `..\plasmid-designer V1.01\sequencing results`（不入库；本机在场 → 5 项回归真跑） |
| CI | `.github/workflows/ci.yml`（push main / PR 触发）：**尺寸预算** `scripts/check_file_size.py`（新文件 ≤900 行、白名单 11 个只许变小，`scripts/size_budgets.json` 记账）→ `pytest tests -q` → pip-audit；前端 `vue-tsc` + `vitest` + **build** + `npm audit` ×2 全阻断。**本地只跑测试不够，动代码后要把门禁本地预演一遍**（[ci-red-chain 留档](postmortems/2026-10-26-ci-red-chain-and-backup.md) 教训） |
| 线上部署 | **Docker Compose on VPS**（`ssh myvps`，`/opt/plasmid-designer`）：`git fetch --tags && git reset --hard <tag>` → `cd deploy/docker && docker compose build backend frontend` → `up -d --force-recreate backend frontend`（前端必须 force-recreate）；四容器 plasmid-backend(127.0.0.1:18000)/frontend(80)/db/redis，版本横幅 `🧬 Plasmid Designer API vX.Y.Z`（`docker logs plasmid-backend`）。cron 备份 `deploy/scripts/backup.sh`（v2.29.4 修复后恢复正常退出） |
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

- 线上是 **Docker Compose on VPS**（`ssh myvps`，`/opt/plasmid-designer`）。发版：
  `git fetch --tags && git reset --hard <tag>` → `cd deploy/docker && docker compose
  build backend frontend` → `up -d --force-recreate backend frontend`（前端必须
  force-recreate）；验 `docker logs plasmid-backend | grep 'Plasmid Designer API'`。
- 裸机 systemd（`deploy/bare/`）只是备选路线，线上未采用；旧流程「本地 build 传
  dist/ + systemctl restart」已废弃。
- nginx：`deploy/*/nginx.conf` 有 `location ^~ /api/map/`（请求体 17 m）等更新，改过
  就要一起上。新增环境变量要同步 `docker-compose.yml` 透传。

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

## 8. 建议的下一步（按优先级）

| 优先 | 事项 | 为什么 |
|---|---|---|
| **P1** | **拆分 BatchDesignView.vue**（1318 行）：预览表/载体池/参数区各自成组件 | 连续两轮 CI 尺寸预算记账（900→1195→1318），再涨就要频繁动白名单；拆完白名单应只减不增 |
| **P1** | CI 尺寸预算纪律：动代码后本地先跑 `python scripts/check_file_size.py`，超线先拆分或明确记账 | v2.30.0 又红过一次才记账；把预演纳入收尾动作可避免「本地绿 CI 红」复发 |
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
