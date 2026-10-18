# 交接文档

> **谁该读**：接手本项目的工程师、下一次 AI 会话、以及过一段时间回来的自己。
> **更新**：2026-10-18（`v2.16.2`；5 项红灯已清账见 §7，SnapGene 「搜得到导不进」见 §7 末尾）。
>
> 本文只回答「现在在哪、怎么接、下一步做什么」。功能清单、API、环境变量看
> [README.md](../README.md)；工作规范与代码地图看 [AGENTS.md](../AGENTS.md)；
> 约束、挂账与历次审计索引看 [KNOWN_ISSUES.md](KNOWN_ISSUES.md)；
> 逐版本变化看 [CHANGELOG.md](../CHANGELOG.md)；图谱契约看 [MAP.md](MAP.md)。
> 下面凡与这些文档冲突处，以代码与实测为准，并顺手修正对应文档。

## 1. 一句话现状

单用户 / 小团队**自托管**的质粒构建设计与 Sanger 测序验证平台，当前 **v2.16.2**，
功能完整可用、有持续的真实数据回归；本机全量测试 **1491 passed / 39 skipped /
0 failed**（2026-10-18 起全绿，此前 5 项红灯的病因见 §7）。

## 2. 事实清单（2026-10-17 本机实测，可直接引用）

| 项 | 值 |
|---|---|
| 版本 / 标签 | `v2.16.2`（2026-10-18 的补丁版：修 SnapGene 在线库「搜得到、导不进」，后端一处校验，无 API 形状变化、前端无需重建） |
| 后端测试 | **1491 passed / 39 skipped / 0 failed**（Windows 本机全绿；CI 上 5 项真实数据回归被 skip） |
| 前端测试 | **67 个文件 / 531 passed**；`vue-tsc --noEmit` **0 error** |
| 运行时 | Python 3.12.5（仓库根 `.venv`）+ Node ≥ 22.12；FastAPI + Vue3/Vite |
| 依赖 | 只改 `requirements*.in`，再 `bash scripts/lock_requirements.sh` 生成 `--require-hashes` 锁文件 |
| 业务数据 | `data/vectors/`（9 份内置载体 YAML，带 `data_provenance`）、`data/codon_tables/`（4 物种）、`data/features/`（图谱元件库） |
| 真实测序数据 | 仓库外 `..\plasmid-designer V1.01\sequencing results`（不入库；本机在场 → 回归会跑） |
| 部署模板 | `deploy/docker`（Compose + nginx）、`deploy/bare`（Ubuntu systemd + nginx）；`deploy/scripts/`（deploy.sh / Makefile） |
| 线上部署 | 仓库里**没有**主机名/凭据。按 KNOWN_ISSUES「部署备忘」与 v2.15.0 的 Cloudflare 修复推断：1 核 2G VPS、裸机 systemd、前端 `dist/` 本地构建后上传、Cloudflare 前置——**接手须向所有者确认**，不要猜着连 |

## 3. v2.12 之后发生了什么（近一周主线）

| 版本 | 主题 | 关键点 |
|---|---|---|
| v2.13.0 | v2.12.0 审查修复 + 能力增强 | 在线来源 Cookie/重定向加固、`SNAPGENE_ONLINE_ENABLED` 独立开关、落盘撞车 409、图谱缓存与限额变量、前端路由级拆包（主包 481→192 kB）、图谱酶详情「修饰依赖型」 |
| v2.14.0 / 2.14.1 | SnapGene 集合选择体验 | 集合改成可输入组合框；开关**改回默认开启**（仍仅登录可用、落库管理员） |
| v2.15.0 | 跨集合按质粒名搜索 | `PLASMID_NAME` 跨 19 个集合检索（首查 ~20 s，之后 1 小时缓存）；未知集合名不再打成 502，Cloudflare 520–527 统一中文提示 |
| v2.15.1 | 测序结论与图谱显示修复 | 结论写清差异类型、互检改看 ±25 bp 区域质量、单 read 差异可判为测序误差直接排除、拼接结果只显示实测片段（参考填充灰显）、环形图谱放大不再被裁 |
| v2.16.0 | read 首尾可信区逐条判定 | 首 30 bp 恒不可信、31–60 bp 按 Q 值/峰型判定、700 bp 后默认不可信（701–900 灰区）；「高级参数」可调四个边界（`reliable_zone`），read 概况表新增「可信区」列 |
| v2.16.1 | 测序结论坐标口径统一 | 混合双峰详情行（修剪后 read 坐标）与结论尾部（原始 read 坐标）不一致，同一条结论里两处「可信区」自相矛盾；不同时一并写出原始区间 |
| v2.16.2 | SnapGene 在线库「搜得到、导不进」 | 序列 ID 校验是字符白名单，而索引里 18%（488/2822）的 `url` 带 `( ) + ' &`；搜索不校验 ID、导入才校验，故症状是「列表里有、点了 422」。改为排除法（只禁 `/ \ ? # %` 与控制字符），编码与主机白名单不变 |

