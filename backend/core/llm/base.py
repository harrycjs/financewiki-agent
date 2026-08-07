"""
LLM基类和工厂
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, AsyncGenerator, Optional
import httpx
import json

from ...config import settings


class BaseLLM(ABC):
    """LLM基类"""

    # 供 stream_agent 复用的模型名（子类覆盖）。原有 chat/stream_chat 里
    # 硬编码的模型名保持不动，避免影响记忆压缩/KG 抽取等既有调用方。
    MODEL: str = "deepseek-chat"
    # MiniMax 的 abab 系列不支持 OpenAI 风格 function calling，置 False 后
    # 走纯文本模式（工具不可用但对话正常）
    SUPPORTS_TOOLS: bool = True

    def __init__(self, api_key: str, api_base: str):
        self.api_key = api_key
        self.api_base = api_base

    @abstractmethod
    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """对话接口"""
        pass

    @abstractmethod
    async def stream_chat(self, messages: List[Dict[str, str]], **kwargs) -> AsyncGenerator[str, None]:
        """流式对话接口"""
        pass

    async def stream_agent(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """带工具的流式对话，产出结构化事件。

        四家 provider 都是 OpenAI 兼容的 /chat/completions，所以这里在基类
        实现一次即可，子类只需覆盖 MODEL。

        产出事件：
          {"type": "text", "content": "增量文本"}
          {"type": "tool_calls", "tool_calls": [{"id", "name", "arguments"}]}

        tool_calls 的 delta 是分片到达的（name 在第一片，arguments 逐字符累加），
        按 index 累积完整后在流结束时一次性产出。
        """
        payload: Dict[str, Any] = {
            "model": self.MODEL,
            "messages": messages,
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 4096),
            "stream": True,
        }
        if tools and self.SUPPORTS_TOOLS:
            payload["tools"] = tools
            payload["tool_choice"] = kwargs.get("tool_choice", "auto")

        pending: Dict[int, Dict[str, str]] = {}

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise RuntimeError(f"LLM 接口返回 {response.status_code}: {body[:300]}")

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}

                    if delta.get("content"):
                        yield {"type": "text", "content": delta["content"]}

                    for call in delta.get("tool_calls") or []:
                        slot = pending.setdefault(
                            call.get("index", 0), {"id": "", "name": "", "arguments": ""}
                        )
                        if call.get("id"):
                            slot["id"] = call["id"]
                        fn = call.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]

        if pending:
            yield {
                "type": "tool_calls",
                "tool_calls": [pending[i] for i in sorted(pending)],
            }

    async def generate_json(self, prompt: str) -> Dict[str, Any]:
        """生成JSON格式响应"""
        messages = [{"role": "user", "content": prompt}]
        response = await self.chat(messages)

        # 解析JSON
        start = response.find("{")
        end = response.rfind("}") + 1
        if start != -1 and end > start:
            json_str = response[start:end]
            return json.loads(json_str)
        return {}

    @classmethod
    def create(cls, provider: str, api_key: str, api_base: str) -> "BaseLLM":
        """工厂方法：创建LLM实例"""
        if provider == "zhipu":
            return ZhipuLLM(api_key, api_base)
        elif provider == "deepseek":
            return DeepSeekLLM(api_key, api_base)
        elif provider == "kimi":
            return KimiLLM(api_key, api_base)
        elif provider == "minimax":
            return MiniMaxLLM(api_key, api_base)
        else:
            return DeepSeekLLM(api_key, api_base)


class ZhipuLLM(BaseLLM):
    """智谱AI"""

    MODEL = "glm-4"

    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "glm-4",
                    "messages": messages,
                    "temperature": kwargs.get("temperature", 0.7),
                    "max_tokens": kwargs.get("max_tokens", 4096)
                },
                timeout=60
            )
            result = response.json()
            return result["choices"][0]["message"]["content"]

    async def stream_chat(self, messages: List[Dict[str, str]], **kwargs) -> AsyncGenerator[str, None]:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "glm-4",
                    "messages": messages,
                    "temperature": kwargs.get("temperature", 0.7),
                    "max_tokens": kwargs.get("max_tokens", 4096),
                    "stream": True
                },
                timeout=60
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            continue


class DeepSeekLLM(BaseLLM):
    """DeepSeek"""

    MODEL = "deepseek-chat"

    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "deepseek-chat",
                    "messages": messages,
                    "temperature": kwargs.get("temperature", 0.7),
                    "max_tokens": kwargs.get("max_tokens", 4096)
                },
                timeout=60
            )
            result = response.json()
            return result["choices"][0]["message"]["content"]

    async def stream_chat(self, messages: List[Dict[str, str]], **kwargs) -> AsyncGenerator[str, None]:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "deepseek-chat",
                    "messages": messages,
                    "temperature": kwargs.get("temperature", 0.7),
                    "max_tokens": kwargs.get("max_tokens", 4096),
                    "stream": True
                },
                timeout=60
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            continue


class KimiLLM(BaseLLM):
    """Kimi (Moonshot)"""

    MODEL = "moonshot-v1-8k"

    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "moonshot-v1-8k",
                    "messages": messages,
                    "temperature": kwargs.get("temperature", 0.7),
                    "max_tokens": kwargs.get("max_tokens", 4096)
                },
                timeout=60
            )
            result = response.json()
            return result["choices"][0]["message"]["content"]

    async def stream_chat(self, messages: List[Dict[str, str]], **kwargs) -> AsyncGenerator[str, None]:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "moonshot-v1-8k",
                    "messages": messages,
                    "temperature": kwargs.get("temperature", 0.7),
                    "max_tokens": kwargs.get("max_tokens", 4096),
                    "stream": True
                },
                timeout=60
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            continue


class MiniMaxLLM(BaseLLM):
    """MiniMax"""

    MODEL = "abab5.5-chat"
    SUPPORTS_TOOLS = False

    async def chat(self, messages: List[Dict[str, str]], **kwargs) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "abab5.5-chat",
                    "messages": messages,
                    "temperature": kwargs.get("temperature", 0.7),
                    "max_tokens": kwargs.get("max_tokens", 4096)
                },
                timeout=60
            )
            result = response.json()
            return result["choices"][0]["message"]["content"]

    async def stream_chat(self, messages: List[Dict[str, str]], **kwargs) -> AsyncGenerator[str, None]:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "abab5.5-chat",
                    "messages": messages,
                    "temperature": kwargs.get("temperature", 0.7),
                    "max_tokens": kwargs.get("max_tokens", 4096),
                    "stream": True
                },
                timeout=60
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except json.JSONDecodeError:
                            continue


def get_active_llm() -> BaseLLM:
    """获取当前激活的 LLM 实例。

    与 ResponseGenerator._get_llm / TripleRetriever._get_llm 行为一致：优先读
    model_configs 表里 is_active=1 的配置，没有则回退到 settings 里的 DeepSeek。
    抽成模块级函数供记忆系统（摘要压缩、长期事实抽取）复用。
    """
    from ...database import execute_query

    try:
        rows = execute_query(
            "SELECT provider, api_key, api_base FROM model_configs WHERE is_active = 1"
        )
        if rows:
            provider, api_key, api_base = rows[0]
            return BaseLLM.create(provider, api_key, api_base)
    except Exception as e:
        print(f"⚠️ 读取激活模型配置失败，回退 DeepSeek: {e}")

    return BaseLLM.create(
        "deepseek", settings.DEEPSEEK_API_KEY, settings.DEEPSEEK_API_BASE
    )
