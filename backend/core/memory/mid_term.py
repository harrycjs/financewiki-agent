"""
中期记忆模块 - 跨会话的历史问答召回

原实现是 `content LIKE '%query%'` 且 session_id 硬编码 'mid_term'，从未被调用。
现在改为：
- 写入：问答对向量化后进 Qdrant chat_memory collection，同时把原文落 mid_term_qa 表
- 召回：向量相似度检索，排除当前会话（当前会话由短期记忆负责，避免重复注入）
- 降级：Qdrant / embedding 不可用时回落 mid_term_qa 表的 SQL LIKE
"""
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ...config import settings
from ...database import execute_query, execute_update
from .mid_term_store import MidTermVectorStore


class MidTermMemory:
    """中期记忆：跨会话历史问答"""

    def __init__(
        self,
        vector_store: Optional[MidTermVectorStore] = None,
        embedding_service=None,
    ):
        self.vector_store = vector_store or MidTermVectorStore()
        self._embedding_service = embedding_service

    def _get_embedding_service(self):
        if self._embedding_service is None:
            from ..embedding.embedding_service import EmbeddingService

            self._embedding_service = EmbeddingService()
        return self._embedding_service

    async def init(self):
        await self.vector_store.init_collection()

    # ---------------- 写入 ----------------

    async def save(
        self,
        session_id: str,
        user_msg: str,
        ai_msg: str,
        sources: Optional[List[str]] = None,
    ):
        """保存一条问答记忆。向量化失败不影响原文落库。"""
        record_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat()
        embedding_id = None

        try:
            emb = await self._get_embedding_service().embed(user_msg)
            payload = {
                "record_id": record_id,
                "session_id": session_id,
                "user_msg": user_msg[:1000],
                "ai_msg": ai_msg[:2000],
                "sources": sources or [],
                "created_at": created_at,
            }
            await self.vector_store.upsert_memory(record_id, emb, payload)
            embedding_id = record_id
        except Exception as e:
            print(f"⚠️ 中期记忆向量化失败，仅落原文: {e}")

        try:
            execute_update(
                """INSERT INTO mid_term_qa
                   (id, session_id, user_msg, ai_msg, sources, embedding_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record_id,
                    session_id,
                    user_msg,
                    ai_msg,
                    json.dumps(sources or [], ensure_ascii=False),
                    embedding_id,
                ),
            )
        except Exception as e:
            print(f"⚠️ 中期记忆落库失败: {e}")

    # ---------------- 召回 ----------------

    async def search_similar(
        self,
        query: str,
        top_k: Optional[int] = None,
        exclude_session: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """向量召回相似历史问答；失败时回落 SQL LIKE"""
        top_k = top_k or settings.MEMORY_MID_TERM_TOP_K
        if not query:
            return []
        try:
            emb = await self._get_embedding_service().embed(query)
            hits = await self.vector_store.search_memory(
                emb,
                top_k=top_k * 3,  # 多取一些，过滤当前会话后仍够用
                score_threshold=settings.MEMORY_MID_TERM_SCORE_THRESHOLD,
            )
            results = []
            for h in hits:
                payload = h.get("payload") or {}
                if exclude_session and payload.get("session_id") == exclude_session:
                    continue
                results.append(
                    {
                        "user_msg": payload.get("user_msg", ""),
                        "ai_msg": payload.get("ai_msg", ""),
                        "session_id": payload.get("session_id"),
                        "created_at": payload.get("created_at"),
                        "score": h.get("score", 0.0),
                    }
                )
                if len(results) >= top_k:
                    break
            if results:
                return results
        except Exception as e:
            print(f"⚠️ 中期记忆向量召回失败，回落 SQL LIKE: {e}")

        return self._search_sql_fallback(query, top_k, exclude_session)

    @staticmethod
    def _search_sql_fallback(
        query: str, top_k: int, exclude_session: Optional[str]
    ) -> List[Dict[str, Any]]:
        """兜底：关键词模糊匹配"""
        try:
            if exclude_session:
                rows = execute_query(
                    """SELECT user_msg, ai_msg, session_id, created_at
                       FROM mid_term_qa
                       WHERE user_msg LIKE ? AND session_id != ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (f"%{query}%", exclude_session, top_k),
                )
            else:
                rows = execute_query(
                    """SELECT user_msg, ai_msg, session_id, created_at
                       FROM mid_term_qa
                       WHERE user_msg LIKE ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (f"%{query}%", top_k),
                )
        except Exception as e:
            print(f"⚠️ 中期记忆 SQL 兜底失败: {e}")
            return []
        return [
            {
                "user_msg": r[0],
                "ai_msg": r[1],
                "session_id": r[2],
                "created_at": r[3],
                "score": 0.0,
            }
            for r in rows
        ]

    # ---------------- 维护 ----------------

    async def delete_session(self, session_id: str):
        """会话删除时清理对应的中期记忆"""
        try:
            await self.vector_store.delete_by_session(session_id)
        except Exception as e:
            print(f"⚠️ 清理中期记忆向量失败: {e}")
        try:
            execute_update(
                "DELETE FROM mid_term_qa WHERE session_id = ?", (session_id,)
            )
        except Exception as e:
            print(f"⚠️ 清理中期记忆原文失败: {e}")

    async def cleanup(self, days: int = 90):
        """清理过期的中期记忆原文"""
        try:
            return execute_update(
                "DELETE FROM mid_term_qa WHERE created_at < datetime('now', ?)",
                (f"-{days} days",),
            )
        except Exception as e:
            print(f"⚠️ 中期记忆清理失败: {e}")
            return 0
