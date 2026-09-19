# AGENTS.md — 项目上下文（供 AI 助手 / 新会话最低成本了解）

> 任何 AI 工具（Claude Code / Codex / Cursor / ZCode 等）开始工作前先读完本文件。
> 深入细节再按「文档索引」按需加载，不要一次读完所有文档。

## 一句话

Plasmid Designer：自动化质粒构建设计平台。输入氨基酸/DNA 序列 + 选载体 + 选
「插入片段来源（PCR/全基因合成）」+ 选「克隆方法（Gibson/GoldenGate/双酶切）」→
密码子优化、引物或合成 oligo 设计、克隆方案、质粒图谱、序列分析、多格式导出。

## 技术栈与代码地图

```
src/backend/                FastAPI（设计主线纯标准库；Sanger 分析用 biopython）
  app/main.py               入口 ~120 行（仅组装：CORS/中间件/路由/启动）
  app/routes/               design|batch|vector|codon|analysis 路由 + models.py（共享 Pydantic 契约）
  app/design_service.py     ★ 单任务与批量共用的设计流水线（核心业务）
  app/auth/                 JWT（passlib+bcrypt）；auth/middleware.py 写 request.state.user 供限流
  app/cache.py              缓存（内存/Redis 自动降级）；cache_routes 管理端点
  core/codon_optimizer.py   ★ 密码子优化 v2（5' ramp/发夹削减/隐蔽motif审查/滑窗精修/score）
  core/primer_designer.py   ★ PCR/Gibson/GoldenGate/双酶切引物 + 合成 oligo（错位交替/Tm均一）
  core/sequence_analysis.py 酶切位点/ORF/GC 分析器；RESTRICTION_ENZYMES 表
  core/clone_strategy.py    克隆方案文本生成；core/export_formats.py 多格式导出
  core/vector_library.py    载体库（data/vectors/*.yaml）
  core/enzyme_sites.py      ★ 内置 ~48 种常用限制酶表，扫描序列生成图谱酶切位点
  core/sanger/              ★ Sanger 测序全自动管线：abif_reader（SeqIO abi 主路径 +
                            内置解析器回退）/ aligner（PairwiseAligner 双向判向）/
                            annotator（突变→特征/氨基酸/移码/酶切位点注释）/
                            pipeline（修剪→比对→共识投票→自动结论；可选 tracy 解卷积）/
                            batch（批量归组与一句话结论，网页批量端点与离线脚本共用）
src/frontend/               Vue3+TS+Vite+Pinia（dev 端口 3000，代理 /api → 8000）
  src/api/index.ts          全部后端调用（axios，40+ 函数）——改后端契约必同步这里
  src/views/AnalysisView.vue 序列分析页（位点/ORF/GC/消化模拟/兼容性/导出）
  src/components/EnzymeAutocomplete.vue 可搜索酶选择器（单/多选，全站推广）
  src/components/PlasmidMap.vue      ★ SnapGene 风格环形图谱（Canvas：wrap特征/双向箭头/
                                     弧外标签分轨避让/酶位点层/自适应刻度/缩放/exportPng）
  src/components/SequenceView.vue    ★ 线性序列视图（虚拟滚动/翻译AA/酶标注/scrollTo联动）
  src/components/SequencingPanel.vue ★ Sanger 上传→一键分析→结论/突变表/比对校验视图
                                     （逐列 read vs 参考+Q 值）/峰图/共识差异高亮/导出
data/                       codon_tables(4物种 YAML) + vectors(9 载体 YAML)
deploy/                     docker-compose / hf-docker / hf-gradio / bare(Ubuntu systemd)
tests/                      后端 pytest（219 用例，含 test_sanger_pipeline/test_enzyme_sites/
                            test_sequencing_routes/test_batch_sequencing；tests/abif_utils.py
                            合成 ab1 生成器）+ 前端 vitest（70 用例，src/frontend/tests）
```

## 命令（Windows Git Bash，均已验证）

