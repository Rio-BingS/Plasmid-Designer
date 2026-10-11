# 更新日志

本文件记录各版本面向使用者与部署者的变化，格式参考
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号沿用仓库标签。
每个版本按「升级注意 / 新增 / 变更 / 修复」分节；**升级注意**列出需要部署者
或 API 调用方动手处理、或会改变既有结果的变化，升级前请先读这一节。
逐条提交见 `git log <上一标签>..<本标签>`。

## [Unreleased]

（暂无）

## [2.9.0] - 2026-10-11

审查 Medium 批次：认证、防滥用、部署与健壮性。

### 升级注意

- **Docker 前端端口默认只绑定本机**：`docker-compose.yml` 改为发布到
  `${FRONTEND_BIND:-127.0.0.1}:${FRONTEND_PORT}`，宿主机前置 nginx/TLS 反代的部署
  无需改动；不经反代、直接对公网提供前端端口的旧部署，升级后需在 `.env` 设
  `FRONTEND_BIND=0.0.0.0`，否则外网无法访问。Docker 自建的 iptables 规则会绕过
  ufw，直接对外时请另行限制来源。
- **Docker 可信代理与网络需手动迁移**：后端 `TRUSTED_PROXIES` 默认值由
  `127.0.0.1,::1,172.16.0.0/12` 收窄为 `127.0.0.1,::1,172.28.0.10`（前端 nginx
  容器的固定地址）。旧 `.env` 里写着 `...,172.16.0.0/12` 的必须改成新默认值；
  compose 网络改为固定网段 `PLASMID_SUBNET=172.28.0.0/24`（网关
  `PLASMID_GATEWAY=172.28.0.1`、前端 `FRONTEND_IPV4=172.28.0.10`），已存在的网络
  不会自动改网段，需 `docker compose down` 后再 `docker compose up -d` 重建
  （不要加 `-v`，数据卷保留）。网段与宿主机冲突时可改，但这三项、
  `TRUSTED_PROXIES` 与 `nginx.conf` 中 `geo` 里的网关地址必须一并修改。
- **转发协议透传**：容器 nginx 只对可信网关透传 `X-Forwarded-Proto`。宿主机
  前置做 TLS 时请按 `deploy/docker/README.md` 的示例设置该头，
  `AUTH_COOKIE_SECURE=auto` 才会给会话 Cookie 加 Secure。
- **`.env` 权限收紧为 0600**：`deploy.sh` 每次运行都会 `chmod 600 .env`，
  `install.sh` 以 umask 077 创建。若有其它账号或工具需要读取 `.env`，请改用
  属主或 sudo。
- **裸机 SQLite 库位置**：全新裸机安装的 SQLite 库放在 0700 的
  `/opt/plasmid-designer/db/` 下（`DATABASE_URL` 指向 `db/plasmid_designer.db`），
  service 增加 `UMask=0077`。旧安装的库仍在原位置，重跑 `install.sh` 只把它及
  `-journal`/`-wal`/`-shm` 改为 0600，不会搬迁；如需搬到 `db/`，停服务后移动并
  同步修改 `.env` 的 `DATABASE_URL`。
- **邮箱验证码每日上限**：除单码 5 次校验外，新增每用户 24 小时最多发送 10 个
  验证码、最多校验 20 次（跨验证码累计），超限分别返回「今日验证码发送次数已达
  上限」「验证码错误次数过多」；重发冷却改按最近发出的一条计算，作废后不能立即
  重发。`email_verifications` 表新增 `attempts` 列，启动时自动迁移。
- **测序记录保留上限按属主分桶**：新增 `SEQUENCING_DB_MAX_RECORDS_PER_OWNER`
  （每个登录用户，默认 2000）与 `SEQUENCING_DB_MAX_ANON_RECORDS`（匿名记录合计，
  默认 2000），超出只淘汰该桶最旧记录；`SEQUENCING_DB_MAX_RECORDS` 含义改为
  **全库总上限**，默认由 2000 改为 20000，超出时先删最旧匿名记录。此前显式设置过
  `SEQUENCING_DB_MAX_RECORDS` 的部署请按新含义复核取值。
