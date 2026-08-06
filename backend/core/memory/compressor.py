"""
上下文摘要压缩器

参考主流做法：
- Claude Code：到阈值把老消息压成固定结构的摘要，保留最近若干轮原文作为锚点
- LangChain ConversationSummaryBufferMemory：摘要 + buffer 双轨
- MemGPT：递归摘要——旧摘要与新增消息一起送进下一次摘要，摘要不会无限增长

本实现 = 结构化 JSON 摘要 + 锚点原文 + 递归叠加。
任何一步失败都降级为截断拼接，绝不让主流程报错。
"""
import json
from typing import Any, Dict, List, Optional, Tuple

from ...config import settings
from ...database import execute_update
from .token_counter import get_token_counter


SUMMARIZE_PROMPT = """你是金融投研助手的会话摘要员。请把下面的内容合并成一份**结构化摘要**，
它将替代原始对话作为后续回答的上下文，因此信息不能丢。

要求：
1. 中文，信息密度高，不要客套话
2. 必须保留：数字、金额、比例、日期、股票/基金代码、专有名词、用户的明确结论
3. 禁止编造原文没有的信息；不确定的内容不要写
4. 如果输入里已有"累积摘要"，把它和新增对话**融合**成一份新摘要，不要简单堆叠
5. 总长度控制在 {max_tokens} tokens 以内

严格只输出如下 JSON，不要任何解释文字：
{{
  "user_intent": "用户到目前为止的核心意图（1-2 句）",
  "confirmed_facts": ["已被确认的事实，含具体数字/代码/日期"],
  "key_data": {{"指标或名称": "数值或区间"}},
  "open_questions": ["用户提出但尚未解答的问题"],
  "rejected_options": ["用户明确否定或排除的方案"],
  "pending_todos": ["助手承诺但尚未完成的事项"],
  "context_anchors": ["后续对话必须理解的实体、简称、代号"]
}}

=== 待摘要内容 ===
{content}
"""


