# Plasmid Designer v2

自动化质粒构建设计平台 — 源码与部署配置分离版

## 项目结构

```
plasmid-designer-v2/
├── src/                        # 纯源代码
│   ├── backend/                # FastAPI 后端
│   │   ├── app/                # 应用层
│   │   │   ├── main.py         # 入口 (~97行，路由挂载)
│   │   │   ├── config.py       # 配置 + sys.path
│   │   │   ├── routes/         # 路由模块
│   │   │   │   ├── design_routes.py   # 设计任务 API
│   │   │   │   ├── batch_routes.py    # 批量设计 API
│   │   │   │   ├── vector_routes.py   # 载体库 API
│   │   │   │   ├── codon_routes.py    # 密码子表 API
│   │   │   │   └── models.py          # 共享 Pydantic 模型
│   │   │   ├── storage/        # 双模式存储层
│   │   │   │   ├── __init__.py        # STORAGE_MODE 工厂
│   │   │   │   ├── base.py            # 抽象基类
│   │   │   │   ├── memory_store.py    # 内存实现 (HF)
│   │   │   │   └── db_store.py        # 数据库实现
│   │   │   ├── auth/           # JWT 认证 (数据库版)
│   │   │   ├── database/       # SQLAlchemy 数据模型与 CRUD
│   │   │   ├── cache_routes.py # 缓存管理 API
│   │   │   ├── rate_limit_routes.py  # 速率限制 API
│   │   │   └── analysis_routes.py    # 序列分析 & 导出 API
│   │   └── core/               # 核心引擎（引物设计、密码子优化等）
│   └── frontend/               # Vue 3 前端
│       └── src/
│           ├── api/            # API 调用层 (44个函数)
│           ├── stores/         # Pinia 状态管理
│           │   ├── auth.ts     # 认证 Store
│           │   ├── design.ts   # 设计 Store
│           │   └── vectors.ts  # 载体 Store
│           ├── views/          # 页面组件
│           ├── components/     # 通用组件
│           └── types/          # TypeScript 类型
├── deploy/                     # 部署配置（与源码分离）
│   ├── hf-docker/              # HuggingFace Spaces 部署
│   ├── docker/                 # Docker Compose 部署
│   └── scripts/                # 部署脚本
├── data/                       # 静态数据
│   ├── codon_tables/           # 密码子表 (YAML)
│   └── vectors/                # 载体模板 (YAML)
├── tests/                      # 测试
└── docs/                       # 文档
```

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 20+
- npm 10+

### 本地开发

```bash
# 1. 后端
cd src/backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# 2. 前端（新开终端）
cd src/frontend
npm install
npm run dev
```

前端开发服务器启动后访问 http://localhost:5173，Vite 会自动代理 `/api` 请求到后端 8000 端口。

### 存储模式

后端支持两种存储模式，通过环境变量 `STORAGE_MODE` 切换：

| 模式 | 环境变量 | 适用场景 | 说明 |
|------|----------|----------|------|
| **database** | `STORAGE_MODE=database` (默认) | Docker/生产部署 | SQLAlchemy + PostgreSQL/SQLite，数据持久化 |
| **memory** | `STORAGE_MODE=memory` | HuggingFace Spaces | 纯内存 dict，重启丢失，无需数据库 |

```bash
# 本地开发（默认使用数据库模式，SQLite）
uvicorn app.main:app --reload

# 内存模式（无需数据库）
STORAGE_MODE=memory uvicorn app.main:app --reload
```

### 部署到 HuggingFace Spaces

```bash
# 只需拷贝 deploy/hf-docker/ 目录 + src/ 目录
cp -r deploy/hf-docker/* <hf-repo>/
cp -r src/ <hf-repo>/src/
```

HF Spaces 使用独立的 `deploy/hf-docker/main.py`（精简版，纯内存存储，无数据库/Redis/认证依赖），自动设置 `STORAGE_MODE=memory`。

### Docker Compose 部署

```bash
cd deploy/docker
docker-compose up -d
```

Docker Compose 默认使用数据库模式，包含：
- **backend**: FastAPI (端口 8000)
- **frontend**: Nginx 反向代理 (端口 80)
- **db**: PostgreSQL 15
- **redis**: Redis 7 (缓存)

通过 `.env` 文件或环境变量配置：
```bash
# docker-compose/.env
DB_USER=plasmid
DB_PASSWORD=your_secure_password
SECRET_KEY=your_jwt_secret_key
REDIS_ENABLED=true
STORAGE_MODE=database
```

## API 概览

启动后端后访问 Swagger 文档: http://localhost:8000/docs

