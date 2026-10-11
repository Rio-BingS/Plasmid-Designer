# Plasmid Designer v2

自动化质粒构建设计平台 — 源码与部署配置分离版（当前版本 v2.7.0）

## 项目结构

```
plasmid-designer-v2/
├── src/                        # 纯源代码
│   ├── backend/                # FastAPI 后端
│   │   ├── app/                # 应用层
│   │   │   ├── main.py         # 入口（仅组装：CORS/中间件/路由/生命周期）
│   │   │   ├── config.py       # 配置（环境变量 / .env）+ sys.path
│   │   │   ├── design_service.py      # 单任务与批量共用的设计流水线
│   │   │   ├── routes/         # 路由模块
│   │   │   │   ├── design_routes.py   # 设计任务 API
│   │   │   │   ├── batch_routes.py    # 批量设计 API
│   │   │   │   ├── vector_routes.py   # 载体库 API
│   │   │   │   ├── codon_routes.py    # 密码子表 API
│   │   │   │   ├── sequencing_routes.py  # Sanger 测序分析 API
│   │   │   │   └── models.py          # 共享 Pydantic 模型
│   │   │   ├── storage/        # 双模式存储层（STORAGE_MODE 工厂 + 内存/数据库实现）
│   │   │   ├── auth/           # 认证：JWT 会话 Cookie + CSRF + 登出吊销
│   │   │   ├── database/       # SQLAlchemy 数据模型与 CRUD
│   │   │   ├── admin_routes.py # 站点设置 / 用户管理 API（仅管理员）
│   │   │   ├── cache_routes.py # 缓存管理 API
│   │   │   ├── rate_limit_routes.py  # 速率限制 API
│   │   │   └── analysis_routes.py    # 序列分析 & 导出 API
│   │   └── core/               # 核心引擎（密码子优化、引物设计、酶切、sanger/ 测序管线等）
│   └── frontend/               # Vue 3 前端
│       ├── src/
│       │   ├── api/            # API 调用层（axios，改后端契约必同步这里）
│       │   ├── stores/         # Pinia 状态管理（auth / design / vectors）
│       │   ├── views/          # 页面组件
│       │   ├── components/     # 通用组件（图谱、序列视图、测序面板等）
│       │   ├── composables/    # 组合式函数（峰图融合视图 useSeqViz）
│       │   └── types/          # TypeScript 类型
│       └── tests/              # vitest 单测
├── deploy/                     # 部署配置（与源码分离）
│   ├── docker/                 # Docker Compose 部署
│   ├── bare/                   # Ubuntu systemd 裸机部署
│   └── scripts/                # 部署脚本
├── data/                       # 静态数据
│   ├── codon_tables/           # 密码子表 (YAML)
│   └── vectors/                # 载体模板 (YAML)
├── tests/                      # 测试
├── scripts/                    # 离线工具（fetch_vector_sequences 载体库刷新、
│                             # batch_sequencing_report 批量测序整理分析）
└── docs/                       # 文档
```

## 快速开始

### 环境要求

- Python 3.11+
- Node.js 22.12+（22 LTS；Vite 8 / Vitest 5 的最低要求）
- npm 10+

### 本地开发

```bash
# 1. 后端
cd src/backend
pip install -r requirements.txt
# SECRET_KEY 必填：未设置、占位、过短或低熵一律拒绝启动（本地开发同样适用）
echo "SECRET_KEY=$(openssl rand -hex 32)" > .env
uvicorn app.main:app --reload

# 2. 前端（新开终端）
cd src/frontend
npm install
npm run dev
```

前端开发服务器启动后访问 http://localhost:3000，Vite 会自动代理 `/api` 请求到后端 8000 端口
（同源，无需配置 CORS；`AUTH_COOKIE_SECURE` 默认 `auto`，http 下会话 Cookie 不置 Secure，可正常登录）。

### 存储模式

后端支持两种存储模式，通过环境变量 `STORAGE_MODE` 切换：

