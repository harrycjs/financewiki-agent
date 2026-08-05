"""
队列生产者 - 支持Redis和内存模式
"""
import json
from collections import deque
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backend.config import settings


class QueueProducer:
    """队列生产者 - 支持Redis和内存模式"""

    def __init__(self):
        self.redis = None
        self.use_memory = True  # 默认使用内存模式
        self.memory_queues = {
            "document": deque(),
            "embedding": deque(),
            "kg": deque()
        }

        # 尝试连接Redis
        try:
            import redis
            r = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
            r.ping()
            import redis.asyncio as redis_async
            self.redis = redis_async.from_url(
                f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}",
                decode_responses=True
            )
            self.use_memory = False
            print("✅ 队列使用Redis")
        except Exception as e:
            print(f"⚠️ Redis不可用，队列使用内存模式: {e}")
            self.use_memory = True

        self.queues = {
            "document": "queue:document",
            "embedding": "queue:embedding",
            "kg": "queue:kg"
        }

    async def enqueue_document(self, doc_id: str, file_path: str, content: str):
        """入队文档处理任务

        注意：完整内容原样传递，绝不截断。截断会丢失文档尾部信息，
        必须通过下游的 chunk 切分（worker.chunk_document）来控制单片大小。
        """
        task = {
            "type": "document",
            "doc_id": doc_id,
            "file_path": file_path,
            "content": content  # 完整内容，不截断
        }

        if self.use_memory:
            # 内存模式 - 直接处理
            print(f"📤 文档任务入队(内存模式): {doc_id} (内容长度: {len(content)})")
            # 在内存模式下，直接同步处理
            from .worker import document_handler
            await document_handler(task)
        else:
            await self.redis.rpush(self.queues["document"], json.dumps(task))
            print(f"📤 文档任务入队: {doc_id} (内容长度: {len(content)})")

    async def enqueue_embedding(self, doc_id: str, chunks: list):
        """入队embedding计算任务"""
        task = {
            "type": "embedding",
            "doc_id": doc_id,
            "chunks": chunks
        }

        if self.use_memory:
            print(f"📤 Embedding任务入队(内存模式): {doc_id}")
        else:
            await self.redis.rpush(self.queues["embedding"], json.dumps(task))
            print(f"📤 Embedding任务入队: {doc_id}")

    async def enqueue_kg(self, doc_id: str, content: str):
        """入队知识图谱提取任务

        完整内容原样传递，由下游 KGBuilder 按段落处理并在段落内分块送 LLM，
        避免在入队处截断导致长文档后段知识丢失。
        """
        task = {
            "type": "kg",
            "doc_id": doc_id,
            "content": content  # 完整内容，不截断
        }

        if self.use_memory:
            print(f"📤 知识图谱任务入队(内存模式): {doc_id} (内容长度: {len(content)})")
        else:
            await self.redis.rpush(self.queues["kg"], json.dumps(task))
            print(f"📤 知识图谱任务入队: {doc_id} (内容长度: {len(content)})")

    async def get_queue_length(self, queue_name: str) -> int:
        """获取队列长度"""
        if self.use_memory:
            return len(self.memory_queues.get(queue_name, []))
        else:
            queue_key = self.queues.get(queue_name)
            if queue_key:
                return await self.redis.llen(queue_key)
            return 0

    async def get_all_queue_lengths(self) -> dict:
        """获取所有队列长度"""
        if self.use_memory:
            return {
                name: len(queue)
                for name, queue in self.memory_queues.items()
            }
        else:
            lengths = {}
            for name, key in self.queues.items():
                lengths[name] = await self.redis.llen(key)
            return lengths