主线规律：**v2.13 之后的工作几乎全是「真实数据驱动的精修」**——测序结论口径、
图谱边界、SnapGene 用户操作体验。新增功能少，改行为多，因此**每次动口径都要同时
处理缓存版本与回归断言**（2026-10-18 之前那 2 项真实数据红灯就是这么来的，见 §7）。

## 4. 代码从哪看起（按任务选入口）

1. **设计流水线**：`app/design_service.py`（单条与批量共用；密码子优化 → 引物/oligo → 组装 → 存储）
2. **密码子与引物算法**：`core/codon_optimizer.py`、`core/primer_designer.py`（口径与暂缓项见 `docs/ALGORITHM_ROADMAP.md`）
3. **质粒图谱**：后端 `core/plasmid_map/` + `app/routes/map_routes.py`，前端 `src/map/`（契约 `plasmid-map/1`，见 `docs/MAP.md`）
4. **Sanger 测序**：`core/sanger/`（`pipeline` 主流程、`aligner` 双向判向与环状折回、`annotator`、`batch` 归组与结论），网页端点与离线脚本共用
5. **载体库与外部导入**：`core/vector_library.py` + `app/vector_import.py`（落盘形状唯一入口）+ `app/online_import.py`（来源清单/许可/缓存）+ 两个路由模块（载体库侧、图谱侧）
6. **横切**：`app/gating.py`（功能开关）、`app/rate_limit.py`（按「方法 + 路由模板」分档）、`app/cache.py`（内存/Redis 自动降级）、`app/auth/`（httpOnly Cookie + CSRF）

## 5. 怎么跑 / 怎么验 / 怎么发

```bash
# 后端（仓库根 .venv；改 app/core 代码必须重启，未开 --reload）
# 需先在 src/backend/.env 或环境变量里设置 SECRET_KEY（openssl rand -hex 32），否则拒绝启动
cd src/backend && ../../.venv/Scripts/python -m uvicorn app.main:app --port 8000

# 测试
cd tests && ../.venv/Scripts/python -m pytest -q        # 后端
cd src/frontend && npx vitest run && npx vue-tsc --noEmit   # 前端

# 冒烟（需后端在跑；20 项，覆盖注册/登录/设计/图谱/测序主链路）
powershell -ExecutionPolicy Bypass -File smoke_test.ps1

# 联网冒烟（默认不跑；真打 SnapGene/Ensembl/UniProt/Addgene，只读不写）
.venv/Scripts/python scripts/smoke_online_sources.py --run
```

**发布**（用户明确说「发版」才做）：改 `src/backend/core/_version.py` + 前端
`package.json` / `package-lock.json` → 补 `CHANGELOG.md` 的 `[x.y.z]` 与链接引用 →
`git commit -m "chore: 发布 vX.Y.Z"` → `git tag -a vX.Y.Z -F <说明>` →
`git push --follow-tags`。`tests/test_version_sync.py` 会校验三处版本号一致。

## 6. 一碰就容易出事的约束（Top 8）

1. **改结果口径必须递增缓存版本**：`ANALYSIS_ENZYMES_VERSION`（酶表/分析）与
   `MAP_ALGO_VERSION`（图谱），否则用户命中旧缓存、以为改动没生效。
