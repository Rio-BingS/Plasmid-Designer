"""部署依赖清单一致性守门

Python 依赖分两层：人工维护的源清单（*.in，只写直接依赖并精确 pin）与由
scripts/lock_requirements.sh 生成的锁文件（*.txt，含全部传递依赖与 sha256
哈希，安装一律 --require-hashes）。

- src/backend/requirements.in  -> src/backend/requirements.txt（开发/CI/裸机）
- deploy/docker/requirements.backend.in -> requirements.backend.txt（Docker 镜像）

两份清单曾发生漂移（openpyxl 只加了 src 侧，容器内批量测序解析 Excel 直接 500）。
本测试保证：Docker 源清单是 src 源清单的子集且版本一致；锁文件与源清单同步、
每一项都带哈希；各安装入口确实按锁文件校验哈希安装。
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCKER_IN = REPO / "deploy" / "docker" / "requirements.backend.in"
DOCKER_LOCK = REPO / "deploy" / "docker" / "requirements.backend.txt"
SRC_IN = REPO / "src" / "backend" / "requirements.in"
SRC_LOCK = REPO / "src" / "backend" / "requirements.txt"

# 包名可带 extras（uvicorn[standard]）与环境标记；取行首包名与 == 版本
PKG_RE = re.compile(
    r"^\s*([A-Za-z0-9_.-]+)(\[[^\]]*\])?\s*(?:==\s*([A-Za-z0-9_.+!-]+))?\s*([<>=!~;\\]|$)"
)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins(path: Path) -> dict:
    """源清单：包名 -> 版本（跳过注释与空行）"""
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = PKG_RE.match(line)
        if m:
            pins[_norm(m.group(1))] = m.group(3)
    return pins


def _lock_entries(path: Path) -> list:
    """锁文件：[(包名, 版本, 哈希数)]，按续行符把一项的多行拼起来"""
    entries, buf = [], ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip() if not raw.lstrip().startswith("--hash") else raw.rstrip()
        if not line.strip():
            continue
        buf += " " + line.rstrip("\\").strip()
        if not line.endswith("\\"):
            entries.append(buf.strip())
            buf = ""
    if buf.strip():
        entries.append(buf.strip())
    out = []
    for e in entries:
        m = re.match(r"^([A-Za-z0-9_.-]+)(\[[^\]]*\])?==([^\s;]+)", e)
        assert m, f"{path.name} 中有未精确 pin 的条目：{e[:80]}"
        out.append((_norm(m.group(1)), m.group(3), len(re.findall(r"--hash=sha256:[0-9a-f]{64}", e))))
    return out


def test_docker_requirements_are_subset_of_src():
    docker_pins, src_pins = _pins(DOCKER_IN), _pins(SRC_IN)
    missing = set(docker_pins) - set(src_pins)
    assert not missing, (
        f"deploy/docker/requirements.backend.in 中存在 src/backend/requirements.in "
        f"没有的包：{sorted(missing)}——两份清单必须同步维护（历史上曾因此容器缺 openpyxl）"
    )
    drift = {p: (v, src_pins[p]) for p, v in docker_pins.items() if src_pins[p] != v}
    assert not drift, f"两份源清单版本不一致（docker, src）：{drift}"


def test_key_runtime_packages_present_in_docker_list():
    """核心运行时包抽查：防止单个包从两份清单同时丢失（biopython 已并入主清单）"""
    docker_pins = _pins(DOCKER_IN)
    for pkg in ("fastapi", "uvicorn", "sqlalchemy", "snapgene-reader", "openpyxl", "biopython"):
        assert pkg in docker_pins, f"Docker 依赖清单缺少 {pkg}"


def test_lock_files_match_sources_and_carry_hashes():
    """锁文件必须由当前源清单生成：每个直接依赖都在锁里且版本相同，每项都带哈希"""
    for src, lock in ((SRC_IN, SRC_LOCK), (DOCKER_IN, DOCKER_LOCK)):
        entries = _lock_entries(lock)
        locked = {}
        for name, ver, n_hash in entries:
            assert n_hash > 0, f"{lock.name} 中 {name}=={ver} 没有哈希"
            locked.setdefault(name, set()).add(ver)
        for name, ver in _pins(src).items():
            assert ver, f"{src.name} 中 {name} 未精确 pin"
            assert locked.get(name) == {ver}, (
                f"{lock.relative_to(REPO)} 与 {src.name} 不同步：{name} 期望 {ver}，"
                f"锁中为 {sorted(locked.get(name, []))}——请运行 scripts/lock_requirements.sh"
            )


def test_install_entrypoints_require_hashes():
    """Dockerfile、裸机 install.sh 与 CI 都按锁文件 --require-hashes 安装"""
    dockerfile = (REPO / "deploy" / "docker" / "Dockerfile.backend").read_text(encoding="utf-8")
    assert "COPY deploy/docker/requirements.backend.txt" in dockerfile
    # 允许续行：pip install ... \ 换行后再带 --require-hashes
    assert re.search(r"pip install(?:[^\n]|\\\n)*--require-hashes", dockerfile)
    assert not re.search(r'pip install[^\n]*"?biopython==', dockerfile), (
        "biopython 已并入锁文件，Dockerfile 不应再单独无哈希安装"
    )
    install_sh = (REPO / "deploy" / "bare" / "install.sh").read_text(encoding="utf-8")
    assert re.search(r"--require-hashes[^\n]*\\\n\s*-r \"\$INSTALL_DIR/src/backend/requirements\.txt\"",
                     install_sh)
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pip install --require-hashes -r src/backend/requirements.txt" in ci