```bash
# 后端（.venv 是仓库根的正式环境；改 core/app 代码必须重启，未开 --reload）
cd src/backend && ../../.venv/Scripts/python -m uvicorn app.main:app --port 8000
# 后端测试（conftest.py 已注入路径，无需额外 PYTHONPATH）
cd tests && ../.venv/Scripts/python -m pytest -q
# 前端
cd src/frontend && npm run dev        # 或 test:run / build（依赖已装）
# 冒烟（20 项，需后端先起）
powershell -ExecutionPolicy Bypass -File smoke_test.ps1
```

## Git 工作流（长期指令，优先于默认保守边界）

- 一轮修复/功能**完成且验证通过后，主动按 git-commit-style skill 提交并推送**，
  不必等用户每次说「提交一下/推上去」；仅当用户明确说「先不要提交」时才停住。
  skill 本身是按任务意图触发的——没有提交动作时不会自动加载，所以这里显式授权
- 提交/推送仍必须走 git-commit-style skill 的完整流程：自审 diff（调试残留/敏感
  信息/超范围改动）、Conventional Commits、push 前 `git pull --rebase` 并展示
  `git log origin/main..HEAD`；`.env`、密钥、密码、token 绝不提交
- 版本发布（升 APP_VERSION、打 tag、`push --follow-tags`）不属于上述授权，
  仍需用户明确发起（如「发版」「打个 tag」）

## 关键机制（改代码前必读）

- `STORAGE_MODE=memory`（默认，重启丢数据）/ `database`（SQLite 或 PG）；
  设计结果三写：内存 designs_db → storage → 完成态进缓存
- 密码子优化缓存键含 `algo="v2"` 与 exclude_enzymes——**升级算法必须递增版本**，否则命中旧结果
- 合成 oligo 设计：错位交替（无完全互补对）、片数恒偶、DNAWorks 式 Tm 均一分片、
  交叉杂交 12mer 检查告警；长度范围 [oligo_length_min, max]（max 硬上限）
- 插入片段来源与克隆方法正交（`insert_source` × `cloning_method`）；
  旧值 `cloning_method=gene_synthesis` 由模型校验器归一（向后兼容）
- 限制性克隆双酶切 `enzyme_5/enzyme_3`；Gibson `gibson_site` 定位重组点；
  合成可 `exclude_enzymes` 让优化序列避开酶位点
- 酶选择器统一用 `EnzymeAutocomplete`（可按酶名/识别序列搜索；API 失败回退内置酶表）

## 文档索引（按需加载）

| 文档 | 内容 |
|---|---|
| docs/ALGORITHM_ROADMAP.md | 算法已实现清单 + 暂缓项路线图（含文献/专利出处） |
| docs/CACHE.md | 缓存策略与现状 |
| docs/FIXPLAN.md | 历史修复记录（2025 核查） |
| deploy/DEPLOY_GUIDE.md | 三种部署方式 |

## 已知坑

- Git Bash 下路径带空格必须引号；后端日志在 `src/backend/logs/`（gitignored）
- uvicorn 未开 --reload；改后端代码后忘记重启是「改了没生效」的头号原因
- package-lock.json 的镜像源已重写为 npmmirror（曾指向不可达的腾讯云内网源）
- in-app 浏览器标签页与用户共享：验证 UI 时先 snapshot 确认状态，避免和用户操作互相干扰
- 传给 `pytest` 的测试文件里遗留 `/root/.openclaw/...` 的 sys.path 死路径无害
  （conftest.py 会重新注入正确路径）

## 当前状态（2026-09-19）

