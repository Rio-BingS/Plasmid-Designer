"""部署依赖清单一致性守门

Docker 镜像的 Python 依赖"唯一来源"是 deploy/docker/requirements.backend.txt
（见 Dockerfile.backend），而开发/测试环境装的是 src/backend/requirements.txt。
两份清单曾发生漂移（openpyxl 只加了 src 侧，容器内批量测序解析 Excel 直接 500）。
本测试保证：Docker 清单里的每个包都存在于 src 清单，杜绝镜像缺包。
（biopython 走 Dockerfile 第二层独立安装，但 src 清单同样必须声明。）
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCKER_REQ = REPO / "deploy" / "docker" / "requirements.backend.txt"
SRC_REQ = REPO / "src" / "backend" / "requirements.txt"

PKG_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*[<>=!~]")


def _packages(path: Path) -> set:
    """取清单中的包名集合（跳过注释与空行，不比较版本约束）"""
    pkgs = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = PKG_RE.match(line)
        if m:
            pkgs.add(m.group(1).lower().replace("_", "-"))
    return pkgs


def test_docker_requirements_are_subset_of_src():
    docker_pkgs = _packages(DOCKER_REQ)
    src_pkgs = _packages(SRC_REQ)
    missing = docker_pkgs - src_pkgs
    assert not missing, (
        f"deploy/docker/requirements.backend.txt 中存在 src/backend/requirements.txt "
        f"没有的包：{sorted(missing)}——镜像依赖唯一来源是 Docker 清单，"
        f"两份清单必须同步维护（历史上曾因此容器缺 openpyxl）"
    )


def test_key_runtime_packages_present_in_docker_list():
    """核心运行时包抽查：防止单个包从两份清单同时丢失"""
    docker_pkgs = _packages(DOCKER_REQ)
    for pkg in ("fastapi", "uvicorn", "sqlalchemy", "snapgene-reader", "openpyxl"):
        assert pkg in docker_pkgs, f"Docker 依赖清单缺少 {pkg}"


def test_biopython_installed_by_dockerfile():
    """biopython 刻意走 Dockerfile 第二层独立安装（不动主清单层缓存），
    这里保证该安装行不被误删"""
    dockerfile = (REPO / "deploy" / "docker" / "Dockerfile.backend").read_text(encoding="utf-8")
    assert re.search(r'pip install.*biopython', dockerfile), (
        "Dockerfile.backend 丢失 biopython 安装层（Sanger 测序分析必需）"
    )
