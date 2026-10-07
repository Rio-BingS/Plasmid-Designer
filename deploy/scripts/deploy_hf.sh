#!/bin/bash
# Plasmid Designer v2 — HuggingFace Spaces 自动部署脚本
# 用法:
#   ./deploy_hf.sh gradio  <hf-username> [space-name] [target-dir]
#   ./deploy_hf.sh docker  <hf-username> [space-name] [target-dir]
#   ./deploy_hf.sh clean   <target-dir>

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 默认值
DEFAULT_SPACE_GRADIO="plasmid-designer"
DEFAULT_SPACE_DOCKER="plasmid-designer-full"

# ==================== 工具函数 ====================

info()  { echo -e "\033[1;34m[INFO]\033[0m $*"; }
ok()    { echo -e "\033[1;32m[OK]\033[0m $*"; }
warn()  { echo -e "\033[1;33m[WARN]\033[0m $*"; }
error() { echo -e "\033[1;31m[ERROR]\033[0m $*"; exit 1; }

check_cmd() {
    command -v "$1" &>/dev/null || error "$1 未安装，请先安装"
}

# ==================== 复制核心模块 ====================

copy_core_modules() {
    local target_dir="$1"
    info "复制后端核心模块..."

    mkdir -p "$target_dir/backend/core"

    # 基础核心模块 (无 app.config 依赖)
    local core_modules=(
        "__init__.py"
        "seq_utils.py"
        "codon_optimizer.py"
        "primer_designer.py"
        "vector_library.py"
        "clone_strategy.py"
        "sequence_validator.py"
        "sequence_analysis.py"
        "export_formats.py"
        "output_generator.py"
    )

    for mod in "${core_modules[@]}"; do
        local src="$PROJECT_ROOT/src/backend/core/$mod"
        if [ -f "$src" ]; then
            cp "$src" "$target_dir/backend/core/"
            ok "  $mod"
        else
            warn "  $mod 不存在，跳过"
        fi
    done

    # 高级模块 (依赖 app.config，需创建适配层)
    local enhanced_modules=(
        "advanced_primer_designer.py"
        "enhanced_codon_optimizer.py"
        "external_vector_importer.py"
        "vector_data_sources.py"
    )

    for mod in "${enhanced_modules[@]}"; do
        local src="$PROJECT_ROOT/src/backend/core/$mod"
        if [ -f "$src" ]; then
            # 创建不依赖 app.config 的适配版本
            sed 's/^from app\.config import.*$/# [HF适配] 已移除 app.config 依赖/' "$src" \
                | sed 's/^import app\.config.*$/# [HF适配] 已移除 app.config 依赖/' \
                > "$target_dir/backend/core/$mod"
            ok "  $mod (已适配 HF 环境)"
        fi
    done

    # backend __init__.py
    echo '"""Plasmid Designer Backend (HF Spaces)"""' > "$target_dir/backend/__init__.py"

    ok "核心模块复制完成"
}

# ==================== 复制数据文件 ====================

copy_data_files() {
    local target_dir="$1"
    info "复制数据文件..."

    cp -r "$PROJECT_ROOT/data" "$target_dir/data"

    local file_count
    file_count=$(find "$target_dir/data" -type f | wc -l)
    ok "数据文件复制完成 ($file_count 个文件)"
}

# ==================== Gradio 模式部署 ====================

