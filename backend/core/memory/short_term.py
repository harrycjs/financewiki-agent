"""
短期记忆模块 - 支持Redis和内存模式
"""
import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from ...config import settings


class ShortTermMemory:
    """短期记忆：支持Redis和内存模式"""

    def __init__(self):
        self.max_context_length = 10  # 最多保留10轮对话
        self.ttl = 1800  # 30分钟过期
        self.redis = None
        self.use_memory = True  # 默认使用内存模式
        self.memory_store = {}  # 内存存储

        # 尝试连接Redis
        try:
            import redis.asyncio as redis_async
            self.redis = redis_async.from_url(
                f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}",
                decode_responses=True
            )
            # 测试连接
            import redis
            r = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
            r.ping()
            self.use_memory = False
            print("✅ 短期记忆使用Redis")
        except Exception as e:
            print(f"⚠️ Redis不可用，短期记忆使用内存模式: {e}")
            self.use_memory = True

    async def get_context(self, session_id: str) -> List[Dict[str, Any]]:
        """获取会话上下文"""
        key = f"session:{session_id}"

        if self.use_memory:
            # 内存模式
            if key in self.memory_store:
                item = self.memory_store[key]
                if item["expire"] > datetime.now():
                    return item["value"]
                else:
                    del self.memory_store[key]
            return []
        else:
            cached = await self.redis.get(key)
            if cached:
                return json.loads(cached)
            return []

    async def add(self, session_id: str, user_msg: str, ai_msg: str):
        """添加对话到上下文"""
        context = await self.get_context(session_id)

        # 添加新的对话
        context.append({"role": "user", "content": user_msg})
        context.append({"role": "assistant", "content": ai_msg})

        # 限制上下文长度
        if len(context) > self.max_context_length * 2:
            context = context[-(self.max_context_length * 2):]

        # 保存
        key = f"session:{session_id}"
        if self.use_memory:
            # 内存模式
            self.memory_store[key] = {
                "value": context,
                "expire": datetime.now() + timedelta(seconds=self.ttl)
            }
        else:
            await self.redis.setex(key, self.ttl, json.dumps(context))

    async def clear(self, session_id: str):
        """清空会话上下文"""
        key = f"session:{session_id}"
        if self.use_memory:
            # 内存模式
            if key in self.memory_store:
                del self.memory_store[key]
        else:
            await self.redis.delete(key)

    async def get_recent(self, session_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """获取最近的对话"""
        context = await self.get_context(session_id)
        return context[-(limit * 2):]
