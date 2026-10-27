# 交接文档

> **谁该读**：接手本项目的工程师、下一次 AI 会话、以及过一段时间回来的自己。
> **更新**：2026-10-27（`v2.30.1`；批量设计两步工作流收尾，CI 自 v2.18.0 起连续红灯清零，
> 本次修复留档见 §7.4，下一步见 §8）。
>
> 本文只回答「现在在哪、怎么接、下一步做什么」。功能清单、API、环境变量看
> [README.md](../README.md)；工作规范与代码地图看 [AGENTS.md](../AGENTS.md)；
> 约束、挂账与历次审计索引看 [KNOWN_ISSUES.md](KNOWN_ISSUES.md)；
> 逐版本变化看 [CHANGELOG.md](../CHANGELOG.md)；图谱契约看 [MAP.md](MAP.md)。
> 下面凡与这些文档冲突处，以代码与实测为准，并顺手修正对应文档。

## 1. 一句话现状

单用户 / 小团队**自托管**的质粒构建设计与 Sanger 测序验证平台，当前 **v2.30.1**
（2026-10-27 发布，标签与 `main` 均已推送，CI 绿）。功能完整可用、有持续的真实数据回归。
2026-10-26 实测：后端 **1796 passed / 43 skipped / 0 failed**（CI 口径，含此前被
尺寸预算挡住从未跑过的 `test_deploy_backup.py`），前端 **82 文件 / 636 passed**，
`vue-tsc --noEmit` **0 error**。线上为 Docker Compose 部署（VPS，ssh 别名 `myvps`，
`/opt/plasmid-designer`），发版即 `git fetch --tags && git reset --hard <tag>` + 重建
backend/frontend 容器（流程见 §2「线上部署」行）。

**当前最大缺口是批量页体量**：`src/frontend/src/views/BatchDesignView.vue` 已 **1318 行**、
连续两轮触发 CI 尺寸预算记账——预览表/载体池/参数区应拆成独立组件。见 §8 P1。

## 2. 事实清单（2026-10-26/27 实测，可直接引用）

| 项 | 值 |
|---|---|
| 版本 / 标签 | `v2.30.1`（2026-10-27：复杂批量载体池 + 表格参数列对齐；CI 修复链 v2.29.3–v2.29.5 见 §7.4） |
| 后端测试 | **1796 passed / 43 skipped / 0 failed**（约 105 秒；43 skipped 含本机 Windows 平台跳过的 deploy_backup 与真实数据回归的条件跳过） |
| 前端测试 | **82 个文件 / 636 passed**；`vue-tsc --noEmit` **0 error** |
| 运行时 | Python **3.12**（仓库根 `.venv`）+ Node 22+；FastAPI + Vue3/Vite |
| 本机后端启动前提 | **没有 `src/backend/.env`**：起服务前必须自己在环境变量里给 `SECRET_KEY`（`openssl rand -hex 32`），低熵值会被拒绝启动 |
| 依赖 | 只改 `requirements*.in`，再 `bash scripts/lock_requirements.sh` 生成 `--require-hashes` 锁文件 |
| 缓存版本常量 | `CODON_OPT_ALGO="v2.3"`、`ANALYSIS_ENZYMES_VERSION="v3"`（`app/cache.py`）、`MAP_ALGO_VERSION="4"`（`app/routes/map_routes.py`）；与代码的一致性由 `scripts/check_docs_sync.py` 门禁校验 |
| 业务数据 | `data/vectors/` 载体 YAML（带 `data_provenance`）、`data/codon_tables/` 4 物种、`data/features/common_features.json`（图谱元件库） |
| 真实测序数据 | 仓库外 `..\plasmid-designer V1.01\sequencing results`（不入库；本机在场 → 5 项回归真跑） |
| CI | `.github/workflows/ci.yml`（push main / PR 触发）：**尺寸预算** `scripts/check_file_size.py`（新文件 ≤900 行、白名单 11 个只许变小，`scripts/size_budgets.json` 记账）→ `pytest tests -q` → pip-audit；前端 `vue-tsc` + `vitest` + **build** + `npm audit` ×2 全阻断。**本地只跑测试不够，动代码后要把门禁本地预演一遍**（§7.4 教训） |
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
| v2.16.2 | SnapGene 在线库「搜得到、导不进」 | 序列 ID 校验是字符白名单，而索引里 18%（488/2822）的 `url` 带 `( ) + ' &`；搜索不校验 ID、导入才校验，症状就是「列表里有、点了 422」。改为排除法，编码与主机白名单不变（详见 §7.3） |

