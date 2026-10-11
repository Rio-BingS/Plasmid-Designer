# Redis 缓存使用指南

> **当前状态（已接线）**：以下业务流程已接入缓存读写——
> - 设计结果（完成态）：后台任务完成后写入；`GET /api/design/{id}` 读缓存，未命中读存储并回填（TTL 24h）
> - 密码子优化：`app/design_service.py` 按 (算法版本 `algo`=`CODON_OPT_ALGO`, 密码子表摘要,
>   序列, 物种, GC 区间, 排除酶) 键缓存（TTL 24h）。
>   - 密码子表摘要：该物种实际加载的 `CODON_TABLES_DIR` 下 YAML 文件内容的 sha256 前 16 位
>     （`core.codon_optimizer.codon_table_fingerprint`），进程内按 (路径, mtime, 大小) 缓存，
>     表文件不变时不重复读盘。改表（含换 `CODON_TABLES_DIR`）后摘要随之变化，旧结果
>     自动不再命中，**无需**递增 `CODON_OPT_ALGO`；旧条目在 Redis 里按 TTL 自然过期。
>     多 worker / 多实例各自读本机文件，各实例的表须一致，否则同一请求会算出不同结果。
>   - 改优化器代码而改变输出口径时，仍必须递增 `app/cache.py` 中的 `CODON_OPT_ALGO`，
>     否则命中旧结果
> - 载体列表与详情：`GET /api/vectors*` 读缓存；删除/更新/上传/导入写操作统一失效（TTL 7 天）
> - 密码子表列表、限制性酶表：静态数据（TTL 7 天）；酶表键形如
>   `analysis_enzymes:<ANALYSIS_ENZYMES_VERSION>:<hash>`，酶表口径变化时递增该版本
>   （`@cached(..., version=...)`）
>
> **明确不缓存**：批量任务进度（状态频繁变化，缓存会读到过期进度）、
> analysis 的 POST 端点（输入序列键空间不可控，内存后端无淘汰机制会持续增长）。
> 本地开发默认使用内存后端（`REDIS_ENABLED` 默认 false），生产建议开启 Redis；
> `GET /api/cache/stats` 可查看实际缓存键数量。

## 环境配置

### 1. 安装 Redis

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install redis-server

# 启动 Redis
sudo systemctl start redis
sudo systemctl enable redis
```

### 2. 安装 Python 依赖

`redis` 客户端已在 `src/backend/requirements.txt` 中精确 pin，按项目依赖安装即可，无需单独安装。

### 3. 配置环境变量

```bash
# 启用 Redis 缓存
export REDIS_ENABLED=true

# Redis 连接 URL (可选，默认 redis://localhost:6379/0)
export REDIS_URL=redis://localhost:6379/0

# 生产环境必须设置密码（redis-cli CONFIG SET requirepass "<密码>" 或 redis.conf
# 中 requirepass 行），REDIS_URL 同步改为 redis://:<密码>@localhost:6379/0
```

## 使用方式

### 自动降级

系统支持自动降级：
- Redis 可用时 → 使用 Redis 缓存
- Redis 不可用时 → 自动使用内存缓存

### API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/cache/stats` | GET | 获取缓存统计（仅管理员） |
| `/api/cache/clear` | POST | 清除缓存（仅管理员） |
| `/api/cache/invalidate/design/{id}` | POST | 使设计缓存失效（仅管理员） |
| `/api/cache/invalidate/vector/{id}` | POST | 使载体缓存失效（仅管理员） |
| `/api/cache/health` | GET | 缓存健康检查 |

内存后端写入时顺带清理过期项，并有 `MAX_ENTRIES=5000` 条目上限；Redis 初始连接失败时
回退内存，并每 60 秒（`CacheManager.REDIS_RETRY_SECONDS`）重试升级回 Redis。

### 代码示例

```python
from app.cache import cache, cached

# 手动缓存
cache.cache_design_result("design_123", result_data)
result = cache.get_design_result("design_123")

# 使用装饰器自动缓存
@cached("expensive_function", ttl=3600)
def expensive_computation(param1, param2):
    # 耗时计算...
    return result

# 清除缓存
cache.clear_all()
```

## 缓存 TTL

| 类型 | TTL | 说明 |
|------|-----|------|
| 设计结果 | 24小时 | 完成的设计任务 |
| 密码子优化 | 24小时 | 优化结果 |
| 载体信息 | 7天 | 载体列表与详情 |
| 密码子表 / 酶表 | 7天 | 静态数据 |

## Docker Compose 配置

`deploy/docker/docker-compose.yml` 已包含 Redis 7 服务：端口只绑 `127.0.0.1`，启用
`requirepass`（密码取 `.env` 的 `REDIS_PASSWORD`，必填），backend 的 `REDIS_URL` 由 compose
用同一变量拼接（`redis://:<密码>@redis:6379/0`），无需手工配置。裸机部署默认
`REDIS_ENABLED=false`，使用内存缓存。