| 模式 | 环境变量 | 适用场景 | 说明 |
|------|----------|----------|------|
| **database** | `STORAGE_MODE=database` (默认) | Docker/生产部署 | SQLAlchemy + PostgreSQL/SQLite，数据持久化 |
| **memory** | `STORAGE_MODE=memory` | 测试/演示 | 纯内存 dict，重启丢失，无需数据库 |

```bash
# 本地开发（默认使用数据库模式，SQLite 库位于 data/plasmid_designer.db）
uvicorn app.main:app --reload

# 内存模式（无需数据库）
STORAGE_MODE=memory uvicorn app.main:app --reload
```

### 部署

| 方式 | 组成 | 文档 |
|------|------|------|
| Docker Compose | backend（FastAPI，仅绑 127.0.0.1:8000）+ frontend（Nginx，80）+ PostgreSQL 15 + Redis 7 | [deploy/docker/README.md](deploy/docker/README.md) |
| 裸机 systemd（Ubuntu） | uvicorn（127.0.0.1:8000）+ Nginx，SQLite 或 PostgreSQL | [deploy/bare/README.md](deploy/bare/README.md) |

部署相关的关键环境变量（完整列表见各自的 `.env.example`）：

| 变量 | 说明 |
|------|------|
| `SECRET_KEY` | 必填，`openssl rand -hex 32` 生成；占位/过短/低熵值拒绝启动 |
| `CORS_ORIGINS` | 前端经 Nginx 同源反代 `/api` 时无需改动；前端另域部署时必须填**显式来源**（逗号分隔或 JSON 数组），通配 `*` 下后端关闭凭证模式，浏览器不会携带会话 Cookie |
| `AUTH_COOKIE_SECURE` | 会话 Cookie 的 Secure 标记：`auto`（默认，按请求协议 / 可信代理的 `X-Forwarded-Proto` 判定）、`true`（HTTPS 在更外层代理终止时必须设）、`false` |
| `TRUSTED_PROXIES` | 可信反向代理 IP/CIDR，只有来自它们的 `X-Real-IP` / `X-Forwarded-For` 才用于限流 |
| `STORAGE_MODE` / `DATABASE_URL` / `REDIS_ENABLED` | 存储模式、数据库连接串、是否启用 Redis 缓存 |

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
| POST | `/api/vectors/import/batch` | 批量导入 |

### 序列分析 & 导出

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/analysis/analyze` | 综合序列分析 |
| POST | `/api/analysis/restriction-sites` | 限制性酶切位点 |
| POST | `/api/analysis/orfs` | ORF 预测 |
| POST | `/api/analysis/gc-analysis` | GC 含量分析 |
| POST | `/api/analysis/digest` | 酶切消化模拟（单/双酶；可选 `circular` 按环状质粒消化，默认线性） |
| POST | `/api/analysis/compatibility` | 克隆兼容性检查 |
| GET | `/api/analysis/enzymes` | 酶列表 |
| GET | `/api/analysis/export/formats` | 导出格式列表 |
| POST | `/api/analysis/export` | 单格式导出 |
| POST | `/api/analysis/export/all` | 全格式导出 (ZIP) |
| GET | `/api/analysis/design/{id}/export` | 导出设计结果（`?format=`，默认 genbank） |
| GET | `/api/analysis/vector/{id}/export` | 导出载体序列（`?format=`，默认 genbank） |

### 密码子表

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/codon-tables` | 列出可用密码子表 |

