# 更新日志

本文件记录各版本面向使用者与部署者的变化，格式参考
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号沿用仓库标签。
每个版本按「升级注意 / 新增 / 变更 / 修复」分节；**升级注意**列出需要部署者
或 API 调用方动手处理、或会改变既有结果的变化，升级前请先读这一节。
逐条提交见 `git log <上一标签>..<本标签>`。

## [Unreleased]

### 升级注意

- **SnapGene 在线库默认关闭**：新增独立开关 `SNAPGENE_ONLINE_ENABLED`（默认
  `false`，与 `ONLINE_IMPORT_ENABLED` 无关）。升级后载体库与图谱页的「SnapGene 在线库」
  页签消失，相关端点（`/api/vectors/import/snapgene*`、`/api/map/import/snapgene*`）返回
  404 + 说明。需要该功能的部署方须显式设为 `true`，并知悉其网站条款禁止存入检索系统、
  robots.txt 禁止 `/local`（仅建议学术/非营利自用实例开启，见 `docs/KNOWN_ISSUES.md`
  「已接受风险」）。开启后这些只读端点也**需要登录**（匿名 401），存入载体库仍需管理员。
  Docker 部署需同步 `docker-compose.yml`（新增该变量透传）；已导入的 SnapGene 载体不受影响。
- `ADDGENE_SESSION_COOKIE` 明确为**仅适合单用户自托管**：配置了它且站点开放注册或已有
  1 个以上启用账号时，后端启动会记一条告警（多用户下等同共享你的 Addgene 账号）。此前
  文档称其「不落盘」不准确——它就在 `.env` 与容器环境里；多用户实例请清空该变量，改为
  浏览器下载序列文件后「上传文件」导入。
- 导入/上传/另存为载体不再静默覆盖**另一条**载体：目标 YAML 已被不同 ID 的载体占用时
  返回 409（此前名称含空格、超长截断等情况会让不同载体撞到同一文件互相覆盖）；同一 ID
  再次导入仍会覆盖，回执新增 `replaced` 并在 `warnings` 中写明。新导入载体的 ID 与文件名
  使用同一套字符归一（空格、冒号等变成下划线，如 `snapgene_SARS Genome` →
  `snapgene_SARS_Genome`），已有载体不受影响。
- 图谱缓存口径版本 `MAP_ALGO_VERSION` 递增，旧缓存自然失效，无需手动清除。
- 图谱行为变化：`annotate` 缺省时超过 50 kb 的序列不再自动注释常见元件（`meta.warnings`
  提示，显式 `annotate=true` 仍注释）；新增可选环境变量 `MAP_AUTO_ANNOTATE_MAX_BP`、
  `MAP_ANNOTATE_MAX_CELLS`（见 docs/MAP.md）。
- 图谱酶组变化：`commercial` / `unique6` / `type_iis` 与厂商表不再包含修饰依赖型酶
  （AbaSI、MspJI、FspEI、SgeI、LpnPI 及 SibEnzyme 的 9 把 5mC 依赖酶）；`MapEnzyme` 新增
  可选字段 `sites_truncated`（位点明细被截断时为 true，`cut_count` 仍是完整计数），
  酶详情新增 `modification_dependent`。新增可选环境变量 `MAP_MAX_SITES_PER_ENZYME`、
  `MAP_MAX_TOTAL_SITES`、`MAP_CACHE_MAX_DOC_BYTES`、`MAP_CACHE_MAX_TOTAL_BYTES`。
- `NCBI_TIMEOUT` 语义改为单次 NCBI 导入的总时长上限（默认由 20 改为 30 秒；此前只是单次
  socket 读超时）；新增可选 `NCBI_MAX_CONCURRENCY`（默认 4）。
- **nginx 配置更新**：两份 `deploy/*/nginx.conf` 新增 `location ^~ /api/map/`
  （`client_max_body_size 17m`）。裸机部署须把新的 nginx.conf 复制到位并 `nginx -s reload`；
  Docker 部署重建前端镜像。新增可选环境变量 `MAP_MAX_JSON_MB`（默认 16，`/api/map/*` 除
  `/parse` 外写请求的体积上限）；调大它或 `MAP_MAX_UPLOAD_MB` 时同步调大 nginx 这一项。
- Docker 部署需同步 `docker-compose.yml`：新增透传 `MAP_MAX_JSON_MB`、
  `MAP_AUTO_ANNOTATE_MAX_BP`、`MAP_ANNOTATE_MAX_CELLS`、`MAP_MAX_SITES_PER_ENZYME`、
  `MAP_MAX_TOTAL_SITES`、`MAP_CACHE_MAX_DOC_BYTES`、`MAP_CACHE_MAX_TOTAL_BYTES`、
  `NCBI_TIMEOUT`、`NCBI_MAX_CONCURRENCY`（缺省值与代码默认一致；两份 `.env.example`
  已补说明），否则在 `.env` 里调整这些值对容器不生效。
- 前端改为按路由拆包：`dist/assets/` 里会多出一批分包文件（`MapView-*.js`、
  `MapViewer-*.js` 等），部署时请整体替换 `dist/`（裸机/Docker 现有流程本来就是
  整目录复制，无需改动）。`index.html` 不应被长缓存；还开着旧版本页面的标签页在
  重新部署后首次切页若加载分包失败，会自动整页刷新一次拿到新版本

### 变更

- 环形图谱在手机上不再压住页面滚动：未放大时单指在图上滑动照常滚动页面，
  **长按约 0.35 秒后**拖动才是选区/旋转，轻点仍选中特征或落光标；支持双指捏合
  缩放。放大后与桌面一致（单指平移/选区）。鼠标操作不变
