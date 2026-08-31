"""
Redis 缓存模块 - 设计结果缓存
支持缓存设计结果、载体信息等数据，减少重复计算
"""
import json
import hashlib
import os
import time
from threading import Lock
from typing import Optional, Any, Dict, List
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Redis 连接配置
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_ENABLED = os.getenv("REDIS_ENABLED", "false").lower() == "true"

# 缓存默认 TTL (秒)
DEFAULT_CACHE_TTL = 3600  # 1 小时
DESIGN_CACHE_TTL = 86400  # 24 小时
VECTOR_CACHE_TTL = 604800  # 7 天


class CacheBackend:
    """缓存后端抽象类"""
    
    def get(self, key: str) -> Optional[Any]:
        raise NotImplementedError
    
    def set(self, key: str, value: Any, ttl: int = DEFAULT_CACHE_TTL) -> bool:
        raise NotImplementedError
    
    def delete(self, key: str) -> bool:
        raise NotImplementedError
    
    def exists(self, key: str) -> bool:
        raise NotImplementedError
    
    def clear_pattern(self, pattern: str) -> int:
        raise NotImplementedError


class RedisCache(CacheBackend):
    """Redis 缓存实现"""
    
    def __init__(self, url: str = REDIS_URL):
        try:
            import redis
            self.client = redis.from_url(url, decode_responses=True)
            self._test_connection()
            self.available = True
            logger.info(f"Redis 连接成功: {url}")
        except ImportError:
            logger.warning("redis 库未安装，使用内存缓存")
            self.available = False
        except Exception as e:
            logger.warning(f"Redis 连接失败: {e}，使用内存缓存")
            self.available = False
    
    def _test_connection(self):
        """测试连接"""
        self.client.ping()
    
    def get(self, key: str) -> Optional[Any]:
        if not self.available:
            return None
        try:
            value = self.client.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Redis get 错误: {e}")
            return None
    
    def set(self, key: str, value: Any, ttl: int = DEFAULT_CACHE_TTL) -> bool:
        if not self.available:
            return False
        try:
            self.client.setex(key, ttl, json.dumps(value, default=str))
            return True
        except Exception as e:
            logger.error(f"Redis set 错误: {e}")
            return False
    
    def delete(self, key: str) -> bool:
        if not self.available:
            return False
        try:
            self.client.delete(key)
            return True
        except Exception as e:
            logger.error(f"Redis delete 错误: {e}")
            return False
    
    def exists(self, key: str) -> bool:
        if not self.available:
            return False
        try:
            return self.client.exists(key) > 0
        except Exception as e:
            logger.error(f"Redis exists 错误: {e}")
            return False
    
    def clear_pattern(self, pattern: str) -> int:
        """清除匹配模式的所有键"""
        if not self.available:
            return 0
        try:
            keys = self.client.keys(pattern)
            if keys:
                return self.client.delete(*keys)
            return 0
        except Exception as e:
            logger.error(f"Redis clear_pattern 错误: {e}")
            return 0


class MemoryCache(CacheBackend):
    """内存缓存实现（备用方案）"""

    # 条目上限：写入时顺带清理过期项，仍超限则按到期时间淘汰最早的。
    # 此前仅惰性过期（再次访问才删），无人再查的 key 会永久驻留（KNOWN_ISSUES 2.2）
    MAX_ENTRIES = 5_000

    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = Lock()
        logger.info("使用内存缓存")

    def _evict_under_lock(self, now: float) -> None:
        """清理已过期条目并在超限时淘汰最早到期者；必须在持有 _lock 时调用。"""
        expired = [k for k, e in self._cache.items() if e["expires_at"] <= now]
        for k in expired:
            del self._cache[k]
        if len(self._cache) > self.MAX_ENTRIES:
            overflow = len(self._cache) - self.MAX_ENTRIES
            for k, _ in sorted(self._cache.items(), key=lambda kv: kv[1]["expires_at"])[:overflow]:
                del self._cache[k]

    def get(self, key: str) -> Optional[Any]:
        now = datetime.now().timestamp()
        with self._lock:
            entry = self._cache.get(key)
            if entry:
                if now < entry["expires_at"]:
                    return entry["value"]
                del self._cache[key]
        return None

    def set(self, key: str, value: Any, ttl: int = DEFAULT_CACHE_TTL) -> bool:
        now = datetime.now().timestamp()
        with self._lock:
            self._cache[key] = {
                "value": value,
                "expires_at": now + ttl
            }
            if len(self._cache) > self.MAX_ENTRIES:
                self._evict_under_lock(now)
        return True

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
        return False

    def exists(self, key: str) -> bool:
        now = datetime.now().timestamp()
        with self._lock:
            entry = self._cache.get(key)
            if entry:
                if now < entry["expires_at"]:
                    return True
                del self._cache[key]
        return False

    def clear_pattern(self, pattern: str) -> int:
        """清除匹配模式的所有键"""
        import fnmatch
        with self._lock:
            keys_to_delete = [
                k for k in self._cache.keys()
                if fnmatch.fnmatch(k, pattern)
            ]
            for key in keys_to_delete:
                del self._cache[key]
        return len(keys_to_delete)