- **`/restriction-sites` 反链位点的 `cut_position`**：改为正向链切点，Type IIS
  （BsaI/BsmBI/BbsI 等）反链位点的值与旧版相差约 4 bp，与 Bio.Restriction 及同页
  消化片段边界一致；按旧值做后处理的调用方需复核。
- **分析接口统一去除序列空白**：各分析请求在长度校验前去掉空白，换行、空格不再
  计入长度上限，ORF 坐标、GC、位点结果按去空白后的序列计算。

### 新增

- 前端在会话中途失效（过期或在别处登出）时提示重新登录，并按匿名刷新功能集；
  切回页面时节流复查会话。

### 变更

- 站点设置读取失败时沿用最近一次成功读取的值（从未成功时用默认功能清单，注册类
  开关仍收紧），不再整站 403。
- 限流计数表改为 O(1) 淘汰，容量放宽到 5 万；登录失败计数单独成表，洪泛其它 key
  无法冲掉某账号的失败计数。
- 离线测序脚本逐质粒落盘并释放文件字节，中断时已完成的质粒保留；产物结构不变。

### 修复

- 登出不再计入认证限流；登出失败时前端保持登录态并提示重试，不再假装已登出。
- 测序记录从库回灌改为先鉴权，回灌遵守内存容量上限。
- 容器 nginx 不再用 `$scheme` 覆盖可信前置传来的 `https`。

## [2.8.1] - 2026-10-11

修复审查遗留的 4 个 High。

### 升级注意

- **批量 / 离线测序结论可能变为「无法判定」**：有 read 只部分比对上参考
  （partial_alignment），或既无完整覆盖的 CDS、实测又不足 100 bp 时，一句话结论
  输出「无法判定：…」并归入 `无法判定/`，不再给「合格」。旧版判为合格、归入
  `正确/` 的此类样品升级后会换目录。
- **登录按账号失败闸**：同一邮箱 15 分钟内登录失败 10 次后返回 429（带
  `Retry-After`），与来源 IP 无关；锁定期内正确口令同样被拦，口令正确登录后清零。
- **`deploy.sh` 不再替换已有数据卷的数据库口令**：检测到
  `<项目名>_postgres-data` 卷已存在、而 `.env` 的 `DB_PASSWORD` 为空或为旧公开
  占位值时，脚本中止并给出 `ALTER USER` 步骤，不再生成新口令；Docker 不可达时同样
  中止。`deploy.sh` / `install.sh` 改为等待 `/health` 报告 `db=ok`，超时以非零码
  退出，不再照常打印「部署完成」。
- **`/health` 探测数据库**：执行一次 `SELECT 1`（3 秒超时），失败时返回 **503**
  且 `db=error`；成功时新增 `db=ok` 字段，其余字段不变。依赖 `/health` 恒为 200
  的外部监控需按新语义调整。
- 限流键对 IPv6 客户端按 /64 前缀聚合（IPv4 映射地址按 IPv4 计），同一 /64 内的
  地址共享配额。

### 修复

- 环状参考上跨原点的缺失计入 CDS 判定与特征归属，不再漏报 ATG 等缺失。

## [2.8.0] - 2026-10-11

算法审计 13 项优化。

### 升级注意

- **限制酶表统一到 Bio.Restriction（REBASE）**：默认菜单合并为 51 种，
  `/analysis/enzymes` 由 26 种扩为 47 种；回文酶位点不再正反链重复计数（可判为
  唯一位点）；HindIII 与 SacI 不再误报可互连。使用 Redis 时，部署后请清除
  `analysis_enzymes:*` 缓存（管理员 `POST /api/cache/clear?pattern=analysis_enzymes:*`），
  否则 7 天内仍返回旧酶表。
- **引物 Tm 改用最近邻法**（Biopython Tm_NN + 盐校正）：同一引物的 Tm 数值比旧版
  经验式通常高约 7 °C，58–62 °C 筛选窗口不变，设计结果会随之变化。
