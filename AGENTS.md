# AGENTS.md — 项目上下文（供 AI 助手 / 新会话最低成本了解）

> 任何 AI 工具（Claude Code / Codex / Cursor / ZCode 等）开始工作前先读完本文件。
> 深入细节再按「文档索引」按需加载，不要一次读完所有文档。
> 面向用户的功能说明、API 一览与环境变量见 [README.md](README.md)，本文件不重复。

## 一句话

Plasmid Designer：自动化质粒构建设计平台。输入氨基酸/DNA 序列 + 选载体 + 选
「插入片段来源（PCR/全基因合成）」+ 选「克隆方法（Gibson/GoldenGate/双酶切）」→
密码子优化、引物或合成 oligo 设计、克隆方案、质粒图谱、序列分析、多格式导出；
另有 Sanger 测序全自动验证（单样品 / 批量交付文件夹，支持环状参考）。

## 技术栈与代码地图

```
src/backend/                FastAPI（Sanger 分析依赖 biopython + snapgene-reader，tracy 可选）
  app/main.py               入口（仅组装：CORS/中间件/路由/生命周期）
  app/routes/               design|batch|vector|online_source|map_online|codon|sequencing 路由 + models.py（共享 Pydantic 契约）
  app/vector_import.py      ★ 外部/在线序列 → 载体库 YAML（形状与 data/vectors/*.yaml 一致，导入统一入口）
  app/online_import.py      ★ 在线来源共享装配层（来源清单/许可、SnapGene 集合索引缓存、单序列下载+命名）
  app/design_service.py     ★ 单任务与批量共用的设计流水线（核心业务）
  app/auth/                 JWT 会话：httpOnly Cookie（pd_session）+ CsrfMiddleware（X-Requested-With）
                            + 登出吊销（jti）；middleware.py 写 request.state.user 供限流
  app/gating.py / features.py / site_settings.py   功能矩阵门控与站点开关（管理员不受限）
  app/cache.py              缓存（内存/Redis 自动降级）；cache_routes 管理端点
  app/sequencing_store.py   测序分析记录落库（database 模式）
  app/sequencing_batch.py   批量测序归组与逐组分析（网页端点与离线脚本共用）
  app/sequencing_report.py  批量整理包（报告/归档/回填信息表）构建（同上共用）
  core/codon_optimizer.py   ★ 密码子优化 v2（5' ramp/发夹削减/隐蔽motif审查/滑窗精修/score）
  core/primer_designer.py   ★ PCR/Gibson/GoldenGate/双酶切引物 + 合成 oligo（错位交替/Tm均一）
  core/sequence_analysis.py 酶切位点/ORF/GC/消化模拟/兼容性；RESTRICTION_ENZYMES 兼容视图
  core/clone_strategy.py    克隆方案文本生成；core/export_formats.py 多格式导出
  core/vector_library.py    载体库（data/vectors/*.yaml）
  core/plasmid_map/         ★ 图谱契约 plasmid-map/1（解析/酶切/ORF/元件库自动注释/GenBank 导出，docs/MAP.md）
  core/enzyme_sites.py      ★ 唯一限制酶模型（Bio.Restriction/REBASE，默认菜单 51 种），扫描生成图谱酶切位点
  core/sanger/              ★ Sanger 测序全自动管线：reference_parser（GenBank/FASTA/.dna +
                            拓扑）/ abif_reader（SeqIO abi 主路径 + 内置解析器回退）/
                            aligner（PairwiseAligner 双向判向；环状参考延长比对 + 坐标折回）/
                            annotator / signal（poly 区峰图信号）/
                            pipeline（修剪→比对→共识投票→自动结论；可选 tracy 解卷积）/
                            batch（批量归组与一句话结论，网页批量端点与离线脚本共用）
src/frontend/               Vue3+TS+Vite 8+Pinia（dev 端口 3000，代理 /api → 8000；Node ≥22.12）
  src/api/index.ts          全部后端调用（axios，withCredentials + X-Requested-With）——改后端契约必同步这里
  src/api/session.ts        会话代际（迟到的 401 不踢掉新会话）
  src/composables/useIsAdmin.ts 管理员判定（无 pinia 宿主降级为 false）——隐藏导入/上传等写入口
  src/views/AnalysisView.vue 序列分析页（位点/ORF/GC/消化模拟/兼容性/导出）
  src/components/EnzymeAutocomplete.vue 可搜索酶选择器（单/多选，全站统一）
  src/map/                           ★ 质粒图谱（契约 plasmid-map/1，见 types.ts/api.ts）：core/ 纯算法
                                     （坐标/分道/标签布局/酶过滤/搜索/Tm，均有单测）+ store.ts 共享
                                     状态 + components/（MapViewer 组合 Circular/Linear/SequenceMap）
  src/components/SequencingPanel.vue ★ Sanger 上传→一键分析→结论/突变表/比对峰图融合视图；
                                     展示卡在 components/sequencing/（PolyCard/ConclusionCards/
                                     ReadTable/VariantTable/AlleleCard/ConsensusCard/MatchMap→map/LinearMap）
  src/composables/useSeqViz.ts       ★ 峰图融合视图（状态/交互/canvas 绘制）
data/                       codon_tables(4物种 YAML) + vectors(9 载体 YAML，带 data_provenance)
deploy/                     docker-compose / bare(Ubuntu systemd)
tests/                      后端 pytest（tests/abif_utils.py 合成 ab1 生成器；
                            test_real_data_regression 真实 ab1 回归，skip-if-missing）
src/frontend/tests/         前端 vitest
```

