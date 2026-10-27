# CI 连续红灯三层根因 + backup.sh 两个真实缺陷（v2.29.3–v2.29.5，2026-10-26）

> 来源：HANDOVER §7.4（2026-10-27 拆出）。


- **现象**：CI 自 v2.18.0（2026-10-22）起连续 23 次失败，本地全量测试一直是绿的，无人察觉。
- **根因三层（逐层剥开）**：
  1. **门禁失败，测试从未跑到**：①文件尺寸预算被 v2.18→v2.29 的功能迭代打穿
     （6 个文件越线，其中 `construct_assembly.py` 与 `BatchDesignView.vue` 硬超 900 行默认
     预算）；②`npm audit --audit-level=high` 被 happy-dom 17.x 的 CVE-2026-33943（high）
     卡住 → 尺寸预算 `--refresh` 记账 + happy-dom 升 21.0.4。
  2. **`tests/test_deploy_backup.py` 从未真正运行过**：Windows 被 `skipif(platform=="win")`
     跳过、CI 被尺寸预算挡在测试步骤之前。修好门禁后首个 Linux 运行即暴露测试自身两处 bug：
     `run_backup` 调 bash 时漏传脚本路径（`--keep` 被 bash 当成自己的选项）、fake docker 按
     参数固定位置匹配与真实调用 `docker compose -f <path> ps` 对不上。
  3. **CI runner 环境差异**：Ubuntu runner 自带真实 sqlite3，「无 sqlite3 走拷贝回退」的
     用例删掉假工具后 `command -v` 仍找到真的 → 真拒绝伪造 DB 文件。修法：把子进程 PATH
     收紧到只剩测试沙箱（并补齐脚本依赖的 dirname 等符号链接）。
- **连带修复的 backup.sh 两个真实生产缺陷**（Linux 实测暴露）：`set -u` 下 PostgreSQL 模式
  走到 uploads 打包段 `DATA_DIR` 未定义（只在 SQLite 分支赋值）→ 备份完成后退出 1；保留
  策略在某前缀无备份时 `ls` 退出码 2 被 `pipefail` 误判为失败。cron 备份自此恢复正常退出。
- **验证**：VPS（Ubuntu/py3.10）实测 4 passed；CI run #106 起回绿。
- **教训**：①**被 skip 的测试等于不存在的测试**——写入平台相关测试时就该在目标平台跑一次；
  ②**本地绿 ≠ CI 绿**——CI 独有的门禁（尺寸预算/audit/build）要在本地预演，改完代码先跑
  `python scripts/check_file_size.py`；③连续红灯无人看的根因是这几轮迭代只盯本地测试，
  没有「推完看一眼 CI」的收尾动作。

