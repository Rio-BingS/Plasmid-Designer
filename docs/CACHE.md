# Redis 缓存使用指南

> **当前状态（已接线）**：以下业务流程已接入缓存读写——
> - 设计结果（完成态）：后台任务完成后写入；`GET /api/design/{id}` 读缓存，未命中读存储并回填（TTL 24h）
> - 密码子优化：`process_sequence` 按 (序列, 物种, GC 区间) 键缓存（TTL 24h）
> - 载体列表与详情：`GET /api/vectors*` 读缓存；删除/更新/上传/导入写操作统一失效（TTL 7 天）
> - 密码子表列表、限制性酶表：静态数据（TTL 7 天）
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

```bash
pip install redis>=5.0.0
```

### 3. 配置环境变量

```bash
# 启用 Redis 缓存
export REDIS_ENABLED=true

# Redis 连接 URL (可选，默认 redis://localhost:6379/0)
export REDIS_URL=redis://localhost:6379/0
```

## 使用方式

### 自动降级

系统支持自动降级：
- Redis 可用时 → 使用 Redis 缓存
- Redis 不可用时 → 自动使用内存缓存

### API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/cache/stats` | GET | 获取缓存统计 |
| `/api/cache/clear` | POST | 清除缓存 |
| `/api/cache/invalidate/design/{id}` | POST | 使设计缓存失效 |
| `/api/cache/invalidate/vector/{id}` | POST | 使载体缓存失效 |
| `/api/cache/health` | GET | 缓存健康检查 |

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
| 载体信息 | 7天 | 载体库数据 |
| 密码子表 | 7天 | 密码子频率表 |

## Docker Compose 配置

```yaml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes
  
  backend:
    environment:
      - REDIS_ENABLED=true
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - redis

volumes:
  redis_data:
```
