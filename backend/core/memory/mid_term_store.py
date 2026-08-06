"""
中期记忆专用向量库

复用 QdrantVectorStore 的连接/降级逻辑，但使用独立的 collection（默认 chat_memory），
并提供直接以原始 payload 存取的接口 —— 父类的 add_document/search 是为文档 chunk
设计的，payload 结构固定为 doc_id/chunk_id/content/metadata，不适合问答记忆。
"""
from typing import Any, Dict, List, Optional

import numpy as np

from ...config import settings
from ..rag.vector_store import QdrantVectorStore


class MidTermVectorStore(QdrantVectorStore):
    """chat_memory collection：存跨会话的问答对向量"""

    def __init__(self, collection_name: Optional[str] = None):
        super().__init__(
            collection_name=collection_name or settings.MEMORY_MID_TERM_COLLECTION
        )

    async def upsert_memory(
        self, point_id: str, embedding: List[float], payload: Dict[str, Any]
    ):
        """写入一条记忆向量"""
        if self.use_memory:
            self.vectors[point_id] = {
                "vector": np.array(embedding),
                "payload": payload,
            }
            return

        from qdrant_client.models import PointStruct

        self.client.upsert(
            collection_name=self.collection_name,
            points=[PointStruct(id=point_id, vector=embedding, payload=payload)],
        )

    async def search_memory(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        score_threshold: float = 0.6,
    ) -> List[Dict[str, Any]]:
        """检索，返回 [{id, score, payload}]"""
        if self.use_memory:
            query_vec = np.array(query_embedding)
            q_norm = np.linalg.norm(query_vec)
            if q_norm == 0:
                return []
            results = []
            for point_id, data in self.vectors.items():
                vec = data["vector"]
                v_norm = np.linalg.norm(vec)
                if v_norm == 0:
                    continue
                score = float(np.dot(query_vec, vec) / (q_norm * v_norm))
                if score >= score_threshold:
                    results.append(
                        {"id": point_id, "score": score, "payload": data["payload"]}
                    )
            results.sort(key=lambda x: x["score"], reverse=True)
            return results[:top_k]

        hits = self.client.search(
            collection_name=self.collection_name,
            query_vector=query_embedding,
            limit=top_k,
            score_threshold=score_threshold,
        )
        return [
            {"id": h.id, "score": h.score, "payload": h.payload or {}} for h in hits
        ]

    async def delete_by_session(self, session_id: str):
        """删除某个会话的全部记忆向量（会话删除时调用）"""
        if self.use_memory:
            for pid in [
                pid
                for pid, d in self.vectors.items()
                if d["payload"].get("session_id") == session_id
            ]:
                del self.vectors[pid]
            return

        from qdrant_client.models import Filter, FieldCondition, MatchValue

        self.client.delete(
            collection_name=self.collection_name,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="session_id", match=MatchValue(value=session_id)
                    )
                ]
            ),
        )
