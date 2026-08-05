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
    """BM25 关键词索引（带倒排）"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_count = 0
        self.avg_doc_len = 0
        self.doc_freqs = defaultdict(int)
        self.doc_lens = {}
        self.documents = {}
        # 倒排索引：term → {doc_id: term_count}
        # 搜索时只遍历包含查询词的候选文档，避免 O(N×M) 全扫
        self.inverted_index: Dict[str, Dict[str, int]] = defaultdict(dict)

    def add_document(self, doc_id: str, content: str):
        """添加文档（同步建倒排索引）"""
        tokens = list(jieba.cut(content))
        self.documents[doc_id] = tokens
        self.doc_lens[doc_id] = len(tokens)
        self.doc_count += 1

        # 更新平均文档长度
        total_len = sum(self.doc_lens.values())
        self.avg_doc_len = total_len / self.doc_count if self.doc_count > 0 else 0

        # 更新词频 + 倒排索引
        tf_counts: Dict[str, int] = {}
        for token in tokens:
            tf_counts[token] = tf_counts.get(token, 0) + 1
        for token, tf in tf_counts.items():
            self.doc_freqs[token] += 1
            self.inverted_index[token][doc_id] = tf

    def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """BM25 检索：仅遍历候选文档（命中查询词的 doc）"""
        query_tokens = list(jieba.cut(query))
        # 去重 query tokens（避免重复加 IDF 分）
        unique_query_tokens = set(t for t in query_tokens if t in self.doc_freqs)
        if not unique_query_tokens:
            return []

        # 取候选文档：所有出现任一查询词的 doc
        candidate_docs: set = set()
        for token in unique_query_tokens:
            candidate_docs.update(self.inverted_index.get(token, {}).keys())

        scores = {}
        for doc_id in candidate_docs:
            doc_tokens = self.documents[doc_id]
            doc_len = self.doc_lens[doc_id]

            score = 0.0
            for token in unique_query_tokens:
                tf = self.inverted_index[token].get(doc_id, 0)
                if tf == 0:
                    continue
                df = self.doc_freqs[token]
                idf = math.log((self.doc_count - df + 0.5) / (df + 0.5) + 1)
                tf_norm = (tf * (self.k1 + 1)) / (tf + self.k1 * (1 - self.b + self.b * doc_len / self.avg_doc_len))
                score += idf * tf_norm

            if score > 0:
                scores[doc_id] = score

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
        # 3.1 向量：批量 embedding + 并发 Qdrant 搜索（4变体并发，节省 3×embedding 耗时）
        vector_results = await self._vector_search_batch(query_variants, top_k)

        # 3.2 BM25：in-memory 倒排索引，已经很快，仍并行起 4 个变体
        bm25_results = await asyncio.gather(*[
            asyncio.to_thread(self.bm25_search, v, top_k)
            for v in query_variants
        ])
        bm25_results = [r for sub in bm25_results for r in sub]

        # 3.3 KG：每变体独立查询
        kg_results = await asyncio.gather(*[
            self.kg_search(v, top_k) for v in query_variants
        ])
        kg_results = [r for sub in kg_results for r in sub]

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

    async def _vector_search_batch(
        self,
        variants: List[str],
        top_k: int
    ) -> List[Dict[str, Any]]:
        """批量向量检索：所有变体的 embedding 并发计算 + Qdrant 搜索并发

        替代原来"for variant: await vector_search" 的串行模式：
          - embedding：N 次串行 → N 次并发（asyncio.gather），缓存命中跳过
          - Qdrant 搜索：N 次串行 → N 次并发
        """
        from ..embedding.embedding_service import EmbeddingService
        embedding_service = EmbeddingService()

        # 1. 并发查 embedding 缓存 + 计算未命中的
        cached_map: Dict[str, Any] = {}
        todo_variants: List[str] = []
        todo_keys: List[str] = []

        for v in variants:
            emb = await self.cache.get_embedding_cache(v)
            if emb is not None:
                cached_map[v] = emb.tolist() if hasattr(emb, "tolist") else list(emb)
            else:
                todo_variants.append(v)
                todo_keys.append(v)

        # 并发算未命中的 embedding
        if todo_variants:
            new_embeddings = await embedding_service.embed_batch(todo_variants)
            for v, emb in zip(todo_variants, new_embeddings):
                cached_map[v] = emb
                # 写回缓存（不 await，避免阻塞）
                asyncio.create_task(self.cache.set_embedding_cache(v, emb))

        # 2. 并发查 Qdrant
        async def _q(variant, emb):
            hits = await self.vector_store.search(query_embedding=emb, top_k=top_k)
            for h in hits:
                h["source"] = "vector"
                h["matched_variant"] = variant
            return hits

        all_hits = await asyncio.gather(*[
            _q(v, cached_map[v]) for v in variants
        ])
        return [h for sub in all_hits for h in sub]

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
