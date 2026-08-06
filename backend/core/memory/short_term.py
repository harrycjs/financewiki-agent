"""
短期记忆模块 - 当前会话的原文窗口 + 累积摘要

设计要点：
1. 不再按轮数/字符硬截断。窗口大小由 token 预算与压缩器决定，这里只负责存取。
2. __init__ 不做任何 IO；Redis 探测放在 async init()，由 MemoryManager 在应用
   启动时调用一次（此前每个请求 new 一个实例并同步 ping，既慢又在内存模式下丢数据）。
3. Redis / 内存都 miss 时，从 SQLite chat_history 回填（rehydrate）。SQLite 是
   source of truth，因此重启后端、Redis 过期都不会让模型失忆。
4. 摘要单独一个 key，并双写 short_term_summaries 表，Redis 挂掉也不丢。
"""
import json
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

from ...config import settings
from ...database import execute_query, execute_update


class ShortTermMemory:
    """短期记忆：Redis 优先，内存降级，SQLite 兜底回填"""

    KEY_PREFIX = "session:"
    SUMMARY_SUFFIX = ":summary"

    def __init__(self):
        self.redis = None
        self.use_memory = True
        self.memory_store: Dict[str, Dict[str, Any]] = {}
        self._initialized = False

    @property
    def ttl(self) -> int:
        return settings.MEMORY_SHORT_TERM_TTL

    async def init(self):
        """应用启动时调用一次，完成 Redis 探测"""
        if self._initialized:
            return
        try:
            import redis.asyncio as redis_async

            self.redis = redis_async.from_url(
                f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await self.redis.ping()
            self.use_memory = False
            print("✅ 短期记忆使用 Redis")
        except Exception as e:
            print(f"⚠️ Redis 不可用，短期记忆使用内存模式: {e}")
            self.redis = None
            self.use_memory = True
        self._initialized = True

    # ---------------- 原文窗口 ----------------

    async def get_context(self, session_id: str) -> List[Dict[str, Any]]:
        """获取会话原文窗口；缓存 miss 时从 SQLite 回填"""
        key = f"{self.KEY_PREFIX}{session_id}"
        cached = await self._raw_get(key)
        if cached is not None:
            try:
                return json.loads(cached)
            except (TypeError, ValueError):
                pass  # 脏数据，走回填重建

        context = self._rehydrate_from_sqlite(session_id)
        if context:
            await self._raw_set(key, json.dumps(context, ensure_ascii=False))
        return context

    async def add(self, session_id: str, user_msg: str, ai_msg: str):
        """追加一轮对话。不截断——窗口由压缩器管理"""
        context = await self.get_context(session_id)
        # 幂等保护：窗口可能刚从 chat_history 回填过，本轮已经在里面了
        if self._tail_matches(context, user_msg, ai_msg):
            return
        context.append({"role": "user", "content": user_msg})
        context.append({"role": "assistant", "content": ai_msg})
        await self.replace_context(session_id, context)

    @staticmethod
    def _tail_matches(
        context: List[Dict[str, Any]], user_msg: str, ai_msg: str
    ) -> bool:
        if len(context) < 2:
            return False
        last_user, last_ai = context[-2], context[-1]
        return (
            last_user.get("role") == "user"
            and last_user.get("content") == user_msg
            and last_ai.get("role") == "assistant"
            and last_ai.get("content") == ai_msg
        )

    async def replace_context(self, session_id: str, context: List[Dict[str, Any]]):
        """整体覆盖窗口（压缩后写回锚点用）"""
        key = f"{self.KEY_PREFIX}{session_id}"
        await self._raw_set(key, json.dumps(context, ensure_ascii=False))

    async def clear(self, session_id: str):
        """清空会话上下文与摘要"""
        await self._raw_del(f"{self.KEY_PREFIX}{session_id}")
        await self._raw_del(f"{self.KEY_PREFIX}{session_id}{self.SUMMARY_SUFFIX}")
        try:
            execute_update(
                "DELETE FROM short_term_summaries WHERE session_id = ?", (session_id,)
            )
        except Exception as e:
            print(f"⚠️ 清理摘要表失败: {e}")

    async def get_recent(self, session_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """获取最近 limit 轮对话"""
        context = await self.get_context(session_id)
        return context[-(limit * 2):] if limit > 0 else context

    # ---------------- 累积摘要 ----------------

    async def get_summary(self, session_id: str) -> Optional[str]:
        key = f"{self.KEY_PREFIX}{session_id}{self.SUMMARY_SUFFIX}"
        cached = await self._raw_get(key)
        if cached:
            return cached
        # 缓存 miss → 从 SQLite 兜底
        try:
            rows = execute_query(
                "SELECT summary FROM short_term_summaries WHERE session_id = ?",
                (session_id,),
            )
            if rows:
                summary = rows[0][0]
                await self._raw_set(key, summary)
                return summary
        except Exception as e:
            print(f"⚠️ 读取摘要表失败: {e}")
        return None

    async def set_summary(self, session_id: str, summary: str):
        """写摘要：缓存 + SQLite 双写"""
        key = f"{self.KEY_PREFIX}{session_id}{self.SUMMARY_SUFFIX}"
        await self._raw_set(key, summary)
        try:
            execute_update(
                """INSERT INTO short_term_summaries (session_id, summary, updated_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(session_id) DO UPDATE SET
                       summary = excluded.summary,
                       updated_at = CURRENT_TIMESTAMP""",
                (session_id, summary),
            )
        except Exception as e:
            print(f"⚠️ 摘要落库失败（缓存仍有效）: {e}")

    # ---------------- 内部：存储抽象 ----------------

    async def _raw_get(self, key: str) -> Optional[str]:
        if self.use_memory or self.redis is None:
            item = self.memory_store.get(key)
            if item and item["expire"] > datetime.now():
                return item["value"]
            self.memory_store.pop(key, None)
            return None
        try:
            return await self.redis.get(key)
        except Exception as e:
            print(f"⚠️ Redis 读取失败，本次降级内存: {e}")
            self.use_memory = True
            return None

    async def _raw_set(self, key: str, value: str):
        if self.use_memory or self.redis is None:
            self.memory_store[key] = {
                "value": value,
                "expire": datetime.now() + timedelta(seconds=self.ttl),
            }
            return
        try:
            await self.redis.setex(key, self.ttl, value)
        except Exception as e:
            print(f"⚠️ Redis 写入失败，本次降级内存: {e}")
            self.use_memory = True
            self.memory_store[key] = {
                "value": value,
                "expire": datetime.now() + timedelta(seconds=self.ttl),
            }

    async def _raw_del(self, key: str):
        if self.use_memory or self.redis is None:
            self.memory_store.pop(key, None)
            return
        try:
            await self.redis.delete(key)
        except Exception as e:
            print(f"⚠️ Redis 删除失败: {e}")

    @staticmethod
    def _rehydrate_from_sqlite(session_id: str) -> List[Dict[str, Any]]:
        """从 chat_history 重建窗口（SQLite 是 source of truth）。

        若该会话已经有累积摘要，说明更早的内容已被压缩进摘要，此时只回填最近
        anchor 轮原文，避免把已摘要的历史重新灌回来导致反复压缩。
        """
        limit = None
        try:
            has_summary = execute_query(
                "SELECT 1 FROM short_term_summaries WHERE session_id = ?", (session_id,)
            )
            if has_summary:
                limit = settings.COMPRESSION_ANCHOR_RECENT_TURNS * 2
        except Exception:
            pass

        try:
            if limit:
                rows = execute_query(
                    """SELECT role, content FROM chat_history
                       WHERE session_id = ?
                       ORDER BY id DESC
                       LIMIT ?""",
                    (session_id, limit),
                )
                rows = list(reversed(rows))
            else:
                rows = execute_query(
                    """SELECT role, content FROM chat_history
                       WHERE session_id = ?
                       ORDER BY id ASC""",
                    (session_id,),
                )
        except Exception as e:
            print(f"⚠️ 短期记忆回填失败: {e}")
            return []
        return [{"role": r[0], "content": r[1]} for r in rows if r[1]]
