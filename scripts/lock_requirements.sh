#!/usr/bin/env bash
# 从人工维护的 *.in 源清单重新生成带哈希的 Python 锁文件：
#   src/backend/requirements.in          -> src/backend/requirements.txt
#   deploy/docker/requirements.backend.in -> deploy/docker/requirements.backend.txt
#
# 锁文件含全部传递依赖与各平台 wheel/sdist 的 sha256，按 --require-hashes 安装。
# 用 uv 的 universal 解析：一份锁同时适用于 Python 3.11（Docker、裸机）与
# 3.12（CI），平台差异以环境标记写在行尾。--fork-strategy fewest 让 Linux 上
# 3.11 与 3.12 解析到同一组版本，CI 在 3.12 上跑的测试与 pip-audit 覆盖的
# 正是镜像和裸机实际安装的版本。
#
# 用法（仓库根目录）：bash scripts/lock_requirements.sh
#   需要 uv；没有时脚本在临时 venv 里装一份，用完即删，不影响项目环境。
#   只想升级某个传递依赖：UV_ARGS="--upgrade-package starlette" bash scripts/lock_requirements.sh
#   默认保留锁文件里已有的版本（只解析 .in 的改动），全量升级用 UV_ARGS=--upgrade。
set -euo pipefail

cd "$(dirname "$0")/.."

PY_MIN=3.11
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
TMP_VENV=""
cleanup() { if [ -n "$TMP_VENV" ]; then rm -rf "$TMP_VENV"; fi; }
trap cleanup EXIT

if [ -z "$UV_BIN" ]; then
    TMP_VENV="$(mktemp -d)"
    python3 -m venv "$TMP_VENV"
    "$TMP_VENV/bin/pip" install --quiet uv
    UV_BIN="$TMP_VENV/bin/uv"
fi

lock() {
    local src="$1" out="$2"
    # shellcheck disable=SC2086
    "$UV_BIN" pip compile "$src" -o "$out" \
        --universal --python-version "$PY_MIN" --fork-strategy fewest --generate-hashes \
        --custom-compile-command "bash scripts/lock_requirements.sh" \
        ${UV_ARGS:-}
}

lock src/backend/requirements.in src/backend/requirements.txt
lock deploy/docker/requirements.backend.in deploy/docker/requirements.backend.txt

echo "锁文件已更新；请跑全量测试后与 .in 一起提交。"