class CacheManager:
    """缓存管理器"""

    # Redis 初始连接失败回退内存后，间隔多久重试升级回 Redis（KNOWN_ISSUES 3.4）
    REDIS_RETRY_SECONDS = 60

    def __init__(self, use_redis: bool = REDIS_ENABLED):
        self._use_redis = use_redis
        self._backend: Optional[CacheBackend] = None
        self._redis_retry_at: Optional[float] = None

    @property
    def backend(self) -> CacheBackend:
        """延迟初始化缓存后端。

        此前模块导入时即连接 Redis：容器启动顺序中 Redis 晚于后端就绪时，
        会永久回退内存缓存直到重启。现改为首次使用时才连接；若届时 Redis
        仍不可用，先回退内存并按 REDIS_RETRY_SECONDS 周期重试升级。
        """
        now = time.monotonic()
        if self._backend is None:
            if self._use_redis:
                redis_backend = RedisCache()
                if redis_backend.available:
                    self._backend = redis_backend
                else:
                    self._backend = MemoryCache()
                    self._redis_retry_at = now + self.REDIS_RETRY_SECONDS
            else:
                self._backend = MemoryCache()
        elif self._redis_retry_at is not None and now >= self._redis_retry_at:
            redis_backend = RedisCache()
            if redis_backend.available:
                self._backend = redis_backend
                self._redis_retry_at = None
                logger.info("Redis 恢复可用，缓存后端已从内存切换回 Redis")
            else:
                self._redis_retry_at = now + self.REDIS_RETRY_SECONDS
        return self._backend
    
    def _generate_key(self, prefix: str, *args, **kwargs) -> str:
        """生成缓存键"""
        content = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True)
        hash_value = hashlib.md5(content.encode()).hexdigest()
        return f"{prefix}:{hash_value}"
    
    # ==================== 设计结果缓存 ====================
    
    def cache_design_result(self, design_id: str, result: Dict) -> bool:
        """缓存设计结果"""
        key = f"design:{design_id}"
        return self.backend.set(key, result, DESIGN_CACHE_TTL)
    
    def get_design_result(self, design_id: str) -> Optional[Dict]:
        """获取缓存的设计结果"""
        key = f"design:{design_id}"
        return self.backend.get(key)
    
    def invalidate_design(self, design_id: str) -> bool:
        """使设计缓存失效"""
        key = f"design:{design_id}"
        return self.backend.delete(key)
    
    # ==================== 密码子优化缓存 ====================
    
    def cache_codon_optimization(
        self,
        sequence: str,
        species: str,
        gc_min: float,
        gc_max: float,
        result: Dict,
        exclude_enzymes: Optional[List[str]] = None
    ) -> bool:
        """缓存密码子优化结果（exclude_enzymes 参与缓存键）"""
        key = self._generate_key(
            "codon_opt",
            algo="v2",  # 算法升级时递增，避免命中旧版本结果
            sequence=sequence,
            species=species,
            gc_min=gc_min,
            gc_max=gc_max,
            exclude_enzymes=sorted(exclude_enzymes or []),
        )
        return self.backend.set(key, result, DESIGN_CACHE_TTL)

    def get_codon_optimization(
        self,
        sequence: str,
        species: str,
        gc_min: float,
        gc_max: float,
        exclude_enzymes: Optional[List[str]] = None
    ) -> Optional[Dict]:
        """获取缓存的密码子优化结果"""
        key = self._generate_key(
            "codon_opt",
            algo="v2",
            sequence=sequence,
            species=species,
            gc_min=gc_min,
            gc_max=gc_max,
            exclude_enzymes=sorted(exclude_enzymes or []),
        )
        return self.backend.get(key)
    
    # ==================== 载体信息缓存 ====================
    
    def cache_vector(self, vector_id: str, vector_data: Dict) -> bool:
        """缓存载体信息"""
        key = f"vector:{vector_id}"
        return self.backend.set(key, vector_data, VECTOR_CACHE_TTL)
    
    def get_vector(self, vector_id: str) -> Optional[Dict]:
        """获取缓存的载体信息"""
        key = f"vector:{vector_id}"
        return self.backend.get(key)
    
    def cache_vector_list(self, filters: Dict, vectors: List[Dict]) -> bool:
        """缓存载体列表"""
        key = self._generate_key("vector_list", **filters)
        return self.backend.set(key, vectors, VECTOR_CACHE_TTL)
    
    def get_vector_list(self, filters: Dict) -> Optional[List[Dict]]:
        """获取缓存的载体列表"""
        key = self._generate_key("vector_list", **filters)
        return self.backend.get(key)
    
    def invalidate_vector(self, vector_id: str) -> bool:
        """使载体缓存失效"""
        key = f"vector:{vector_id}"
        self.backend.delete(key)
        # 同时清除列表缓存
        self.backend.clear_pattern("vector_list:*")
        return True
    
    # ==================== 密码子表缓存 ====================
    
    def cache_codon_tables(self, tables: List[Dict]) -> bool:
        """缓存密码子表列表"""
        return self.backend.set("codon_tables:list", tables, VECTOR_CACHE_TTL)
    
    def get_codon_tables(self) -> Optional[List[Dict]]:
        """获取缓存的密码子表列表"""
        return self.backend.get("codon_tables:list")
    
    # ==================== 批量任务缓存 ====================
    
    def cache_batch_progress(self, batch_id: str, progress: Dict) -> bool:
        """缓存批量任务进度"""
        key = f"batch:{batch_id}"
        return self.backend.set(key, progress, DESIGN_CACHE_TTL)
    
    def get_batch_progress(self, batch_id: str) -> Optional[Dict]:
        """获取缓存的批量任务进度"""
        key = f"batch:{batch_id}"
        return self.backend.get(key)
    
    def invalidate_batch(self, batch_id: str) -> bool:
        """使批量任务缓存失效"""
        key = f"batch:{batch_id}"
        return self.backend.delete(key)
    
    # ==================== 工具方法 ====================
    
    def clear_all(self) -> int:
        """清除所有缓存"""
        return self.backend.clear_pattern("*")
    
    def get_stats(self) -> Dict:
        """获取缓存统计信息"""
        if isinstance(self.backend, MemoryCache):
            return {
                "type": "memory",
                "keys": len(self.backend._cache),
                "available": True
            }
        elif isinstance(self.backend, RedisCache):
            if self.backend.available:
                try:
                    info = self.backend.client.info("memory")
                    return {
                        "type": "redis",
                        "used_memory": info.get("used_memory_human", "unknown"),
                        "keys": self.backend.client.dbsize(),
                        "available": True
                    }
                except:
                    pass
        return {"type": "unknown", "available": False}


# 全局缓存管理器实例
cache = CacheManager()


# 装饰器：自动缓存函数结果
def cached(prefix: str, ttl: int = DEFAULT_CACHE_TTL):
    """
    缓存装饰器（同步与 async 函数均支持）

    用法:
        @cached("my_function", ttl=3600)
        def my_function(arg1, arg2):
            return expensive_operation(arg1, arg2)
    """
    import functools
    import inspect

    def decorator(func):
        key_func = lambda *args, **kwargs: cache._generate_key(prefix, *args, **kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            key = key_func(*args, **kwargs)
            cached_result = cache.backend.get(key)
            if cached_result is not None:
                logger.debug(f"缓存命中: {key}")
                return cached_result
            result = await func(*args, **kwargs)
            cache.backend.set(key, result, ttl)
            logger.debug(f"缓存存储: {key}")
            return result

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = key_func(*args, **kwargs)
            cached_result = cache.backend.get(key)
            if cached_result is not None:
                logger.debug(f"缓存命中: {key}")
                return cached_result
            result = func(*args, **kwargs)
            cache.backend.set(key, result, ttl)
            logger.debug(f"缓存存储: {key}")
            return result

        return async_wrapper if inspect.iscoroutinefunction(func) else wrapper
    return decorator
