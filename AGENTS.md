# AGENTS.md — 项目上下文（供 AI 助手 / 新会话最低成本了解）

> 任何 AI 工具（Claude Code / Codex / Cursor / ZCode 等）开始工作前先读完本文件。
> 深入细节再按「文档索引」按需加载，不要一次读完所有文档。

## 一句话

Plasmid Designer：自动化质粒构建设计平台。输入氨基酸/DNA 序列 + 选载体 + 选
「插入片段来源（PCR/全基因合成）」+ 选「克隆方法（Gibson/GoldenGate/双酶切）」→
密码子优化、引物或合成 oligo 设计、克隆方案、质粒图谱、序列分析、多格式导出。

## 技术栈与代码地图

```
src/backend/                FastAPI（纯标准库算法，无生信三方依赖）
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
src/frontend/               Vue3+TS+Vite+Pinia（dev 端口 3000，代理 /api → 8000）
  src/api/index.ts          全部后端调用（axios，40+ 函数）——改后端契约必同步这里
  src/views/AnalysisView.vue 序列分析页（位点/ORF/GC/消化模拟/兼容性/导出）
  src/components/EnzymeAutocomplete.vue 可搜索酶选择器（单/多选，全站推广）
data/                       codon_tables(4物种 YAML) + vectors(9 载体 YAML)
deploy/                     docker-compose / hf-docker / hf-gradio / bare(Ubuntu systemd)
tests/                      后端 pytest（121 用例）+ 前端 vitest 文件已移至 src/frontend/tests
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

## 当前状态（2026-08-31）

- pytest **121 通过**；前端 vitest **42 通过**；冒烟 **20 通过**；GitHub main 已同步
- 版本 v2.0.0（tag）；远程 https://github.com/gui123a1/Plasmid-Designer
- 未竟事项：分析页「双酶消化模拟」UI 全流程曾因会话中断未走完最后一步
  （后端 /analysis/digest 已有 4 个单测覆盖，EcoRI 单酶 UI 实测通过）
- 暂缓的算法增强见 docs/ALGORITHM_ROADMAP.md（用户明确要求短期不做）