- 首屏变小：除首页与 403 页外的页面改为路由级动态导入，结果页的图谱查看器在点
  "查看质粒图谱"后才加载。主 JS 包 481.7 kB → 约 192 kB（gzip 166.0 → 约 71 kB），
  主 CSS 79.9 kB → 11.4 kB
- 图谱查看器侧栏（特征 / 酶切 / 消化）与 /map 页来源标签支持键盘 ←/→/Home/End 切换

### 修复

- `GET /api/vectors/import/addgene/{质粒号}?try_sequence=true` 改为仅管理员（匿名 401、
  普通用户 403）：配置了 `ADDGENE_SESSION_COOKIE` 时该分支会以部署方的 Addgene 账号
  下载全长序列，此前任何访客都能驱动；来源清单与该端点的 `session_configured` 也只对
  管理员如实下发。
- 载体出处链接只接受 `http(s)://`：`POST /api/vectors/import/from-map` 的 `url` 不合规时
  422，落盘与读取 `data_provenance` 时也会丢弃非 http(s) 的 `url` / `*_url`（此前
  `javascript:` 链接会在载体详情页渲染成可点击链接，形成存储型 XSS）。
- 导入载体里 `polyA_signal` 类型现在正确归为调控元件（别名表键大小写不一致，此前
  永远落成 other）。
- NCBI 导入（`/api/vectors/import/ncbi`、`ncbi-id`、批量导入）写出 LOCUS 行的真实拓扑：
  此前这条路径不写 `topology`，线性 GenBank 记录仍被画成环状（2.12.0 的「按真实拓扑
  显示」只覆盖了上传/在线来源/另存为）。已导入的旧载体需重新导入才会更新。
- Addgene 本地索引搜索把解析结果缓存在进程内（按文件修改时间与大小失效），不再每次
  请求整读约 7 MB 的 JSON。
- 载体文件上传按图谱同一序列上限（`MAP_MAX_LENGTH`，默认 300 kb）解析，超长 413；
  解析器意外异常返回 400、落盘失败返回 500 通用文案（不含内部信息）。各导入端点的回执
  不再包含服务器上的绝对路径 `path` 字段。
- 在线来源的错误信息不再回显上游响应的前若干字节（可能是登录页 HTML）；来源清单与
  出处链接里的 `*_BASE` 会去掉 `user:pass@` 凭据部分再展示。
- 在线来源的进程级节流改为按站点预占时间槽、锁外等待：此前全局锁内 sleep，SnapGene
  的 1 秒间隔会阻塞 Ensembl/UniProt/Addgene，并发请求排成长队占满线程池。现在同站点
  排队超过 `ONLINE_THROTTLE_MAX_WAIT`（默认 10 秒）直接返回 429。
- 载体库侧会外呼的只读代理（SnapGene 集合 / 索引 / 预览、Ensembl lookup / 预览）改走
  `ncbi` 限流档，与图谱侧和文档描述一致（此前落在 100 次/分钟的 default 档）。
- 在线来源外呼不再自动跟随重定向：每一跳都重新校验 https + 主机白名单（按裸主机名，
  最多 3 跳），Addgene 会话 Cookie 改为不随重定向转发、跨主机即丢弃；上游给出的
  `file://`、`http://` 等非 https 直链一律拒绝。此前一次 302 就能把 Cookie 发到任意
  主机（含 http 明文降级），SnapGene/Ensembl/UniProt 外呼也可被 302 到内网地址。
- 图谱：负链跨原点特征导出 GenBank 时片段顺序反了（写成
  `complement(join(1..e,s..L))`），SnapGene `.dna` 中的负链跨原点特征解析出的段
  顺序也一样，在 SnapGene/Benchling 里打开序列与翻译都错。现在导出写
  `complement(join(s..L,1..e))`，`segments` 统一为生物学顺序（负链坐标降序）。
  v2.12.0 导出的含负链跨原点特征的 .gb 需重新导出。
- 图谱自动注释：串联重复的元件片段会把整条序列链成一个比对窗口（300 kb 单个请求
  ~8 s / ~750 MB，匿名可触发）。现在单次比对窗口恒为元件长 + 60 bp，候选按种子
  覆盖跨度排序并受比对次数与 DP 单元格总量预算约束（超出告警），同样输入约 1.7 s /
  30 MB；正负链共用排序，负链元件不再因正链先用完预算被静默漏掉。
- 图谱 `commercial` 酶组：修饰依赖型酶让位点数爆炸（300 kb 响应 45 MB，50 kb 文档
  在内存缓存里常驻约 35 MB/条），且 MspJI/LpnPI/SgeI 计数与 Biopython 不一致。现在排除
  这类酶，位点明细按酶/按文档封顶，序列化超过 1 MB 的文档不进缓存、缓存总字节封顶；
  300 kb commercial 响应约 6 MB。
- NCBI 导入：节流锁里 sleep 使并发导入串行排队、各自占着全局线程池令牌，慢速响应还能
  把线程占任意久。现在锁内只预约时间点、锁外等待，导入跑在独立的小线程令牌池上，并有
  整次取数的总时长上限。
- 图谱 `/parse`：产出的文档可能违反 `MapDocument` 自身约束（segments >200、qualifiers >100、
  primers 无上限、长 primer_bind 的序列 >1000），再提交给 `/analyze` / `/export/genbank` 会
  422；超长 `join()` 解析要 5 s 以上。现在解析与组装按同一份常量截断并告警，分段超过
  1000 的位置串在进入 Biopython 前直接 422。
- 图谱请求体：此前先整体读入（JSON `request.body()`、multipart spool、`/analyze` / `/export`
  由 pydantic 全量解析）才比较大小，chunked 传输还能绕过 Content-Length 预检，实际上限是
  nginx 的 100 MB。现在 `/api/map/*` 由 ASGI 中间件边收边计数、超限即 413，nginx 也单独收紧。