- **合成 oligo 按重叠双链 Tm 均一化**：重叠长度在 18–25 nt 间可调，1.2 kb 基因的
  oligo 数量通常比旧版多 2–4 条。
- 删除三个零引用模块（`enhanced_primer_designer`、`advanced_primer_designer`、
  `enhanced_codon_optimizer`），直接 import 它们的外部脚本会失败。

### 新增

- 酶切消化模拟支持环状质粒（请求可选 `circular`，默认 false）。
- Golden Gate overhang 连接保真度筛查，常见 MCS 回文 overhang 给出警告。
- Sanger：tracy 次级 basecall 作为混合峰的独立印证（`tracy_corroboration`）；
  >20 kb 参考先 k-mer 定位再窗口比对，内存大幅下降。

### 变更

- 共识投票按逐列 Q 值加权；read 中的 N/IUPAC 调用不再计为替换突变。
- 密码子优化滑窗精修只查改动窗口附近的 motif。

### 修复

- GenBank 导出的 LOCUS 行改为标准列格式。
- GenBank `join()` 特征逐段解析，跨原点 CDS 按拼接序列判读。
- 离线整理清单记录序号另存后的实际路径；重跑时清理判定变更后的旧副本。

## [2.7.1] - 2026-10-11

### 升级注意

- **离线测序脚本 `scripts/batch_sequencing_report.py` 输出格式整体变化（破坏性，
  虽为补丁版本号）**：脚本改为复用网页端整理包逻辑，命令行参数不变，但产物结构
  与网页整理包一致：
  - read 不再平铺在 `<输出>/<质粒>/`，改放 `<质粒>/正确|错误|无法判定/`；新增
    `未匹配文件/` 与 `批次总览.txt`。
  - `分析结果.json` 移到 `<质粒>/<结论目录>/` 下，多组时为
    `分析结果-<组名>.json`；字段去掉 `_generated_at`、`_reference_file`，新增
    `analysis_id`、`sample_name`、`created_at`、`features`。
  - `整理清单.csv` 列名由英文
    `plasmid,name,kind,how,src,dst,kb,md5` 改为中文
    `质粒,克隆,文件,类型,结论,匹配方式,归档路径,大小KB,MD5,原始路径`。
  - 按旧路径、旧字段、旧列名解析产物的下游脚本需要改写；请输出到新目录，或先
    清空旧的输出目录，避免新旧两种布局混在一起（v2.8.0 起重跑会清理判定变更后
    的旧副本）。
- 测试依赖由 `httpx` 换为 `httpx2`（starlette 1.x 的 TestClient 所需），只影响
  开发与 CI 环境。

### 变更

- 密码哈希改为直接调用 `bcrypt`，移除 `passlib`；配置与 ORM 改用新写法，消除
  Pydantic / SQLAlchemy 弃用告警。
- 文档按 v2.7.0 代码全面校正；删除已完成的修复计划文档。

### 修复

- 双酶切模拟去除序列空白并限制酶数。

## [2.7.0] - 2026-10-11

会话安全与环状参考支持。

### 升级注意

- **会话改为 httpOnly Cookie**：浏览器端登录令牌改由 `pd_session` Cookie
  （`Path=/api`、`SameSite=Lax`）下发，前端不再保存令牌；**升级后浏览器里的旧
  会话会失效一次，需要重新登录**。登录响应体仍返回 `access_token`，Bearer 头继续
  可用且优先于 Cookie。
- **Cookie 会话的写请求必须带 `X-Requested-With` 头**，否则 403（登录、注册、
  验证、重发豁免；Bearer 请求不检查）。自建前端或脚本若用 Cookie 会话需补该头。
- HTTPS 在外部终止时设 `AUTH_COOKIE_SECURE=true`（或按 v2.9.0 起的转发协议透传
  使用 `auto`）；前端另域部署时 `CORS_ORIGINS` 必须是明确来源，通配 `*` 时后端
  关闭凭证模式、浏览器不携带会话 Cookie。
