# Plasmid Designer v2 — HuggingFace Spaces 部署教程

本文档提供两种部署方式的完整教程，以及自动化部署工具和 CI/CD 配置说明。

---

## 目录

- [方案对比](#方案对比)
- [前置准备](#前置准备)
- [方案 A: Gradio 模式部署](#方案-a-gradio-模式部署)
- [方案 B: Docker 模式部署](#方案-b-docker-模式部署)
- [方案 C: 自动化脚本部署](#方案-c-自动化脚本部署)
- [方案 D: GitHub Actions CI/CD](#方案-d-github-actions-cicd)
- [常见问题排查](#常见问题排查)
- [更新部署](#更新部署)
- [方案 A 与方案 B 功能对比](#方案-a-与方案-b-功能对比)

---

## 方案对比

| 维度 | Gradio 模式 | Docker 模式 |
|------|------------|-------------|
| 部署难度 | 简单，3 分钟完成 | 中等，需要先构建前端 |
| 构建速度 | 快 (~1 分钟) | 较慢 (~3-5 分钟) |
| 用户界面 | Gradio Web UI | 原版 Vue 3 界面 |
| 功能完整度 | 核心功能 | 核心 + 批量设计 + 图谱 API |
| 适用场景 | 快速展示、个人使用 | 需要完整前端的项目展示 |

> 如果不确定选哪个，**建议先试 Gradio 模式**，成功后再考虑 Docker 模式。

---

## 前置准备

无论哪种方案，都需要以下准备：

### 1. HuggingFace 账号

- 访问 [https://huggingface.co/join](https://huggingface.co/join) 注册账号
- 记住你的用户名（下文用 `<your-username>` 代替）

### 2. Git 环境

确保本地已安装 Git：

```bash
git --version
# 应输出 git version 2.x.x
```

如未安装，前往 [https://git-scm.com/downloads](https://git-scm.com/downloads) 下载。

### 3. 项目文件位置确认

确认你已经有 Plasmid Designer v2 项目文件。下文用 `$PROJECT` 代表项目根目录（即 `plasmid-designer-v2/` 所在路径）：

```bash
# 确认项目结构
ls $PROJECT/deploy/hf-gradio/
# 应看到: app.py README.md requirements.txt

ls $PROJECT/deploy/hf-docker/
# 应看到: Dockerfile main.py nginx.conf README.md requirements.txt start.sh

ls $PROJECT/src/backend/core/
# 应看到: __init__.py clone_strategy.py codon_optimizer.py ...

ls $PROJECT/data/
# 应看到: codon_tables/ vectors/
```

---

## 方案 A: Gradio 模式部署

### Step 1: 注册与创建 Space

1. 登录 [https://huggingface.co](https://huggingface.co)
2. 点击右上角头像 → **New Space**
3. 填写信息：
   - **Space name**: `plasmid-designer`（或你喜欢的名字）
   - **License**: MIT
   - **SDK**: 选择 **Gradio**
   - **Hardware**: **Free CPU** (默认)
   - **Visibility**: Public（公开可访问）或 Private
4. 点击 **Create Space**
5. 创建后会跳转到 Space 页面，记住页面上的仓库地址，格式为：
   ```
   https://huggingface.co/spaces/<your-username>/plasmid-designer
   ```

### Step 2: 安装 Git LFS

HuggingFace 仓库使用 Git LFS 管理大文件。首次使用需要安装：

```bash
# 安装 Git LFS (Windows 已内置在 Git for Windows 中)
# macOS
brew install git-lfs

# Linux
sudo apt-get install git-lfs

# 初始化 (只需执行一次)
git lfs install
```

### Step 3: 克隆 Space 仓库

```bash
# 替换 <your-username> 为你的 HF 用户名
git clone https://huggingface.co/spaces/<your-username>/plasmid-designer hf-space
cd hf-space
```

如果提示输入凭据，使用你的 HF 用户名和 Access Token（在 [https://huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) 创建，需要 `write` 权限）。

### Step 4: 复制项目文件

在 `hf-space/` 目录下执行以下命令，将所有必要文件复制过来：

```bash
# 设置项目根目录路径 (根据实际位置调整)
PROJECT=/path/to/plasmid-designer-v2

# ---- 复制部署入口文件 ----
cp $PROJECT/deploy/hf-gradio/app.py .
cp $PROJECT/deploy/hf-gradio/requirements.txt .
# README.md 同时作为 HF Space 卡片和元数据配置 (含 sdk/app_file 等字段)
cp $PROJECT/deploy/hf-gradio/README.md .

# ---- 复制后端核心代码 ----
# Gradio 模式只需要 core/ 目录，不需要 app/ 层（路由、认证等）
mkdir -p backend/core

# 复制 __init__.py
cp $PROJECT/src/backend/core/__init__.py backend/core/

# 复制核心引擎模块
cp $PROJECT/src/backend/core/codon_optimizer.py backend/core/
cp $PROJECT/src/backend/core/primer_designer.py backend/core/
cp $PROJECT/src/backend/core/vector_library.py backend/core/
cp $PROJECT/src/backend/core/clone_strategy.py backend/core/
cp $PROJECT/src/backend/core/sequence_validator.py backend/core/

# 复制完整功能扩展模块 (序列分析、多格式导出、输出生成)
cp $PROJECT/src/backend/core/sequence_analysis.py backend/core/
cp $PROJECT/src/backend/core/export_formats.py backend/core/
cp $PROJECT/src/backend/core/output_generator.py backend/core/

# 复制增强模块 (自动移除 app.config 依赖以适配 HF 环境)
for mod in advanced_primer_designer.py enhanced_codon_optimizer.py external_vector_importer.py vector_data_sources.py; do
  if [ -f "$PROJECT/src/backend/core/$mod" ]; then
    sed 's/^from app\.config import.*$/# [HF适配] 已移除 app.config 依赖/' "$PROJECT/src/backend/core/$mod" \
      | sed 's/^import app\.config.*$/# [HF适配] 已移除 app.config 依赖/' \
      > "backend/core/$mod"
  fi
done

# 复制 backend 层的 __init__.py
echo '"""Plasmid Designer Backend"""' > backend/__init__.py

# ---- 复制数据文件 ----
cp -r $PROJECT/data/ ./data/
```

执行完成后，目录结构应该是：

```
hf-space/
├── app.py              # Gradio 入口 (含5个Tab: 设计/批量/分析/导出/载体库)
├── requirements.txt    # Python 依赖
├── README.md           # Space 元数据
├── backend/
│   ├── __init__.py
│   └── core/
│       ├── __init__.py
│       ├── codon_optimizer.py
│       ├── primer_designer.py
│       ├── vector_library.py
│       ├── clone_strategy.py
│       ├── sequence_validator.py
│       ├── sequence_analysis.py
│       ├── export_formats.py
│       ├── output_generator.py
│       ├── advanced_primer_designer.py
│       ├── enhanced_codon_optimizer.py
│       ├── external_vector_importer.py
│       └── vector_data_sources.py
└── data/
    ├── codon_tables/
    │   ├── CHO.yaml
    │   ├── Ecoli_K12.yaml
    │   ├── Human.yaml
    │   └── Yeast.yaml
    └── vectors/
        ├── pCDNA3.1.yaml
        ├── pET-21a.yaml
        ├── pET-28a.yaml
        ├── pFastBac1.yaml
        ├── pGEX-4T-1.yaml
        ├── pGEX-6P-1.yaml
        ├── pLVX.yaml
        ├── pUC19.yaml
        └── pYES2.yaml
```

### Step 5: 推送到 HF

```bash
# 查看所有文件，确认无误
git status

# 添加所有文件
git add .

# 提交
git commit -m "Deploy Plasmid Designer (Gradio) to HF Spaces"

# 推送到 HuggingFace
git push
```

推送后，HF Spaces 会自动开始构建。构建过程约 1-2 分钟。

### Step 6: 验证部署

1. 回到浏览器，打开你的 Space 页面：
   ```
   https://huggingface.co/spaces/<your-username>/plasmid-designer
   ```
2. 页面会显示 "Building" 状态，等待它变成 "Running"
3. 出现 Gradio 界面后，测试一下：
   - 在 "输入序列" 框中输入一个氨基酸序列，例如：`MKTLLILAVVATAIATLAVGGVALAAG`
   - 点击 "示例序列" 中的示例快速填入
   - 点击 **开始设计** 按钮
   - 右侧应显示密码子优化结果、引物设计、序列验证、克隆方案
   - 可下载 GenBank 文件、引物 TSV、设计报告

---

## 方案 B: Docker 模式部署

### Step 1: 注册与创建 Space

1. 登录 [https://huggingface.co](https://huggingface.co)
2. 点击右上角头像 → **New Space**
3. 填写信息：
   - **Space name**: `plasmid-designer-full`（或你喜欢的名字）
   - **License**: MIT
   - **SDK**: 选择 **Docker**
   - **Hardware**: **Free CPU** (默认)
   - **Visibility**: Public 或 Private
4. 点击 **Create Space**

> **重要**: SDK 必须选 **Docker**，如果选了 Gradio，Dockerfile 会被忽略。

### Step 2: 安装 Git LFS

同方案 A 的 [Step 2](#step-2-安装-git-lfs)，如已安装可跳过。

### Step 3: 构建前端

Docker 模式需要预构建的 Vue 前端文件。在项目目录下执行：

```bash
# 进入前端目录
cd $PROJECT/src/frontend

# 安装依赖 (需要 Node.js 18+)
npm install

# 构建生产版本
npm run build
```

构建完成后，确认 `dist/` 目录已生成：

```bash
ls src/frontend/dist/
# 应看到: index.html assets/
```

> **注意**: 如果你本地没有 Node.js，可以在 [https://nodejs.org](https://nodejs.org) 下载安装。需要 Node.js 18 或更高版本。

### Step 4: 克隆 Space 仓库

```bash
# 回到工作目录
cd /path/to/your/workspace

# 克隆 HF 仓库
git clone https://huggingface.co/spaces/<your-username>/plasmid-designer-full hf-space-docker
cd hf-space-docker
```

### Step 5: 复制项目文件

在 `hf-space-docker/` 目录下执行：

```bash
PROJECT=/path/to/plasmid-designer-v2

# ---- 复制部署配置文件 ----
cp $PROJECT/deploy/hf-docker/Dockerfile .
cp $PROJECT/deploy/hf-docker/nginx.conf .
cp $PROJECT/deploy/hf-docker/start.sh .
cp $PROJECT/deploy/hf-docker/requirements.txt .
cp $PROJECT/deploy/hf-docker/main.py .
cp $PROJECT/deploy/hf-docker/README.md .

# 确保 start.sh 有执行权限
chmod +x start.sh

# ---- 复制后端核心代码 ----
mkdir -p backend/core
cp $PROJECT/src/backend/core/__init__.py backend/core/
cp $PROJECT/src/backend/core/codon_optimizer.py backend/core/
cp $PROJECT/src/backend/core/primer_designer.py backend/core/
cp $PROJECT/src/backend/core/vector_library.py backend/core/
cp $PROJECT/src/backend/core/clone_strategy.py backend/core/
cp $PROJECT/src/backend/core/sequence_validator.py backend/core/
cp $PROJECT/src/backend/core/sequence_analysis.py backend/core/
cp $PROJECT/src/backend/core/export_formats.py backend/core/
cp $PROJECT/src/backend/core/output_generator.py backend/core/

# 增强模块 (自动移除 app.config 依赖)
for mod in advanced_primer_designer.py enhanced_codon_optimizer.py external_vector_importer.py vector_data_sources.py; do
  if [ -f "$PROJECT/src/backend/core/$mod" ]; then
    sed 's/^from app\.config import.*$/# [HF适配] 已移除 app.config 依赖/' "$PROJECT/src/backend/core/$mod" \
      | sed 's/^import app\.config.*$/# [HF适配] 已移除 app.config 依赖/' \
      > "backend/core/$mod"
  fi
done

echo '"""Plasmid Designer Backend"""' > backend/__init__.py

# ---- 复制前端构建产物 ----
mkdir -p frontend/dist
cp -r $PROJECT/src/frontend/dist/* ./frontend/dist/

# ---- 复制数据文件 ----
cp -r $PROJECT/data/ ./data/
```

执行完成后，目录结构应该是：

```
hf-space-docker/
├── Dockerfile          # Docker 构建文件
├── nginx.conf          # Nginx 配置
├── start.sh            # 启动脚本
├── requirements.txt    # Python 依赖
├── main.py             # 适配版 FastAPI 入口 (含批量设计/分析/导出 API)
├── README.md           # HF Space 卡片 + 元数据配置 (sdk: docker)
├── backend/
│   ├── __init__.py
│   └── core/
│       ├── __init__.py
│       ├── codon_optimizer.py
│       ├── primer_designer.py
│       ├── vector_library.py
│       ├── clone_strategy.py
│       ├── sequence_validator.py
│       ├── sequence_analysis.py
│       ├── export_formats.py
│       ├── output_generator.py
│       ├── advanced_primer_designer.py
│       ├── enhanced_codon_optimizer.py
│       ├── external_vector_importer.py
│       └── vector_data_sources.py
├── frontend/
│   └── dist/
│       ├── index.html
│       └── assets/
│           ├── index-xxxx.js
│           └── index-xxxx.css
└── data/
    ├── codon_tables/
    │   ├── CHO.yaml
    │   ├── Ecoli_K12.yaml
    │   ├── Human.yaml
    │   └── Yeast.yaml
    └── vectors/
        ├── pET-21a.yaml
        ├── pET-28a.yaml
        ├── pGEX-4T-1.yaml
        ├── pGEX-6P-1.yaml
        ├── pLVX.yaml
        ├── pUC19.yaml
        └── pYES2.yaml
```

**验证关键文件存在：**

```bash
# 确认前端构建产物存在（Docker 构建会 COPY 这些文件）
ls frontend/dist/index.html
# 必须有输出，否则 Docker 构建会失败

# 确认 start.sh 有执行权限
ls -l start.sh
# 应显示 -rwxr-xr-x
```

### Step 6: 推送到 HF

```bash
# 查看所有文件
git status

# 添加所有文件
git add .

# 提交
git commit -m "Deploy Plasmid Designer (Docker) to HF Spaces"

# 推送
git push
```

推送后 HF Spaces 自动构建 Docker 镜像，这个过程比 Gradio 模式慢，约 3-5 分钟。

### Step 7: 验证部署

1. 打开 Space 页面：
   ```
   https://huggingface.co/spaces/<your-username>/plasmid-designer-full
   ```
2. 等待状态从 "Building" → "Running"
3. 页面加载后应显示 Vue 3 前端界面
4. 测试 API 是否正常：
   - 在浏览器访问 `https://<your-username>-plasmid-designer-full.hf.space/health`
   - 应返回 `{"status":"healthy",...}`
   - 在前端界面中输入序列并提交设计
   - 测试批量设计 API: `POST /api/design/batch`
   - 测试导出 API: `POST /api/analysis/export`

---

## 方案 C: 自动化脚本部署

项目提供了自动化部署脚本 `deploy/scripts/deploy_hf.sh`，可替代方案 A/B 中的手动文件复制步骤。

### 使用方法

```bash
# Gradio 模式 (一行命令)
./deploy/scripts/deploy_hf.sh gradio <your-username>

# Docker 模式 (自动构建前端)
./deploy/scripts/deploy_hf.sh docker <your-username>

# 自定义 Space 名称和目标目录
./deploy/scripts/deploy_hf.sh gradio myuser my-space ./my-deploy-dir

# 清理部署目录
./deploy/scripts/deploy_hf.sh clean hf-space-gradio
```

### 脚本功能

- 自动复制所有核心模块 (含增强模块适配)
- Docker 模式自动执行 `npm install && npm run build`
- 自动初始化 Git 仓库并添加 HF remote
- 部署后验证关键文件是否完整
- 显示下一步操作指引

### 脚本执行后

脚本只准备文件，不自动推送。你需要手动执行：

```bash
cd hf-space-gradio  # 或 hf-space-docker
git add .
git commit -m "Deploy Plasmid Designer to HF Spaces"
git push -u origin main
```

---

## 方案 D: GitHub Actions CI/CD

项目提供了 GitHub Actions 工作流，可实现推送到 main 分支时自动部署到 HF Spaces。

### 配置步骤

1. **添加 GitHub Secrets** (Settings → Secrets and variables → Actions)：

   | Secret 名称 | 说明 | 获取方式 |
   |-------------|------|---------|
   | `HF_USERNAME` | HF 用户名 | 你的 HuggingFace 用户名 |
   | `HF_TOKEN` | HF Access Token | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) 创建，需 `write` 权限 |
   | `HF_SPACE_GRADIO` | (可选) Gradio Space 名称 | 默认: `plasmid-designer` |
   | `HF_SPACE_DOCKER` | (可选) Docker Space 名称 | 默认: `plasmid-designer-full` |

2. **自动触发**：推送到 `main` 分支且修改了以下路径时自动部署 Gradio 模式：
   - `src/backend/core/**`
   - `src/frontend/**`
   - `data/**`
   - `deploy/hf-*/**`

3. **手动触发**：在 GitHub Actions 页面选择 "Deploy to HuggingFace Spaces" workflow → "Run workflow" → 选择部署模式 (gradio / docker / both)。

---

## 常见问题排查

### Q1: Space 一直显示 "Building" / "Error"

**检查方法：**
1. 进入 Space 页面，点击 **Logs** 标签页查看构建日志
2. 常见错误及解决方案：

| 错误信息 | 原因 | 解决方案 |
|---------|------|---------|
| `ImportError: cannot import name 'HfFolder' from 'huggingface_hub'` | gradio 4.x 与新版 huggingface_hub 不兼容 | 将 `requirements.txt` 中 `gradio>=4.0.0` 改为 `gradio>=5.0.0`，并添加 `huggingface_hub>=0.26.0` |
| `ModuleNotFoundError: No module named 'pydantic_settings'` | requirements.txt 缺少依赖 | 确认使用的是 `deploy/hf-gradio/requirements.txt` 或 `deploy/hf-docker/requirements.txt`，不是 `src/backend/requirements.txt` |
| `FileNotFoundError: data/vectors/` | 数据目录路径不对 | 确认 `data/` 目录被完整复制到仓库根目录 |
| `cannot import 'core.codon_optimizer'` | backend/core 目录缺失 | 确认 `backend/core/` 下有所有 .py 文件和 `__init__.py` |
| `COPY failed: frontend/dist/` | Docker 模式前端未构建 | 先执行 `npm run build`，确保 `src/frontend/dist/` 存在后再复制 |
| `Port 7860 is not accessible` | Docker 内服务未监听 7860 | 确认 `nginx.conf` 中 `listen 7860`；确认 `start.sh` 启动了 Nginx |
| `App file not found: app.py` | Gradio 模式缺少 app.py | 确认仓库根目录有 `app.py`，且 `README.md` 中 `app_file: app.py` |
| `ModuleNotFoundError: No module named 'app.config'` | 增强模块未适配 HF 环境 | 确认使用了 `sed` 命令移除 `app.config` 依赖，或使用自动化脚本部署 |

### Q2: Space 正常运行但界面空白

**Gradio 模式：** 检查 `app.py` 末尾是否正确设置 `server_port=7860`。

**Docker 模式：** 检查 `frontend/dist/index.html` 是否存在，以及 `nginx.conf` 中 `root /app/frontend/dist` 路径是否与 Dockerfile 中 `COPY` 路径一致。

### Q3: 推送时提示认证失败

```bash
# 使用 HF Token 认证
huggingface-cli login
# 输入你的 Access Token (从 https://huggingface.co/settings/tokens 获取)

# 或者用 Token URL 直接推送
git push https://<your-username>:<your-token>@huggingface.co/spaces/<your-username>/plasmid-designer
```

### Q4: Space 运行一段时间后自动休眠

HF Spaces 免费版在 48 小时无访问后会自动休眠。首次访问需要等待约 30 秒唤醒。

解决方法：
- 定期访问你的 Space URL（可用 UptimeRobot 等监控服务 ping）
- 或升级为付费 Hardware（不会自动休眠）

### Q5: 想要私密部署，不想公开代码

创建 Space 时选择 **Private** 可见性即可。只有你自己和你邀请的协作者能访问。

### Q6: Gradio 界面功能不完整

确认 `app.py` 是 `deploy/hf-gradio/app.py`（完整版含 5 个 Tab），不是旧版 `deploy/huggingface/app.py`（已移除）。完整版包含：
- Tab 1: 单序列设计（含下载 GenBank/引物/报告）
- Tab 2: 批量设计（ZIP 下载）
- Tab 3: 序列分析（GC 分布图 + 克隆兼容性检查）
- Tab 4: 多格式导出（GenBank/FASTA/SnapGene/Benchling/SBOL）
- Tab 5: 载体库浏览（详情 + FASTA/GenBank 下载）

---

## 更新部署

### 手动更新

当你修改了项目源码后，需要同步更新 HF Space：

```bash
cd hf-space  # 或 hf-space-docker

# 重新复制修改过的文件
cp $PROJECT/src/backend/core/codon_optimizer.py backend/core/
# ... 其他修改的文件

# 提交推送
git add .
git commit -m "Update codon optimizer"
git push
```

或重新运行部署脚本：

```bash
# 重新生成所有文件 (不覆盖 .git)
./deploy/scripts/deploy_hf.sh gradio <username> <space-name> hf-space

cd hf-space
git add . && git commit -m "Update" && git push
```

### CI/CD 自动更新

配置了 GitHub Actions 后，推送到 `main` 分支会自动触发部署，无需手动操作。

---

## 方案 A 与方案 B 功能对比

| 功能 | Gradio 模式 | Docker 模式 |
|------|:-----------:|:-----------:|
| 密码子优化 | OK | OK |
| 引物设计 (PCR/Gibson/Golden Gate) | OK | OK |
| 克隆策略生成 | OK | OK |
| 序列验证 | OK | OK |
| 序列分析 (限制性位点/ORF/GC) | OK | OK |
| 克隆兼容性检查 | OK | OK |
| 多格式导出 (GenBank/FASTA/SnapGene等) | OK | OK |
| 载体库浏览 | OK | OK |
| GC 分布可视化图 | OK | - |
| 载体图谱可视化 | - | OK |
| GenBank 文件下载 | OK | OK |
| 引物订单 TSV 下载 | OK | OK |
| 设计报告下载 | OK | OK |
| 批量设计 (多序列) | OK (ZIP) | OK (API) |
| REST API (可编程调用) | - | OK |
| 自定义前端界面 | - | OK |
| 用户认证/登录 | - | - |
| 数据持久化 | - | - |
| NCBI 在线导入 | 受限 | 受限 |

> "OK" = 支持, "-" = 不支持或受限

---

## 项目部署文件结构

```
deploy/
├── DEPLOY_GUIDE.md          # 本文档
├── docker/                   # Docker Compose 自托管部署
│   ├── Dockerfile.backend
│   ├── Dockerfile.frontend
│   ├── docker-compose.yml
│   ├── nginx.conf
│   ├── requirements.backend.txt
│   └── .env.example
├── hf-gradio/               # Gradio 模式 (HF Spaces)
│   ├── app.py               # Gradio 入口 (5 Tab)
│   ├── requirements.txt     # 精简依赖
│   └── README.md            # HF Space 元数据
├── hf-docker/               # Docker 模式 (HF Spaces)
│   ├── Dockerfile           # Docker 构建
│   ├── main.py              # FastAPI 入口 (含批量/分析/导出)
│   ├── nginx.conf           # Nginx 代理配置
│   ├── start.sh             # 启动脚本
│   ├── requirements.txt     # Python 依赖
│   └── README.md            # HF Space 元数据
└── scripts/
    ├── deploy.sh            # Docker Compose 部署
    ├── deploy_hf.sh         # HF Spaces 自动部署脚本
    └── Makefile             # 构建命令
```