deploy_gradio() {
    local username="$1"
    local space_name="${2:-$DEFAULT_SPACE_GRADIO}"
    local target_dir="${3:-hf-space-gradio}"

    info "=== Gradio 模式部署 ==="
    info "HF 用户名: $username"
    info "Space 名称: $space_name"
    info "目标目录: $target_dir"

    # 检查前置条件
    check_cmd git
    check_cmd python3

    # 创建目标目录
    mkdir -p "$target_dir"

    # 复制部署入口文件
    info "复制 Gradio 部署文件..."
    cp "$PROJECT_ROOT/deploy/hf-gradio/app.py" "$target_dir/"
    cp "$PROJECT_ROOT/deploy/hf-gradio/requirements.txt" "$target_dir/"
    cp "$PROJECT_ROOT/deploy/hf-gradio/README.md" "$target_dir/"

    # 复制核心模块
    copy_core_modules "$target_dir"

    # 复制数据文件
    copy_data_files "$target_dir"

    # 验证关键文件
    info "验证文件..."
    local missing=0
    for f in app.py requirements.txt README.md backend/core/__init__.py backend/core/seq_utils.py backend/core/codon_optimizer.py; do
        if [ ! -f "$target_dir/$f" ]; then
            error "缺少关键文件: $f"
            missing=1
        fi
    done
    [ "$missing" -eq 1 ] && error "关键文件缺失，请检查"

    ok "文件验证通过"

    # 初始化 Git 仓库 (如果目标目录是新的)
    if [ ! -d "$target_dir/.git" ]; then
        info "初始化 Git 仓库..."
        cd "$target_dir"
        git init
        git lfs install 2>/dev/null || warn "Git LFS 未安装，大文件可能无法正常管理"
        git remote add origin "https://huggingface.co/spaces/$username/$space_name" 2>/dev/null || true
        cd - >/dev/null
    fi

    # 显示目录结构
    info "目标目录结构:"
    cd "$target_dir"
    find . -not -path './.git/*' -not -path './.git' | head -40 | sort
    cd - >/dev/null

    echo ""
    ok "Gradio 部署文件准备完成!"
    echo ""
    echo "  下一步操作:"
    echo "  1. cd $target_dir"
    echo "  2. git add ."
    echo "  3. git commit -m 'Deploy Plasmid Designer (Gradio) to HF Spaces'"
    echo "  4. git push -u origin main"
    echo ""
    echo "  或直接运行:  cd $target_dir && git add . && git commit -m 'Deploy' && git push -u origin main"
}

# ==================== Docker 模式部署 ====================

deploy_docker() {
    local username="$1"
    local space_name="${2:-$DEFAULT_SPACE_DOCKER}"
    local target_dir="${3:-hf-space-docker}"

    info "=== Docker 模式部署 ==="
    info "HF 用户名: $username"
    info "Space 名称: $space_name"
    info "目标目录: $target_dir"

    # 检查前置条件
    check_cmd git
    check_cmd python3
    check_cmd node
    check_cmd npm

    # 构建前端
    info "构建 Vue 前端..."
    cd "$PROJECT_ROOT/src/frontend"
    npm install --quiet 2>/dev/null
    npm run build
    cd - >/dev/null

    # 验证前端构建产物
    if [ ! -d "$PROJECT_ROOT/src/frontend/dist" ] || [ ! -f "$PROJECT_ROOT/src/frontend/dist/index.html" ]; then
        error "前端构建失败: dist/index.html 不存在"
    fi
    ok "前端构建完成"

    # 创建目标目录
    mkdir -p "$target_dir"

    # 复制部署配置文件
    info "复制 Docker 部署文件..."
    cp "$PROJECT_ROOT/deploy/hf-docker/Dockerfile" "$target_dir/"
    cp "$PROJECT_ROOT/deploy/hf-docker/nginx.conf" "$target_dir/"
    cp "$PROJECT_ROOT/deploy/hf-docker/start.sh" "$target_dir/"
    cp "$PROJECT_ROOT/deploy/hf-docker/requirements.txt" "$target_dir/"
    cp "$PROJECT_ROOT/deploy/hf-docker/main.py" "$target_dir/"
    cp "$PROJECT_ROOT/deploy/hf-docker/README.md" "$target_dir/"
    chmod +x "$target_dir/start.sh"

    # 复制核心模块
    copy_core_modules "$target_dir"

    # 复制前端构建产物
    info "复制前端构建产物..."
    mkdir -p "$target_dir/frontend/dist"
    cp -r "$PROJECT_ROOT/src/frontend/dist/"* "$target_dir/frontend/dist/"

    # 复制数据文件
    copy_data_files "$target_dir"

    # 验证关键文件
    info "验证文件..."
    local missing=0
    for f in Dockerfile nginx.conf start.sh requirements.txt main.py README.md \
             frontend/dist/index.html backend/core/__init__.py backend/core/seq_utils.py backend/core/codon_optimizer.py; do
        if [ ! -f "$target_dir/$f" ]; then
            error "缺少关键文件: $f"
            missing=1
        fi
    done
    [ "$missing" -eq 1 ] && error "关键文件缺失，请检查"

    # 验证 start.sh 执行权限
    if [ ! -x "$target_dir/start.sh" ]; then
        chmod +x "$target_dir/start.sh"
    fi

    ok "文件验证通过"

    # 初始化 Git 仓库
    if [ ! -d "$target_dir/.git" ]; then
        info "初始化 Git 仓库..."
        cd "$target_dir"
        git init
        git lfs install 2>/dev/null || warn "Git LFS 未安装"
        git remote add origin "https://huggingface.co/spaces/$username/$space_name" 2>/dev/null || true
        cd - >/dev/null
    fi

    # 显示目录结构
    info "目标目录结构:"
    cd "$target_dir"
    find . -not -path './.git/*' -not -path './.git' | head -50 | sort
    cd - >/dev/null

    echo ""
    ok "Docker 部署文件准备完成!"
    echo ""
    echo "  下一步操作:"
    echo "  1. cd $target_dir"
    echo "  2. git add ."
    echo "  3. git commit -m 'Deploy Plasmid Designer (Docker) to HF Spaces'"
    echo "  4. git push -u origin main"
    echo ""
    echo "  Space URL: https://huggingface.co/spaces/$username/$space_name"
    echo "  API Docs:  https://$username-$space_name.hf.space/docs"
}

