"""
知识图谱检索器
"""
from typing import List, Dict, Any

from .builder import KnowledgeGraphBuilder
from ..llm.base import BaseLLM
from ...database import execute_query
from ...config import settings


class KnowledgeGraphRetriever:
    """基于知识图谱的检索"""

    def __init__(self):
        self.builder = KnowledgeGraphBuilder()
        self.builder.load_from_db()
        self.llm = None

    def _get_llm(self):
        """获取LLM实例"""
        if self.llm is None:
            rows = execute_query(
                "SELECT provider, api_key, api_base FROM model_configs WHERE is_active = 1"
            )
            if rows:
                provider, api_key, api_base = rows[0]
                self.llm = BaseLLM.create(provider, api_key, api_base)
            else:
                self.llm = BaseLLM.create(
                    "deepseek",
                    settings.DEEPSEEK_API_KEY,
                    settings.DEEPSEEK_API_BASE
                )
        return self.llm

    async def retrieve(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """实体识别 → 子图扩展 → 相关性排序"""
        # 1. 识别查询中的实体
        entities = await self.extract_query_entities(query)

        # 2. 扩展相关实体（1-2跳）
        expanded_entities = []
        for entity in entities:
            neighbors = self.builder.get_entity_neighbors(entity, hops=2)
            expanded_entities.extend(neighbors)
        expanded_entities = list(set(expanded_entities))

        # 3. 获取相关文档片段
        results = []
        seen_docs = set()

        for entity in expanded_entities:
            # 获取与该实体相关的所有文档
            related_docs = self.get_entity_documents(entity)
            for doc in related_docs:
                doc_id = doc.get("id")
                if doc_id and doc_id not in seen_docs:
                    seen_docs.add(doc_id)
                    # 计算相关性分数
                    score = self.calculate_relevance(query, entity, doc)
                    results.append({
                        "id": doc_id,
                        "score": score,
                        "content": doc.get("content", "")[:500],
                        "entity": entity,
                        "source": "kg"
                    })

        # 4. 排序并返回top-k
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    async def extract_query_entities(self, query: str) -> List[str]:
        """从查询中提取实体"""
        llm = self._get_llm()

        prompt = f"""从以下金融查询中提取实体名称。

查询：{query}

请严格按照以下JSON格式输出，不要添加任何其他内容：
{{
    "entities": ["实体1", "实体2"]
}}

实体类型：公司名、财务指标、人名、行业、概念"""

        try:
            response = await llm.chat([{"role": "user", "content": prompt}])
            # 解析JSON
            start = response.find("{")
            end = response.rfind("}") + 1
            if start != -1 and end > start:
                json_str = response[start:end]
                import json
                result = json.loads(json_str)
                return result.get("entities", [])
        except Exception as e:
            print(f"实体提取失败: {e}")

        # 降级：简单分词
        from ..rag.retriever import BM25Index
        tokens = list(jieba.cut(query))
        # 只返回在图中存在的实体
        graph_nodes = set(self.builder.graph.nodes())
        return [t for t in tokens if t in graph_nodes]

    def get_entity_documents(self, entity: str) -> List[Dict[str, Any]]:
        """获取与实体相关的文档"""
        # 从数据库查询包含该实体的文档
        rows = execute_query(
            """SELECT d.id, d.content, d.filename
               FROM documents d
               JOIN entities e ON e.doc_id = d.id
               WHERE e.name = ?""",
            (entity,)
        )

        return [
            {"id": row[0], "content": row[1], "filename": row[2]}
            for row in rows
        ]

    def calculate_relevance(self, query: str, entity: str, doc: Dict) -> float:
        """计算相关性分数"""
        # 基础分数：实体在图中的重要性
        if entity in self.builder.graph:
            degree = self.builder.graph.degree(entity)
            base_score = min(degree / 10.0, 1.0)
        else:
            base_score = 0.1

        # 查询匹配分数
        query_lower = query.lower()
        entity_lower = entity.lower()
        if entity_lower in query_lower:
            query_score = 1.0
        else:
            # 简单的词重叠
            query_tokens = set(query_lower.split())
            entity_tokens = set(entity_lower.split())
            overlap = len(query_tokens & entity_tokens)
            query_score = overlap / max(len(query_tokens), 1)

        # 综合分数
        return base_score * 0.4 + query_score * 0.6
