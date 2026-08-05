"""
三路召回检索器
"""
import asyncio
from typing import List, Dict, Any
import jieba
import math
from collections import defaultdict

from ...config import settings
from .vector_store import QdrantVectorStore
from ..cache.cache_service import CacheService
from ..llm.base import BaseLLM


class BM25Index:
    """BM25关键词索引"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_count = 0
        self.avg_doc_len = 0
        self.doc_freqs = defaultdict(int)
        self.doc_lens = {}
        self.documents = {}

    def add_document(self, doc_id: str, content: str):
        """添加文档"""
        # 使用jieba分词
        tokens = list(jieba.cut(content))
        self.documents[doc_id] = tokens
        self.doc_lens[doc_id] = len(tokens)
        self.doc_count += 1

        # 更新平均文档长度
        total_len = sum(self.doc_lens.values())
        self.avg_doc_len = total_len / self.doc_count

        # 更新词频
        unique_tokens = set(tokens)
        for token in unique_tokens:
            self.doc_freqs[token] += 1

    def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """BM25检索"""
        query_tokens = list(jieba.cut(query))
        scores = {}

        for doc_id, doc_tokens in self.documents.items():
            score = 0.0
            doc_len = self.doc_lens[doc_id]

            for token in query_tokens:
                if token not in self.doc_freqs:
                    continue

                # 计算IDF
                df = self.doc_freqs[token]
                idf = math.log((self.doc_count - df + 0.5) / (df + 0.5) + 1)

                # 计算TF
                tf = doc_tokens.count(token)
                tf_norm = (tf * (self.k1 + 1)) / (tf + self.k1 * (1 - self.b + self.b * doc_len / self.avg_doc_len))

                score += idf * tf_norm

            if score > 0:
                scores[doc_id] = score

        # 排序返回top-k
        sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return [
            {"id": doc_id, "score": score, "content": " ".join(self.documents[doc_id])}
            for doc_id, score in sorted_results
        ]


class TripleRetriever:
    """三路召回检索器：向量 + 关键词 + 知识图谱"""

    def __init__(self):
        self.vector_store = QdrantVectorStore()
        self.cache = CacheService()
        self.bm25 = BM25Index()
        self.llm = None  # 延迟初始化

    def _get_llm(self):
        """获取LLM实例"""
        if self.llm is None:
            from ...database import execute_query
            rows = execute_query(
                "SELECT provider, api_key, api_base FROM model_configs WHERE is_active = 1"
            )
            if rows:
                provider, api_key, api_base = rows[0]
                self.llm = BaseLLM.create(provider, api_key, api_base)
            else:
                # 使用默认配置
                from ...config import settings
                self.llm = BaseLLM.create(
                    "deepseek",
                    settings.DEEPSEEK_API_KEY,
                    settings.DEEPSEEK_API_BASE
                )
        return self.llm

    async def retrieve(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """三路并行检索 + RRF融合"""
        # 1. 检查缓存
        cached = await self.cache.get_query_cache(query, "default", top_k)
        if cached:
            return cached

        # 2. 查询改写
        query_variants = await self.generate_query_variants(query)

        # 3. 三路并行检索
        vector_results = []
        bm25_results = []
        kg_results = []

        for variant in query_variants:
            # 向量检索
            vec_res = await self.vector_search(variant, top_k)
            vector_results.extend(vec_res)

            # 关键词检索
            bm25_res = self.bm25_search(variant, top_k)
            bm25_results.extend(bm25_res)

            # 知识图谱检索
            kg_res = await self.kg_search(variant, top_k)
            kg_results.extend(kg_res)

        # 4. 去重
        vector_results = self.deduplicate(vector_results)
        bm25_results = self.deduplicate(bm25_results)
        kg_results = self.deduplicate(kg_results)

        # 5. 三路RRF融合
        merged = self.rrf_fusion(vector_results, bm25_results, kg_results)

        # 6. 重排序
        reranked = await self.rerank(query, merged[:top_k * 2])

        results = reranked[:top_k]

        # 7. 缓存结果
        await self.cache.set_query_cache(query, "default", top_k, results)

        return results

    async def generate_query_variants(self, query: str) -> List[str]:
        """LLM生成查询变体，扩大召回"""
        llm = self._get_llm()
        prompt = f"""你是一个金融投研助手。请将以下问题改写为3个不同表述的查询，用于检索相关文档。