### 设计任务

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/design` | 创建设计任务（后台异步） |
| GET | `/api/design/{id}` | 查询设计结果 |
| GET | `/api/design/{id}/download/genbank` | 下载 GenBank 文件 |
| GET | `/api/design/{id}/download/primers` | 下载引物订单 (TSV) |
| GET | `/api/design/{id}/map` | 获取质粒图谱数据 |

### 批量设计

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/design/batch` | 创建批量设计任务 |
| GET | `/api/design/batch/{id}` | 查询批量进度 |
| GET | `/api/design/batch/{id}/download` | 下载批量结果 (ZIP) |
| GET | `/api/design/batch/{id}/report` | 获取汇总报告 |

### 载体库

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/vectors` | 列出载体（支持类型/宿主过滤） |
| GET | `/api/vectors/{id}` | 获取载体详情 |
| GET | `/api/vectors/{id}/map` | 获取载体图谱数据 |
| GET | `/api/vectors/{id}/sequence` | 获取载体序列 (FASTA/GenBank) |
| DELETE | `/api/vectors/{id}` | 删除载体 |
| PUT | `/api/vectors/{id}` | 更新载体信息 |
| POST | `/api/vectors/import/ncbi` | 从 NCBI 搜索导入 |
| POST | `/api/vectors/import/ncbi-id` | 通过 NCBI ID 直接导入 |
| GET | `/api/vectors/search/ncbi` | 搜索 NCBI（不导入） |
| GET | `/api/vectors/preview/ncbi/{id}` | 预览 NCBI 载体 |
| POST | `/api/vectors/import/upload` | 上传载体文件 |
| POST | `/api/vectors/import/file` | 从本地文件导入 |
| POST | `/api/vectors/import/batch` | 批量导入 |

### 序列分析 & 导出

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/analysis/analyze` | 综合序列分析 |
| POST | `/api/analysis/restriction-sites` | 限制性酶切位点 |
| POST | `/api/analysis/orfs` | ORF 预测 |
| POST | `/api/analysis/gc-analysis` | GC 含量分析 |
| POST | `/api/analysis/compatibility` | 克隆兼容性检查 |
| GET | `/api/analysis/enzymes` | 酶列表 |
| GET | `/api/analysis/export/formats` | 导出格式列表 |
| POST | `/api/analysis/export` | 单格式导出 |
| POST | `/api/analysis/export/all` | 全格式导出 (ZIP) |

