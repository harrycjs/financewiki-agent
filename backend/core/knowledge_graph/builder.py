"""
知识图谱构建器
"""
import networkx as nx
import json
import asyncio
from typing import List, Dict, Any, Tuple
import uuid

from ...database import execute_query, get_db
from ..llm.base import BaseLLM
from ...config import settings


# 单次送入 LLM 的文本块大小（字符）。超过则分块+滑窗拼接结果，确保不丢内容。
LLM_CHUNK_SIZE = 1800
LLM_CHUNK_OVERLAP = 200

# KG 段落并发上限（同时向 LLM 发多少请求）。过高会触发限流/配额/超时。
KG_PARALLEL_CONCURRENCY = 4


def _split_for_llm(text: str, size: int = LLM_CHUNK_SIZE, overlap: int = LLM_CHUNK_OVERLAP) -> List[str]:
    """将长文本切分为多个不超过 size 的块，相邻块保留 overlap 字符重叠

    重叠保证跨块实体/关系在 LLM 视角下能被关联上（去重在调用方做）。
    最后一块可以小于 size 但保证非空，绝不丢尾部。
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        chunks.append(text[start:end])
        if end >= n:
            break
        # 下一块起点 = end - overlap，保证边界不漏
        start = end - overlap
        if start <= 0 or chunks[-1] == text[start:end]:
            # 防止零步死循环
            start = end
    return chunks


class KnowledgeGraphBuilder:
    """从文档中提取实体和关系，构建知识图谱"""

    def __init__(self):
        self.graph = nx.DiGraph()
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

    async def extract_entities(self, text: str) -> List[Dict[str, Any]]:
        """LLM提取实体（公司、指标、人物等）。

        长文本按 LLM_CHUNK_SIZE 切块逐次抽取，再合并去重 —— 不再截断。
        """
        llm = self._get_llm()
        prompt_template = """从以下金融文本中提取实体，返回JSON格式。

文本：
{text}

实体类型：公司名、财务指标、人名、行业、概念

请严格按照以下JSON格式输出，不要添加任何其他内容：
{{
    "entities": [
        {{"name": "实体名称", "type": "实体类型", "attributes": {{"属性": "值"}}}}
    ]
}}

实体类型说明：
- 公司：上市公司、企业名称
- 指标：财务指标（如市盈率、市净率、ROE等）
- 人物：公司高管、分析师等
- 行业：行业分类（如科技、金融、消费等）
- 概念：投资概念（如新能源、人工智能等）"""

        results: List[Dict[str, Any]] = []
        for chunk in _split_for_llm(text):
            try:
                response = await llm.chat([{"role": "user", "content": prompt_template.format(text=chunk)}])
                start = response.find("{")
                end = response.rfind("}") + 1
                if start != -1 and end > start:
                    json_str = response[start:end]
                    result = json.loads(json_str)
                    results.extend(result.get("entities", []))
            except Exception as e:
                print(f"实体提取失败（chunk len={len(chunk)}）: {e}")

        return results

    async def extract_relations(
        self,
        text: str,
        entities: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """LLM提取实体关系。

        长文本按块逐次抽取，再合并去重 —— 不再截断。
        """
        llm = self._get_llm()

        entity_names = [e["name"] for e in entities]
        prompt_template = """基于以下文本和实体列表，提取实体间的关系。

文本：
{text}

实体列表：{entity_names}

请严格按照以下JSON格式输出，不要添加任何其他内容：
{{
    "relations": [
        {{
            "source": "源实体名称",
            "target": "目标实体名称",
            "relation": "关系类型",
            "weight": 0.9
        }}
    ]
}}