主线规律：**v2.13 之后几乎全是「真实数据驱动的精修」**——测序口径、图谱边界、SnapGene
操作体验。新增功能少、改行为多，所以**每次动口径都要同时处理缓存版本与回归断言**；
而「搜得到、导不进」这类问题的共性是**两条路径的校验口径不一致**（§7.3）。

## 4. 代码从哪看起（按任务选入口）

1. **设计流水线**：`app/design_service.py`（单条与批量共用：密码子优化 → 引物/oligo → 组装 → 存储）
2. **密码子与引物算法**：`core/codon_optimizer.py`、`core/primer_designer.py`（口径与暂缓项见 `docs/ALGORITHM_ROADMAP.md`）
3. **质粒图谱**：后端 `core/plasmid_map/` + `app/routes/map_routes.py`（缓存键在 `map_routes.py`），前端 `src/map/`（契约 `plasmid-map/1`，见 `docs/MAP.md`）
4. **Sanger 测序**：`core/sanger/`（`pipeline` 主流程、`reliable_zone` 可信区、`aligner` 双向判向与环状折回、`annotator`、`batch` 归组），网页端点与离线脚本共用
5. **载体库与外部导入**：`core/vector_library.py` + `app/vector_import.py`（落盘形状**唯一入口**）+ `app/online_import.py`（来源清单/许可/缓存）+ 两个路由模块（载体库侧 `online_source_routes`、图谱侧 `map_online_routes`）
6. **在线来源取字节层**：`core/online_sources.py`（限速 + 体积上限 + https 主机白名单 + 逐跳校验；ID 校验在这里，见 §7.3）
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

**部署**（细节见 `deploy/*/README.md`）：

- 前端：**本地** `npm run build`，把 `src/frontend/dist/` **整目录替换**到线上（VPS 上构建会 OOM）；
  `dist/` 不入库。v2.13 起产物是多个路由 chunk，**只覆盖 index.html 会白屏**。
- 后端：`git pull` + `systemctl restart`（未开 reload）；新增环境变量要同步 `docker-compose.yml` 透传。
- nginx：`deploy/*/nginx.conf` 有 `location ^~ /api/map/`（请求体 17 m）等更新，改过就要一起上。

## 6. 一碰就容易出事的约束（Top 9）

1. **改结果口径必须递增缓存版本**：`ANALYSIS_ENZYMES_VERSION`（分析/酶表，现 `v3`）、
   `MAP_ALGO_VERSION`（图谱，现 `4`，在 `map_routes.py`）、`CODON_OPT_ALGO`（现 `v2.2`）。
   否则用户命中旧缓存、以为改动没生效。只改 `data/codon_tables/*.yaml` 时内容摘要自动变化。
2. **载体 YAML 契约**：`features[]` 必须带 `name`；出处写结构化 `data_provenance`
   （描述里那句可读标注不算）；拓扑按真实值落 `topology`；落盘撞车 409，同 ID 重导入才覆盖。
   形状统一由 `app/vector_import.py` 决定。
3. **外部数据源合规边界**：SnapGene（默认开启、只读须登录、落库须管理员、只低频单次读取）、
   Addgene（无凭据不绕登录，凭据仅单用户自托管、仅 https 发往 addgene.org、禁自动重定向）、
   UniProt 只做「反查核酸入口」。**不要为了"顺手"改成匿名/批量/定时同步**。
4. **在线来源的 ID 校验用排除法，不要写字符白名单**：索引里的 `url` 与集合 `path` 真实取
   值带 `( ) + ' &`（`pET-28a(+)` 等 18%）；只禁 `/ \ ? # %` 与控制字符 + 限长。
   搜索/列表路径与导入路径的口径必须一致，否则又是「搜得到、点不开」（§7.3）。
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
   `--reload` 没开导致「改了没生效」。三条都已踩过，见 §7.1 与 AGENTS「已知坑」。

## 7. 已修问题留档（避免下次按直觉误判）

### 7.1 三项 Windows 红灯（2026-10-18 清账）