## 命令（Windows Git Bash，均已验证）

```bash
# 后端（.venv 是仓库根的正式环境；改 core/app 代码必须重启，未开 --reload）
# 需先在 src/backend/.env 或环境变量里设置 SECRET_KEY（openssl rand -hex 32），否则拒绝启动
cd src/backend && ../../.venv/Scripts/python -m uvicorn app.main:app --port 8000
# 后端测试（conftest.py 已注入路径、隔离测试库与随机 SECRET_KEY）
cd tests && ../.venv/Scripts/python -m pytest -q
# 前端
cd src/frontend && npm run dev        # 或 test:run / type-check / build
# 冒烟（20 项，需后端先起）
powershell -ExecutionPolicy Bypass -File smoke_test.ps1
# 联网冒烟（默认不联网，只读不写；--run 或 ONLINE_SMOKE=1 才真打 SnapGene/Ensembl/UniProt/Addgene）
.venv/Scripts/python scripts/smoke_online_sources.py --run
```

测试基线（本机 Windows，2026-10-17 实测，v2.16.0）：pytest **1477 passed / 39 skipped /
5 failed**，vitest **531**，vue-tsc **0**。5 项失败逐项定性见 `docs/HANDOVER.md` §7：
3 项**环境性**（`test_batch_report_disk_store` / `test_sequencing_upload_sessions` 的
POSIX 权限位断言在 Windows 上恒报 0o777；`test_session_finalize_lazy_read` 抢线程时序），
2 项**真实数据回归待按新口径更新**（`test_real_data_regression` 的混合双峰计数与
「互检矛盾」措辞随 v2.15.1/v2.16.0 口径变化，本机有 ab1 数据才跑，CI skip）。
CI（`.github/workflows/ci.yml`）：push main / PR 跑 pytest + pip-audit、vue-tsc + vitest + npm audit。

Python 依赖：只改源清单 `src/backend/requirements.in` / `deploy/docker/requirements.backend.in`
（直接依赖、精确 pin、两份同名包同版本；仅测试用的包放 `requirements-dev.in`），然后 `bash scripts/lock_requirements.sh`
重新生成同名 `.txt` 锁文件（含传递依赖与 sha256，**勿手改**）并一起提交；安装一律
`pip install --require-hashes -r <锁文件>`。锁与源不同步时 test_docker_requirements_sync 变红。

## Git 工作流（长期指令，优先于默认保守边界）

- 一轮修复/功能**完成且验证通过后，主动按 git-commit-style skill 提交并推送**，
  不必等用户每次说「提交一下/推上去」；仅当用户明确说「先不要提交」时才停住。
  skill 本身是按任务意图触发的——没有提交动作时不会自动加载，所以这里显式授权
- 提交/推送仍必须走 git-commit-style skill 的完整流程：自审 diff（调试残留/敏感
  信息/超范围改动）、Conventional Commits、push 前 `git pull --rebase` 并展示
  `git log origin/main..HEAD`；`.env`、密钥、密码、token 绝不提交
- 版本发布（升版本号 `core/_version.py` + package.json、打 tag、`push --follow-tags`）不属于上述授权，
  仍需用户明确发起（如「发版」「打个 tag」）

## 关键机制（改代码前必读）

- `STORAGE_MODE=database`（默认；SQLite 或 PG）/ `memory`（重启丢数据）；
  设计结果三写：内存 designs_db → storage → 完成态进缓存
- 密码子优化缓存键含 `algo`（`app/cache.py` 的 `CODON_OPT_ALGO`）、所用密码子表文件的
  内容摘要与 exclude_enzymes，`/analysis/enzymes` 键含 `ANALYSIS_ENZYMES_VERSION`——
  **改变结果口径的代码（含酶表数据）必须递增版本**，否则命中旧结果（纯 bug 修复且结果
  不变时不递增）；只改 `data/codon_tables/*.yaml` 时摘要自动变化，无需递增
- 合成 oligo 设计：错位交替（无完全互补对）、片数恒偶、DNAWorks 式 Tm 均一分片、
  交叉杂交 12mer 检查告警；长度范围 [oligo_length_min, max]（max 硬上限）