关系类型说明：
- 属于：实体A属于实体B（如：贵州茅台 属于 白酒行业）
- 具有：实体A具有实体B（如：贵州茅台 具有 市盈率指标）
- 相关：实体A与实体B相关（如：新能源汽车 相关 锂电池）
- 导致：实体A导致实体B（如：加息 导致 股市下跌）
- 位于：实体A位于实体B（如：某公司 位于 某地区）"""

        results: List[Dict[str, Any]] = []
        for chunk in _split_for_llm(text):
            try:
                response = await llm.chat([
                    {"role": "user", "content": prompt_template.format(
                        text=chunk, entity_names=json.dumps(entity_names, ensure_ascii=False)
                    )}
                ])
                start = response.find("{")
                end = response.rfind("}") + 1
                if start != -1 and end > start:
                    json_str = response[start:end]
                    result = json.loads(json_str)
                    results.extend(result.get("relations", []))
            except Exception as e:
                print(f"关系提取失败（chunk len={len(chunk)}）: {e}")

        return results

    async def build_from_document(self, doc_id: str, content: str) -> Tuple[int, int]:
        """从文档构建知识图谱（增量追加，不删除任何已有实体/关系）

        设计：
        - 每条实体/关系都分配全新 UUID，INSERT OR REPLACE 不会触发 PK 冲突，
          因此旧文档的数据完全保留
        - 单文档内按 (name, type) 去重实体；按 (source, target, relation) 去重关系
        - 跨文档同名实体在 DB 里是多行（每 doc 一行），由 API 层聚合成一个节点
        """
        # 分段处理
        paragraphs = [p for p in content.split("\n\n") if len(p.strip()) >= 50]

        # 段落级并发：每个段落独立走 extract_entities + extract_relations，
        # 段内仍串行（关系依赖本段实体），跨段用 Semaphore 限流并发。
        sem = asyncio.Semaphore(KG_PARALLEL_CONCURRENCY)

        async def _process_one(para: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
            async with sem:
                ents = await self.extract_entities(para)
                rels: List[Dict[str, Any]] = []
                if ents:
                    rels = await self.extract_relations(para, ents)
                return ents, rels

        per_para_results = await asyncio.gather(
            *(_process_one(p) for p in paragraphs),
            return_exceptions=True,
        )

        all_entities: List[Dict[str, Any]] = []
        all_relations: List[Dict[str, Any]] = []
        for i, r in enumerate(per_para_results):
            if isinstance(r, Exception):
                print(f"⚠️ 段落 {i} KG 抽取失败: {r}")
                continue
            ents, rels = r
            all_entities.extend(ents)
            all_relations.extend(rels)

        # 去重实体
        unique_entities: Dict[str, Dict[str, Any]] = {}
        for entity in all_entities:
            key = entity["name"]
            if key not in unique_entities:
                unique_entities[key] = entity

        # 去重关系（按 source-target-relation 三元组去重）
        unique_relations: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for rel in all_relations:
            key = (rel.get("source", ""), rel.get("target", ""), rel.get("relation", ""))
            if all(key) and key not in unique_relations:
                unique_relations[key] = rel

        # 保存到数据库
        entity_count = 0
        relation_count = 0

        with get_db() as conn:
            cursor = conn.cursor()

            # 保存实体
            entity_id_map: Dict[str, str] = {}
            for name, entity in unique_entities.items():
                entity_id = str(uuid.uuid4())
                entity_id_map[name] = entity_id

                cursor.execute(
                    """INSERT OR REPLACE INTO entities (id, name, type, attributes, doc_id)
                       VALUES (?, ?, ?, ?, ?)""",
                    (entity_id, name, entity["type"],
                     json.dumps(entity.get("attributes", {})), doc_id)
                )
                entity_count += 1

            # 保存关系
            for relation in unique_relations.values():
                source_id = entity_id_map.get(relation["source"])
                target_id = entity_id_map.get(relation["target"])

                if source_id and target_id:
                    relation_id = str(uuid.uuid4())
                    cursor.execute(
                        """INSERT OR REPLACE INTO relations (id, source_id, target_id, relation, weight, doc_id)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (relation_id, source_id, target_id,
                         relation["relation"], relation.get("weight", 1.0), doc_id)
                    )
                    relation_count += 1

            # 更新文档统计
            cursor.execute(
                """UPDATE documents SET entities_count = ?, relations_count = ?
                   WHERE id = ?""",
                (entity_count, relation_count, doc_id)
            )

            conn.commit()

        # 更新内存图
        self.load_from_db()

        return entity_count, relation_count

    def load_from_db(self):
        """从数据库加载图谱到内存"""
        # 清空现有图
        self.graph.clear()

        # 加载实体
        entities = execute_query("SELECT id, name, type, attributes FROM entities")
        for entity in entities:
            self.graph.add_node(
                entity[1],  # 使用名称作为节点ID
                id=entity[0],
                type=entity[2],
                attributes=json.loads(entity[3]) if entity[3] else {}
            )

        # 加载关系
        relations = execute_query(
            """SELECT e1.name, e2.name, r.relation, r.weight
               FROM relations r
               JOIN entities e1 ON r.source_id = e1.id
               JOIN entities e2 ON r.target_id = e2.id"""
        )
        for rel in relations:
            if rel[0] in self.graph and rel[1] in self.graph:
                self.graph.add_edge(
                    rel[0],
                    rel[1],
                    relation=rel[2],
                    weight=rel[3]
                )

        print(f"✅ 加载知识图谱: {self.graph.number_of_nodes()} 节点, {self.graph.number_of_edges()} 边")

    def get_entity_neighbors(self, entity_name: str, hops: int = 2) -> List[str]:
        """获取实体的邻居节点"""
        if entity_name not in self.graph:
            return []

        expanded = set()
        frontier = {entity_name}

        for _ in range(hops):
            new_frontier = set()
            for entity in frontier:
                # 获取出边邻居
                neighbors = set(self.graph.successors(entity))
                # 获取入边邻居
                neighbors.update(self.graph.predecessors(entity))
                new_frontier.update(neighbors - expanded - {entity_name})
            expanded.update(new_frontier)
            frontier = new_frontier

        return list(expanded)