- Golden Gate：只检查插入片段内部（且按切点过滤，贴着末端、切点落在片段外的位点漏报）；
  引物尾（位点 + 间隔 + overhang）与插入片段拼接处新生的位点不报。现在按识别序列计数，
  对完整扩增子数位点、多出的写进设计告警与引物备注；间隔碱基不再固定为 A，按拼接上下文
  挑一个不形成额外位点的组合（例如 BfuAI 系 + overhang CCTG）。
- 图谱导出：线性分子上 `start > end` 的特征此前照样写成跨原点 `join(s..L,1..e)`（线性记录里
  无意义），现在 `/export/genbank` 返回 422。
- 图谱解析：分段跨正负两条链的 `join()` 与 `order()` 在契约里存不下（按无方向 / `join()`
  处理，导出时丢失），此前静默，现在写入 `meta.warnings`。
- SnapGene `.dna` 特征颜色改按特征名对齐：此前按序号对齐，Features 包里的 `primer_bind`
  特征不消耗序号，其后所有特征的颜色整体错位。
- 载体图谱缓存键补上载体名称、拓扑与 MCS 名：管理员只改这些字段时不再最多 1 小时内返回旧图谱。
- 图谱查看器：切换酶组 / 重新分析的请求在途时换了文档，旧结果不再覆盖新文档
  （也不再发出 doc-change 写回 /map 页）；请求期间对特征的编辑保留，返回的酶切、
  ORF 与新识别的自动特征合并进当前文档
- /map 页：文档过大、暂存 sessionStorage 失败时删掉旧暂存，刷新后不再静默恢复
  上一份序列（与"刷新后需重新打开"的提示一致）
- 图谱触屏交互：环形 / 线性 / 序列视图与缩略导航条处理 `pointercancel` 与
  `lostpointercapture`，浏览器接管滚动或系统手势后拖拽状态复位，不再出现
  "手指已离开、选区还跟着动 / 悬停提示失效"
- 图谱悬停性能：指针在同一元件上移动只更新提示框坐标，不再重算各视图高亮与
  环形图标签；序列视图的行内容不再随悬停 / 选区 / 搜索重建，特征按可见窗口筛选、
  搜索命中按行分桶。300 kb、3000 个特征、2000 条命中时 200 次悬停由约 11 s 降到
  约 0.4 s（测试环境）
- 载体详情页：在页内快速从载体 A 切到 B 时，A 较慢的图谱 / 出处响应不再覆盖 B
  （标题与下载按钮不再错位）；切换时先清空出处，B 的出处加载失败时不再残留 A 的
- /map 页与载体库页的 SnapGene 在线库：快速切换集合时只认最后一次请求，乱序
  返回的旧集合不再覆盖列表（避免用当前集合号去开另一个集合的条目），"加载中"
  也不会被先结束的旧请求提前收起
- 外链协议白名单（纵深防御）：载体详情页的出处链接（含"从图谱另存"写入的 URL）、
  载体库页的 Addgene 元数据直链、UniProt 交叉引用、在线预览的原始来源与 NCBI 链接
  只在 `http(s)://` 时渲染为链接，`javascript:` 等按纯文本显示；Addgene 质粒号与
  UniProt 登录号拼进链接前做 URL 编码
- 酶选择框（单条/批量设计的 Golden Gate 酶、限制酶）：下拉候选不再用 `v-html`
  拼酶名，后端返回的名字里即使含 HTML 也只按文本显示
- 载体库页在线导入（SnapGene 列表导入、预览弹窗"导入到载体库"、Addgene 导入）
  加在途锁，连点不再重复导入或得到一次 409；/map 页解析中再拖入 / 选择文件时
  只采用最后一次的结果
- 图谱序列视图的 CDS 翻译：后端改为按生物学顺序给出 `segments`（负链坐标降序）后，
  前端仍把各段按顺序拼接再整体倒序，负链多段 / 跨原点拆段特征（如
  `complement(join(1..100,900..1000))`）的翻译与密码子对位错乱。现在逐段取正向位置、
  负链段内倒序后按段顺序拼接，与后端 `extract_feature` 逐字一致。
- 图谱环形 / 线性视图：负链多段特征的箭头按坐标定位到 3' 端（整体起点）所在段，
  不再依赖段顺序（`segments` 改为生物学顺序后会画到错误的一段上）。


## [2.12.0] - 2026-10-12

### 升级注意

- 可选新环境变量 `ADDGENE_SESSION_COOKIE`：填写你自己在浏览器登录 Addgene 后的
  会话 Cookie，后端才能取**你有权访问**的全长序列并直接导入载体库（只发给
  `addgene.org`，不落盘/不回显/不记日志）。不填也能用：相关端点返回 403 + 合法
  路径指引，工具始终不代登录、不绕登录墙。Docker 部署需同步 `docker-compose.yml`
  （新增三个环境变量透传），裸机部署随项目文件复制即可，无数据迁移。
- 载体响应新增字段（向后兼容，前端刷新即可）：`GET /api/vectors`、
  `GET /api/vectors/{id}` 增加 `topology` 与 `data_provenance`。行为变化：导入的
  线性片段（PCR 产物、Ensembl 片段、线性 GenBank 记录）现在按其真实拓扑显示为
  线性图谱，此前一律当环状质粒画。

### 新增

