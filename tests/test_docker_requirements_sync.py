"""部署依赖清单一致性守门

Python 依赖分两层：人工维护的源清单（*.in，只写直接依赖并精确 pin）与由
scripts/lock_requirements.sh 生成的锁文件（*.txt，含全部传递依赖与 sha256
哈希，安装一律 --require-hashes）。

- src/backend/requirements.in  -> src/backend/requirements.txt（运行时：裸机/开发/CI）
- src/backend/requirements-dev.in -> requirements-dev.txt（仅测试用的包，开发/CI）
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
DEV_IN = REPO / "src" / "backend" / "requirements-dev.in"
DEV_LOCK = REPO / "src" / "backend" / "requirements-dev.txt"
AUDIT_IN = REPO / ".github" / "ci" / "requirements-audit.in"
AUDIT_LOCK = REPO / ".github" / "ci" / "requirements-audit.txt"

# 只在测试中使用的包：不得进入运行时清单与锁（审查 09 L-4）
TEST_ONLY = ("pytest", "httpx2", "httpx", "pluggy", "iniconfig")

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
        if not line or line.startswith(("#", "-r", "-c")):
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
    for src, lock in ((SRC_IN, SRC_LOCK), (DOCKER_IN, DOCKER_LOCK), (DEV_IN, DEV_LOCK),
                      (AUDIT_IN, AUDIT_LOCK)):
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
    assert re.search(
        r"pip install --require-hashes \\\n\s*-r src/backend/requirements\.txt \\\n"
        r"\s*-r src/backend/requirements-dev\.txt", ci
    ), "CI 须同时按哈希安装运行时锁与开发锁"


def test_test_only_packages_not_in_production():
    """测试依赖只在开发锁里：运行时与 Docker 源清单、锁文件都不含（审查 09 L-4）"""
    for path in (SRC_IN, DOCKER_IN):
        leaked = set(_pins(path)) & set(TEST_ONLY)
        assert not leaked, f"{path.relative_to(REPO)} 含测试依赖 {sorted(leaked)}"
    for path in (SRC_LOCK, DOCKER_LOCK):
        leaked = {n for n, _, _ in _lock_entries(path)} & set(TEST_ONLY)
        assert not leaked, f"{path.relative_to(REPO)} 含测试依赖 {sorted(leaked)}"
    dev = _pins(DEV_IN)
    assert "pytest" in dev and "httpx2" in dev


def test_dev_lock_consistent_with_runtime_lock():
    """开发锁以运行时锁为约束：共有包版本一致，两份锁可一起 --require-hashes 安装"""
    dev_text = DEV_IN.read_text(encoding="utf-8")
    assert re.search(r"^-r requirements\.in$", dev_text, re.M)
    assert re.search(r"^-c requirements\.txt$", dev_text, re.M)
    runtime = {n: v for n, v, _ in _lock_entries(SRC_LOCK)}
    dev = {}
    for n, v, _ in _lock_entries(DEV_LOCK):
        dev.setdefault(n, set()).add(v)
    drift = {n: (v, sorted(dev.get(n, []))) for n, v in runtime.items() if v not in dev.get(n, set())}
    assert not drift, f"开发锁与运行时锁版本不一致（runtime, dev）：{drift}"


def test_ci_pip_audit_pinned_with_hashes():
    """CI 的 pip-audit 按带哈希的锁安装，不再 `pip install pip-audit` 取最新版（审查 09 L-1）"""
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert not re.search(r"pip install\s+pip-audit\b", ci), "pip-audit 未锁版本"
    assert re.search(
        r"pip\"? install --require-hashes \\\n\s*-r \.github/ci/requirements-audit\.txt", ci
    )
    pins = _pins(AUDIT_IN)
    assert pins.get("pip-audit"), "requirements-audit.in 须精确 pin pip-audit"


# 文档与脚本里所有「pip install -r 锁文件」命令都要带 --require-hashes
# （审查 03/09：裸机 README 的更新步骤曾漏掉，按文档升级即无哈希校验）
_SWEEP_SUFFIXES = {".md", ".sh", ".ps1", ".yml", ".yaml", ".txt", ".in", ".toml", ".cfg"}
_SWEEP_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__",
                    ".pytest_cache"}


def _sweep_files():
    for path in REPO.rglob("*"):
        rel = path.relative_to(REPO)
        if any(part in _SWEEP_SKIP_DIRS for part in rel.parts) or not path.is_file():
            continue
        if path.suffix in _SWEEP_SUFFIXES or path.name.startswith(("Dockerfile", "Makefile")):
            yield path


def _pip_install_commands(text: str):
    """按续行符拼接后取出每条 pip install 命令（到行尾/引号/反引号为止）"""
    joined = re.sub(r"\\\r?\n\s*", " ", text)
    for m in re.finditer(r"pip\"?\s+install\b[^\n`]*", joined):
        yield re.sub(r"\s#.*$", "", m.group(0))  # 行尾注释不算命令


def test_documented_lock_installs_require_hashes():
    offenders, dev_without_runtime = [], []
    for path in _sweep_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for cmd in _pip_install_commands(text):
            if not re.search(r"(^|\s)-r\s", cmd):
                continue  # pip 自身升级、可选依赖示例等不装锁文件
            if "--require-hashes" not in cmd:
                offenders.append(f"{path.relative_to(REPO)}: {cmd.strip()}")
            if "requirements-dev.txt" in cmd and not re.search(r"-r\s+\S*requirements\.txt", cmd):
                dev_without_runtime.append(f"{path.relative_to(REPO)}: {cmd.strip()}")
    assert not offenders, "无哈希的锁文件安装命令:\n" + "\n".join(offenders)
    # 开发锁以运行时锁为约束，只装开发锁会缺运行时依赖
    assert not dev_without_runtime, "\n".join(dev_without_runtime)


def test_dev_install_docs_include_dev_lock():
    """开发安装说明（README 本地开发、后端 README、Makefile）同时装开发锁"""
    for rel in ("README.md", "src/backend/app/README.md", "deploy/scripts/Makefile"):
        cmds = list(_pip_install_commands((REPO / rel).read_text(encoding="utf-8")))
        assert any("--require-hashes" in c and "requirements-dev.txt" in c
                   and re.search(r"-r\s+\S*requirements\.txt", c) for c in cmds), rel
