"""
Token 计数器

主路径使用 tiktoken 的 cl100k_base 编码做近似计数（DeepSeek / GLM / Kimi / MiniMax
都没有官方 Python tokenizer，cl100k 对中文的偏差约 10~20%，配合 80% 触发阈值有
足够的安全边际）。tiktoken 不可用时降级为字符启发式，保证进程始终能算出一个数。
"""
from typing import Dict, List, Optional

from ...config import settings


def _is_cjk(ch: str) -> bool:
    """判断是否 CJK 统一表意文字（含扩展 A 区）"""
    code = ord(ch)
    return 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF


class TokenCounter:
    """token 计数与上下文预算判定"""

    # 每条 message 的角色/分隔符固定开销（OpenAI 官方给的经验值）
    PER_MESSAGE_OVERHEAD = 4

    def __init__(self):
        self._enc = None
        self.mode = "heuristic"
        try:
            import tiktoken

            enc = tiktoken.get_encoding("cl100k_base")
            enc.encode("测试 hello")  # 探活，避免运行期才发现编码表缺失
            self._enc = enc
            self.mode = "tiktoken"
        except Exception as e:  # pragma: no cover - 依赖缺失路径
            print(f"⚠️ tiktoken 不可用，token 计数降级为字符启发式: {e}")

    # ---------------- 计数 ----------------

    def count(self, text: Optional[str]) -> int:
        """统计单段文本的 token 数"""
        if not text:
            return 0
        if self._enc is not None:
            try:
                return len(self._enc.encode(text))
            except Exception:
                pass  # 单次编码失败就地降级，不影响后续调用
        return self._heuristic(text)

    @staticmethod
    def _heuristic(text: str) -> int:
        """字符启发式：中文 1 字 ≈ 0.7 token，其他 4 字符 ≈ 1 token"""
        cjk = sum(1 for c in text if _is_cjk(c))
        other = len(text) - cjk
        return int(cjk * 0.7 + other / 4) + 1

    def count_messages(self, messages: List[Dict[str, str]]) -> int:
        """统计 [{role, content}, ...] 形式的消息列表"""
        if not messages:
            return 0
        total = 0
        for m in messages:
            total += self.PER_MESSAGE_OVERHEAD
            total += self.count(m.get("content", ""))
            total += self.count(m.get("role", ""))
        return total

    # ---------------- 预算判定 ----------------

    @property
    def context_window(self) -> int:
        return settings.COMPRESSION_CONTEXT_WINDOW

    @property
    def trigger_tokens(self) -> int:
        """后台主通道触发线（默认 80%）"""
        return int(self.context_window * settings.COMPRESSION_TRIGGER_RATIO)

    @property
    def hard_tokens(self) -> int:
        """请求路径同步安全阀（默认 95%）"""
        return int(self.context_window * settings.COMPRESSION_HARD_RATIO)

    def should_compress(self, total_tokens: int) -> bool:
        return total_tokens >= self.trigger_tokens

    def must_compress(self, total_tokens: int) -> bool:
        return total_tokens >= self.hard_tokens

    def usage_ratio(self, total_tokens: int) -> float:
        if self.context_window <= 0:
            return 0.0
        return total_tokens / self.context_window

    def remaining(self, total_tokens: int) -> int:
        return max(0, self.context_window - total_tokens)


_token_counter: Optional[TokenCounter] = None


def get_token_counter() -> TokenCounter:
    """模块级单例（tiktoken 编码表加载有成本，只做一次）"""
    global _token_counter
    if _token_counter is None:
        _token_counter = TokenCounter()
    return _token_counter