- 在线来源导入收尾（载体库页与图谱页）：
  - **结构化出处**：导入的载体落 `data_provenance`（来源 / URL / 许可 / 抓取时间 +
    集合、质粒号、登录号等），载体详情页新增「出处 / 许可」块，便于成果中标注来源；
    内置载体与 NCBI 批量导入路径同形状
  - **图谱页「另存为载体」**：`POST /api/vectors/import/from-map` 把当前打开（含编辑器
    里改过的注释）的序列与特征存进载体库，后端不再外呼——保存的与看到的一定是同一份
  - **UniProt 通道**：按基因 / 蛋白名 / 登录号检索条目并反查 Entrez / EMBL / RefSeq /
    Ensembl 交叉引用（UniProt 是蛋白库、不含质粒序列，只作引路）；载体库新增
    「UniProt」页签
  - **Addgene**：配置自带会话后可把全长序列导入载体库（`POST
    /api/vectors/import/addgene/{质粒号}`）；本地索引搜索与匿名元数据保持原样
  - **联网冒烟**：`scripts/smoke_online_sources.py`（默认不联网，`--run` 才真打
    SnapGene / Ensembl / UniProt / Addgene，只读不写）
- 载体库写入口（上传 / 批量导入 / 编辑 / 删除 / 在线导入 / 另存为载体）对非管理员隐藏，
  后端 403 仍是权威
- 图谱契约的元件类型（`rep_origin`、`mcs`、`marker`、`signal_peptide` 等）导入载体库后
  不再一律显示为 `other`

### 变更

- 载体拓扑按真实值保存与展示：线性片段不再被当环状质粒画（列表缩略图、载体详情、
  载体图谱三处一致）

### 修复

- 导入的载体此前只在描述里写一句来源，出处无法结构化展示、也无法按 URL 重取
- 测试隔离：打桩载体目录只打了 `app.config.settings`，而 `app.design_service` 持有
  reload 前的旧实例，全量跑时表现为「刚导入的载体 404」（单跑该文件是绿的）

## [2.11.1] - 2026-10-12

### 修复

- 环形图谱用 −/+ 按钮放大时以圆心为中心，视野里只剩中间的名称、长度和刻度数字，
  特征环与标签被推出边缘，按钮缩放形同虚设。现在按钮（及新增的缩放滑块）放大时
  以选区中点为锚（只有光标时取光标处；都没有时取 12 点钟或离视野中心最近的环上点），
  环始终留在视野里；Ctrl+滚轮仍以指针为中心。放大后在环带以外的任意空白处（含中心区）
  拖拽或用中键拖拽即可平移，平移范围受限，环不会被整个拖出视野。放大时图谱铺满面板
  整宽，不再在比图宽的面板里被硬裁在左右两侧。线性图的按钮缩放同样以选区为中心。
- 环形图坐标换算在 SVGPoint 不提供 `matrixTransform` 的环境（CI 的 happy-dom 20）里
  抛 TypeError，v2.11.0 的前端 CI 因此失败；现回落到包围盒换算，浏览器行为不变。

## [2.11.0] - 2026-10-12

### 升级注意

- **图谱接口破坏性变更**：`GET /api/design/{id}/map` 与 `GET /api/vectors/{id}/map`
  改为返回 MapDocument（契约 `plasmid-map/1`，见 `docs/MAP.md`），不再返回旧的
  `{name, length, sequence, features, enzyme_sites}`，无兼容层。主要差异：特征
  `strand` 由 `"+"/"-"` 改为 `1/-1/0`，`type` 归一为契约 FeatureType（`resistance` →
  `marker`、`origin` → `rep_origin`、`multiple_cloning_site` → `mcs`…），`description`
  改名 `notes`；酶切位点改为按酶分组的 `enzymes[].sites[]`（回文位点只报一次，
  双链切点 `cut_top/cut_bottom`）；新增 `topology`、`orfs`、`primers`、`meta`。
  直接调用这两个端点的脚本须按新形状改写。
- 新增功能开关 `map`（质粒图谱）。新库默认对访客与普通用户开放；已有站点升级后
  按「能用序列工具（`analysis`）即可用图谱」一次性继承，管理员可在功能矩阵单独收紧。
- Docker 部署须重新构建后端镜像（镜像新增 `data/features/` 元件库）；裸机部署随项目
  文件复制，无需额外操作。
- **Golden Gate 引物结果变化**：识别序列与 overhang 之间的间隔改按酶的切割偏移计算。
  BsaI / BsmBI 不变（1 nt）；**BbsI 引物由 1 个间隔碱基改为 2 个**——此前 BbsI（N2/N6）
  切下的 overhang 错位一位，用旧版设计的 BbsI 引物请重新设计。Golden Gate 设计请求的
  `enzyme` 不是可商购、切 4 nt 5' 突出端的 Type IIS 酶时，现在直接 422（此前在引物设计
  阶段才报错）；`GET /api/analysis/enzymes` 的缓存版本递增为 v3。
- 可选新环境变量 `NCBI_EMAIL`（建议）、`NCBI_API_KEY`（图谱页 NCBI 导入用，Docker 已透传，
  两份 `.env.example` 有说明）；不设也能用。

### 新增

- 质粒图谱后端 `/api/map/*`：解析 GenBank / FASTA / SnapGene `.dna` / 粘贴文本
  （`join()` 与跨原点特征、ApE / SnapGene 颜色）、编辑后重算、酶集合
  （common / unique6 / commercial / golden_gate）、GenBank 导出。环状感知的双链切点、
  ORF 查找。上限 `MAP_MAX_LENGTH`（默认 300000 bp）、`MAP_MAX_UPLOAD_MB`（默认 5）。
- 图谱自动注释：66 条常见元件库（载体库与 NCBI GenBank 中可核验的序列，逐条记录
  来源，`scripts/build_feature_library.py` 重建；含 p15A ori、AcV5 标签与 pUC 标准
  589 bp ori），k-mer 种子 + 局部比对，报告 identity / coverage，覆盖不足标 fragment；
  肽标签按翻译检索。