- 调整（2026-09-19）**双峰（疑似混合样品）检测分级 + N 调用低置信**：用户反馈
  突变/双峰识别准确性不够。根因之一：混合培养物（两个单克隆混测）的逐位双峰
  只存进结构化字段 mixed_positions，结论/一句话结论/整理包报告完全不可见，
  混合样品被报成「合格：与设计一致」。改造（core/sanger/pipeline.py）：
  ①_detect_mixed_detail 输出逐位细节（比例/次级通道/pullup 标记），
  _detect_mixed_positions 保留为兼容入口；②饱和峰拖影（pull-up，主峰 ≥3×
  全 read 峰高中位且次级占比 ≤0.5、与主峰同期）剔除不按混合计；③
  _classify_mixed 做 read 级分级：count ≥5 或连续双峰段 ≥8 → widespread
  （疑似混合样品）、2-4 → scattered（个别双峰提示）、0-1 → none（单点双峰
  与噪声无法区分，交变体峰级证据口径）；次要克隆占比 = r/(1+r)（r=次峰/主峰
  中位，勿再写成 r/(1-r)）；④结论两分支均插入 mixed_lines（widespread 用
  「⚠ 疑似混合样品：…建议重新挑单克隆」，紧跟首行）；⑤N/模糊调用
  （alt_base 含非 ACGT）恒 low 置信——basecaller 拿不准才给 N，Q 再高也不算
  "与参考不同"的证据。excel_conclusion（core/sanger/batch.py）：widespread
  且无确证 CDS 不一致时结论以「疑似混合：」开头（_conclusion_bucket 落
  整理包 无法判定/ 文件夹，不再混进 正确/）；确证不合格时保持不合格前缀、
  混合降为尾部提示；scattered 走尾部提示。_group_report_md（app/
  sequencing_report.py）新增「双峰（疑似混合）检测」章节（每 read 判定/
  位点数/次峰占比/次要克隆占比/位点示例）。tracy decompose 触发改按
  widespread（sequencing_routes _run_full_analysis），scattered 噪声不再
  白跑解卷积。注意：①mixed_profiles 只含 count>0 的 read；per-read 的
  mixed_profile 总是存在；②双峰位点的主峰判据要求 called 碱基 = 主通道，
  basecaller 恰好调了次要碱基的位点检不到（真实混合以多数位点为准）；
  ③离线脚本 scripts/batch_sequencing_report.py 的报告未同步双峰章节。
- 调整（2026-09-19）**整理包按质粒归档 + 正确/错误分置**：同一质粒在信息表里
  写成 '17648'/'MBYSTC'/'17648 MBYSTC' 等多种写法时，原先按名称字符串逐组建
  文件夹被拆散。新布局（app/sequencing_report.py build_batch_zip）：同一质粒
  一个文件夹（表内别名写法合并——X 恰为唯一另一名称 Y 的空白分隔段则并入 Y，
  候选不唯一或无全名行不猜，与 core.sanger.batch 表内别名共用 _squash 同一
  口径，见 _plasmid_folders），参考图谱副本在质粒文件夹根（同质粒多组共享按
  文件名+MD5 去重，只归档一份）；其下固定 正确/ 错误/ 两个子文件夹按各组
  一句话结论分置文件与报告（合格→正确、不合格→错误，分析失败/缺图谱等归
  无法判定/，见 _conclusion_bucket；空桶也保留 正确/错误 目录，无法判定/ 仅
  在有内容时出现），同桶多组（多克隆/别名并档）的报告与 分析结果.json 带
  组名后缀（测序分析报告-<克隆号或原名>.md）。整理清单.csv 新增「结论」列，
  批次总览.txt 写明归档结构。注意：①质粒模式 match_files 没有克隆模式那套
  名称驱动/表内别名图谱共享，只写单段的行可能仍因缺图谱落 无法判定/；②测试
  造「不合格」夹具（_gb_mx 整序列 CDS 的 GenBank）时突变要放中段——read 起端
  会被修剪，位置 2 的错义调不出变异（覆盖 98.2%）；③离线脚本
  scripts/batch_sequencing_report.py 维持平铺结构未同步。
- 新增（2026-09-19）**批量上传防呆（Excel 锁文件/占用文件/体积上限）**：网页端
  「选择文件夹」会把 `~$` 开头的 Excel 锁文件（名称排序先于正主）当成信息表；
  正被 Excel/WPS 占用时浏览器读不出该文件，XHR 在发送阶段整体失败，前端只显示
  Network Error 且源站完全无请求日志（易误判为服务器问题）。修复：前端 addFiles
  跳过 `~$`/desktop.ini 等临时垃圾文件并在芯片行下方提示；runBatch 上传前逐文件
  试读 1 字节（`slice(0,1).text()`），占用文件点名报错且不发起请求；总体积超
  95MB 拦截（Cloudflare 免费版 100MB 请求体硬上限留余量）并提示分批；芯片行显示
  总体积。formatApiError 把裸 Network Error/Failed to fetch 翻译成可行动提示。
  后端 analyze-batch 对 `~$` 信息表与损坏 xlsx（BadZipFile/InvalidFileException，
  原先 500）返回明确 400。注意测试环境是 happy-dom：无 Blob.array()、FileReader
  事件在宏任务触发（flushPromises 刷不到），试读用 text()，测试用覆盖 slice 模拟。
