# Plasmid Designer Makefile（在仓库根目录运行）
#
# 路径对应真实仓库布局：后端 src/backend、前端 src/frontend、
# 后端测试 tests/、compose 文件 deploy/docker/

BACKEND_DIR := src/backend
FRONTEND_DIR := src/frontend
COMPOSE_DIR := deploy/docker
VENV_PY := .venv/Scripts/python    # Windows；Linux/macOS 改为 .venv/bin/python

.PHONY: help install dev-backend dev-frontend dev test test-cov clean \
        docker-up docker-down docker-build docker-logs build build-frontend

help:
	@echo "Plasmid Designer - 命令帮助"
	@echo ""
	@echo "开发命令:"
	@echo "  make install      - 安装所有依赖"
	@echo "  make dev-backend  - 启动后端 (8000)"
	@echo "  make dev-frontend - 启动前端 (3000)"
	@echo "  make test         - 运行后端测试"
	@echo "  make clean        - 清理临时文件"
	@echo ""
	@echo "Docker 命令:"
	@echo "  make docker-up    - 启动 Docker 服务"
	@echo "  make docker-down  - 停止 Docker 服务"
	@echo "  make docker-build - 构建 Docker 镜像"
	@echo "  make docker-logs  - 查看日志"

# 安装依赖
install:
	cd $(BACKEND_DIR) && ../../$(VENV_PY) -m pip install -r requirements.txt
	cd $(FRONTEND_DIR) && npm install

# 开发模式
dev-backend:
	cd $(BACKEND_DIR) && ../../$(VENV_PY) -m uvicorn app.main:app --host 127.0.0.1 --port 8000

dev-frontend:
	cd $(FRONTEND_DIR) && npm run dev

# 测试（conftest.py 已注入路径，无需额外 PYTHONPATH）
test:
	cd tests && ../$(VENV_PY) -m pytest -q

test-cov:
	cd tests && ../$(VENV_PY) -m pytest -q --cov=core --cov=app --cov-report=html

# 清理
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.log" -delete

# Docker（compose 文件在 deploy/docker/）
docker-up:
	cd $(COMPOSE_DIR) && docker compose up -d

docker-down:
	cd $(COMPOSE_DIR) && docker compose down

docker-build:
	cd $(COMPOSE_DIR) && docker compose build

docker-logs:
	cd $(COMPOSE_DIR) && docker compose logs -f

# 构建前端生产包
build-frontend:
	cd $(FRONTEND_DIR) && npm run build
