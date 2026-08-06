"""
记忆系统统一入口

三层职责：
- 短期（ShortTermMemory）：当前会话的原文窗口 + 累积摘要，Redis/内存 + SQLite 回填
- 中期（MidTermMemory）  ：跨会话的历史问答，Qdrant chat_memory 向量召回
- 长期（LongTermMemory） ：LLM 抽取的结构化用户事实，SQLite + embedding 相似度召回

压缩双通道：
- 主通道（后台）：一轮写完后检查占用，≥80% 则后台压缩，下一轮自然读到摘要，用户无感
- 安全阀（同步）：组装上下文时若占用 ≥95%，就地压缩后再组装

MemoryManager 是模块级单例：持有唯一的 Redis 连接与唯一的向量库实例，避免此前
「每个请求 new 一次」导致的内存模式数据丢失与重复连接探测。
"""
import asyncio
from typing import Any, Dict, List, Optional

from ...config import settings
from .compressor import ConversationCompressor
from .long_term import LongTermMemory
from .mid_term import MidTermMemory
from .mid_term_store import MidTermVectorStore
from .short_term import ShortTermMemory
from .token_counter import get_token_counter


class MemoryManager:
    """三层记忆 facade"""

    def __init__(self):
        self.short_term = ShortTermMemory()
        self.mid_term = MidTermMemory(vector_store=MidTermVectorStore())
        self.long_term = LongTermMemory()
        self.compressor = ConversationCompressor()
        self.token_counter = get_token_counter()
        self._locks: Dict[str, asyncio.Lock] = {}
        self._initialized = False

    async def init(self):
        """应用启动时调用一次"""
        if self._initialized:
            return
        await self.short_term.init()
        await self.mid_term.init()
        await self.long_term.init()
        self._initialized = True
        print(f"✅ 记忆系统就绪（token 计数: {self.token_counter.mode}，"
              f"窗口 {self.token_counter.context_window:,}，"
              f"压缩阈值 {self.token_counter.trigger_tokens:,}）")

    def _lock(self, session_id: str) -> asyncio.Lock:
        """per-session 锁，防止并发请求对同一会话重复压缩"""
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    # ---------------- 读：组装上下文 ----------------

    async def assemble_context(self, session_id: str, query: str) -> Dict[str, Any]:
        """汇总三层记忆，返回给 ResponseGenerator 的上下文块"""
        summary = await self.short_term.get_summary(session_id)
        messages = await self.short_term.get_context(session_id)

        # 中期 / 长期并发召回，二者互不依赖
        mid_hits, long_hits = await asyncio.gather(
            self._safe_mid_search(query, session_id),
            self._safe_long_search(query),
            return_exceptions=False,
        )

        extra = self._hits_as_texts(mid_hits, long_hits) + [query]
        total_tokens = self.compressor.estimate_tokens(messages, summary, extra)

        # 安全阀：后台压缩没跟上（或单轮暴涨）时就地同步压缩
        if self.compressor.must_compress(total_tokens):
            async with self._lock(session_id):
                messages, summary, changed = await self.compressor.maybe_compress(
                    session_id,
                    messages,
                    summary,
                    total_tokens=total_tokens,
                    trigger="sync_hard_limit",
                )
                if changed:
                    await self._persist_compression(session_id, messages, summary)
                    total_tokens = self.compressor.estimate_tokens(
                        messages, summary, extra
                    )

        return {
            "short_term_messages": messages,
            "short_term_summary": summary,
            "mid_term_hits": mid_hits,
            "long_term_hits": long_hits,
            "total_tokens": total_tokens,
            "usage_ratio": self.token_counter.usage_ratio(total_tokens),
        }

    async def _safe_mid_search(self, query: str, session_id: str) -> List[Dict[str, Any]]:
        try:
            return await self.mid_term.search_similar(
                query, exclude_session=session_id
            )
        except Exception as e:
            print(f"⚠️ 中期记忆召回异常: {e}")
            return []

    async def _safe_long_search(self, query: str) -> List[Dict[str, Any]]:
        try:
            return await self.long_term.search(query)
        except Exception as e:
            print(f"⚠️ 长期记忆召回异常: {e}")
            return []

    @staticmethod
    def _hits_as_texts(
        mid_hits: List[Dict[str, Any]], long_hits: List[Dict[str, Any]]
    ) -> List[str]:
        """把召回结果摊平成文本，用于 token 估算"""
        texts = []
        for h in mid_hits:
            texts.append(f"{h.get('user_msg', '')}{h.get('ai_msg', '')}")
        for h in long_hits:
            texts.append(h.get("fact", ""))
        return texts

    # ---------------- 写：后台异步 ----------------

    def schedule_write(
        self,
        session_id: str,
        user_msg: str,
        ai_msg: str,
        sources: Optional[List[str]] = None,
    ) -> Optional[asyncio.Task]:
        """fire-and-forget 写入三层记忆，不阻塞回复"""
        try:
            task = asyncio.create_task(
                self._write_all(session_id, user_msg, ai_msg, sources or [])
            )
        except RuntimeError as e:  # 没有运行中的事件循环
            print(f"⚠️ 无法调度记忆写入任务: {e}")
            return None
        task.add_done_callback(self._on_task_done)
        return task

    @staticmethod
    def _on_task_done(task: asyncio.Task):
        if task.cancelled():
            print("⚠️ 记忆写入任务被取消")
            return
        exc = task.exception()
        if exc:
            print(f"⚠️ 记忆写入任务异常: {exc!r}")

    async def _write_all(
        self, session_id: str, user_msg: str, ai_msg: str, sources: List[str]
    ):
        # 短期：追加原文
        try:
            await self.short_term.add(session_id, user_msg, ai_msg)
        except Exception as e:
            print(f"⚠️ 短期记忆写入失败: {e}")

        # 中期：向量化入库
        try:
            await self.mid_term.save(session_id, user_msg, ai_msg, sources)
        except Exception as e:
            print(f"⚠️ 中期记忆写入失败: {e}")

        # 长期：LLM 抽取结构化事实
        try:
            await self.long_term.extract_and_store(session_id, user_msg, ai_msg)
        except Exception as e:
            print(f"⚠️ 长期记忆写入失败: {e}")

        # 主通道：本轮写完后检查是否越过 80%，越过就在后台压缩
        try:
            await self.compress_if_needed(session_id)
        except Exception as e:
            print(f"⚠️ 后台压缩失败: {e}")

    async def compress_if_needed(self, session_id: str) -> bool:
        """后台主通道：达到触发阈值则压缩并落盘"""
        async with self._lock(session_id):
            summary = await self.short_term.get_summary(session_id)
            messages = await self.short_term.get_context(session_id)
            total = self.compressor.estimate_tokens(messages, summary)
            if not self.compressor.should_compress(total):
                return False
            messages, summary, changed = await self.compressor.maybe_compress(
                session_id,
                messages,
                summary,
                total_tokens=total,
                trigger="background",
            )
            if changed:
                await self._persist_compression(session_id, messages, summary)
            return changed

    async def _persist_compression(
        self, session_id: str, messages: List[Dict[str, Any]], summary: Optional[str]
    ):
        if summary:
            await self.short_term.set_summary(session_id, summary)
        await self.short_term.replace_context(session_id, messages)

    # ---------------- 维护 ----------------

    async def forget_session(self, session_id: str):
        """删除会话时清理三层记忆中属于它的部分（长期事实是跨会话资产，保留）"""
        try:
            await self.short_term.clear(session_id)
        except Exception as e:
            print(f"⚠️ 清理短期记忆失败: {e}")
        try:
            await self.mid_term.delete_session(session_id)
        except Exception as e:
            print(f"⚠️ 清理中期记忆失败: {e}")
        self._locks.pop(session_id, None)

    async def stats(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """记忆系统运行状态，供 /api/chat/memory/stats 使用"""
        from ...database import execute_query

        data: Dict[str, Any] = {
            "token_counter_mode": self.token_counter.mode,
            "context_window": self.token_counter.context_window,
            "trigger_tokens": self.token_counter.trigger_tokens,
            "hard_tokens": self.token_counter.hard_tokens,
            "short_term_backend": "memory" if self.short_term.use_memory else "redis",
            "mid_term_backend": "memory" if self.mid_term.vector_store.use_memory else "qdrant",
        }
        try:
            data["mid_term_records"] = execute_query(
                "SELECT COUNT(*) FROM mid_term_qa"
            )[0][0]
            data["long_term_facts"] = execute_query(
                "SELECT COUNT(*) FROM long_term_facts"
            )[0][0]
            data["compression_events"] = execute_query(
                "SELECT COUNT(*) FROM compression_events"
            )[0][0]
        except Exception as e:
            data["db_error"] = str(e)

        if session_id:
            summary = await self.short_term.get_summary(session_id)
            messages = await self.short_term.get_context(session_id)
            used = self.compressor.estimate_tokens(messages, summary)
            data["session"] = {
                "session_id": session_id,
                "messages": len(messages),
                "has_summary": bool(summary),
                "used_tokens": used,
                "usage_ratio": round(self.token_counter.usage_ratio(used), 4),
            }
        return data


_manager: Optional[MemoryManager] = None


def get_memory_manager() -> MemoryManager:
    """模块级单例"""
    global _manager
    if _manager is None:
        _manager = MemoryManager()
    return _manager
