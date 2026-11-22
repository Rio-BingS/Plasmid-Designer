# CLAUDE.md

请先阅读并遵循 [AGENTS.md](AGENTS.md)——它是本仓库的项目上下文与工作规范入口，
按需加载其中的文档索引，避免一次性读入全部文档。

**不可绕过的数据政策（2026-11-21）**：真实测序数据（含脱敏件）及其衍生测试信息
（真实样本代号、真实位点坐标、真实基线）一律不入 git 仓库、不入推送。守卫
`python scripts/check_real_data_absence.py`（CI 全分支阻断 + pre-push 钩子拦截）；
新克隆先跑 `python scripts/setup_hooks.py`。细节见 docs/KNOWN_ISSUES.md
防回潮约束第 23 条。
