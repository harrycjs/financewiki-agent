"""
Embedding服务模块 - 使用BAAI/bge-large-zh-v1.5模型
"""
import numpy as np
from typing import List
import os

from ...config import settings


# BAAI/bge-large-zh-v1.5 最大序列长度 = 512 tokens。
# 中文按"1 token ≈ 1~2 字符"的经验值，留出余量硬截断到 480 字符，
# 避免 sentence-transformers 静默截断导致尾部信息被丢。
EMBED_MAX_CHARS = 480


def _safe_truncate_for_embed(text: str, limit: int = EMBED_MAX_CHARS) -> str:
    """对超长文本做硬截断以匹配 embedding 模型上限。

    设计原则：上游 chunk_document 已经把单片控制在 <=500 字符，这里只是
    兜底；超过 limit 时记录警告，便于后续把 chunk 上限下调。
    """
    if len(text) <= limit:
        return text
    print(f"⚠️  Embedding 输入超长 ({len(text)}>{limit})，按字符硬截断；请检查上游 chunk_document 切分粒度")
    return text[:limit]


class EmbeddingService:
    """Embedding服务：使用BAAI/bge-large-zh-v1.5模型"""

    def __init__(self):
        self.provider = settings.EMBEDDING_PROVIDER
        self.dimension = settings.EMBEDDING_DIMENSION
        self._model = None
        self.model_name = "BAAI/bge-large-zh-v1.5"
        # 模型保存路径 - D盘
        self.model_path = "D:/models/BAAI--bge-large-zh-v1.5"

    def _load_local_model(self):
        """加载本地embedding模型"""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                # 检查本地是否已下载
                if os.path.exists(self.model_path):
                    print(f"📂 加载本地模型: {self.model_path}")
                    self._model = SentenceTransformer(self.model_path)
                else:
                    # 首次使用会自动下载到D盘
                    print(f"📥 下载embedding模型: {self.model_name}")
                    print(f"   保存路径: {self.model_path}")

                    # 创建目录
                    os.makedirs(os.path.dirname(self.model_path), exist_ok=True)

                    # 下载并保存模型
                    self._model = SentenceTransformer(self.model_name)
                    self._model.save(self.model_path)
                    print(f"✅ 模型已保存到: {self.model_path}")

                # 更新维度
                self.dimension = self._model.get_sentence_embedding_dimension()
                print(f"✅ 模型维度: {self.dimension}")

            except Exception as e:
                print(f"❌ 加载模型失败: {e}")
                raise
        return self._model

    async def embed(self, text: str) -> List[float]:
        """将文本转换为向量（单条自动做长度保险）"""
        text = _safe_truncate_for_embed(text)
        if self.provider == "local":
            return await self._embed_local(text)
        elif self.provider == "zhipu":
            return await self._embed_zhipu(text)
        elif self.provider == "deepseek":
            return await self._embed_deepseek(text)
        else:
            return await self._embed_local(text)

    async def _embed_local(self, text: str) -> List[float]:
        """使用本地模型生成embedding"""
        model = self._load_local_model()
        embedding = model.encode(text, normalize_embeddings=True)
        return embedding.tolist()

    async def _embed_zhipu(self, text: str) -> List[float]:
        """使用智谱API生成embedding"""
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.ZHIPU_API_BASE}/embeddings",
                headers={
                    "Authorization": f"Bearer {settings.ZHIPU_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "text-embedding-3",
                    "input": text
                },
                timeout=30
            )
            result = response.json()
            return result["data"][0]["embedding"]

    async def _embed_deepseek(self, text: str) -> List[float]:
        """使用DeepSeek API生成embedding"""
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.DEEPSEEK_API_BASE}/embeddings",
                headers={
                    "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "text-embedding-v1",
                    "input": text
                },
                timeout=30
            )
            result = response.json()
            return result["data"][0]["embedding"]

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量生成embedding（逐条做长度保险，避免长文本触发模型静默截断）"""
        safe_texts = [_safe_truncate_for_embed(t) for t in texts]
        if self.provider == "local":
            model = self._load_local_model()
            embeddings = model.encode(safe_texts, normalize_embeddings=True, batch_size=32)
            return embeddings.tolist()
        else:
            # 逐个调用API
            results = []
            for text in safe_texts:
                embedding = await self.embed(text)
                results.append(embedding)
            return results
