# 三项 Windows 红灯（2026-10-18 清账）

> 来源：HANDOVER §7.1（2026-10-27 拆出）。现象/根因/修法留档如下。


| 测试 | 真实病因 | 修法 |
|---|---|---|
| `test_batch_report_disk_store::test_report_written_to_private_dir_and_not_kept_in_memory` | POSIX 权限位断言：Windows 不表达 rwx，目录恒报 `0o777`（`511 ≠ 448`），`chmod` 只切只读位 | 新增 `_assert_private()`，只在 `os.name == "posix"` 时逐位校验（Linux 覆盖不减） |
| `test_sequencing_upload_sessions::test_private_permissions_and_sequence_disk_names` | 同上 | 同上 |
| `test_session_finalize_lazy_read::test_later_group_files_not_read_while_first_group_aligns` | **不是线程时序**（初判错了）：用例自身的 spy 用 `p.rsplit("/", 1)[-1]` 取文件名，而 `DiskBlob.path` 在 Windows 用反斜杠 → 取到整条路径 → `names[path]` 抛 KeyError，异常发生在 `analyze` 里、被批次管线记成「分析失败」→ 断言处看到的是「analyze 从未调用」 | 改用 `os.path.basename(p)` |