- 图谱酶切位点给出每个粘端位点的实际突出端碱基（`sites[].overhang`，Type IIS 与
  AvaI 等简并识别酶不再只有 `NNNN` 模板），并按 NEB / REBASE 的重叠甲基化表标出对
  Dam / Dcm 敏感的酶（`methylation`）与会被常规菌株甲基化阻断的位点（`sites[].blocked_by`，
  如 XbaI 的 `TCTAGATC`、StuI 的 `AGGCCTGG`）。
- `GET /api/vectors` 列表每项新增 `map_preview`（长度、拓扑、归一后的特征，不含序列），
  供载体列表画图谱缩略图。
- 图谱酶数据（REBASE，经 Biopython `Bio.Restriction`）：`GET /api/map/enzymes/{name}`
  酶详情（REBASE 厂商代码与名称、可商购同切酶 / 新切酶、Type IIS、Golden Gate 适用、
  甲基化敏感性）、`GET /api/map/suppliers` 厂商表，酶组新增 `type_iis`（全部可商购
  Type IIS 酶）。Type IIS 判定有单测守护：非回文、确定碱基 ≥4、切点全在位点同侧之外。
  Biopython 的最适 / 失活温度对全部酶都是 37 / 65 °C 占位值，不予展示。
- 图谱页酶切面板可按厂商（NEB、Thermo 等）筛选，点酶显示详情；取过的详情同时出现在
  悬停提示里。
- 图谱页「NCBI 导入」：输入 GenBank / RefSeq accession（如 `L09137`、`M77789.1`），后端
  `POST /api/map/import/ncbi` 经 E-utilities efetch（`gbwithparts`，带 tool / email，
  可选 API key）取完整记录并走现有解析流水线；校验 accession、沿用体积与长度上限、
  文本缓存 24 小时、限流 `ncbi` 档、受 `map` 功能开关控制。
- Golden Gate 设计的可选酶改由 REBASE 数据给出：全部可商购、切出 4 nt 5' 突出端的
  Type IIS 酶（BsaI / BsmBI / BbsI 仍排最前，另有 Esp3I、PaqCI、BsmAI、Eco31I、BpiI、
  AarI 等），设计页下拉随之扩展；`GET /api/analysis/enzymes` 新增 `golden_gate_enzymes`。
- README「数据来源与致谢」与站内免责声明补充 REBASE 引用（Roberts R.J. et al.,
  Nucleic Acids Res. 51:D629-D630 (2023), doi:10.1093/nar/gkac975）与 NCBI 致谢。

- 全新质粒图谱查看器（前端 `src/map/`，SVG，零新依赖）：环形 / 线性 / 序列 / 分屏
  四种视图共享选区与悬停（任一视图选中，其余同步高亮）；环形图可拖拽中心旋转、
  Ctrl+滚轮缩放，线性图可缩放平移并配缩略导航，序列视图虚拟滚动支持 300 kb。
  特征按链画箭头并自动分道，标签放得下画在元件内、否则外置引线且互不重叠，
  实在放不下的显示「N 项无法显示」并可展开明细。酶切：单切点加粗、平端红色，
  可按酶组（由后端重算）、切点数（1 / ≤2 / 任意）、名称或识别序列、Golden Gate
  过滤并逐个隐藏。Ctrl+F 序列搜索（双链、跨原点、最多 3 个错配、支持 IUPAC），
  选区实时显示长度 / GC% / Tm；选中 CDS 显示翻译；可导出 SVG、2 倍 PNG 与 GenBank。
- 独立图谱页 `/map`（导航「图谱」，受 `map` 功能开关控制）：拖放或选择 GenBank /
  FASTA / SnapGene `.dna` / 纯文本文件（≤ 5 MB），或粘贴序列 / GenBank 文本并指定
  名称与环状/线性；可选自动注释与酶组，「示例：pUC19」一键载入内置载体。文件过大、
  格式不支持、序列无效分别给出中文提示；打开的图谱暂存在本标签页，刷新不丢。
- 图谱编辑（仅 `/map` 页）：特征重命名、改色、改类型/方向、删除，从当前选区新增
  特征，可撤销；「重新分析」按编辑后的特征重算酶切与 ORF（可选重新自动注释）；
  导出 GenBank / SVG / PNG 均反映编辑结果。结果页与载体详情页保持只读。
- 酶切消化面板（查看器侧栏「消化」）：从可见酶中选 1–3 个，列出片段长度、坐标
  （环状感知，跨原点标注）与两端酶，并画虚拟凝胶泳道（1 kb ladder 对照）；点片段
  在图上选中该区域。可选跳过被甲基化阻断的位点。
- 被 dam/dcm 甲基化阻断的酶切位点以灰色虚线显示，悬停提示「被 dam/dcm 甲基化
  阻断」，可用「显示甲基化阻断位点」开关隐藏；IIS 型酶等提示该位点实际突出端。

### 变更

- 设计结果页与载体详情页改用新查看器，旧的 Canvas 环形图与线性序列视图移除；
  载体详情页不再分「图谱 / 元件列表 / 序列」标签页，元件与酶切列表并入查看器侧栏。
- 测序分析的「匹配简图」改用同一套线性图谱绘制，read / 覆盖 / 变异信息与点击
  跳峰图不变，另可缩放并悬停查看详情。