- 新增（2026-09-19）**批量分析整理包 + 分析记录 15 分钟自动删除**：网页批量
  分析（analyze-batch）完成后按上传原始字节现场打包「整理包」并缓存（离线脚本
  产物的网页版：按质粒/克隆归档的文件副本、各组 测序分析报告.md、分析结果.json、
  整理清单.csv、批次总览.txt、结论回填的信息表 + 原始备份；克隆模式按
  （质粒, 克隆）回填，是离线脚本没有的扩展），15 分钟内经
  GET /api/sequencing/batches/{batch_id}/report 重复下载（响应 report_ready
  控制；体积超 MAX_BATCH_CACHE_BYTES=256MB 跳过打包）。构建逻辑在
  app/sequencing_report.py（纯函数），缓存时效清理在 sequencing_routes
  （_sweep_expired）。同时分析记录（_ANALYSES）加 15 分钟 TTL 自动删除
  （ANALYSIS_TTL），MAX_STORED/MAX_TRACE_STORED 容量兜底保留。上传链路：
  容器 nginx client_max_body_size 10m→512m、代理读写超时 120s→600s（批量
  交付几十个 ab1 轻易超 10MB，超限断连在浏览器端表现为 Network Error）。
- 新增（2026-09-19）**克隆模式图谱单段命名 + 表内别名共享**：质粒名三种写法
  （'17648'/'MBYSTC'/'17648 MBYSTC'）与图谱文件名两两等价——图谱文件名也可只写
  一段（'17648.fasta'），与图谱共享该段的行直接命中；只写另一段的行（'MBYSTC'）
  与图谱无共同标识，新增表内别名兜底：信息表内恰有一个以它为组成段的质粒名
  且该名已确定图谱时共享（宿主多于一个或表中无全名行——图谱 '17648' ↔ 表
  'MBYSTC'——确实无法建立对应，维持缺图谱不猜）。
- 新增（2026-09-19）**测序分析与批量测序分析权限彻底拆分**：功能矩阵新增
  「批量测序分析」（sequencing_batch，注册表紧随 sequencing）。后端 main.py 对
  sequencing_router 改挂 require_any_feature（分析记录查看/峰图/导出/删除为两者
  共用，任一开放即可），端点级 require_feature 落到各入口——单样品三入口
  （/sequencing/analyze、designs/{id}/…、vectors/{id}/…）属「测序分析」，
  /sequencing/analyze-batch 属「批量测序分析」；gating.py 新增 require_any_feature。
  存量兼容：site_settings 新增 feature_migrations 标记列（init_db 轻量 ALTER 迁移），
  读路径按 features.SPLIT_INHERIT 做一次性继承迁移（含 sequencing 的清单——站点
  两级矩阵 + 用户个人覆盖——自动补 sequencing_batch，保持拆分前行为；标记保证
  只跑一次，之后管理员显式取消勾选不会被补回）。前端：/sequencing/batch 路由与
  导航改绑新键；/sequencing 路由守卫 anyFeature 任一放行（批量结果深链 ?history=
  的共用详情载体），仅有批量权限时页内隐藏上传分析区降级为只读回看；AdminView
  矩阵由 feature_keys 动态渲染自动出现新键。测试 tests/test_feature_gating_sequencing.py
  7 项（注册表顺序/一次性迁移幂等且不覆盖显式勾选/用户覆盖补齐/端点 403 拆分/
  site-config 传导/矩阵键）+ 前端降级用例；pytest 299 通过，vitest 91 通过