- **数据库与 Redis 密码不再有默认值**：未设置 `DB_PASSWORD` / `REDIS_PASSWORD`
  时 compose 拒绝启动，`deploy.sh` 会自动生成随机值（已有数据卷的处理见 v2.8.1）。
- **构建要求 Node.js ≥ 22.12**（Vite 8 / Vitest 5），CI、Docker 与裸机安装均已
  升到 Node 22 LTS。
- 邮箱验证令牌改为单次有效，重放返回 400 `ALREADY_VERIFIED`。
- 登出时服务端吊销令牌；升级前签发的令牌（无 jti）登出时会使该用户的全部会话
  失效。

### 新增

- 测序比对支持环状参考跨原点：坐标折回、区段拆分绘制、覆盖缺口合并；参考文件
  解析识别环状/线性拓扑。

### 修复

- 密钥熵校验不再误拒随机生成的密钥。
- 会话核实完成前导航栏不再闪现登录按钮；功能清单显式为空时站点配置不再回退默认。
- `install.sh` 重复运行时复用已有数据库密码。

## [2.6.0] - 2026-10-10

复核修复全量收口。

### 升级注意

- **Golden Gate / 限制性克隆不再默认使用 BsaI**：缺酶时设计请求直接 422。
  Golden Gate 必须传 `enzyme`（Type IIS，如 BsaI/BsmBI/BbsI）；限制性克隆必须传
  `enzyme_5` 与 `enzyme_3`（或单酶 `enzyme`）。旧值 `cloning_method=gene_synthesis`
  仍会被归一为「限制性克隆 + 合成插入片段」，但同样需要指定限制酶，否则 422。
  双酶切引物拒绝 Type IIS 酶。
- **匿名测序访问令牌只认 `X-Access-Token` 请求头**，`?token=` 查询参数被移除。
- **无令牌的遗留匿名测序记录改为仅管理员可读/删**（此前公开），列表也不再展示。
- **无效 Bearer 令牌返回 401、禁用用户返回 403**，不再按匿名处理。
- **`SECRET_KEY` 无可用默认值**：未设置、占位、过短或低熵一律拒绝启动，
  DEBUG 下也不再豁免，本地开发同样需要配置。
- **限流只信任 `TRUSTED_PROXIES` 内对端的转发头**，默认只信本机；反代在其它主机
  或容器时需配置（Docker 默认值见 v2.9.0）。
- 匿名批次整理包下载需携带整批访问令牌。
- 升级 PyJWT 到 2.15.1、axios 到 1.20.0、happy-dom 到 20（安全公告）。

### 新增

- 环状序列扫描跨越原点的酶切位点；Sanger 识别跨原点 read 并报出截断段差异。
- 双酶切引物 5' 端加保护碱基；插入片段含所选酶内部位点时告警。
- nginx 增加 CSP；Docker 构建校验 Tracy 二进制 sha256；新增 CI（后端测试、前端
  检查与依赖审计）。

### 修复

- 升级后功能清单被重置、PCR 引物恒回退两处回归。
- Golden Gate 反向引物 IIS 位点方向；7 个限制酶条目的识别序列/切点；CHO 密码子表
  改回中国仓鼠。
- 测序资源上限：GC 滑窗步长、批量总字节、信息表解压体积、重型比对并发、整理包
  缓存总字节。

## [2.5.0] - 2026-10-10

终审 P0 全量清账。

### 升级注意

- **匿名测序记录改为凭令牌访问**：匿名创建时响应下发 `access_token`，读取、峰图、
  导出、删除须携带；列表不再返回匿名记录 ID。（本版仍接受 `?token=`，v2.6.0 起
  只认请求头。）
- **输入上限收紧**：参考序列上限 200 kb（超限 413）；设计序列按类型限制为氨基酸
  5000 / DNA 20000（`MAX_INPUT_AA` / `MAX_INPUT_DNA` 可覆盖）；分析类接口序列上限
  200 kb、窗口下限 10、ORF 最小长度下限 30；批量请求有总字节上限。
- 非 DEBUG 下弱 `SECRET_KEY`（占位值或长度 < 32）拒绝启动；`ADMIN_EMAIL` 引导
  不再把已存在的同邮箱普通账号提升为管理员，需人工处理。