# ==================== 清理 ====================

clean() {
    local target_dir="$1"
    if [ -z "$target_dir" ]; then
        error "请指定要清理的目录"
    fi
    if [ ! -d "$target_dir" ]; then
        error "目录不存在: $target_dir"
    fi
    warn "将删除: $target_dir"
    read -p "确认? (y/N) " confirm
    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        rm -rf "$target_dir"
        ok "已清理: $target_dir"
    else
        info "已取消"
    fi
}

# ==================== 用法 ====================

usage() {
    cat <<EOF
Plasmid Designer v2 — HuggingFace Spaces 部署工具

用法:
  $0 gradio  <hf-username> [space-name] [target-dir]  — Gradio 模式部署
  $0 docker  <hf-username> [space-name] [target-dir]  — Docker 模式部署
  $0 clean   <target-dir>                             — 清理部署目录

示例:
  # Gradio 模式 (简单, ~1 分钟构建)
  $0 gradio myusername

  # Docker 模式 (完整 Vue 前端, ~3-5 分钟构建)
  $0 docker myusername

  # 自定义 Space 名称和目标目录
  $0 gradio myusername my-plasmid-tool ./deploy-target

  # 清理
  $0 clean hf-space-gradio

前置条件:
  - Git + Git LFS
  - Python 3.11+
  - Node.js 18+ (仅 Docker 模式)
  - HuggingFace 账号 + Access Token (write 权限)

首次使用请先认证:
  huggingface-cli login
  或: git config credential.helper store
EOF
}

# ==================== 主入口 ====================

case "${1:-}" in
    gradio)
        [ -z "${2:-}" ] && { error "请提供 HF 用户名: $0 gradio <username>"; }
        deploy_gradio "$2" "${3:-}" "${4:-}"
        ;;
    docker)
        [ -z "${2:-}" ] && { error "请提供 HF 用户名: $0 docker <username>"; }
        deploy_docker "$2" "${3:-}" "${4:-}"
        ;;
    clean)
        [ -z "${2:-}" ] && { error "请提供目录: $0 clean <dir>"; }
        clean "$2"
        ;;
    *)
        usage
        exit 1
        ;;
esac