- 新增（2026-09-18）**批量测序克隆模式**（一个质粒多个克隆）：信息表带「克隆号」列时
  每行一个克隆独立成组、独立分析（core/sanger/batch.py 新增 rows_have_clones +
  match_clone_files；load_excel 识别克隆/样品列、数值单元格 123.0→'123'）。read 匹配：
  「克隆号-引物」组合精确（含忽略分隔符变体 S99678_M13F-75）→ 引物条目精确（兼容旧表
  完整文件名）→ 组合包含（复制版后缀）→ 克隆号前缀兜底（分隔符边界，信息表漏填引物）；
  裸引物名出现在多行/未知克隆号的文件一律不猜判未匹配。图谱匹配支持两段式质粒名
  部分匹配（"17648 MBYSTC" 可只写 "17648"/"MBYSTC"/全名；全名精确 > 忽略分隔符 >
  段一致 > 双向包含[短侧≥4字符]；多文件竞争按强度取最精确，同强度仅主干一致视为
  重复副本，弱匹配并列全判歧义不猜）。同一质粒参考图谱解析一次供全部克隆复用；
  分析记录 sample_name="克隆号 质粒名"。响应新增 clone_mode + item.clone；分析记录
  上限 50→200，峰图原始数据单独限量只留最近 40 次（更早记录结论可看、峰图 404 提示
  已清理）；MAX_BATCH_FILES 200→400 + 克隆分组上限 120。前端 BatchSequencingView
  克隆列/汇总/模式徽标；conftest 新增 autouse 清空限流计数（upload 档 20/小时会被
  全套件历史请求占满导致 429 污染）；离线脚本遇克隆模式信息表明确报错指向网页端。
  pytest 292 通过；真实交付数据（17648 MBYSTC.dna + 4 克隆×5 ab1）端到端验证：
  各克隆独立结论（合格/移码不合格）、S99681 无文件正确报缺 reads
- 新增（2026-09-07）**账号体系**：三层角色（管理员/普通用户/匿名访客，管理员不受任何
  开关限制）+ 站点开关（site_settings 单行表 + app/site_settings.py 读侧 3s TTL 缓存）：
  ①开放注册；②注册邮箱验证（6 位码哈希存储、10 分钟有效、60s 重发冷却，
  EmailVerificationDB 表；users 表 init_db 轻量迁移补 email_verified 列并回填存量用户，
  登录未验证账号返回 verify_token 引导补验证）；③功能开放矩阵（app/features.py 六个
  功能键 design/batch/vectors/sequencing/analysis/codon × 匿名/用户两组，默认全开放保持
  旧行为；前端 NavBar 过滤 + 路由守卫 + ForbiddenView，后端 require_feature 路由依赖
  app/gating.py 兜底拦截）；管理员引导 ADMIN_EMAIL/ADMIN_PASSWORD（app/auth/bootstrap.py，
  存在则提升、缺省不动作）；邮件渠道 app/mailer.py：console（默认，验证码进日志）/
  smtp（QQ/163/Gmail 授权码）/resend（免费 3000/月）/brevo（免费 300/天）；
  /api/admin/settings+/api/admin/users（app/admin_routes.py，仅管理员；自我保护：不能
  禁用/删除自己、不能移除最后一位管理员）；前端 /admin 面板 AdminView（开关+功能矩阵+
  用户管理）+ AuthModal 验证码步骤（注册关闭提示、重发倒计时）；测试
  tests/test_account_system.py 18 项（独立内存库+get_db override+限流打桩）
- 新增（2026-09-07）**批量测序独立网页入口**：归组/结论逻辑从批量脚本抽到
  core/sanger/batch.py（load_excel 支持 bytes、match_files 文件项兼容 name/path、
  excel_conclusion 两端同口径），脚本改为复用；后端新增 POST /api/sequencing/analyze-batch
  （整个交付文件夹 + 可选信息表 → 按质粒归组逐个跑管线出一句话结论；无信息表时按
  图谱名包含关系归组、取最长包含解决 MX/MX2 前缀歧义；成功者注册进标准分析记录）；
  前端新增 /sequencing/batch 路由 + BatchSequencingView + 导航「批量测序」，
  查看详情深链 /sequencing?history=<id>（SequencingView 新增该参数回看载入）；
  单样品入口 /sequencing 保持不变
- pytest **247 通过**（含 test_account_system 18 项、test_docker_requirements_sync 3 项、
  test_sequencing_batch 7 项、
  test_batch_sequencing 11 项、test_vector_data 数据守门 6 项、CDS 测序结论/峰级证据/
  ORF 对齐/嵌套去重/置信度分层等）；前端 vitest **81 通过**；vite build 通过