- 密码子表按 Kazusa 原始值重建、优化迭代循环真正生效，同一输入的优化结果会变化。
  密码子优化缓存键（`algo`）未随之递增，启用 Redis 时升级后建议由管理员
  `POST /api/cache/clear?pattern=codon_opt:*` 清除，或等待 24 小时缓存过期。

### 新增

- 克隆方案的酶切步骤带真实切点坐标，破坏必需元件时告警。

### 变更

- 限流改按「方法 + 路由模板」分类，只读请求一律走 default 档，轮询不占业务配额。

### 修复

- 设计导出端点补属主校验；CORS 中间件移到最外层并放行预检请求。
- 镜像不再携带开发数据库；nginx 配置校验失败即中止。
- 双峰护栏、引物退火区锚定插入片段两端、比对导出坐标修正等。
- 裸机部署：前端 403、后端端口暴露、卸载无确认。

## [2.4.1] - 2026-10-10

密码子 / 引物 / 酶切判定修正批次。

> **已知问题：标签 `v2.4.1`（`e744841`）的前端无法构建，请勿按该标签部署，
> 请直接使用 v2.5.0 或更新版本。** 发版时 `src/frontend/package.json` 被写入
> UTF-8 BOM（`EF BB BF`），vite 的 JSON 解析报错，`npm run build` 与 vitest
> 全部失败；修复提交为 `7d4351d`（已包含在 v2.5.0 中）。已公开的标签不再移动。
> 必须停留在 2.4.1 时，可在检出后执行
> `sed -i '1s/^\xEF\xBB\xBF//' src/frontend/package.json` 去掉 BOM 再构建。

### 升级注意

- 移除 HuggingFace Spaces 部署方式（`hf-docker`、`hf-gradio`、`deploy_hf.sh`
  及对应 CI），只保留 Docker Compose 与裸机 systemd 自托管。
- Python 依赖改为精确 pin（`==`）。

### 新增

- 测序结论一句话总结；分析与导出以实测覆盖区为准；跨 read 干净覆盖反证单 read
  indel 伪影。

### 修复

- BsaHI 位点修正为 GRCGYC；引物 Tm 宣称如实化，Golden Gate 未知酶报错；ramp 改
  频率中位修复 poly 打断卡死；酶兼容按实际突出端判定并修 ORF 反链坐标；克隆方案
  占位符与去磷参数。
- 密码按 bcrypt 72 字节显式截断；批量文件数上限在读取前生效；部署脚本路径。

## [2.4.0] - 2026-09-29

同聚物判读算法级收口 + 批量整理包与融合峰图。更早的变化见 `git log v2.4.0`。

### 新增

- 同聚物判读「先单条分析，再综合评判」：每条 read 五档分级、综合 `run_verdict`
  五档，部分覆盖 read 拼接联合判定，锚压缩检测。
- 批量分析整理包（按质粒并档、正确/错误分置、限时清理）；批量 Excel 结论与离线
  报告按 `run_verdict` 分态。
- 比对峰图融合视图；图谱匹配多级口径。
- 测序分析记录落库持久化，保留上限 `SEQUENCING_DB_MAX_RECORDS`。
- 测序分析与批量测序分析功能开关拆分。

### 修复

- 设计/批量/测序分析记录属主校验；验证码防爆破与并发注册竞态。
- FormData 上传 422；负链 CDS 密码子注释方向；ab1 通道映射。

[Unreleased]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.9.0...HEAD
[2.9.0]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.8.1...v2.9.0
[2.8.1]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.8.0...v2.8.1
[2.8.0]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.7.1...v2.8.0
[2.7.1]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.7.0...v2.7.1
[2.7.0]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.6.0...v2.7.0
[2.6.0]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.5.0...v2.6.0
[2.5.0]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.4.1...v2.5.0
[2.4.1]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.4.0...v2.4.1
[2.4.0]: https://github.com/Rio-BingS/Plasmid-Designer/releases/tag/v2.4.0