- 插入片段来源与克隆方法正交（`insert_source` × `cloning_method`）；
  旧值 `cloning_method=gene_synthesis` 由模型校验器归一为 insert_source=gene_synthesis +
  限制性克隆，但仍须给 enzyme_5/enzyme_3（或 enzyme），否则 422——并非完全向后兼容
- 限制性克隆双酶切 `enzyme_5/enzyme_3`；Gibson `gibson_site` 定位重组点；
  合成可 `exclude_enzymes` 让优化序列避开酶位点
- 酶选择器统一用 `EnzymeAutocomplete`（可按酶名/识别序列搜索；API 失败回退内置酶表）
- 认证：浏览器只走 httpOnly 会话 Cookie，前端不持有令牌；Cookie 认证的写请求缺
  `X-Requested-With` 一律 403（登录/注册/验证/重发豁免）；Bearer 头请求不查 CSRF。
  前端另域部署时 `CORS_ORIGINS` 必须是显式来源（通配时后端关闭凭证）；Cookie 为
  SameSite=Lax（写死），只支持同站另域，跨站部署浏览器不带 Cookie
- 资源属主：design/batch/测序记录按 user_id 校验（创建者可见、管理员全见；design/batch
  无属主公开）；匿名测序记录凭 `access_token` 访问（只认 `X-Access-Token` 头，前端
  sessionStorage 自动带头），无令牌的遗留匿名测序记录仅管理员可读/删、列表不展示；
  载体库是共享库，刻意无属主
- 限流按（方法 + 路由模板）分类：只读请求一律 default 档，轮询不吃业务配额
- 在线序列来源（`core/online_sources.py` + `app/online_import.py` + 两个路由模块）：
  载体库侧 `/api/vectors/import/*`（`online_source_routes.py`，写端点需管理员、走 upload 档）
  与图谱页 `/api/map/import/*`（`map_online_routes.py`，挂 map 开关、写端点走 ncbi 档）
  共用同一套取字节层与**同一批集合/索引缓存**（`online:snapgene:*`）；图谱页的
  MapDocument 缓存按「输入摘要 + 选项 + 元件库/算法版本」分键，命中时不重复下载。
  **许可边界**：SnapGene 库学术/非营利/政府可用但须引用 `www.snapgene.com/resources`，
  商业用途须 GraphPad 书面许可、其 robots.txt `Disallow: /local`；Addgene 全长序列
  2026-08 起需登录——无凭据时代码**不绕登录**（403 + 指引）；配置了用户自备的
  `ADDGENE_SESSION_COOKIE` 时才带凭据取序列（仅管理员触发、仅单用户自托管适用），
  且只以 https 发往 addgene.org：`_http_get` 禁自动重定向、逐跳校验 https + 主机白名单，
  Cookie 用 unredirected 头且跨主机即丢弃；不写入响应/日志。故只做低频单次读取（进程级限速 + 结果缓存），
  `ONLINE_IMPORT_ENABLED=false` 可整体关闭外呼；不要给它加定时同步/批量镜像。
  **UniProt 是蛋白库**：只用于反查 Entrez/EMBL/RefSeq/Ensembl 交叉引用，蛋白序列不导入
- 导入载体的出处与拓扑必须结构化落盘：`data_provenance`（`provenance_block`：
  source/url/fetched_at/license + 来源特有字段，与内置载体 YAML 同形状；描述里那句
  可读标注不能替代）与 `topology`（线性片段不能被当环状画，见
  `app/map_documents._vector_topology`）；图谱页「另存为载体」走
  `POST /api/vectors/import/from-map`（提交当前文档，后端不再外呼）
- 载体库写入口（上传/批量导入/编辑/删除/在线导入/另存为载体）界面按 `useIsAdmin()`
  隐藏，后端 403 仍是权威；载体库是共享库、刻意无属主
- 载体导入落盘形状由 `app/vector_import.py` 统一：features 必须带 `name`（缺省回退到
  type），否则 `VectorLibrary._parse_vector_dict` 抛 KeyError、整份载体被
  `load_from_directory` 的兜底 except 吞掉而静默消失（历史 bug）