### Sanger 测序分析

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/sequencing/analyze` | 上传参考序列文件（.gb/.gbk/.genbank/.fasta/.fa/.fna/.dna）+ .ab1（可多个）全自动测序验证（单样品入口） |
| POST | `/api/sequencing/analyze-batch` | 批量入口：整个交付文件夹（.ab1 + 图谱，可多质粒）+ 可选 Excel 信息表，按质粒归组逐个分析，返回每质粒一句话结论 |
| GET | `/api/sequencing/batches/{batch_id}/report` | 下载批量分析整理包（ZIP，生成后 15 分钟内可重复下载） |
| POST | `/api/designs/{design_id}/sequencing/analyze` | 上传 .ab1（可多个），参考序列取设计构建体（有构建体序列时按环状比对） |
| POST | `/api/vectors/{vector_id}/sequencing/analyze` | 同上，参考序列取自有序列的载体（按环状比对） |
| GET | `/api/sequencing/analyses` | 历史分析列表（摘要，时间倒序） |
| GET | `/api/sequencing/analyses/{id}` | 分析结果（结论/突变表/共识序列/覆盖率） |
| GET | `/api/sequencing/analyses/{id}/trace/{read_index}` | 单条 read 峰图数据（四通道） |
| GET | `/api/sequencing/analyses/{id}/consensus/export` | 导出拼接结果（fasta / genbank） |
| DELETE | `/api/sequencing/analyses/{id}` | 删除分析记录 |

通用上传端点（`core/sanger/reference_parser.py`）按扩展名解析参考文件：GenBank/FASTA 用
Biopython，SnapGene .dna 用 snapgene-reader；特征注释直接取自参考文件的特征表，
样品名取参考文件名主干。

**环状参考**：参考拓扑取自文件（GenBank LOCUS 行 / SnapGene topology 标记），只有明确
标为 linear 才按线性比对，其余（含无拓扑信息的 FASTA）按环状质粒处理——对「参考 + 参考
头部」做比对后把坐标折回 1..L，跨原点的 read 整条比对、在峰图与报告中按折回坐标分两段
展示，原点两侧的覆盖缺口合并后再判定。

全自动管线（`core/sanger/`）：ABIF 解析（主路径 Bio.SeqIO "abi"，无 Biopython 时回退内置解析器）
→ Q 值末端修剪 → 双向比对自动判向（Biopython PairwiseAligner）→ 多 read 共识拼接（质量加权投票）
→ 突变特征注释（所在 CDS/氨基酸变化/移码/酶切位点破坏或新增；插入缺失下游的位点按平移配对，不误报为破坏+新增）→ 自动结论。
末端修剪 Q 阈值合法范围 0–60（默认 20，可在高级参数中调整）。
每条 read 给出 A/B/C 质量评级（Q20 比例/长度/N 碱基，ClinQC 口径），每处差异给出高/中/低置信度——替换变体从峰图提取峰级证据（突变峰占比 mutant_pct = 100×突变通道/(突变+参考通道)，信噪比 SNR = 突变通道峰高/全轨迹本底中位数，Mutation Surveyor 评分要素的工程实现），融合变异位 Q 值、支持 read 数与混合峰信号（次级峰占比 >30%）综合分级；单碱基插入额外计算插入峰强度比（插入峰面积/邻峰主峰中位数，≥0.6 视为峰真实存在——basecaller 在峰压缩区对 indel 系统性给低 Q，不再一票否决；<0.3 视为伪影），read 前导 ~20bp 峰形未稳定不产出插入峰证据，indel 单 read 低质量门槛更严（Q30）。indel 归一化为最左最简表示（Tan 2015，bcftools norm/GATK 同款），同聚物/重复区内不同 read 报告的等价 indel 合并印证、支持数不再分裂；tracy 可用时对每条 read 独立重新 basecall 并交叉比对，报出同一变体时打 corroborated_by_basecall 标记（仅提示两个 caller 读出一致，同一信号上错误相关、不改变置信度）。覆盖缺口按长度排序列出；部分测序是常规策略，结论以引物实测覆盖区为准，未测区域按设计序列对待、不参与判读（不再提示补测）。
CDS 测序结论：特征边界偏差（手动标注常有 1–3bp）时自动对齐区间内最佳 ORF（起始密码子→终止密码子，长度优先）再翻译，被大 CDS 完全包含的小特征（如 6xHis 标签）不重复报告；结论按置信度分层——只将中/高置信变异（确证变异）重建进 CDS 判定，低置信变异（疑似测序噪声）不计入，单独在结论末尾给出待复核提示；翻译产物与参考做 BLOSUM62 全局比对，给出「完整覆盖 + 翻译产物一致 / 移码 / 无义提前终止 / 起始或终止密码子丢失 / 框内插入缺失 / 错义与同义」的整段判定，后果标注 Sequence Ontology 标准词表（stop_gained、frameshift_variant 等，与 VEP/snpEff/bcftools csq 对齐），结论文案为一句话因果链（变异位置 + 类型 + 后果 + 关键数字）；未覆盖区域按参考填充并明确标注未验证。
疑似混合样品可选用 [tracy](https://github.com/gear-genomics/tracy) decompose 解卷积
（Docker 镜像内置二进制，本地安装 `conda install -c bioconda tracy` 或设置 `TRACY_BIN`；缺失时自动降级）。
分析记录在 database 模式下落库（`sequencing_analyses` 表，重启不丢；`SEQUENCING_DB_MAX_RECORDS`
默认 2000 条，超出淘汰最旧），进程内只保留 15 分钟读取缓存；memory 模式下 15 分钟后过期。
匿名创建的记录由创建响应下发的 `access_token` 访问，登录用户的记录仅本人与管理员可见。

**批量分析**：归组与结论逻辑在 `core/sanger/batch.py`，逐组分析与整理包（报告/
归档/回填）在 `app/sequencing_batch.py`、`app/sequencing_report.py`，网页与脚本
两端共用同一实现，产物结构与报告章节一致：

- **网页入口**（`/sequencing/batch`，`BatchSequencingView.vue`）：把整个交付文件夹
  （.ab1 + 图谱，可多质粒混在一起）+ 可选 Excel 信息表一次上传，按质粒归组逐个跑
  全自动管线，结果表给出每质粒一句话结论（合格/不合格/缺图谱…）与覆盖、确证差异
  数；成功分析的质粒注册为标准分析记录，「查看详情」深链到 `/sequencing?history=<id>`
  进入逐碱基核对。不上传信息表时按「测序文件名包含图谱文件名」归组（图谱名互为前缀
  时取最长包含，仍无法唯一归组的列入未匹配清单）。
- **离线脚本**（`scripts/batch_sequencing_report.py`）：面向"Excel 信息表 + 测序
  结果文件夹"的交付场景一键批处理。Excel 表头含"质粒名称/测序引物/测序结果"，引物列填
  测序文件名（分号分隔，与网页端"参考文件与测序文件放同一文件夹"的约定一致）；脚本递归
  扫描文件夹中的 .ab1 与参考图谱（.dna/.gb/.fasta），按引物列文件名与质粒名称自动匹配，
  逐质粒运行全自动分析管线，在 `<数据文件夹>/测序分析/` 写出与网页整理包相同的结构：
  `<质粒名>/` 根放参考图谱，其下 `正确/`、`错误/`、`无法判定/` 按结论分置 read 副本、
  `测序分析报告.md`（含 poly 判读与双峰疑似混合检测章节）与 `分析结果.json`，未匹配
  文件副本在 `未匹配文件/`，另有 `整理清单.csv`（实际写出路径 + MD5 + 原始路径）与
  `批次总览.txt`。原始文件不移动、不删除、不覆盖（输出目录里同名异内容的副本加序号
  另存，清单记录加序号后的实际路径），一句话结论回填 Excel "测序结果"列（原表先备份
  到输出目录）。重跑到同一输出目录时，按上次运行留下的 `.整理记录.json`（旧版本跑出
  的目录退回 `整理清单.csv`）清理本次不再产出的旧副本，例如判定变化后留在旧结论
  文件夹里的 read 与报告；只删内容仍与当时写出一致的文件，用户改过的、记录之外的
  文件与原始输入一律不动，清空的文件夹随之删除（`正确/`、`错误/` 保留）。
  `--dry-run` 只打印将清理的文件，不写、不删、不回填（有上次记录时会只读分析一遍）。
  每个质粒分析完即写出其文件夹并追加清单行（文件逐质粒读入、写完即释放），中途中断
  时已完成的质粒保留在输出目录，下次重跑据 `.整理记录.进行中.jsonl` 一并核对清理。

```bash
python scripts/batch_sequencing_report.py --data-dir "<测序结果文件夹>" \
    [--excel 信息表.xlsx] [--out-dir 目录] [--min-q 20] [--dry-run] [--no-write-back]