2. **载体 YAML 契约**：`features[]` 必须带 `name`；出处写结构化 `data_provenance`
   （描述里那句可读标注不算）；拓扑按真实值落 `topology`；落盘撞车返回 409、同 ID
   重导入才覆盖并标 `replaced`。形状统一由 `app/vector_import.py` 决定。
3. **外部数据源的合规边界**：SnapGene 的证据链与「已接受风险」决策（含
   `SNAPGENE_ONLINE_ENABLED` 默认 true、只读须登录、落库须管理员）、Addgene 凭据
   （仅单用户自托管、仅 https 发往 addgene.org、禁自动重定向、仅管理员可触发取序列）
   都写在 KNOWN_ISSUES「已接受风险 / 防回潮约束」。**不要为了"顺手"改成匿名或批量**。
4. **部署三件事**：前端必须本地 `npm run build` 后整目录替换 `dist/`（VPS 上构建会
   OOM）；`deploy/*/nginx.conf` 有更新（v2.13 新增 `location ^~ /api/map/`，17 MB
   上限）；新增的后端变量要同步 `docker-compose.yml` 透传，否则 `.env` 里改了不生效。
5. **测试隔离**：`tests/test_config_cors.py` 会 `reload(app.config)` —— 打桩配置要
   同时打 `app.config.settings` 与 `app.design_service.settings`，并清
   `cache.backend.clear_pattern("vector:*")`；改 `site_settings` 必须用真实
   `app.database.SessionLocal` 并在结束时恢复。
6. **测序口径不可随手改**：`END_MARGIN`/可靠区边界、次要克隆 = r/(1+r)、共识只取
   实测覆盖区、单 read indel 降级、MX 批次「疑似混合样品」是真实判定。改口径必须
   同时更新 `tests/test_real_data_regression.py` 的断言并递增算法版本。
7. **匿名测序记录的 `access_token` 契约**：创建响应下发、前端 sessionStorage 带头、
   列表永不返回匿名 ID、遗留无 token 记录仅管理员可见。不要改回公开。
8. **前端 API 层即契约**：任何后端字段/路径变化都要同步 `src/frontend/src/api/index.ts`
   （以及 `src/map/api.ts`），否则表现为「点了没反应」。

## 7. 测试红灯：2026-10-18 已清账（留档，避免下次按直觉误判）

接手时本机是 `1477 passed / 39 skipped / 5 failed`，现在 `1482 / 39 / 0`。三项 Windows
红灯里有**一项的真实病因与当时的判断不同**，两项真实数据红灯是 v2.15.1/v2.16.0 改口径后
没跟上的基线——两类都值得记住，别下次又按直觉改。

**(a) 三项 Windows 红灯**

| 测试 | 真实病因 | 修法 |
|---|---|---|
| `test_batch_report_disk_store::test_report_written_to_private_dir_and_not_kept_in_memory` | POSIX 权限位断言：Windows 不表达 rwx，目录恒报 `0o777`（`511 ≠ 448`），`chmod` 只切只读位 | 新增 `_assert_private()`，只在 `os.name == "posix"` 时逐位校验（Linux 覆盖不减） |
| `test_sequencing_upload_sessions::test_private_permissions_and_sequence_disk_names` | 同上 | 同上 |
| `test_session_finalize_lazy_read::test_later_group_files_not_read_while_first_group_aligns` | **不是线程时序**（初判错了）：用例自身的 spy 用 `p.rsplit("/", 1)[-1]` 取文件名，而 `DiskBlob.path` 在 Windows 用反斜杠 → 取到整条路径 → `names[path]` 抛 KeyError，异常发生在 `analyze` 里、被批次管线记成「分析失败」→ 断言处看到的是「analyze 从未调用」 | 改用 `os.path.basename(p)` |

**(b) 两项真实数据回归**（本机有 ab1 才跑、CI skip；用旧口径对照跑同一批数据取证）