| 测试 | 真实病因 | 修法 |
|---|---|---|
| `test_batch_report_disk_store::test_report_written_to_private_dir_and_not_kept_in_memory` | POSIX 权限位断言：Windows 不表达 rwx，目录恒报 `0o777`（`511 ≠ 448`），`chmod` 只切只读位 | 新增 `_assert_private()`，只在 `os.name == "posix"` 时逐位校验（Linux 覆盖不减） |
| `test_sequencing_upload_sessions::test_private_permissions_and_sequence_disk_names` | 同上 | 同上 |
| `test_session_finalize_lazy_read::test_later_group_files_not_read_while_first_group_aligns` | **不是线程时序**（初判错了）：用例自身的 spy 用 `p.rsplit("/", 1)[-1]` 取文件名，而 `DiskBlob.path` 在 Windows 用反斜杠 → 取到整条路径 → `names[path]` 抛 KeyError，异常发生在 `analyze` 里、被批次管线记成「分析失败」→ 断言处看到的是「analyze 从未调用」 | 改用 `os.path.basename(p)` |

### 7.2 两项真实数据回归（v2.16.0 口径变化，不是回归）

用旧口径（`20/20/5000/5000`，≈只扣首尾 20 bp）对照跑同一批数据：

| read | 旧口径 | 新口径（`30/60/700/900`） |
|---|---|---|
| `10570-2-seqF` | 22 处，其中尾部 841–957 占 13 | 9 处，全部 ≤63 |
| `pET-Up` | 31 处，其中尾部 797–1015 占 21 | 10 处，全部 ≤75 |
| `T7-Term` | 16 处，其中尾部 853–971 占 12 | 4 处（61–66） |

→ 差值全是 **700 bp 之后的尾部噪声**，v2.16.0 有意不再计入。断言改为锁新口径本身
（报出的位点必须落在该 read 可信区内、计数锁 `[4, 9, 10]`），并显式断言
`"互检矛盾" not in conclusion`，把「已改成直接排除」钉住。

### 7.3 SnapGene 在线库「搜得到、导不进」（v2.16.2）

- **现象**：能搜到质粒，点导入/预览返回 `422 序列 ID 无效：pET-28a(+)`；图谱页与载体库页一致。
- **根因**：`core/online_sources.validate_plasmid_id` 用**字符白名单**（`[A-Za-z0-9_.\- ]`），
  而 SnapGene 索引 `Plasmid@url` 普遍带 `( ) + ' &`。全量扫 19 个集合 2822 行 →
  **488 个 ID（18.4%）被拒**；`(` / `)` 各 412 次、`+` 96 次。
- **为什么搜索正常**：搜索与集合索引只校验 `set_id`，从不校验 `plasmid_id`——**校验只发生在
  导入/预览**，所以症状必然是「列表里有、点了就报错」。
- **修法**：改成排除法（只禁 `/ \ ? # %` 与控制字符，限长不变 64/120）；拼 URL 仍一律
  `quote(..., safe="")`、主机仍由 https 白名单钉死，**不新增外呼面**。载体库侧预览/导入与
  图谱页共用同一校验，一并修好。
- **验证**：上游实测 4 个特殊 ID（`pcDNA3.1(+)`、`pBC_KS(-)`、`pBluescript_II_KS(+)`、
  `pCR-XL-2-TOPO_(linearized)`）均 200 + SnapGene 魔法串；真实网络下 `pET-28a(+)` 走完整路由
  导入成功（5369 bp / 16 特征）；新增回归 `test_snapgene_ids_with_parens_and_plus_import`
  （搜索 → 图谱导入 → 载体库预览 → 落库 `snapgene_pET-28a`）。
- **教训**：ID 校验要拿真实索引全量回归，别按示例写白名单；「列出来」和「打开」两条路径的
  校验口径必须一致。

### 7.4 CI 连续红灯三层根因 + backup.sh 两个真实缺陷（v2.29.3–v2.29.5，2026-10-26）