```

**结果页内可直接核对准确性，无需导出到其他软件**：每条 read 附带逐列比对视图
（read vs 参考逐碱基对照，错配红底、插入缺失、低质量 Q<20 橙色提示；反向 read 以参考
方向展示）；点击差异行同时精确定位峰图对应碱基（按 read_pos 映射，含 indel 不偏移）
与比对列；共识序列高亮显示与参考不同的位点（测序投票写入处）。前端测序分析为独立模块
（`/sequencing` 路由，`SequencingView.vue`）：把参考序列文件与 .ab1 放在同一文件夹一起
拖入/选择（自动按类型分类）→ 一键分析 → 历史分析查看/删除；设计结果页与载体详情页通过
深链跳转（`?mode=vector|design&ref=<id>`），进入时自动下载对应 GenBank 预填参考序列。
质粒图谱（`PlasmidMap.vue` + `SequenceView.vue`）为 SnapGene 风格双视图：填充式特征弧
（重叠特征自动分层、方向箭头）、外侧特征标签多轨避让与位置刻度、内侧单一酶切位点蓝色
高亮多轨布局、序列视图中酶名/切点标记分层与识别序列底纹、翻译行按链分置、PNG 2x 导出。
图谱数据中的 `enzyme_sites` 由 `core/enzyme_sites.py` 的默认菜单 51 种常用酶扫描生成（含识别序列；酶参数取自 Biopython 自带的 REBASE 数据）。

**载体库数据准确性**：`data/vectors/*.yaml` 内置 9 个常用载体均为真实序列 + 完整注释，
每个文件含 `data_provenance` 血统块（来源/地址/抓取日期）。数据来源：
- [SnapGene Plasmid Library](https://www.snapgene.com/plasmids)（pET 系列 / pcDNA3.1 / pGEX-4T-1 / pFastBac1 / pYES2 / pLVX 等商业载体，官方整理注释）
- NCBI GenBank（pUC19 = L09137，pGEX-6P-1 = U78872）
刷新管线：`python scripts/fetch_vector_sequences.py data/vectors`（自检失败不写入）。
`tests/test_vector_data.py` 守门：序列/特征坐标/类型词表/血统记录/mcs 位点命中/旗舰载体
长度与权威记录交叉核对。

### 认证与账号体系

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 用户注册（受注册开关/邮箱验证开关控制） |
| POST | `/api/auth/login` | 用户登录（未验证邮箱时返回 verify_token 引导补验证） |
| POST | `/api/auth/verify-email` | 提交 6 位邮箱验证码完成注册并登录 |
| POST | `/api/auth/resend-verification` | 重发验证码（60 秒冷却） |
| GET | `/api/auth/site-config` | 公开站点配置（注册开关/功能开放范围，前端渲染依据） |
| POST | `/api/auth/logout` | 用户登出（服务端吊销当前令牌并删除会话 Cookie，幂等） |
| GET | `/api/auth/me` | 获取当前用户 |
| GET | `/api/auth/verify` | 检查当前会话是否有效 |
| GET | `/api/auth/users` | 用户列表（仅管理员） |
| GET/PUT | `/api/admin/settings` | 站点设置（仅管理员）：注册开关、邮箱验证开关、功能开放矩阵 |
| GET | `/api/admin/users` | 用户列表（仅管理员） |
| PUT/DELETE | `/api/admin/users/{id}` | 角色/启用/验证标记、删除（仅管理员） |

**登录会话**：登录 / 注册 / 邮箱验证成功后，后端把 JWT 写入 httpOnly 会话 Cookie
（`pd_session`，`Path=/api`、`SameSite=Lax`，Secure 由 `AUTH_COOKIE_SECURE` 决定），前端脚本
读不到令牌、也不再存 localStorage。Cookie 认证的写请求（POST/PUT/PATCH/DELETE）必须带
`X-Requested-With: XMLHttpRequest` 头（CSRF 防护，前端 axios 实例已统一携带；登录/注册等凭据
端点豁免）。登出时服务端吊销令牌（`jti` 入吊销表），旧令牌即使被截获也无法再用。
API / 脚本客户端仍可用 `Authorization: Bearer <token>`（登录响应体返回令牌），Bearer 请求不做
CSRF 检查。

**账号体系（角色 + 站点开关 + 功能门控）**：用户分三层——管理员（`is_admin`，不受任何开关
限制）、普通登录用户、未登录访客。管理员通过 `/admin` 面板控制：
- **开放注册**：关闭后新用户无法注册，已有账号不受影响；
- **注册邮箱验证**：开启后注册需输入邮箱收到的 6 位验证码（哈希存储、10 分钟有效、
  60 秒重发冷却），验证成功即登录；存量账号自动回填为已验证，不会被锁死；
- **功能开放矩阵**：六个功能键（design 引物/序列设计、batch 批量设计、vectors 载体库、
  sequencing 测序分析、analysis 序列工具、codon 密码子表）按「未登录访客 / 普通用户」
  两组分别勾选——前端隐藏导航入口并拦截直链路由，后端在 API 层同步拒绝（`require_feature`
  路由依赖，见 `app/gating.py`）。默认全开放（保持既有行为），由管理员自行收紧。
存储于 `site_settings` 单行表（`app/site_settings.py`，读侧 3 秒 TTL 缓存）；
功能键注册表在 `app/features.py`。自我保护：不能禁用/删除自己，不能移除最后一位管理员。

**管理员引导**：设置环境变量 `ADMIN_EMAIL` + `ADMIN_PASSWORD`（可选 `ADMIN_USERNAME`，
默认 admin），启动时该邮箱用户不存在则自动创建为管理员、已存在则提升为管理员；
不配置则不动作。

**邮件发送渠道**（`app/mailer.py`，环境变量 `MAIL_PROVIDER`）：
- `console`（默认）：验证码打进后端日志，零配置，适合开发与试用；
- `smtp`：任意邮箱服务商 SMTP（QQ/163/Gmail/Outlook 授权码等，零额外注册），
  配 `SMTP_HOST/PORT/USER/PASSWORD`（465 端口 `SMTP_SSL=true`，587 端口设 false）；
- `resend`：[Resend](https://resend.com) API，免费 3000 封/月（100 封/天），
  邮箱注册、无需信用卡，配 `RESEND_API_KEY`；
- `brevo`：[Brevo](https://www.brevo.com) API，免费 300 封/天，邮箱注册、无需信用卡，
  配 `BREVO_API_KEY`。

### 缓存 & 速率限制

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/cache/stats` | 缓存统计（仅管理员） |
| POST | `/api/cache/clear` | 清除缓存（仅管理员） |
| POST | `/api/cache/invalidate/design/{id}` | 失效设计缓存（仅管理员） |
| POST | `/api/cache/invalidate/vector/{id}` | 失效载体缓存（仅管理员） |
| GET | `/api/cache/health` | 缓存健康检查 |
| GET | `/api/rate-limit/status` | 速率限制状态（仅管理员） |
| GET | `/api/rate-limit/config` | 速率限制配置（仅管理员） |
| GET | `/api/rate-limit/health` | 限流健康检查 |

## 数据文件

- **密码子表**：E.coli K12, Human, CHO, Yeast
- **载体模板**：pET-28a, pET-21a, pcDNA3.1, pGEX-4T-1, pGEX-6P-1, pUC19, pYES2, pLVX, pFastBac1

## 运行测试

```bash
# 后端测试（tests/conftest.py 已注入路径、隔离的测试库与随机 SECRET_KEY）
cd tests
pytest

# 前端测试与类型检查
cd src/frontend
npm run test:run
npm run type-check
```

基线（截至 v2.7.0）：后端 631 passed / 6 skipped（跳过项为本机真实 .ab1 回归集等
skip-if-missing 用例），前端 vitest 181 passed。CI（`.github/workflows/ci.yml`）在 push main
与 PR 时跑 pytest + pip-audit、vue-tsc + vitest + npm audit。

## 模块接线状态

> 如实标注各模块是否真正接入主流程，避免「代码存在但未生效」的误解。历次修复记录见
> [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)。

| 模块 | 状态 | 说明 |
|------|------|------|
| 设计流水线 / 批量设计 / 载体库 / 认证 / 导出 | ✅ 已接线 | 前后端契约对齐 |
| 密码子优化 v2 与合成 oligo 设计 | ✅ 已实现 | 5' translational ramp、发夹削弱、隐蔽 motif 审查、变窗精修（GeneOptimizer 式）、Tm 均一化分片、错位交替 oligo、综合评分；插入片段来源与克隆方法正交，限制性克隆支持双酶切。算法路线图（含暂缓项）见 docs/ALGORITHM_ROADMAP.md |
| Sanger 测序分析（单样品 / 批量 / 环状参考） | ✅ 已接线 | `core/sanger/` + `sequencing_routes`；分析记录 database 模式落库 |
| 序列分析 API（restriction-sites/orfs/gc-analysis/digest/compatibility） | ✅ 已接线 | 统一为 JSON body 参数 |
| 数据库持久化（STORAGE_MODE=database） | ✅ 已接线 | 设计/批量/测序记录均可重启恢复 |
| 登录会话 | ✅ 已接线 | httpOnly Cookie + CSRF 头 + 登出服务端吊销；SECRET_KEY 未设置、占位、过短或低熵一律拒绝启动（含 DEBUG 与本地开发） |
| 请求追踪/慢请求日志中间件 | ✅ 已接线 | main.py 调用 setup_middleware |
| 缓存子系统（app/cache.py + cache_routes） | ✅ 已接线 | 设计结果（24h）/ 密码子优化（24h）/ 载体列表与详情（7天）/ 密码子表与酶表（7天）已接入读写，写操作统一失效；批量进度与 analysis POST 不缓存（状态频繁变化 / 键空间不可控），详见 docs/CACHE.md |
| 用户级限流配额（user_*） | ✅ 已生效 | AuthStateMiddleware（app/auth/middleware.py）解析 Bearer 头或会话 Cookie 写入 request.state.user，匿名请求仍按 IP 限流 |
| vector_data_sources | 🧪 实验性 | 核心引擎备用实现，主流程未调用 |
| enhanced_primer_designer / advanced_primer_designer / enhanced_codon_optimizer | ❌ 已移除 | 全库零引用的平行实现（含重复 Tm 公式），能力以 primer_designer / codon_optimizer 为准 |
| output_generator | ✅ 生产使用 | 单设计主流程未直接调用，但分析页导出与 Sanger 报告生成依赖其数据结构，删除前须确认 |
| task_queue.py + celery/flower | ❌ 已移除 | 全库零引用，实际使用 FastAPI BackgroundTasks |
| primer3-py / pydna / pandas / scipy 等 | ❌ 未纳入 | 全库无代码引用，requirements 不声明；实际依赖见下方「技术栈」 |

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Vue 3 + TypeScript + Vite 8 + Pinia + Vue Router；测试 Vitest 5 |
| 后端 | FastAPI + Pydantic + SQLAlchemy + Redis |
| 生物信息 | Biopython（Sanger ab1 解析 + 序列比对）、snapgene-reader（SnapGene .dna 参考）、openpyxl（批量测序信息表）；[tracy](https://github.com/gear-genomics/tracy) 为可选外部二进制（解卷积 / 重新 basecall，缺失自动降级） |
| 认证 | JWT（PyJWT）存于 httpOnly 会话 Cookie + CSRF 头 + 服务端吊销；密码 bcrypt |
| 部署 | Docker Compose / bare systemd |

## License

[MIT](LICENSE)