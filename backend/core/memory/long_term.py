"""
长期记忆模块 - LLM 抽取的结构化用户事实（mem0 风格）

原实现是 11 个硬编码金融关键词计数 + 两个空函数，且无人调用。现在：
- 每轮对话后（后台异步）用 LLM 抽取值得长期记住的事实
- 写入前用 embedding 余弦相似度去重：高度相似则合并（更新文本 + 取更高置信度），
  否则新增，避免"我偏好稳健投资"被重复记 100 遍
- 召回时全表扫 embedding_blob 算相似度（长期事实是几十~几百条量级，SQLite 够用，
  没必要再开一个 Qdrant collection）
"""
import json
import math
import uuid
from typing import Any, Dict, List, Optional

from ...config import settings
from ...database import execute_query, execute_update


EXTRACT_PROMPT = """你是金融投研助手的记忆抽取器。从下面这轮对话中抽取**值得长期记住**的事实。

只抽取以下三类，其余一律忽略：
- preference：用户稳定的偏好（投资风格、风险偏好、长期关注的行业/标的、信息偏好）
- fact：已经确认的关键事实（持仓、成本价、决策结论、约束条件）
- identity：用户身份信息（自述的职业、资金量级、投资年限）

严格要求：
1. 只抽取用户**明确表达**的内容，禁止推测或编造
2. 一次性的临时问题（如"今天大盘怎么样"）不是长期记忆，不要抽
3. 每条 fact 写成独立完整的一句话，包含具体数字/代码/日期
4. 抽不到任何内容时返回空数组 []

只输出 JSON 数组，不要任何解释文字：
[{{"fact": "...", "category": "preference|fact|identity", "confidence": 0.0~1.0}}]

=== 对话 ===
用户：{user_msg}
助手：{ai_msg}
"""

VALID_CATEGORIES = {"preference", "fact", "identity"}


