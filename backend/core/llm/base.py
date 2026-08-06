"""
LLM基类和工厂
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, AsyncGenerator
import httpx
import json

from ...config import settings


class BaseLLM(ABC):
    """LLM基类"""

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
