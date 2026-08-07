"""
知识图谱接口

设计要点：
- DB 保持"按 doc_id 增量存储"：同名实体可有多行（每文档一行），不删除旧记录
- API 层做"全局合并视图"：同名实体聚合为一个节点，列出其来源 doc_ids
- 默认无 limit（最多取 MAX_LIMIT），避免"新文档把旧文档顶出去"
- 支持 doc_id 参数：只返回某文档内的实体/关系（按文档视图）
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict, Any
import json

from ..database import execute_query

router = APIRouter(prefix="/api/knowledge-graph", tags=["knowledge-graph"])

MAX_LIMIT = 5000  # 硬上限，防止一次性把全库拖死
CORE_LIMIT = 200   # 渐进披露：默认仅返回度数最高的 200 个核心节点，其余按需展开


def _merge_attributes(attr_list: List[dict]) -> dict:
    """合并多份 attributes：后出现的覆盖先出现的（属性键名相同时取后者）"""
    merged: Dict[str, Any] = {}
    for a in attr_list:
        if isinstance(a, dict):
            merged.update(a)
    return merged


def _node_id(name: str, type_: str) -> str:
    """节点唯一 ID：同名不同型拆开（前端 vis-data 不允许重复 id）"""
    return f"{name}|{type_}"


@router.get("")
async def get_knowledge_graph(
    limit: int = Query(CORE_LIMIT, ge=1, le=MAX_LIMIT),
    doc_id: Optional[str] = Query(None, description="按文档过滤；不传则返回全局合并视图"),
):
    """获取知识图谱概览

    全局模式（默认）：
    - 节点按 (name, type) 聚合；同名同类型 = 一个节点，doc_ids 列出所有来源
    - 边按 (source_name, target_name, relation) 聚合
    - 跨文档共享的实体/关系会自然合并，知识图谱是真正"叠加增长"的

    按文档模式（doc_id 传入）：
    - 返回该文档内提取出的实体/关系（不聚合），便于排查单文档
    """
    if doc_id:
        # —— 按文档视图：不过滤名称，直接返回该 doc 的所有行 ——
        entities = execute_query(
            """SELECT id, name, type, attributes, doc_id
               FROM entities
               WHERE doc_id = ?
               ORDER BY name""",
            (doc_id,)
        )
        relations = execute_query(
            """SELECT r.id, r.source_id, r.target_id, r.relation, r.weight,
                      e1.name as source_name, e1.type as source_type,
                      e2.name as target_name, e2.type as target_type
               FROM relations r
               JOIN entities e1 ON r.source_id = e1.id
               JOIN entities e2 ON r.target_id = e2.id
               WHERE r.doc_id = ?
               ORDER BY r.weight DESC""",
            (doc_id,)
        )
        nodes = [
            {
                "id": e[0],
                "name": e[1],
                "type": e[2],
                "attributes": json.loads(e[3]) if e[3] else {},
                "doc_id": e[4],
                "doc_ids": [e[4]] if e[4] else [],
            }
            for e in entities
        ]
        edges = [
            {
                "id": r[0],
                "source": r[1],
                "target": r[2],
                "relation": r[3],
                "weight": r[4],
                "source_name": r[5],
                "source_type": r[6],
                "target_name": r[7],
                "target_type": r[8],
                "doc_id": doc_id,
                "doc_ids": [doc_id],
            }
            for r in relations
        ]
        return {"nodes": nodes, "edges": edges, "scope": "document", "doc_id": doc_id}

    # —— 全局视图：按 name/type 聚合 ——
    # 渐进披露：默认仅取度数最高的 limit 个核心实体，避免一次返回几千节点把前端拖死
    raw_entities = execute_query(
        """SELECT id, name, type, attributes, doc_id,
                  (SELECT COUNT(*) FROM relations WHERE source_id = entities.id) +
                  (SELECT COUNT(*) FROM relations WHERE target_id = entities.id) AS degree
           FROM entities
           ORDER BY degree DESC, name
           LIMIT ?""",
        (limit,)
    )
    core_keys = {(e[1], e[2]) for e in raw_entities}  # 用于过滤边：只保留两端都在核心集里的边

    # 关系：先在 SQL 层按 (源实体名, 目标实体名, 关系类型, doc_id) 去重，
    # 避免因"每文档一个实体行"造成 JOIN 交叉乘 → occurrences 虚高
    raw_relations = execute_query(
        """SELECT source_name, source_type, target_name, target_type,
                  relation, MAX(weight) AS weight, doc_id
           FROM (
               SELECT e1.name AS source_name, e1.type AS source_type,
                      e2.name AS target_name, e2.type AS target_type,
                      r.relation, r.weight, r.doc_id
               FROM relations r
               JOIN entities e1 ON r.source_id = e1.id
               JOIN entities e2 ON r.target_id = e2.id
               WHERE e1.name || '|' || e1.type IN ({ph}) AND e2.name || '|' || e2.type IN ({ph})
           )
           GROUP BY source_name, target_name, relation, doc_id
           ORDER BY weight DESC
           LIMIT ?""".format(ph=", ".join("?" for _ in core_keys)),
        (*[f"{k[0]}|{k[1]}" for k in core_keys], *[f"{k[0]}|{k[1]}" for k in core_keys], limit * 2)
    ) if core_keys else []

    # 节点聚合：key = (name, type)；节点 id 必须全局唯一（同名不同型要拆开），
    # 用 "name|type" 编码即可与边的 source/target 对齐
    node_map: Dict[tuple, Dict[str, Any]] = {}
    for e in raw_entities:
        key = (e[1], e[2])
        attr = json.loads(e[3]) if e[3] else {}
        if key not in node_map:
            node_map[key] = {
                "id": _node_id(e[1], e[2]),
                "name": e[1],
                "type": e[2],
                "attributes": attr,
                "doc_id": e[4],
                "doc_ids": [],
                "occurrences": 0,
                "expanded": False,
                "degree": e[5],
            }
        node = node_map[key]
        node["occurrences"] += 1
        if e[4] and e[4] not in node["doc_ids"]:
            node["doc_ids"].append(e[4])
        # 合并 attributes（后写覆盖前写）
        node["attributes"] = _merge_attributes([node["attributes"], attr])

    # 边聚合：key = (source_name+type, target_name+type, relation)；source/target
    # 编码成与节点 id 完全相同的 "name|type"，vis-network 才能对齐两端
    edge_map: Dict[tuple, Dict[str, Any]] = {}
    for r in raw_relations:
        key = (r[0], r[1], r[2], r[3], r[4])  # source_name, source_type, target_name, target_type, relation
        if key not in edge_map:
            src_id = _node_id(r[0], r[1])
            tgt_id = _node_id(r[2], r[3])
            edge_map[key] = {
                "id": f"agg-{r[0]}-{r[2]}-{r[4]}",
                "source": src_id,
                "target": tgt_id,
                "source_name": r[0],
                "source_type": r[1],
                "target_name": r[2],
                "target_type": r[3],
                "relation": r[4],
                "weight": float(r[5]),
                "doc_ids": [],
                "occurrences": 0,
            }
        edge = edge_map[key]
        edge["occurrences"] += 1
        edge["weight"] = max(edge["weight"], float(r[5]))  # 取最高权重
        if r[6] and r[6] not in edge["doc_ids"]:
            edge["doc_ids"].append(r[6])

    # 总数（用于前端告知用户"还有 N 个节点可展开"）
    total_entities_row = execute_query("SELECT COUNT(*) FROM entities")
    total_entities = total_entities_row[0][0] if total_entities_row else 0
    total_relations_row = execute_query("SELECT COUNT(*) FROM relations")
    total_relations = total_relations_row[0][0] if total_relations_row else 0

    return {
        "nodes": list(node_map.values()),
        "edges": list(edge_map.values()),
        "scope": "core",
        "core_limit": limit,
        "stats": {
            "core_nodes": len(node_map),
            "core_edges": len(edge_map),
            "total_entities": total_entities,
            "total_relations": total_relations,
            "hidden_entities": max(0, total_entities - len(node_map)),
        },
    }


@router.get("/expand/{name}")
async def expand_neighbors(name: str):
    """渐进披露：点击节点时拉取该实体的一阶邻接子图。

    返回：
    - 目标实体本身（同名多型则全部返回）
    - 一阶邻居实体（通过任一关系与之相连的实体）
    - 这些实体之间的所有边

    设计：每个实体行带 `expanded: true` 标记，便于前端区分"初始核心"与"按需展开"。
    """
    # 1. 找到该 name 的所有实体行（同名可能多 type）
    seed_rows = execute_query(
        """SELECT id, name, type, attributes, doc_id
           FROM entities WHERE name = ?""",
        (name,)
    )
    if not seed_rows:
        seed_rows = execute_query(
            """SELECT id, name, type, attributes, doc_id
               FROM entities WHERE name LIKE ? LIMIT 10""",
            (f"%{name}%",)
        )
    if not seed_rows:
        raise HTTPException(status_code=404, detail="实体不存在")

    seed_ids = [r[0] for r in seed_rows]

    # 2. 邻居：与 seed 任一 id 通过关系相连的实体（去重）
    placeholders = ", ".join("?" for _ in seed_ids)
    neighbor_rows = execute_query(
        f"""SELECT DISTINCT e.id, e.name, e.type, e.attributes, e.doc_id
            FROM entities e
            WHERE e.id IN (
                SELECT CASE WHEN r.source_id IN ({placeholders}) THEN r.target_id ELSE r.source_id END
                FROM relations r
                WHERE r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders})
            )""",
        (*seed_ids, *seed_ids, *seed_ids)
    )

    # 3. 合并 seed + neighbor，标记 expanded
    all_entity_rows = {r[0]: r for r in (*seed_rows, *neighbor_rows)}
    nodes = []
    for eid, e in all_entity_rows.items():
        nodes.append({
            "id": _node_id(e[1], e[2]),
            "name": e[1],
            "type": e[2],
            "attributes": json.loads(e[3]) if e[3] else {},
            "doc_id": e[4],
            "doc_ids": [e[4]] if e[4] else [],
            "expanded": eid in set(seed_ids),
        })

    # 4. 边：所有两端都在 {seed ∪ neighbor} 集合内的关系
    all_ids = list(all_entity_rows.keys())
    placeholders2 = ", ".join("?" for _ in all_ids)
    edge_rows = execute_query(
        f"""SELECT e1.name AS source_name, e1.type AS source_type,
                   e2.name AS target_name, e2.type AS target_type,
                   r.relation, MAX(r.weight) AS weight
            FROM relations r
            JOIN entities e1 ON r.source_id = e1.id
            JOIN entities e2 ON r.target_id = e2.id
            WHERE r.source_id IN ({placeholders2}) AND r.target_id IN ({placeholders2})
            GROUP BY source_name, source_type, target_name, target_type, relation""",
        (*all_ids, *all_ids)
    ) if all_ids else []

    edges = []
    seen = set()
    for r in edge_rows:
        key = (r[0], r[2], r[4])
        if key in seen:
            continue
        seen.add(key)
        edges.append({
            "id": f"exp-{r[0]}-{r[2]}-{r[4]}",
            "source": _node_id(r[0], r[1]),
            "target": _node_id(r[2], r[3]),
            "source_name": r[0],
            "source_type": r[1],
            "target_name": r[2],
            "target_type": r[3],
            "relation": r[4],
            "weight": float(r[5]),
        })

    return {
        "seed": name,
        "nodes": nodes,
        "edges": edges,
        "scope": "expand",
    }


@router.get("/entity/{name}")
async def get_entity_detail(name: str):
    """获取实体详情及关联"""
    # 查找实体
    entities = execute_query(
        """SELECT id, name, type, attributes, doc_id
           FROM entities
           WHERE name = ?""",
        (name,)
    )

    if not entities:
        # 尝试模糊匹配
        entities = execute_query(
            """SELECT id, name, type, attributes, doc_id
               FROM entities
               WHERE name LIKE ?""",
            (f"%{name}%",)
        )

    if not entities:
        raise HTTPException(status_code=404, detail="实体不存在")

    entity = entities[0]

    # 获取关联关系
    relations = execute_query(
        """SELECT r.id, r.source_id, r.target_id, r.relation, r.weight,
                  CASE WHEN r.source_id = ? THEN e2.name ELSE e1.name END as related_name,
                  CASE WHEN r.source_id = ? THEN e2.type ELSE e1.type END as related_type,
                  CASE WHEN r.source_id = ? THEN 'outgoing' ELSE 'incoming' END as direction
           FROM relations r
           JOIN entities e1 ON r.source_id = e1.id
           JOIN entities e2 ON r.target_id = e2.id
           WHERE r.source_id = ? OR r.target_id = ?""",
        (entity[0], entity[0], entity[0], entity[0], entity[0])
    )

    return {
        "entity": {
            "id": entity[0],
            "name": entity[1],
            "type": entity[2],
            "attributes": json.loads(entity[3]) if entity[3] else {},
            "doc_id": entity[4]
        },
        "relations": [
            {
                "id": r[0],
                "relation": r[3],
                "weight": r[4],
                "related_name": r[5],
                "related_type": r[6],
                "direction": r[7]
            }
            for r in relations
        ]
    }


@router.get("/search")
async def search_knowledge_graph(q: str, entity_type: Optional[str] = None):
    """搜索实体和关系"""
    # 搜索实体
    if entity_type:
        entities = execute_query(
            """SELECT id, name, type, attributes
               FROM entities
               WHERE (name LIKE ? OR type LIKE ?) AND type = ?""",
            (f"%{q}%", f"%{q}%", entity_type)
        )
    else:
        entities = execute_query(
            """SELECT id, name, type, attributes
               FROM entities
               WHERE name LIKE ? OR type LIKE ?""",
            (f"%{q}%", f"%{q}%")
        )

    # 搜索关系
    relations = execute_query(
        """SELECT r.id, r.relation, r.weight,
                  e1.name as source_name, e1.type as source_type,
                  e2.name as target_name, e2.type as target_type
           FROM relations r
           JOIN entities e1 ON r.source_id = e1.id
           JOIN entities e2 ON r.target_id = e2.id
           WHERE r.relation LIKE ?
           LIMIT 50""",
        (f"%{q}%",)
    )

    return {
        "entities": [
            {
                "id": e[0],
                "name": e[1],
                "type": e[2],
                "attributes": json.loads(e[3]) if e[3] else {}
            }
            for e in entities
        ],
        "relations": [
            {
                "id": r[0],
                "relation": r[1],
                "weight": r[2],
                "source_name": r[3],
                "source_type": r[4],
                "target_name": r[5],
                "target_type": r[6]
            }
            for r in relations
        ]
    }


@router.get("/stats")
async def get_knowledge_graph_stats():
    """获取图谱统计信息"""
    # 实体统计
    entity_stats = execute_query(
        """SELECT type, COUNT(*) as count
           FROM entities
           GROUP BY type"""
    )

    # 关系统计
    relation_stats = execute_query(
        """SELECT relation, COUNT(*) as count
           FROM relations
           GROUP BY relation
           ORDER BY count DESC
           LIMIT 10"""
    )

    # 总数统计
    total_entities = execute_query("SELECT COUNT(*) FROM entities")[0][0]
    total_relations = execute_query("SELECT COUNT(*) FROM relations")[0][0]
    total_docs = execute_query("SELECT COUNT(*) FROM documents")[0][0]

    return {
        "total_entities": total_entities,
        "total_relations": total_relations,
        "total_documents": total_docs,
        "entity_types": {row[0]: row[1] for row in entity_stats},
        "relation_types": {row[0]: row[1] for row in relation_stats}
    }