| read | 旧口径（`20/20/5000/5000`，≈只扣首尾 20bp） | 新口径（`30/60/700/900`） |
|---|---|---|
| `10570-2-seqF` | 22 处，其中尾部 841–957 占 13 | 9 处，全部 ≤63 |
| `pET-Up` | 31 处，其中尾部 797–1015 占 21 | 10 处，全部 ≤75 |
| `T7-Term` | 16 处，其中尾部 853–971 占 12 | 4 处（61–66） |

→ 差值全部是 **700 bp 之后的尾部噪声**，v2.16.0 有意不再计入（CHANGELOG 2.16.0 明说
「双峰计数可能与旧版不同」）。所以这是**预期的口径变化**，不是回归。

断言改成锁新口径本身，而不是删断言：三条 read 都判出双峰、**报出的位点必须落在该 read
可信区内**、计数锁 `[4, 9, 10]`；结论侧改锁「互检排除 … 判为该 read 测序误差，不计入
判定」，并显式断言 `"互检矛盾" not in conclusion`（把「已改成直接排除」这件事钉住）。
`5269` high / `5076` medium / `6653`、`6666` low 这几条原断言保持不变、依旧通过。

**顺带的显示修正**：混合双峰详情行给的是修剪后 read 坐标，而结论尾部「可信区 31–700bp」
是原始坐标——同一条结论里两处「可信区」看起来互相矛盾。现在前者在两者不同时一并写出
原始区间（`可信区 13–682（原始 31–700）`）。

## 8. 建议的下一步（按优先级）

| 优先 | 事项 | 为什么 |
|---|---|---|
| P1 | 重新构建 `dist/` 并做一次线上验收 | 本机 `dist/` 还是 2026-10-08 的旧构建；v2.13 起前端已拆包，部署要整目录替换；v2.16.0 的新参数也只在后端 |
| P2 | 图谱「高级参数」可用性回访 | v2.16.0 新增可调边界，缺真实用户反馈 |
| P2 | 把跨平台断言收进共享 helper | 目前 `_assert_private` 在两个测试文件各一份，再出现第三处就该提取 |
| P3 | iGEM Registry 通道 / UniProt 蛋白级注释 | 目前 UniProt 只做「反查核酸入口」；iGEM 完全未接（用户未表态，先别自行动手） |
| P3 | SnapGene 中长期方案 | 现为「默认开启 + 登录可用」的风险接受态；长期应申请 GraphPad 授权或改用 NCBI 对应记录 |

**明确不要主动做的事**（用户已表态或已定案）：扩展酶表（「不需要，已有在线导入」）、
SnapGene 批量镜像/定时同步、Addgene 代登录、把 `ADDGENE_SESSION_COOKIE` 当多用户特性推广。

**挂账但非 bug**：同聚物内部「缺第几个 A」信息论不可解，维持「计数确证、定位存疑」。

## 9. 接手第一天 checklist

1. 读 `AGENTS.md`（规范与坑）→ 本文 → `KNOWN_ISSUES.md` 的「防回潮约束」与「已接受风险」。
2. 本地起后端（先配 `SECRET_KEY`）与前端，登录管理员账号，点一遍：设计 → 图谱 → 载体库 → 测序。
3. 跑三套测试并**记录本机基线**；与 §2 的数字不一致就先查原因，别直接改断言。
4. 跑 `smoke_test.ps1`（后端需在跑）与（可选）`scripts/smoke_online_sources.py --run`。
5. 向所有者确认 §2 的「线上部署」与「SnapGene 开关 / Addgene 单用户」两问。
6. 动手前先看 `git log --oneline -20`：近一周的提交标题基本就是当前工作重心。

## 10. 需要向项目所有者确认的事

- 线上主机、SSH 方式、Cloudflare 配置（仓库里没有，也不该有）。
- SnapGene 在线库是否保持默认开启（涉及网站条款，见 KNOWN_ISSUES 决策记录）。
- 线上实例是不是**单用户**自托管（若是多用户，应清空 `ADDGENE_SESSION_COOKIE`）。
- 是否需要在本次交接后发版（版本号与 tag 由所有者明确发起）。