class ConversationCompressor:
    """会话压缩器：判定阈值 → 切分锚点 → 生成叠加摘要"""

    def __init__(self, llm=None):
        self._llm = llm
        self.token_counter = get_token_counter()

    def _get_llm(self):
        if self._llm is None:
            from ..llm.base import get_active_llm

            self._llm = get_active_llm()
        return self._llm

    # ---------------- 阈值判定 ----------------

    def estimate_tokens(
        self,
        messages: List[Dict[str, Any]],
        summary: Optional[str] = None,
        extra_texts: Optional[List[str]] = None,
    ) -> int:
        """估算一次请求会占用的上下文 token（历史 + 摘要 + 其他注入块）"""
        total = self.token_counter.count_messages(messages)
        total += self.token_counter.count(summary)
        for text in extra_texts or []:
            total += self.token_counter.count(text)
        return total

    def should_compress(self, total_tokens: int) -> bool:
        return self.token_counter.should_compress(total_tokens)

    def must_compress(self, total_tokens: int) -> bool:
        return self.token_counter.must_compress(total_tokens)

    # ---------------- 压缩 ----------------

    async def maybe_compress(
        self,
        session_id: str,
        context: List[Dict[str, Any]],
        existing_summary: Optional[str] = None,
        total_tokens: Optional[int] = None,
        force: bool = False,
        trigger: str = "background",
    ) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
        """尝试压缩。

        返回 (锚点消息, 新摘要, 是否发生了压缩)。
        未触发或无法压缩时原样返回入参。
        """
        if total_tokens is None:
            total_tokens = self.estimate_tokens(context, existing_summary)

        if not force and not self.should_compress(total_tokens):
            return context, existing_summary, False

        anchor_n = max(0, settings.COMPRESSION_ANCHOR_RECENT_TURNS) * 2
        # 待压缩段至少要有一轮，否则压了也没收益
        if len(context) <= anchor_n + 2:
            return context, existing_summary, False

        to_compress = context[:-anchor_n] if anchor_n else list(context)
        anchor = context[-anchor_n:] if anchor_n else []

        new_summary = await self._summarize(existing_summary, to_compress)

        post_tokens = self.estimate_tokens(anchor, new_summary)
        self._record_event(
            session_id, total_tokens, post_tokens, len(to_compress) // 2, trigger
        )
        ratio = self.token_counter.usage_ratio(total_tokens)
        print(
            f"🗜️ 会话 {session_id} 触发压缩（{trigger}，占用 {ratio:.0%}）："
            f"{total_tokens} → {post_tokens} tokens，压缩 {len(to_compress)} 条消息"
        )
        return anchor, new_summary, True

    async def _summarize(
        self, existing_summary: Optional[str], turns: List[Dict[str, Any]]
    ) -> str:
        """调用 LLM 生成融合后的新摘要；失败降级为截断拼接"""
        content = self._format_input(existing_summary, turns)
        try:
            llm = self._get_llm()
            resp = await llm.chat(
                [
                    {
                        "role": "user",
                        "content": SUMMARIZE_PROMPT.format(
                            max_tokens=settings.COMPRESSION_SUMMARY_MAX_TOKENS,
                            content=content,
                        ),
                    }
                ],
                temperature=settings.COMPRESSION_LLM_TEMPERATURE,
                max_tokens=settings.COMPRESSION_SUMMARY_MAX_TOKENS + 500,
            )
            summary = self._extract_json_block(resp)
            if summary:
                return summary
            print("⚠️ 摘要未返回合法 JSON，使用原始文本")
            return resp.strip()[: settings.COMPRESSION_SUMMARY_MAX_TOKENS * 3]
        except Exception as e:
            print(f"⚠️ 摘要生成失败，降级为截断拼接: {e}")
            return "[摘要降级-以下为历史原文截断]\n" + content[:1500]

    @staticmethod
    def _extract_json_block(resp: str) -> Optional[str]:
        """容错提取最外层 JSON 对象，校验可解析后再返回"""
        if not resp:
            return None
        start, end = resp.find("{"), resp.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        block = resp[start:end]
        try:
            json.loads(block)
        except (ValueError, TypeError):
            return None
        return block

    @staticmethod
    def _format_input(
        existing_summary: Optional[str], turns: List[Dict[str, Any]]
    ) -> str:
        parts = []
        if existing_summary:
            parts.append("【此前的累积摘要】\n" + existing_summary)
        lines = []
        for t in turns:
            role = {"user": "用户", "assistant": "助手"}.get(
                t.get("role", ""), t.get("role", "")
            )
            lines.append(f"{role}: {t.get('content', '')}")
        parts.append("【本次新增待压缩的对话】\n" + "\n".join(lines))
        return "\n\n".join(parts)

    @staticmethod
    def _record_event(
        session_id: str,
        pre_tokens: int,
        post_tokens: int,
        compressed_turns: int,
        trigger: str,
    ):
        try:
            execute_update(
                """INSERT INTO compression_events
                   (session_id, pre_tokens, post_tokens, compressed_turns, trigger)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, pre_tokens, post_tokens, compressed_turns, trigger),
            )
        except Exception as e:
            print(f"⚠️ 压缩事件记录失败: {e}")

    # ---------------- 渲染 ----------------

    @staticmethod
    def render_summary(summary: Optional[str]) -> str:
        """把结构化 JSON 摘要渲染成给 LLM 读的自然文本"""
        if not summary:
            return ""
        try:
            data = json.loads(summary)
        except (ValueError, TypeError):
            return summary  # 降级摘要就是纯文本，直接用
        if not isinstance(data, dict):
            return summary

        labels = [
            ("user_intent", "核心意图"),
            ("confirmed_facts", "已确认事实"),
            ("key_data", "关键数据"),
            ("open_questions", "尚未解答的问题"),
            ("rejected_options", "用户已排除的方案"),
            ("pending_todos", "待办事项"),
            ("context_anchors", "上下文锚点"),
        ]
        lines = []
        for key, label in labels:
            value = data.get(key)
            if not value:
                continue
            if isinstance(value, dict):
                body = "；".join(f"{k}={v}" for k, v in value.items())
            elif isinstance(value, list):
                body = "；".join(str(v) for v in value)
            else:
                body = str(value)
            lines.append(f"- {label}：{body}")
        return "\n".join(lines) if lines else summary