- 载体库列表卡片显示图谱缩略图与长度/拓扑（来自列表接口的 `map_preview`，缺省时不显示）。
- 环形图谱视觉重做（参考 SnapGene）：环外标签按角度排成左右两列的有序扇形，拥挤处
  呈阶梯堆叠、必要时向上/下延伸，引线不再交叉、不压字、不穿回环内；同一切点的酶
  合并成一行（`EagI - NotI (423)`，各酶名仍可单独悬停），名字后用小号灰字写切点位置
  （窄屏省略）；特征弧带加粗，名字放得下就沿弧写在条内，否则用特征色淡底的色块标签；
  双线主链之间画主/次刻度；特征配色整体柔和化（三个视图一致）；悬停特征时加外发光、
  其余特征变淡。导出的 SVG/PNG 与屏幕一致。

### 修复

- 批量设计页选 Golden Gate 时从不发送 `enzyme`，而 Golden Gate 缺酶的设计请求
  自 v2.6.0 起直接 422，整批提交必然失败。批量页新增 Type IIS 酶选择器（候选来自
  `GET /api/analysis/enzymes` 的 `golden_gate_enzymes`，默认 BsaI）并随请求下发。
  服务端维持原契约、**不**为批量接口回落默认 BsaI：静默套酶会产出错误引物，
  批量与单条保持同一校验（整批请求在提交时 422，不创建任务）；逐条失败原因
  改为只给业务消息，不再夹带 pydantic 样板文字。
- 单条设计页在后端酶表不含 BsaI 时，会把 Golden Gate 的酶回退成全酶表首项
  （可能是 EcoRI 这类非 Type IIS 酶），提交即 422；改为只在 Golden Gate 候选内回退。
  两个设计页的提交错误改用统一格式化，422 的数组 `detail` 不再显示为 `[object Object]`。
- Golden Gate 引物对 BbsI 的间隔碱基数错误（一律写 1 个，BbsI 需 2 个），切下的
  overhang 错位一位；现按各酶切割偏移计算，并以「引物装回扩增产物再酶切」的测试守护。
- 经典双酶切拒绝 Type IIS 酶改按 REBASE 判定：手写名单之外的 Type IIS 酶（如 FokI）
  也给出「请改用 Golden Gate」的明确提示，而不是笼统的「未知限制酶」。
- 7 个 SnapGene 来源载体（pcDNA3.1、pET-21a、pET-28a、pFastBac1、pGEX-4T-1、pLVX、
  pYES2）的特征起点整体左移一位，CDS 长度不是 3 的倍数、翻译读码框错位；已更正。
- pUC19 的特征坐标沿用了另一套编号（M77789），与 L09137 序列整体错位：lacZα、AmpR
  不是完整 ORF，MCS 不含所列酶切位点。已按序列本身重标（lacZα complement(146..469)、
  MCS 396..452、lac operator / promoter、CAP 位点、ori 867..1455、AmpR
  complement(1626..2486) 及其启动子）。
- pLVX 的 mcs 列了序列中不存在的 AgeI 位点（mcs 区间因此从 665 起算），已移除。

## [2.10.0] - 2026-10-12

### 升级注意

- 启动时自动迁移数据库：`users` 表新增可空列 `batch_max_reads_per_group`、
  `batch_max_total_reads`，`site_settings` 表新增可空列 `batch_read_limits`
  （SQLite 与 PostgreSQL 均自动 `ALTER`，无需手动操作）。新列为 NULL 即沿用环境
  变量默认，升级后上限与 v2.9.1 一致。
- Docker 部署此前未把 `SEQUENCING_BATCH_MAX_*` 透传给后端容器，在 `.env` 里调整
  不生效；现已透传。若曾在 `.env` 写过这些变量，升级后会开始生效。
- **离线脚本退出码变化**：`scripts/batch_sequencing_report.py` 有质粒分析失败
  （结论「分析失败：…」）时此前仍返回 0，现在整批照常写出、回填后返回 **3**，
  末行打印失败个数。依赖「返回 0 即成功」的调用方请把 3 视为「部分失败、需人工
  查看批次总览.txt」。「无法判定」、缺图谱等数据结论不算失败，仍返回 0；致命错误
  （1）与参数用法错误（2）不变，`--dry-run` 恒为 0。
- 批量测序改为分块上传：Docker 需重新构建后端镜像（预建暂存目录）并按新的
  `docker-compose.yml` 启动，会新建具名卷 `batch-uploads`；裸机部署须同步
  `deploy/bare/nginx.conf` 新增的 `/api/sequencing/batch-sessions` 块（100m、
  读超时 120s），暂存目录默认 `$DATA_DIR/batch_uploads`，服务用户须可写。
  前置在 Cloudflare 之后时无需改 Cloudflare 设置：单个请求 ≤90 MB，分析超过
  60 秒自动转轮询，不会撞上 100 秒超时。磁盘最坏占用约为
  `SEQUENCING_BATCH_SESSIONS_TOTAL × SEQUENCING_BATCH_SESSION_MAX_MB`（默认 16 × 2 GB），
  空间紧张时调小这两项。
- Docker 前端镜像新增入口脚本，须重新构建（`docker compose up -d --build frontend`）。
  前端 nginx 改为只在 `FRONTEND_BIND` 为本机回环时采信 compose 网关转来的
  `X-Real-IP` / `X-Forwarded-For` / `X-Forwarded-Proto`。`FRONTEND_BIND=0.0.0.0`
  又在前面放了宿主机 nginx 的部署，升级后真实客户端 IP 不再被采信（全部计入网关
  一个限流桶）：请改回 `FRONTEND_BIND=127.0.0.1`。新变量 `HOST_PROXY_ADDRS`
  （默认 `auto`，可设 `none` 或单个地址清单）；改过 `PLASMID_GATEWAY` 的部署不必
  再手改 `nginx.conf`。