- 新增（2026-09-07）**批量测序整理分析脚本**（scripts/batch_sequencing_report.py）：离线批处理
  "Excel 信息表 + 测序结果文件夹"——引物列填测序文件名（分号分隔）匹配 .ab1、质粒名称匹配
  参考图谱（.dna/.gb/.fasta）；同一质粒文件【复制】到 <数据目录>/测序分析/<质粒名>/（原件
  不动不覆盖、整理清单.csv 记录原始路径+MD5），逐质粒跑 core/sanger 全自动管线生成
  测序分析报告.md + 分析结果.json，一句话结论回填 Excel"测序结果"列（原表先备份）；
  requirements 补 openpyxl；测试用合成 ab1 + FASTA 端到端覆盖（tests/test_batch_sequencing.py）
- 新增（2026-09-06 三次补充）**比对校验视图**（页内核对测序结果，不再需要导出到其他软件）：
  aligner.align_read 输出逐列对齐 `aligned`（ref_aligned/read_aligned 等长带 gap、read 以
  参考方向展示、q_aligned 逐列 Q 反向 read 随碱基反转）；_summary 每条 read 带 alignment_view；
  consensus 新增 `diffs`（与参考差异位+cons_index）；前端「比对校验」区块（60 列分块、错配红底/
  插缺/低 Q 橙字、read 切换 chip、点击差异行定位到对应列）+ 峰图高亮改用 read_pos 精确坐标
  （原来 ref_pos-ref_start 在有 indel 时偏移）+ 峰图未加载时点差异行会自动加载 + 共识序列
  黄色高亮差异位
- 测序页导入重构（2026-09-06 二次补充）：**「选择参考序列」区块整体移除**（设计数据不持久、
  载体对比场景少，参考序列改为随 .ab1 一起上传）——参考文件（.gb/.gbk/.fasta/.fa/.fna/.dna）
  与 .ab1 放同一文件夹拖入/选择即可，面板自动按扩展名分类（忽略无关文件、提示多余参考）；
  后端新增 POST /api/sequencing/analyze（multipart reference+reads，样品名取参考文件名主干，
  core/sanger/reference_parser.py 用 Biopython/snapgene-reader 解析特征）；
  requirements 补 snapgene-reader；/api/vectors/{id}/sequence?format=genbank 重写为
  Biopython 可回解析的标准格式；深链 ?mode=design|vector&ref=ID 自动下载对应 GenBank
  预填参考（fetchDesignGenbankFile/fetchVectorGenbankFile）；utils/recentDesigns.ts 已删除
- 新增（2026-09-06）：PlasmidMap v2 重写（填充式特征弧+重叠分层+方向箭头、外侧标签多轨、
  内侧单一酶切位点蓝色多轨、PNG 2x 导出、坐标环形取模）；SequenceView v2（酶名两档错层+
  切点标记、识别序列底纹、翻译按链分置、特征条重叠分层）；**测序分析拆为独立模块**
  （/sequencing 路由 + SequencingView：历史分析列表；VectorDetailView 与 ResultView 改为
  深链跳转入口）；后端新增 GET /api/sequencing/analyses 列表接口与
  EnzymeSite.recognition 字段；**载体库数据准确性**：data/vectors 9 载体全部替换为真实
  序列+注释（SnapGene 官方库直链 + NCBI L09137/U78872），YAML 带 data_provenance 血统，
  scripts/fetch_vector_sequences.py 为刷新管线（含自检），ElementType 补 CDS、加载器未知
  类型降级 other
- 2026-09-05：SnapGene 风格图谱初版 + Sanger 测序全自动分析（core/sanger/ +
  sequencing_routes + SequencingPanel，依赖 biopython，可选 tracy 解卷积；分析记录为进程内存存储）
- 旧状态：2026-09-04 pytest 126 / vitest 42；冒烟 20 通过；GitHub main 已同步
- 版本 v2.0.0（tag）；远程 https://github.com/gui123a1/Plasmid-Designer
- 未竟事项：分析页「双酶消化模拟」UI 全流程曾因会话中断未走完最后一步
  （后端 /analysis/digest 已有 4 个单测覆盖，EcoRI 单酶 UI 实测通过）
- 暂缓的算法增强见 docs/ALGORITHM_ROADMAP.md（用户明确要求短期不做）
