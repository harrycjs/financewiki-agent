"""
向量存储模块 - 支持Qdrant和ChromaDB
"""
from typing import List, Dict, Any, Optional
import uuid
import numpy as np

from ...config import settings


class QdrantVectorStore:
    """向量数据库封装 - 支持Qdrant和内存模式"""

    def __init__(self):
        self.collection_name = "finance_docs"
        self.vector_size = settings.EMBEDDING_DIMENSION
        self.vectors = {}  # 内存存储
        self.client = None
        self.use_memory = True  # 默认使用内存模式

        # 尝试连接Qdrant
        try:
            from qdrant_client import QdrantClient
            self.client = QdrantClient(
                host=settings.QDRANT_HOST,
                port=settings.QDRANT_PORT
            )
            # 测试连接
            self.client.get_collections()
            self.use_memory = False
            print("✅ 连接到Qdrant向量数据库")
        except Exception as e:
            print(f"⚠️ Qdrant不可用，使用内存模式: {e}")
            self.use_memory = True

    async def init_collection(self):
        """初始化向量集合"""
        if self.use_memory:
            print("✅ 使用内存向量存储")
            return

        try:
            collections = self.client.get_collections().collections
            collection_names = [c.name for c in collections]

            if self.collection_name not in collection_names:
                from qdrant_client.models import VectorParams, Distance
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.vector_size,
                        distance=Distance.COSINE
                    )
                )
                print(f"✅ 创建向量集合: {self.collection_name}")
        except Exception as e:
            print(f"⚠️ 初始化Qdrant失败，切换到内存模式: {e}")
            self.use_memory = True

    async def add_document(
        self,
        doc_id: str,
        chunk_id: str,
        embedding: List[float],
        metadata: Dict[str, Any]
    ):
        """添加文档向量"""
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}_{chunk_id}"))

        if self.use_memory:
            # 内存模式
            self.vectors[point_id] = {
                "vector": np.array(embedding),
                "payload": {
                    "doc_id": doc_id,
                    "chunk_id": chunk_id,
                    **metadata
                }
            }
        else:
            from qdrant_client.models import PointStruct
            self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=embedding,
                        payload={
                            "doc_id": doc_id,
                            "chunk_id": chunk_id,
                            **metadata
                        }
                    )
                ]
            )

    async def batch_add(
        self,
        doc_id: str,
        chunks: List[Dict[str, Any]]
    ):
        """批量添加文档向量"""
        if self.use_memory:
            # 内存模式
            for chunk in chunks:
                point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}_{chunk['id']}"))
                self.vectors[point_id] = {
                    "vector": np.array(chunk["embedding"]),
                    "payload": {
                        "doc_id": doc_id,
                        "chunk_id": chunk["id"],
                        "content": chunk["content"],
                        "metadata": chunk.get("metadata", {})
                    }
                }
        else:
            from qdrant_client.models import PointStruct
            points = []
            for chunk in chunks:
                point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}_{chunk['id']}"))
                points.append(
                    PointStruct(
                        id=point_id,
                        vector=chunk["embedding"],
                        payload={
                            "doc_id": doc_id,
                            "chunk_id": chunk["id"],
                            "content": chunk["content"],
                            "metadata": chunk.get("metadata", {})
                        }
                    )
                )

            # 分批插入，每批100条
            batch_size = 100
            for i in range(0, len(points), batch_size):
                batch = points[i:i + batch_size]
                self.client.upsert(
                    collection_name=self.collection_name,
                    points=batch
                )

    async def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        score_threshold: float = 0.5,
        filters: Optional[Dict[str, Any]] = None
    ):
        """向量检索"""
        if self.use_memory:
            # 内存模式 - 使用余弦相似度
            query_vec = np.array(query_embedding)
            results = []

            for point_id, data in self.vectors.items():
                # 应用过滤
                if filters:
                    match = True
                    for key, value in filters.items():
                        if data["payload"].get(key) != value:
                            match = False
                            break
                    if not match:
                        continue

                # 计算余弦相似度
                vec = data["vector"]
                similarity = np.dot(query_vec, vec) / (np.linalg.norm(query_vec) * np.linalg.norm(vec))

                if similarity >= score_threshold:
                    results.append({
                        "id": point_id,
                        "score": float(similarity),
                        "doc_id": data["payload"].get("doc_id"),
                        "chunk_id": data["payload"].get("chunk_id"),
                        "content": data["payload"].get("content"),
                        "metadata": data["payload"].get("metadata", {})
                    })

            # 按分数排序
            results.sort(key=lambda x: x["score"], reverse=True)
            return results[:top_k]
        else:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            search_filter = None
            if filters:
                conditions = []
                for key, value in filters.items():
                    conditions.append(
                        FieldCondition(key=key, match=MatchValue(value=value))
                    )
                search_filter = Filter(must=conditions)

            results = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_embedding,
                limit=top_k,
                score_threshold=score_threshold,
                query_filter=search_filter
            )

            return [
                {
                    "id": hit.id,
                    "score": hit.score,
                    "doc_id": hit.payload.get("doc_id"),
                    "chunk_id": hit.payload.get("chunk_id"),
                    "content": hit.payload.get("content"),
                    "metadata": hit.payload.get("metadata", {})
                }
                for hit in results
            ]

    async def delete_by_doc_id(self, doc_id: str):
        """删除指定文档的所有向量"""
        if self.use_memory:
            # 内存模式
            to_delete = []
            for point_id, data in self.vectors.items():
                if data["payload"].get("doc_id") == doc_id:
                    to_delete.append(point_id)
            for point_id in to_delete:
                del self.vectors[point_id]
        else:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="doc_id",
                            match=MatchValue(value=doc_id)
                        )
                    ]
                )
            )

    async def get_collection_stats(self):
        """获取集合统计信息"""
        if self.use_memory:
            return {
                "total_vectors": len(self.vectors),
                "status": "memory_mode"
            }
        else:
            info = self.client.get_collection(self.collection_name)
            return {
                "total_vectors": info.vectors_count,
                "status": info.status
            }
