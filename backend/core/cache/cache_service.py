"""
缓存服务模块 - 支持Redis和内存模式
"""
import json
import hashlib
import numpy as np
from typing import Any, Optional
from datetime import datetime, timedelta

from ...config import settings


class CacheService:
    """缓存服务 - 支持Redis和内存模式"""

    def __init__(self):
        self.default_ttl = settings.CACHE_TTL
        self.embedding_ttl = settings.EMBEDDING_CACHE_TTL
        self.redis = None
        self.use_memory = True  # 默认使用内存模式
        self.memory_cache = {}  # 内存缓存

        # 尝试连接Redis
        try:
            import redis.asyncio as redis
            self.redis = redis.from_url(
                f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}",
                decode_responses=False
            )
            # 测试连接（同步方式）
            import redis
            r = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
            r.ping()
            self.use_memory = False
            print("✅ 连接到Redis缓存")
        except Exception as e:
            print(f"⚠️ Redis不可用，使用内存缓存: {e}")
            self.use_memory = True

    async def get_query_cache(self, query: str, model: str, top_k: int, skills_sig: str = "") -> Optional[Any]:
        """获取查询结果缓存

        skills_sig: 已启用技能 name 列表的有序签名。
        注入技能后不同时间点启用不同技能 → 答案可能不同，
        所以把签名加入 cache key 让缓存自动随技能变化失效。
        """
        cache_key = self._generate_query_key(query, model, top_k, skills_sig)

        if self.use_memory:
            # 内存模式
            if cache_key in self.memory_cache:
                item = self.memory_cache[cache_key]
                if item["expire"] > datetime.now():
                    return item["value"]
                else:
                    del self.memory_cache[cache_key]
            return None
        else:
            cached = await self.redis.get(cache_key)
            if cached:
                return json.loads(cached)
            return None

    async def set_query_cache(self, query: str, model: str, top_k: int, results: Any, skills_sig: str = ""):
        """设置查询结果缓存"""
        cache_key = self._generate_query_key(query, model, top_k, skills_sig)

        if self.use_memory:
            # 内存模式
            self.memory_cache[cache_key] = {
                "value": results,
                "expire": datetime.now() + timedelta(seconds=self.default_ttl)
            }
        else:
            await self.redis.setex(cache_key, self.default_ttl, json.dumps(results))

    async def get_embedding_cache(self, text: str) -> Optional[np.ndarray]:
        """获取向量缓存"""
        cache_key = f"emb:{hashlib.md5(text.encode()).hexdigest()}"

        if self.use_memory:
            # 内存模式
            if cache_key in self.memory_cache:
                item = self.memory_cache[cache_key]
                if item["expire"] > datetime.now():
                    return item["value"]
                else:
                    del self.memory_cache[cache_key]
            return None
        else:
            cached = await self.redis.get(cache_key)
            if cached:
                return np.frombuffer(cached, dtype=np.float32)
            return None

    async def set_embedding_cache(self, text: str, embedding):
        """设置向量缓存"""
        cache_key = f"emb:{hashlib.md5(text.encode()).hexdigest()}"

        if self.use_memory:
            # 内存模式
            self.memory_cache[cache_key] = {
                "value": embedding,
                "expire": datetime.now() + timedelta(seconds=self.embedding_ttl)
            }
        else:
            # 转换为numpy数组再保存
            if not isinstance(embedding, np.ndarray):
                embedding = np.array(embedding)
            await self.redis.setex(cache_key, self.embedding_ttl, embedding.tobytes())

    async def get_session_context(self, session_id: str):
        """获取会话上下文"""
        cache_key = f"session:{session_id}"

        if self.use_memory:
            # 内存模式
            if cache_key in self.memory_cache:
                item = self.memory_cache[cache_key]
                if item["expire"] > datetime.now():
                    return item["value"]
                else:
                    del self.memory_cache[cache_key]
            return []
        else:
            cached = await self.redis.get(cache_key)
            if cached:
                return json.loads(cached)
            return []

    async def set_session_context(self, session_id: str, context: list, ttl: int = 1800):
        """设置会话上下文"""
        cache_key = f"session:{session_id}"

        if self.use_memory:
            # 内存模式
            self.memory_cache[cache_key] = {
                "value": context,
                "expire": datetime.now() + timedelta(seconds=ttl)
            }
        else:
            await self.redis.setex(cache_key, ttl, json.dumps(context))

    async def invalidate_query_cache(self):
        """清除所有查询缓存"""
        if self.use_memory:
            # 内存模式
            keys_to_delete = [k for k in self.memory_cache.keys() if k.startswith("query:")]
            for key in keys_to_delete:
                del self.memory_cache[key]
        else:
            keys = await self.redis.keys("query:*")
            if keys:
                await self.redis.delete(*keys)

    async def invalidate_embedding_cache(self):
        """清除所有向量缓存"""
        if self.use_memory:
            # 内存模式
            keys_to_delete = [k for k in self.memory_cache.keys() if k.startswith("emb:")]
            for key in keys_to_delete:
                del self.memory_cache[key]
        else:
            keys = await self.redis.keys("emb:*")
            if keys:
                await self.redis.delete(*keys)

    async def get_cache_stats(self):
        """获取缓存统计"""
        if self.use_memory:
            return {
                "hits": 0,
                "misses": 0,
                "keys": len(self.memory_cache),
                "mode": "memory"
            }
        else:
            info = await self.redis.info("stats")
            keyspace = await self.redis.info("keyspace")
            return {
                "hits": info.get("keyspace_hits", 0),
                "misses": info.get("keyspace_misses", 0),
                "keys": keyspace.get(f"db{settings.REDIS_DB}", {}).get("keys", 0),
                "mode": "redis"
            }

    def _generate_query_key(self, query: str, model: str, top_k: int, skills_sig: str = "") -> str:
        """生成查询缓存Key（skills_sig 让启用技能变化时自动失效）"""
        key_str = f"{query}:{model}:{top_k}:{skills_sig}"
        return f"query:{hashlib.md5(key_str.encode()).hexdigest()}"