def _cosine(a: List[float], b: List[float]) -> float:
    """余弦相似度（纯 Python，避免为几十条数据引入 numpy 转换开销）"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class LongTermMemory:
    """长期记忆：结构化用户事实"""

    def __init__(self, embedding_service=None, llm=None):
        self._embedding_service = embedding_service
        self._llm = llm

    def _get_embedding_service(self):
        if self._embedding_service is None:
            from ..embedding.embedding_service import EmbeddingService

            self._embedding_service = EmbeddingService()
        return self._embedding_service

    def _get_llm(self):
        if self._llm is None:
            from ..llm.base import get_active_llm

            self._llm = get_active_llm()
        return self._llm

    async def init(self):
        """无外部资源需要初始化"""
        return

    # ---------------- 抽取与写入 ----------------

    async def extract_and_store(self, session_id: str, user_msg: str, ai_msg: str):
        """LLM 抽取事实并入库。全程 fail-soft，绝不影响主流程。"""
        if not settings.MEMORY_ENABLE_LONG_TERM_EXTRACT:
            return []
        try:
            llm = self._get_llm()
            prompt = EXTRACT_PROMPT.format(
                user_msg=user_msg[:1500], ai_msg=ai_msg[:2000]
            )
            resp = await llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=600,
            )
            facts = self._parse_facts(resp)
        except Exception as e:
            print(f"⚠️ 长期记忆抽取失败: {e}")
            return []

        stored = []
        for fact in facts:
            try:
                await self._add_fact_with_dedup(session_id, fact)
                stored.append(fact)
            except Exception as e:
                print(f"⚠️ 长期事实写入失败: {e}")
        if stored:
            print(f"🧠 长期记忆新增/更新 {len(stored)} 条事实")
        return stored

    @staticmethod
    def _parse_facts(resp: str) -> List[Dict[str, Any]]:
        """容错解析 LLM 返回的 JSON 数组"""
        if not resp:
            return []
        start, end = resp.find("["), resp.rfind("]") + 1
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(resp[start:end])
        except (ValueError, TypeError):
            return []
        if not isinstance(data, list):
            return []

        cleaned = []
        for item in data:
            if not isinstance(item, dict):
                continue
            text = (item.get("fact") or "").strip()
            if not text:
                continue
            category = (item.get("category") or "fact").strip()
            if category not in VALID_CATEGORIES:
                category = "fact"
            try:
                confidence = float(item.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            cleaned.append(
                {
                    "fact": text[:500],
                    "category": category,
                    "confidence": min(max(confidence, 0.0), 1.0),
                }
            )
        return cleaned

    async def _add_fact_with_dedup(self, session_id: str, fact: Dict[str, Any]):
        """同 category 内做相似度去重：高度相似则合并，否则新增"""
        embedding = None
        try:
            embedding = await self._get_embedding_service().embed(fact["fact"])
        except Exception as e:
            print(f"⚠️ 长期事实向量化失败，跳过去重直接入库: {e}")

        if embedding:
            # 直接读已存的 embedding_blob，不重新 embed 老数据
            rows = execute_query(
                """SELECT id, confidence, embedding_blob
                   FROM long_term_facts
                   WHERE category = ? AND embedding_blob IS NOT NULL""",
                (fact["category"],),
            )
            for row_id, old_conf, blob in rows:
                try:
                    old_emb = json.loads(blob)
                except (ValueError, TypeError):
                    continue
                if _cosine(embedding, old_emb) >= settings.MEMORY_LONG_TERM_DEDUP_THRESHOLD:
                    execute_update(
                        """UPDATE long_term_facts
                           SET fact = ?, confidence = ?, embedding_blob = ?,
                               updated_at = CURRENT_TIMESTAMP
                           WHERE id = ?""",
                        (
                            fact["fact"],
                            max(old_conf or 0.0, fact["confidence"]),
                            json.dumps(embedding),
                            row_id,
                        ),
                    )
                    return

        execute_update(
            """INSERT INTO long_term_facts
               (id, session_id, fact, category, confidence, embedding_blob)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                session_id,
                fact["fact"],
                fact["category"],
                fact["confidence"],
                json.dumps(embedding) if embedding else None,
            ),
        )

    # ---------------- 召回 ----------------

    async def search(
        self, query: str, top_k: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """按语义相似度召回长期事实"""
        top_k = top_k or settings.MEMORY_LONG_TERM_TOP_K
        if not query:
            return []
        try:
            rows = execute_query(
                """SELECT fact, category, confidence, embedding_blob
                   FROM long_term_facts
                   ORDER BY updated_at DESC"""
            )
        except Exception as e:
            print(f"⚠️ 长期记忆读取失败: {e}")
            return []
        if not rows:
            return []

        try:
            q_emb = await self._get_embedding_service().embed(query)
        except Exception as e:
            print(f"⚠️ 长期记忆查询向量化失败，回退最近 N 条: {e}")
            return [
                {"fact": r[0], "category": r[1], "confidence": r[2], "score": 0.0}
                for r in rows[:top_k]
            ]

        scored = []
        for fact_text, category, confidence, blob in rows:
            if not blob:
                continue
            try:
                emb = json.loads(blob)
            except (ValueError, TypeError):
                continue
            score = _cosine(q_emb, emb)
            if score >= settings.MEMORY_LONG_TERM_SCORE_THRESHOLD:
                scored.append(
                    {
                        "fact": fact_text,
                        "category": category,
                        "confidence": confidence,
                        "score": score,
                    }
                )
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    # ---------------- 画像（保留原有关键词统计作为附加信号） ----------------

    async def get_user_profile(self) -> Dict[str, Any]:
        """关注领域的关键词频次统计。零成本，作为抽取事实之外的补充信号。"""
        try:
            rows = execute_query(
                """SELECT content FROM chat_history
                   WHERE role = 'user'
                   ORDER BY created_at DESC
                   LIMIT 100"""
            )
        except Exception:
            return {"focus_areas": [], "total_queries": 0}

        keywords: Dict[str, int] = {}
        for row in rows:
            content = row[0] or ""
            for word in [
                "股票", "基金", "债券", "期货", "外汇", "黄金",
                "科技", "金融", "消费", "医疗", "新能源",
            ]:
                if word in content:
                    keywords[word] = keywords.get(word, 0) + 1

        return {
            "focus_areas": sorted(keywords, key=keywords.get, reverse=True)[:5],
            "total_queries": len(rows),
        }

    async def list_facts(
        self, category: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """列出已记住的事实（供管理/调试接口使用）"""
        try:
            if category:
                rows = execute_query(
                    """SELECT fact, category, confidence, updated_at
                       FROM long_term_facts WHERE category = ?
                       ORDER BY updated_at DESC LIMIT ?""",
                    (category, limit),
                )
            else:
                rows = execute_query(
                    """SELECT fact, category, confidence, updated_at
                       FROM long_term_facts
                       ORDER BY updated_at DESC LIMIT ?""",
                    (limit,),
                )
        except Exception as e:
            print(f"⚠️ 长期记忆列表读取失败: {e}")
            return []
        return [
            {"fact": r[0], "category": r[1], "confidence": r[2], "updated_at": r[3]}
            for r in rows
        ]
