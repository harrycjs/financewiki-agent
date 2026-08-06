"""
响应生成器
"""
from typing import List, Dict, Any, Optional

from ..llm.base import BaseLLM
from ..memory.compressor import ConversationCompressor
from ...config import settings
from .skill_resolver import get_resolver


# 基础 system prompt（提取引出常量，便于 _build_system_prompt 复用）
BASE_SYSTEM_PROMPT = """你是一个专业的金融投研助手。请基于提供的文档内容回答用户的问题。

回答要求：
1. 准确、专业、简洁
2. 如果文档中有相关信息，请引用具体来源
3. 如果文档中没有相关信息，请说明并提供一般性建议
4. 使用Markdown格式组织回答"""


class ResponseGenerator:
    """响应生成器"""

    def __init__(self):
        self.llm = None

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
                self.llm = BaseLLM.create(
                    "deepseek",
                    settings.DEEPSEEK_API_KEY,
                    settings.DEEPSEEK_API_BASE
                )
        return self.llm

    def _build_system_prompt(self, query: str) -> str:
        """构建 system prompt（含 Skills 渐进式披露注入）

        三阶段：
        1. 始终注入 BASE_SYSTEM_PROMPT
        2. 注入"可用技能索引"（所有 enabled 技能的 name+description）
        3. 关键词预筛后注入相关技能的完整 instructions
        """
        resolver = get_resolver()
        index_text = resolver.get_active_index()
        selected_names = resolver.select_relevant_skills(query)
        full_text = resolver.get_full_instructions(selected_names)

        skills_block = ""
        if index_text:
            skills_block += f"\n\n## 可用技能索引\n{index_text}"
            # 提示 AI 可以主动声明使用某个技能
            skills_block += "\n\n（当你判断需要使用某个技能时，请遵循其完整指令）"
        if full_text:
            skills_block += f"\n\n## 当前已加载技能\n{full_text}"

        return BASE_SYSTEM_PROMPT + skills_block

    async def generate(
        self,
        query: str,
        context: Any,
        documents: List[Dict[str, Any]]
    ) -> str:
        """生成回答

        context 可以是 MemoryManager.assemble_context() 返回的 dict（推荐），
        也可以是旧的消息列表（向后兼容）。
        """
        llm = self._get_llm()
        messages = self._build_messages(query, context, documents)
        response = await llm.chat(messages)
        return response

    async def generate_stream(
        self,
        query: str,
        context: Any,
        documents: List[Dict[str, Any]]
    ):
        """流式生成回答"""
        llm = self._get_llm()
        messages = self._build_messages(query, context, documents)

        async for chunk in llm.stream_chat(messages):
            yield chunk

    def _build_messages(
        self,
        query: str,
        context: Any,
        documents: List[Dict[str, Any]]
    ) -> List[Dict[str, str]]:
        """组装最终发给 LLM 的 messages"""
        block = self._normalize_context(context)

        doc_context = self._build_document_context(documents)
        mid_context = self._build_mid_term_context(block.get("mid_term_hits") or [])
        long_context = self._build_long_term_context(block.get("long_term_hits") or [])
        history_context = self._build_history_context(
            block.get("short_term_messages") or [],
            block.get("short_term_summary"),
        )
        system_prompt = self._build_system_prompt(query)

        user_prompt = f"""用户问题：{query}

## 相关文档
{doc_context}

## 相关历史问答（来自其他会话，仅作参考）
{mid_context}

## 长期记忆（用户偏好与已确认事实）
{long_context}

## 本次会话上下文
{history_context}

请基于以上信息回答用户问题。"""

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

    @staticmethod
    def _normalize_context(context: Any) -> Dict[str, Any]:
        """兼容 dict（新）与 list（旧）两种入参"""
        if isinstance(context, dict):
            return context
        return {"short_term_messages": context or []}

    def _build_document_context(self, documents: List[Dict[str, Any]]) -> str:
        """构建文档上下文"""
        if not documents:
            return "暂无相关文档"

        context_parts = []
        for i, doc in enumerate(documents[:5], 1):  # 最多使用5个文档
            content = doc.get("content", "")
            source = doc.get("source", "未知")
            score = doc.get("score", 0)

            context_parts.append(
                f"[文档{i}] (来源: {source}, 相关度: {score:.2f})\n{content[:500]}"
            )

        return "\n\n".join(context_parts)

    @staticmethod
    def _build_mid_term_context(hits: List[Dict[str, Any]]) -> str:
        """构建中期记忆（跨会话相似问答）上下文"""
        if not hits:
            return "暂无相关历史问答"

        limit = settings.MEMORY_MID_TERM_SNIPPET_CHARS
        parts = []
        for i, h in enumerate(hits, 1):
            user_msg = (h.get("user_msg") or "").strip()
            ai_msg = (h.get("ai_msg") or "").strip()
            score = h.get("score", 0) or 0
            parts.append(
                f"[历史{i}] (相似度: {score:.2f})\n"
                f"问: {user_msg[:limit]}\n答: {ai_msg[:limit]}"
            )
        return "\n\n".join(parts)

    @staticmethod
    def _build_long_term_context(hits: List[Dict[str, Any]]) -> str:
        """构建长期记忆（结构化事实）上下文"""
        if not hits:
            return "暂无长期记忆"

        limit = settings.MEMORY_LONG_TERM_SNIPPET_CHARS
        label = {"preference": "偏好", "fact": "事实", "identity": "身份"}
        parts = []
        for h in hits:
            fact = (h.get("fact") or "").strip()
            if not fact:
                continue
            category = label.get(h.get("category"), h.get("category") or "事实")
            parts.append(f"- [{category}] {fact[:limit]}")
        return "\n".join(parts) if parts else "暂无长期记忆"

    def _build_history_context(
        self,
        context: List[Dict[str, Any]],
        summary: Optional[str] = None
    ) -> str:
        """构建本次会话上下文：累积摘要 + 锚点原文

        锚点原文不再截断——超出预算的部分已经由压缩器压进摘要里了。
        """
        parts = []

        rendered_summary = ConversationCompressor.render_summary(summary)
        if rendered_summary:
            parts.append(f"【此前对话摘要】\n{rendered_summary}")

        anchor_limit = settings.MEMORY_SHORT_TERM_ANCHOR_RENDER
        messages = context[-anchor_limit:] if anchor_limit and anchor_limit > 0 else context

        history_parts = []
        for item in messages:
            role = item.get("role", "")
            content = item.get("content", "")
            if not content:
                continue
            if role == "user":
                history_parts.append(f"用户: {content}")
            elif role == "assistant":
                history_parts.append(f"助手: {content}")

        if history_parts:
            parts.append("【最近对话原文】\n" + "\n".join(history_parts))

        return "\n\n".join(parts) if parts else "暂无对话历史"