- **批量测序新增计算预算与软时限**：比对前按 Σ(参考长度 × read 数) 估算，超过
  `SEQUENCING_BATCH_COMPUTE_BUDGET_MBP`（默认 50 Mbp·read）返回 413；整批运行
  超过 `SEQUENCING_BATCH_SOFT_TIMEOUT`（默认 1800 秒）后剩余分组记为「分析失败」。
  默认 read 上限下不会触发；曾在 `/admin` 大幅调高 read 上限、且参考图谱很大的
  站点可能开始收到 413，需要时在 `.env` 调大或设 0 关闭（Docker 已透传）。

### 新增

- 管理员可在 `/admin` 按分组（匿名访客 / 普通用户 / 管理员）设置批量测序的
  「每组最多 read 数」「整批最多 read 数」，并为单个账号单独设置（留空即跟随
  分组）；生效顺序为个人设置 → 分组设置 → 服务器默认（环境变量，仍为 24 / 240）。
  对应 `GET/PUT /api/admin/settings` 的 `batch_read_limits`，
  `PUT /api/admin/users/{id}` 的 `batch_max_reads_per_group`、
  `batch_max_total_reads`（显式 `null` 清除覆盖）。
- 新增服务器安全上限 `SEQUENCING_BATCH_READS_PER_GROUP_HARD_MAX`（默认 384）、
  `SEQUENCING_BATCH_READS_HARD_MAX`（默认 5000）：管理员填写的值超出时返回 422，
  生效值也按它截断。
- `GET /api/auth/site-config` 新增可选字段 `batch_read_limits`（当前账号的生效
  上限），批量测序页据此显示「本账号单批最多 N 条 read」。
- 批量测序分块上传：新增 `/api/sequencing/batch-sessions` 系列接口（建会话、追加
  分块、提交分析、查询、取消），批量测序页自动把文件切成 ≤90 MB / ≤200 个的分块
  依次上传并显示「上传中 3/7」进度，失败分块自动重试，取消即删除服务器暂存；
  服务器拼成一个批次分析一次，结果与单请求入口一致。超过 Cloudflare 免费版
  100 MB 请求体上限的批次不再被页面拦截。新环境变量
  `SEQUENCING_BATCH_SESSION_MAX_MB`（单批体积，默认 2048）、
  `SEQUENCING_BATCH_SESSIONS_PER_OWNER` / `SEQUENCING_BATCH_SESSIONS_TOTAL`
  （同时进行中的会话，默认 2 / 16）、`SEQUENCING_BATCH_FINALIZE_WAIT`（默认 60 秒）、
  `SEQUENCING_BATCH_SESSION_DIR`（暂存目录）。site-config 的 `batch_read_limits`
  附加 `chunk_max_files`、`chunk_max_mb`、`session_max_mb`。

### 变更

- 批量测序超出 read 上限时，413 提示写明生效的上限值及其来源（个人设置 / 分组
  设置 / 服务器默认 / 安全上限）。read 上限成为批次的唯一规模上限：批量测序页
  不再受单次 400 个文件与 500 MB 限制（这两项只约束保留的单请求接口
  `POST /api/sequencing/analyze-batch`），单批体积改由 `SEQUENCING_BATCH_SESSION_MAX_MB` 约束。

### 修复

- 离线脚本分析失败时返回非零退出码（3），不再把部分失败报告为成功。
- 登录、注册的 bcrypt 密码哈希/校验移到线程池执行，并发登录不再阻塞整个服务。
- 共识序列导出（`covered_only` 的 N 屏蔽与排版）移到线程池，N 屏蔽改为线性
  复杂度，碎片化覆盖的 200 kb 共识导出不再卡住事件循环；导出内容不变。
- 批量测序信息表改为 openpyxl 只读流式解析，行列上限边读边查（不再信任文件
  自带的 dimension 声明），大表解析的内存与耗时显著下降；只有结论回填时才另行
  以可写模式加载。解析结果不变。
- 批量整理包（ZIP）改为写入磁盘私有目录（缺省 `$DATA_DIR/batch_uploads/reports`，
  可用 `SEQUENCING_BATCH_REPORT_DIR` 指定），不再驻留各 worker 内存：
  `BACKEND_WORKERS>1` 时下载不再随机 404，下载改为流式发送。接口不变。
- 分块上传的批次提交分析（finalize）不再把整批文件（最多 2 GB）一次读入内存：
  逐组比对、逐个打包时按需从暂存目录读取，峰值内存约为最大分组的文件 + 整理包。
- 整理包（网页 ZIP 与离线脚本输出共用）超过 80 字符的文件名/文件夹名截断时
  保留扩展名（此前 `….fasta` 会被截成无扩展名、下游软件打不开）；不同质粒名
  安全化或截断后撞成同一文件夹名时（如 `a/b` 与 `a:b`、前 80 字符相同的长名）
  不再合并进同一文件夹，后者按原名排序加 `~2`、`~3` 区分。80 字符以内且不撞名
  的名字与此前完全一致。
- 整理清单.csv（网页 ZIP 与离线脚本共用）防 CSV 公式注入：以 `=`、`+`、`-`、
  `@`、制表符或回车开头的单元格（如文件名 `=HYPERLINK(…)`、质粒名 `-X`）写出时
  前缀 `'`，Excel/WPS 打开不再当作公式执行。**按 CSV 读取清单的下游脚本会看到
  这类单元格多一个开头的 `'`**（Excel 中显示为前缀）：`'` 之后紧跟上述字符时
  去掉这一个 `'` 即为原值，其余单元格不变。离线脚本重跑读回清单时自动还原。
  报告 Markdown 表格里的 `|` 转义为 `\|`、换行替换为空格，不再把表格行打乱。
