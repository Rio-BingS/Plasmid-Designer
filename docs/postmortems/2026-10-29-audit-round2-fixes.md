# 2026-10-29 第二轮代码审计与终审修复批次

## 现象

对后端路由/基础设施与前端做了一轮安全 + 健壮性复审，确认 15 组问题（完整清单见
[KNOWN_ISSUES.md](../KNOWN_ISSUES.md) 「2026-10-29 第二轮代码审计与终审」一节）。
典型代表：

- 客户端可控的 `X-Request-ID` 原样进访问日志与响应头（日志注入；非 latin-1 字符 500）。
- 外部载体库导出文件名只替换 `/`，Windows 上 `..\` 可越出目标目录。
- console 邮件渠道把验证码明文写进 5×10MB 滚动日志。
- 设计/批量内存镜像 `in` + `[]` 两步读取在容量淘汰下有竞态；后台设计任务重跑时
  镜像若已淘汰该条目，重建的结果不带属主 → 落成「无属主公开」。
- `REDIS_URL` 只读 `os.environ`，`.env` 配置被静默忽略（双真相源）。
- 前端：环形图空序列取模得 NaN、Addgene 迟到响应覆盖新结果、批量页重复进入叠加
  轮询 interval、隐私模式下 localStorage 访问抛错白屏。
- 死代码：两个无引用 Pinia store、若干死导出（净 −180 行）。
- 部署：compose 前端健康检查用 curl（nginx:alpine 没有 → 容器永远 unhealthy）。

## 根因

共性不是「某处写错」而是**缺一类统一的纪律**：

1. 客户端可控字符串进日志/响应头/文件路径前没有强制白名单——4 处同型问题全部
   「正常输入跑得通」，只有恶意/极端输入显形。
2. 内存镜像 + 持久层双层读取的原子性从未被审视：`in` + `[]` 在单线程 asyncio 里
   看似安全，但容量淘汰发生在两步之间。
3. 「读失败静默返回空/None」把存储故障伪装成 404，掩盖排障线索。
4. 配置双真相源（环境变量 vs `.env`）在 2.4 只修了 DATABASE_URL 一条，同类问题
   没有横向清查。

## 修法

- 后端 12 项：消毒（X-Request-ID、LOCUS 文件名、Content-Disposition）、边界校验
  （NCBI limit）、临时文件残留、BoundedMirror `get()` 化、后台任务显式传属主、
  REDIS_URL 三级回退、目录缺失容错、读失败留痕、阻塞调用移交线程池。
- 前端 4 项 + 清理：NaN 守卫、Addgene 请求代次、`startPolling` 先 `stopPolling`、
  localStorage 读写收敛到 `utils/batchHistory.storageGet/storageSet`（批量历史模块
  内部 3 处 try-catch 同步吃狗粮）；死 store/死导出全删。
- 部署 4 项：wget 健康检查、`.gitignore` 补 WAL/SHM 与 `data/backups/`、README
  端口漂移、deploy.sh ssl 目录。
- 防回潮：`tests/test_filename_safety.py` 扩展——文件名清洗白名单正则锁定为
  `app/filename.py` 单实现，仓库内出现副本即红灯。

## 验证

- 后端全量 1823 passed / 43 skipped（约 100 s）。
- 前端 vitest 661 passed、`vue-tsc --noEmit` 0 error、`npm run build` 通过、
  `npm audit --omit=dev` 0 漏洞。
- 门禁：`check_file_size.py` 通过（BatchDesignView.vue 1318 行恰好保持预算，
  本轮修复靠收敛而非记账）、`check_docs_sync.py` 通过。

## 教训

1. **审计产出要先横向归并再修**：本批 4 处「可控字符串未消毒」、2 处 BoundedMirror
   竞态、1 处双真相源残留都是同一根因的多点显形，逐点打补丁不如把口径收敛到单一
   实现（filename.py、storageGet、`dict.get()`）并加防回潮测试。
2. **「静默降级」必须留痕**：`except: return None` 三连（sequencing_store 3 处）
   让 404 有了第二种成因，排障时无从区分；降级可以，warning 不可省。
3. **部署镜像里的默认工具不能假设**：nginx:alpine 没有 curl，健康检查配置在
   「换了基础镜像」时就是死配置——健康检查失败会伪装成「服务不可用」，比没有
   健康检查更误导。
4. **本地门禁全绿才叫绿**：本批次收尾按 [ci-red-chain 教训](2026-10-26-ci-red-chain-and-backup.md)
   把 pytest / vitest / vue-tsc / build / npm audit / 尺寸预算 / 文档同步七道
   全部本地预演后才提交。