- **现象**：CI 自 v2.18.0（2026-10-22）起连续 23 次失败，本地全量测试一直是绿的，无人察觉。
- **根因三层（逐层剥开）**：
  1. **门禁失败，测试从未跑到**：①文件尺寸预算被 v2.18→v2.29 的功能迭代打穿
     （6 个文件越线，其中 `construct_assembly.py` 与 `BatchDesignView.vue` 硬超 900 行默认
     预算）；②`npm audit --audit-level=high` 被 happy-dom 17.x 的 CVE-2026-33943（high）
     卡住 → 尺寸预算 `--refresh` 记账 + happy-dom 升 21.0.4。
  2. **`tests/test_deploy_backup.py` 从未真正运行过**：Windows 被 `skipif(platform=="win")`
     跳过、CI 被尺寸预算挡在测试步骤之前。修好门禁后首个 Linux 运行即暴露测试自身两处 bug：
     `run_backup` 调 bash 时漏传脚本路径（`--keep` 被 bash 当成自己的选项）、fake docker 按
     参数固定位置匹配与真实调用 `docker compose -f <path> ps` 对不上。
  3. **CI runner 环境差异**：Ubuntu runner 自带真实 sqlite3，「无 sqlite3 走拷贝回退」的
     用例删掉假工具后 `command -v` 仍找到真的 → 真拒绝伪造 DB 文件。修法：把子进程 PATH
     收紧到只剩测试沙箱（并补齐脚本依赖的 dirname 等符号链接）。
- **连带修复的 backup.sh 两个真实生产缺陷**（Linux 实测暴露）：`set -u` 下 PostgreSQL 模式
  走到 uploads 打包段 `DATA_DIR` 未定义（只在 SQLite 分支赋值）→ 备份完成后退出 1；保留
  策略在某前缀无备份时 `ls` 退出码 2 被 `pipefail` 误判为失败。cron 备份自此恢复正常退出。
- **验证**：VPS（Ubuntu/py3.10）实测 4 passed；CI run #106 起回绿。
- **教训**：①**被 skip 的测试等于不存在的测试**——写入平台相关测试时就该在目标平台跑一次；
  ②**本地绿 ≠ CI 绿**——CI 独有的门禁（尺寸预算/audit/build）要在本地预演，改完代码先跑
  `python scripts/check_file_size.py`；③连续红灯无人看的根因是这几轮迭代只盯本地测试，
  没有「推完看一眼 CI」的收尾动作。

### 7.5 合成分子取向簿记的根治（v2.26.x，2026-10-26）

- **现象**：全基因合成的合成 oligo 两端与构建体图谱对不上（用户 SnapGene 实测反馈），
  v2.26.0/v2.26.2 两次修复都只对了一半（负链装配时同源臂左右互换且各自反互补）。
- **根因**：同源臂计划里有两套取向——`arm_5_seq/arm_3_seq` 是「基因取向」（供 PCR 引物），
  合成分子误用了它；本质是**用簿记推坐标，而构建体本身坐标自洽**。
- **修法（v2.26.3 架构性根治）**：合成分子直接取**构建体上的现成段落**（片段±侧翼），
  oligo↔图谱的对应关系由构造保证，不再做任何取向换算；连带 v2.26.4 修了环状构建体
  跨原点场景（旋转后右连接点贴字符串末端，取段/命中判定/图谱轨道都要环绕语义）。
- **教训**：凡是「从既成结果反推」的场景，直接取既成结果的段落，别重建——簿记迟早左右互换。

## 8. 建议的下一步（按优先级）

| 优先 | 事项 | 为什么 |
|---|---|---|
| **P1** | **拆分 BatchDesignView.vue**（1318 行）：预览表/载体池/参数区各自成组件 | 连续两轮 CI 尺寸预算记账（900→1195→1318），再涨就要频繁动白名单；拆完白名单应只减不增 |
| **P1** | CI 尺寸预算纪律：动代码后本地先跑 `python scripts/check_file_size.py`，超线先拆分或明确记账 | v2.30.0 又红过一次才记账；把预演纳入收尾动作可避免「本地绿 CI 红」复发 |
| P2 | 批量二期候选：每条序列更多参数覆盖、服务端批量历史列表 API（现为 localStorage 本地恢复，换设备不可见） | 用户工作流可能需要 |
| P2 | 白名单历史大文件拆分收敛（design_service.py 2235、primer_designer 1048 等） | 与 P1 同一轮重构做，白名单只减不增 |
| P3 | 批量执行并发度（现为串行逐条，100 条上限） | 常规基因每条 ~1s，暂够用 |

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