- 信息表「测序结果」列回填（网页整理包与离线脚本共用）重跑时替换上次写入的
  结论，不再一层层叠加。按结论固定开头识别本工具写过的行（`合格：`、`不合格：`、
  `疑似混合：`/旧版 `疑似混合样品：`、`无法判定：`、`分析失败`、`已整理 N 个测序
  文件，但缺少同名参考图谱…`、`未在文件夹中找到…`、`未找到克隆 … 的图谱或测序
  文件`、`已找到参考图谱，但未匹配到…`），旧版本叠加出的多行也一并清理；其余行
  视为用户手写内容原样保留，新结论写在最后一行。结论文本格式不变（未加新前缀，
  按「合格」等开头筛选的表格照常可用）；用户自己以上述开头写的行会被当作旧结论
  替换。
- Docker：前端端口对外发布时，经 docker-proxy 转发（IPv6 入站、userland-proxy、
  rootless 等）的直连来源显示为 compose 网关而被信任，客户端可伪造 `X-Real-IP`
  换限流桶；客户端自带的 `X-Forwarded-For` 也不再经非可信来源转给后端。
- 裸机部署文档的「更新部署」与「换 pip 镜像源」命令补上 `--require-hashes`，
  按文档升级不再绕过锁文件哈希校验；后端 README 的开发安装同时装开发锁。
- 密码子优化结果缓存键加入所用密码子表文件的内容摘要：修改
  `data/codon_tables/*.yaml`（或改 `CODON_TABLES_DIR`）后，Redis 中按旧表算出的结果
  不再命中，不必再手动递增 `CODON_OPT_ALGO`。升级后已有的密码子优化缓存全部失效
  一次（首次请求重新计算）。

## [2.9.1] - 2026-10-11

### 升级注意

- Python 依赖改为带哈希的锁文件：源清单改名为 `src/backend/requirements.in`、
  `deploy/docker/requirements.backend.in`，同名 `.txt` 变为生成的锁文件（含全部
  传递依赖与 sha256）。CI、Docker 镜像与裸机 `install.sh` 改用
  `pip install --require-hashes`；手动安装或自定义镜像源的部署请同样从锁文件安装，
  镜像源必须提供与 PyPI 相同的文件。biopython 并入 Docker 锁文件，不再单独安装。
  更新方法见 README「Python 依赖锁定」。
- **测试依赖移出运行时清单**：pytest、httpx2 及其传递依赖移到
  `src/backend/requirements-dev.in` / `requirements-dev.txt`，裸机部署与 Docker
  镜像不再安装；本地开发跑测试需
  `pip install --require-hashes -r requirements.txt -r requirements-dev.txt`。
  已有裸机 venv 中的旧包不会自动卸载，可手动 `pip uninstall pytest httpx2`。
- **Docker 部署默认关闭 API 文档**：compose 新增 `ENABLE_API_DOCS`（默认 `false`），
  `/docs`、`/redoc`、`/openapi.json` 返回 404，不再经前端端口对公网公开。需要时在
  `.env` 设 `ENABLE_API_DOCS=true`，并在宿主机 nginx 限制来源。本地开发与裸机部署
  默认仍开启（裸机 nginx 已只对本机/内网开放）。
- **批量测序新增 read 数上限**：每组最多 `SEQUENCING_BATCH_MAX_READS_PER_GROUP`
  （默认 24）条、整批最多 `SEQUENCING_BATCH_MAX_TOTAL_READS`（默认 240）条，超出
  返回 413 并提示拆批。单批确需更多 read 时在 `.env` 调大。
- 高负载时上传在读取文件前就排队等待并发槽；参考序列解析、从数据库重新加载旧
  记录同样可能返回 503「繁忙」。

### 修复

- 测序重负载阶段（ab1 读入解析、分析、落库、批量 Excel 解析、报告打包、旧记录
  解压回灌）纳入并发闸并移出事件循环，单个大请求不再卡住整个服务。
- 离线脚本换输出目录重跑时跳过此前的输出目录，不再把旧副本当作新 read 重复分析；
  输出目录为数据目录本身/其上级或为普通文件时直接报错；信息表损坏时给中文提示；
  Excel 回填改为临时文件原子替换，失败不损坏原表；克隆模式提示改指真实页面
  `/sequencing/batch`。
- `core.__version__` 与 `APP_VERSION` 改为同一来源。
- CI 中 pip-audit 改为按哈希锁安装。

- 密码子优化缓存键 `algo` 由 v2.1 递增为 v2.2，`/analysis/enzymes` 缓存键增加
  版本段：v2.5.0–v2.8.0 重建密码子表、改动优化器和酶表后键未变，启用 Redis 时
  会在 TTL 内（24 小时 / 7 天）返回旧结果。升级后旧条目自然过期，无需再手动
  `POST /api/cache/clear`。

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
  唯一位点）；HindIII 与 SacI 不再误报可互连。（旧酶表缓存问题见 [Unreleased]，
  升级到其后版本无需手动清除。）
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
  （缓存键版本已在 [Unreleased] 递增，升级到其后版本无需手动清缓存。）

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

[Unreleased]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.12.0...HEAD
[2.12.0]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.11.1...v2.12.0
[2.11.1]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.11.0...v2.11.1
[2.11.0]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.10.0...v2.11.0
[2.10.0]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.9.1...v2.10.0
[2.9.1]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.9.0...v2.9.1
[2.9.0]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.8.1...v2.9.0
[2.8.1]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.8.0...v2.8.1
[2.8.0]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.7.1...v2.8.0
[2.7.1]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.7.0...v2.7.1
[2.7.0]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.6.0...v2.7.0
[2.6.0]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.5.0...v2.6.0
[2.5.0]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.4.1...v2.5.0
[2.4.1]: https://github.com/Rio-BingS/Plasmid-Designer/compare/v2.4.0...v2.4.1
[2.4.0]: https://github.com/Rio-BingS/Plasmid-Designer/releases/tag/v2.4.0
