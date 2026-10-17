# 文档索引

项目总览、快速开始、API 一览与环境变量见根目录 [README.md](../README.md)；
面向 AI 助手 / 维护者的项目上下文与工作规范见 [AGENTS.md](../AGENTS.md)。

## 本目录

| 文档 | 内容 | 何时阅读 |
|------|------|----------|
| [HANDOVER.md](HANDOVER.md) | 交接文档：现状事实清单、近一周主线、入口地图、当前红灯逐项定性、下一步优先级 | **刚接手时第一份**；每次交接/换会话前更新 |
| [KNOWN_ISSUES.md](KNOWN_ISSUES.md) | 未解决挂账、部署备忘、防回潮约束、历次审计修复索引 | 改动安全/限流/存储/测序相关代码前；代码注释出现「KNOWN_ISSUES x.y」「终审 X-NN」时 |
| [CACHE.md](CACHE.md) | 缓存后端（Redis / 内存自动降级）、已接入缓存的业务、TTL 与管理端点 | 调整缓存或排查「结果没更新」时 |
| [MAP.md](MAP.md) | 质粒图谱契约 plasmid-map/1、`/api/map` 端点、自动注释元件库、限制与缓存 | 改动 `core/plasmid_map/`、图谱端点或元件库前 |
| [ALGORITHM_ROADMAP.md](ALGORITHM_ROADMAP.md) | 密码子优化与合成 oligo 算法：已实现清单 + 暂缓项（短期不做） | 改动 `core/codon_optimizer.py` / `core/primer_designer.py` 前 |

## 其他位置的文档

| 文档 | 内容 |
|------|------|
| [deploy/docker/README.md](../deploy/docker/README.md) | Docker Compose 部署、排障、域名 / HTTPS |
| [deploy/bare/README.md](../deploy/bare/README.md) | Ubuntu systemd 裸机部署 |
| [src/backend/app/README.md](../src/backend/app/README.md) | 后端应用层目录说明与启动方式 |