原始问题：{query}

请按照以下格式输出，每行一个查询：
1. [查询1]
2. [查询2]
3. [查询3]"""

        try:
            response = await llm.chat([{"role": "user", "content": prompt}])
            # 解析响应
            variants = []
            for line in response.split("\n"):
                line = line.strip()
                if line.startswith("1.") or line.startswith("2.") or line.startswith("3."):
                    variant = line.split(".", 1)[1].strip()
                    if variant:
                        variants.append(variant)
            return [query] + variants[:3]
        except Exception as e:
            print(f"查询改写失败: {e}")
            return [query]

    async def vector_search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        """向量检索"""
        from ..embedding.embedding_service import EmbeddingService
        embedding_service = EmbeddingService()

        # 检查embedding缓存
        cached_embedding = await self.cache.get_embedding_cache(query)
        if cached_embedding is not None:
            query_embedding = cached_embedding.tolist()
        else:
            query_embedding = await embedding_service.embed(query)
            await self.cache.set_embedding_cache(query, query_embedding)

        results = await self.vector_store.search(
            query_embedding=query_embedding,
            top_k=top_k
        )

        # 添加来源标记
        for r in results:
            r["source"] = "vector"

        return results

    def bm25_search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        """BM25关键词检索"""
        results = self.bm25.search(query, top_k)
        for r in results:
            r["source"] = "bm25"
        return results

    async def kg_search(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        """知识图谱检索"""
        from ..knowledge_graph.kg_retriever import KnowledgeGraphRetriever
        kg_retriever = KnowledgeGraphRetriever()
        results = await kg_retriever.retrieve(query, top_k)
        for r in results:
            r["source"] = "kg"
        return results

    def deduplicate(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """去重"""
        seen = set()
        unique = []
        for r in results:
            key = r.get("id") or r.get("content", "")[:100]
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    def rrf_fusion(
        self,
        results_a: List[Dict],
        results_b: List[Dict],
        results_c: List[Dict],
        k: int = 60
    ) -> List[Dict[str, Any]]:
        """三路Reciprocal Rank Fusion"""
        scores = {}
        doc_map = {}

        # 向量检索分数
        for rank, doc in enumerate(results_a):
            doc_id = doc.get("id") or doc.get("content", "")[:100]
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)
            doc_map[doc_id] = doc

        # 关键词检索分数
        for rank, doc in enumerate(results_b):
            doc_id = doc.get("id") or doc.get("content", "")[:100]
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)
            if doc_id not in doc_map:
                doc_map[doc_id] = doc

        # 知识图谱分数（权重更高）
        for rank, item in enumerate(results_c):
            doc_id = item.get("id") or item.get("content", "")[:100]
            scores[doc_id] = scores.get(doc_id, 0) + 1.5 / (k + rank)
            if doc_id not in doc_map:
                doc_map[doc_id] = item

        # 按分数排序
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

        results = []
        for doc_id in sorted_ids:
            doc = doc_map[doc_id]
            doc["rrf_score"] = scores[doc_id]
            results.append(doc)

        return results

    async def rerank(self, query: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """重排序（简化版：基于内容相关性）"""
        # 简单的关键词匹配重排序
        query_tokens = set(jieba.cut(query))

        for doc in results:
            content = doc.get("content", "")
            content_tokens = set(jieba.cut(content))
            overlap = len(query_tokens & content_tokens)
            doc["rerank_score"] = doc.get("rrf_score", 0) + overlap * 0.1

        # 按重排序分数排序
        results.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        return results
