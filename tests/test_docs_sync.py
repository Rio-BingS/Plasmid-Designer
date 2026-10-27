"""文档同步门禁：pytest 包装 scripts/check_docs_sync.py

两者调用同一实现，改检查逻辑只改 scripts/check_docs_sync.py 一处。
本机跑 pytest 或 CI 均会触发；也可单独 `python scripts/check_docs_sync.py`。
"""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_docs_sync.py"
_spec = importlib.util.spec_from_file_location("check_docs_sync", SCRIPT)
check_docs_sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_docs_sync)


def test_docs_sync():
    # 直接跑脚本的 main，让输出与 CI 手动运行完全一致，便于对照修复
    rc = check_docs_sync.main()
    assert rc == 0, "文档同步检查未通过，输出见上方（修复后重跑）"