### Sanger 测序分析

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/designs/{design_id}/sequencing/analyze` | 上传 .ab1（可多个）对设计构建体全自动测序验证 |
| POST | `/api/vectors/{vector_id}/sequencing/analyze` | 同上，参考序列取自有序列的载体 |
| GET | `/api/sequencing/analyses` | 历史分析列表（摘要，时间倒序） |
| GET | `/api/sequencing/analyses/{id}` | 分析结果（结论/突变表/共识序列/覆盖率） |
| GET | `/api/sequencing/analyses/{id}/trace/{read_index}` | 单条 read 峰图数据（四通道） |
| GET | `/api/sequencing/analyses/{id}/consensus/export` | 导出拼接结果（fasta / genbank） |
| DELETE | `/api/sequencing/analyses/{id}` | 删除分析记录 |

全自动管线（`core/sanger/`）：ABIF 解析（主路径 Bio.SeqIO "abi"，无 Biopython 时回退内置解析器）
→ Q 值末端修剪 → 双向比对自动判向（Biopython PairwiseAligner）→ 多 read 共识拼接（质量加权投票）
→ 突变特征注释（所在 CDS/氨基酸变化/移码/酶切位点破坏或新增）→ 自动结论。
疑似混合样品可选用 [tracy](https://github.com/gear-genomics/tracy) decompose 解卷积
（Docker 镜像内置二进制，本地安装 `conda install -c bioconda tracy` 或设置 `TRACY_BIN`；缺失时自动降级）。
分析记录为进程级内存存储（重启失效）。前端测序分析为独立模块（`/sequencing` 路由，
`SequencingView.vue`）：选择参考序列（载体库 / 设计结果 ID）→ 上传 .ab1 一键分析 →
历史分析查看/删除；设计结果页与载体详情页通过深链跳转（`?mode=vector|design&ref=<id>`）。
质粒图谱（`PlasmidMap.vue` + `SequenceView.vue`）为 SnapGene 风格双视图：填充式特征弧
（重叠特征自动分层、方向箭头）、外侧特征标签多轨避让与位置刻度、内侧单一酶切位点蓝色
高亮多轨布局、序列视图中酶名/切点标记分层与识别序列底纹、翻译行按链分置、PNG 2x 导出。
图谱数据中的 `enzyme_sites` 由 `core/enzyme_sites.py` 内置 ~48 种常用酶扫描生成（含识别序列）。

**载体库数据准确性**：`data/vectors/*.yaml` 内置 9 个常用载体均为真实序列 + 完整注释，
每个文件含 `data_provenance` 血统块（来源/地址/抓取日期）。数据来源：
- [SnapGene Plasmid Library](https://www.snapgene.com/plasmids)（pET 系列 / pcDNA3.1 / pGEX-4T-1 / pFastBac1 / pYES2 / pLVX 等商业载体，官方整理注释）
- NCBI GenBank（pUC19 = L09137，pGEX-6P-1 = U78872）
刷新管线：`python scripts/fetch_vector_sequences.py data/vectors`（自检失败不写入）。
`tests/test_vector_data.py` 守门：序列/特征坐标/类型词表/血统记录/mcs 位点命中/旗舰载体
长度与权威记录交叉核对。

### 认证

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 用户注册 |
| POST | `/api/auth/login` | 用户登录 |
| POST | `/api/auth/logout` | 用户登出 |
| GET | `/api/auth/me` | 获取当前用户 |
| GET | `/api/auth/verify` | 验证令牌 |

### 缓存 & 速率限制

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/cache/stats` | 缓存统计 |
| POST | `/api/cache/clear` | 清除缓存 |
| POST | `/api/cache/invalidate/design/{id}` | 失效设计缓存 |
| POST | `/api/cache/invalidate/vector/{id}` | 失效载体缓存 |
| GET | `/api/cache/health` | 缓存健康检查 |
| GET | `/api/rate-limit/status` | 速率限制状态 |
| GET | `/api/rate-limit/config` | 速率限制配置 |

## 数据文件

- **密码子表**：E.coli K12, Human, CHO, Yeast
- **载体模板**：pET-28a, pET-21a, pcDNA3.1, pGEX-4T-1, pGEX-6P-1, pUC19, pYES2, pLVX, pFastBac1

## 运行测试

```bash
# 后端测试
cd tests
pytest

# 前端测试
cd src/frontend
npm run test:run
```

## 模块接线状态

> 2025 功能完整性核查后的如实标注，避免「代码存在但未生效」的误解。

| 模块 | 状态 | 说明 |
|------|------|------|
| 设计流水线 / 批量设计 / 载体库 / 认证 / 导出 | ✅ 已接线 | 前后端契约对齐 |
| 密码子优化 v2 与合成 oligo 设计 | ✅ 已实现 | 5' translational ramp、发夹削弱、隐蔽 motif 审查、变窗精修（GeneOptimizer 式）、Tm 均一化分片、错位交替 oligo、综合评分；插入片段来源与克隆方法正交，限制性克隆支持双酶切。算法路线图（含暂缓项）见 docs/ALGORITHM_ROADMAP.md |
| 序列分析 API（restriction-sites/orfs/gc-analysis/compatibility） | ✅ 已修复 | 统一为 JSON body 参数 |
| 数据库持久化（STORAGE_MODE=database） | ✅ 已修复 | id 错位与字段回填问题已解决 |
| JWT SECRET_KEY | ✅ 已修复 | 从环境变量读取，未设置时使用开发默认值并告警 |
| 请求追踪/慢请求日志中间件 | ✅ 已接线 | main.py 调用 setup_middleware |
| 缓存子系统（app/cache.py + cache_routes） | ✅ 已接线 | 设计结果（24h）/ 密码子优化（24h）/ 载体列表与详情（7天）/ 密码子表与酶表（7天）已接入读写，写操作统一失效；批量进度与 analysis POST 不缓存（状态频繁变化 / 键空间不可控），详见 docs/CACHE.md |
| 用户级限流配额（user_*） | ✅ 已生效 | AuthStateMiddleware（app/auth/middleware.py）解析 Bearer Token 写入 request.state.user，匿名请求仍按 IP 限流 |
| enhanced_primer_designer / advanced_primer_designer / enhanced_codon_optimizer / vector_data_sources | 🧪 实验性 | 核心引擎备用实现，主流程未调用 |
| output_generator | ✅ 生产使用 | 单设计主流程未直接调用，但 HF 部署入口（deploy/hf-docker/main.py、deploy/hf-gradio/app.py）依赖它生成导出文件，删除前须确认 |
| task_queue.py + celery/flower | ❌ 已移除 | 全库零引用，实际使用 FastAPI BackgroundTasks |
| BioPython / primer3-py / pydna / pandas 等 | ❌ 已移除声明 | core 算法为纯 Python 标准库自研实现；requirements 已同步瘦身并补上实际缺失的 bcrypt、psycopg2-binary |

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Vue 3 + TypeScript + Vite + Pinia + Vue Router |
| 后端 | FastAPI + Pydantic + SQLAlchemy + Redis |
| 生物信息 | 纯 Python 标准库自研实现（无第三方生信依赖） |
| 认证 | JWT (PyJWT) + bcrypt (passlib) |
| 部署 | Docker Compose / HuggingFace Spaces |