- 测序管线口径（`core/sanger/`）：
  - 参考拓扑：文件明确标 linear 才线性比对，否则按环状——比对「参考 + 参考头部」后折回
    1..L，跨原点 read 整条比对，原点两侧覆盖缺口合并后再过滤
  - read 可信区**逐条判定**（`core/sanger/reliable_zone.py`，默认边界 30/60/700/900 bp）：
    首端硬区一律不可信、首端灰区按 Q 值与峰型找起点、中段信号提前衰减即截断、尾端灰区
    靠其他引物覆盖定案；硬边界不被信号推翻，信号只能把可信区往里收。「高级参数」可用
    `reliable_zone`（如 `30,60,700,900`）覆盖。**不要退回「固定扣首尾 20 bp」的旧做法**
    （`END_MARGIN=20` 现仅作 poly 骤降判定的 read 末端豁免宽度 = 2×END_MARGIN）
  - 次要克隆占比 = r/(1+r)（r = 次峰/主峰面积比），勿写成 r/(1-r)
  - 置信分层：只有中/高置信变异进 CDS 判定与共识；N/模糊调用恒 low；单 read 支持的
    indel 若被其他 read 干净跨过则标 cross_read_conflict 并降为 low
  - 同聚物（poly）按「先逐 read 判读，再综合投票」：run_verdict 五态
    accepted / contradictory / undetermined / deficit_observed / caller_only
  - 部分测序是常规策略：结论以引物覆盖区为准，未覆盖区「按设计序列对待」，不建议补测
  - 前端 read↔ref 映射用 `query_start/query_end`（比对块绝对坐标），不要回退到镜像式

## 文档索引（按需加载）

| 文档 | 内容 |
|---|---|
| README.md | 功能、API 一览、环境变量、测试命令 |
| docs/HANDOVER.md | 交接文档：现状事实清单、近一周主线、当前红灯逐项定性、下一步优先级（刚接手先读） |
| docs/KNOWN_ISSUES.md | 未解决挂账 + 防回潮约束 + 历次审计修复索引（代码注释「KNOWN_ISSUES x.y」「终审 X-NN」所指） |
| docs/ALGORITHM_ROADMAP.md | 算法已实现清单 + 暂缓项路线图（含文献/专利出处；用户明确要求短期不做） |
| docs/CACHE.md | 缓存策略与现状 |
| deploy/docker/README.md、deploy/bare/README.md | Docker Compose / Ubuntu systemd 部署 |

## 已知坑

- Git Bash 下路径带空格必须引号；后端日志在 `src/backend/logs/`（gitignored）
- uvicorn 未开 --reload；改后端代码后忘记重启是「改了没生效」的头号原因
- in-app 浏览器标签页与用户共享：验证 UI 时先 snapshot 确认状态，避免和用户操作互相干扰
- 部分测试文件里遗留 `/root/.openclaw/...` 的 sys.path 死路径，无害
  （conftest.py 会重新注入正确路径）
- **测试里改 site_settings 必须用真实 `app.database.SessionLocal`**，结束时恢复默认并
  `invalidate_cache()`，否则污染后续模块（设计端点 403）
- **`tests/test_config_cors.py` 会 `reload(app.config)`**（生成新的 settings 实例）：
  模块里 `from app.config import settings` 拿到的是旧对象，之后用例打桩
  `settings.VECTORS_DIR` 之类对那个模块不生效——路由里一律用 `config.settings.X`
  在调用时解析（曾导致测试把载体写进真实 `data/vectors/`）。**测试打桩载体目录要打两处**
  （`app.config.settings` + `app.design_service.settings`，后者供 `get_vector_library`），
  并清 `cache.backend.clear_pattern("vector:*")`：否则全量跑时「刚导入的载体 404」
  （单跑该文件是绿的）
- **PowerShell 数组切片改文件会把整个文件清空**：结构化编辑一律用小步 edit 锚点
- **从父组件拆子组件时，模板用到的 scoped CSS 必须随迁**：父级 `<style scoped>` 只作用到
  子组件根节点；共享状态用 defineModel，不要在子组件里另起孤立 ref
- composable 解构出的绑定若模板没用会报 TS6133；测试需经 wrapper.vm 调用的要保留并 void 引用
- happy-dom：无 canvas 2d（`getContext` 不是函数，需 typeof 守卫）、`clientWidth` 恒 0、
  无 `Blob.array()`、FileReader 事件在宏任务触发（试读用 `text()`）；ref 缓存取出的是
  reactive 代理，断言用 `toEqual` 不用 `toBe`
- 自造 ab1 峰形时峰心必须对齐 `make_ab1` 的 PLOC（`i*spb+1`），否则全线误报混合
- git 全局代理 127.0.0.1:7890 而代理客户端没开时，push 常超时：提交留本地，等代理恢复再推

## 当前状态

- 当前版本 v2.16.0；远程 https://github.com/Rio-BingS/Plasmid-Designer
  （2026-10 用户改了 GitHub 用户名，旧地址 gui123a1/... 已失效，勿再引用）
- 刚接手先读 docs/HANDOVER.md（现状、红灯定性、下一步优先级）
- 功能演进历史看 `git log`；审计修复看 docs/KNOWN_ISSUES.md「修复历史」
- 未竟事项见 docs/KNOWN_ISSUES.md「未解决 / 挂账」
- 暂缓的算法增强见 docs/ALGORITHM_ROADMAP.md（用户明确要求短期不做）
