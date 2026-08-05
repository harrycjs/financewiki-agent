"""
队列消费者（异步任务处理）- 支持Redis和内存模式
"""
import json
import asyncio
from typing import Callable, Dict
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backend.config import settings


class QueueWorker:
    """队列消费者 - 支持Redis和内存模式"""

    def __init__(self):
        self.redis = None
        self.use_memory = True  # 默认使用内存模式
        self.queues = {
            "document": "queue:document",
            "embedding": "queue:embedding",
            "kg": "queue:kg"
        }
        self.handlers: Dict[str, Callable] = {}
        self.running = False

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
            print("✅ 队列消费者使用Redis")
        except Exception as e:
            print(f"⚠️ Redis不可用，队列消费者使用内存模式: {e}")
            self.use_memory = True

    def register_handler(self, queue_name: str, handler: Callable):
        """注册队列处理器"""
        self.handlers[queue_name] = handler
        print(f"✅ 注册处理器: {queue_name}")

    async def start(self):
        """启动所有队列消费者"""
        self.running = True

        if self.use_memory:
            # 内存模式 - 不需要消费者，任务直接处理
            print("🚀 队列消费者已启动(内存模式)")
            return

        tasks = []
        for queue_name in self.handlers:
            tasks.append(asyncio.create_task(self._consume(queue_name)))
        print("🚀 队列消费者已启动")
        await asyncio.gather(*tasks)

    async def stop(self):
        """停止所有队列消费者"""
        self.running = False
        print("⏹️ 队列消费者已停止")

    async def _consume(self, queue_name: str):
        """消费队列"""
        handler = self.handlers.get(queue_name)
        if not handler:
            print(f"❌ 未找到处理器: {queue_name}")
            return

        queue_key = self.queues[queue_name]
        print(f"👂 开始监听队列: {queue_name}")

        while self.running:
            try:
                # 使用blpop等待新任务
                result = await self.redis.blpop(queue_key, timeout=1)
                if result:
                    task_data = json.loads(result[1])
                    print(f"📥 收到任务: {queue_name} - {task_data.get('doc_id', 'unknown')}")
                    try:
                        await handler(task_data)
                        print(f"✅ 任务完成: {queue_name}")
                    except Exception as e:
                        print(f"❌ 任务处理失败: {e}")
                        # 可以选择重新入队
                        await self.redis.rpush(queue_key, result[1])
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ 队列消费错误: {e}")
                await asyncio.sleep(1)


# 默认处理器
async def document_handler(task: dict):
    """文档处理任务"""
    from ..services.document_service import DocumentService
    from ..core.embedding.embedding_service import EmbeddingService
    from ..core.knowledge_graph.builder import KnowledgeGraphBuilder

    doc_id = task["doc_id"]
    content = task["content"]

    print(f"📄 处理文档: {doc_id}")

    # 1. 文档切分
    chunks = chunk_document(content)
    print(f"  ✂️ 文档切分为 {len(chunks)} 个片段")

    # 2. 计算embedding
    embedding_service = EmbeddingService()
    embeddings = await embedding_service.embed_batch([c["content"] for c in chunks])
    for i, emb in enumerate(embeddings):
        chunks[i]["embedding"] = emb
    print(f"  🧮 计算完成 {len(embeddings)} 个embedding")

    # 3. 存储到向量数据库
    from ..core.rag.vector_store import QdrantVectorStore
    vector_store = QdrantVectorStore()
    await vector_store.batch_add(doc_id, chunks)
    print(f"  💾 向量已存储")

    # 4. 构建知识图谱
    kg_builder = KnowledgeGraphBuilder()
    entity_count, relation_count = await kg_builder.build_from_document(doc_id, content)
    print(f"  🕸️ 提取 {entity_count} 个实体, {relation_count} 个关系")

    # 5. 更新BM25索引
    from ..core.rag.retriever import TripleRetriever
    retriever = TripleRetriever()
    for chunk in chunks:
        retriever.bm25.add_document(f"{doc_id}_{chunk['id']}", chunk["content"])
    print(f"  📝 BM25索引已更新")

    print(f"✅ 文档处理完成: {doc_id}")


def chunk_document(content: str, max_paragraph_len: int = 500, max_sentence_len: int = 200) -> list:
    """文档切分：段落+句子双粒度，确保不丢任何尾部内容

    切分策略：
    1. 按段落（\\n\\n）粗切：短段落直接成 chunk；
    2. 长段落按中英文句末标点细切：累积成 <= max_sentence_len 的 chunk；
    3. 单句/无标点长段若仍超过 max_sentence_len，按字符硬切（确保覆盖末尾）；
    4. 末尾 current_chunk 必须 flush，绝不丢弃。
    """
    import re
    import uuid

    chunks = []

    def _hard_split(text: str, limit: int) -> list:
        """单段文本超过 limit 时按字符硬切，最后一片可小于 limit 但非空"""
        if len(text) <= limit:
            return [text]
        parts = []
        for i in range(0, len(text), limit):
            seg = text[i:i + limit]
            if seg:
                parts.append(seg)
        return parts

    # 按段落切分
    paragraphs = content.split("\n\n")

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(para) <= max_paragraph_len:
            # 段落足够短，直接作为chunk（仍按硬切兜底，保证单片不超 max_sentence_len）
            for seg in _hard_split(para, max_sentence_len):
                chunks.append({
                    "id": str(uuid.uuid4()),
                    "type": "paragraph",
                    "content": seg,
                    "metadata": {"level": "paragraph"}
                })
        else:
            # 段落过长，按句子切分
            sentences = re.split(r'([。！？；\n])', para)
            current_chunk = ""

            def _flush(buf: str):
                """把累积缓冲按硬切切成 chunk，保证不丢"""
                if not buf.strip():
                    return
                for seg in _hard_split(buf.strip(), max_sentence_len):
                    chunks.append({
                        "id": str(uuid.uuid4()),
                        "type": "sentence",
                        "content": seg,
                        "metadata": {"level": "sentence"}
                    })

            for sent in sentences:
                # 单句本身就比 max_sentence_len 长：单独 flush，再把这一长句硬切
                if len(sent) > max_sentence_len:
                    _flush(current_chunk)
                    current_chunk = ""
                    _flush(sent)  # _hard_split 内部保证完整覆盖
                    continue

                if len(current_chunk) + len(sent) <= max_sentence_len:
                    current_chunk += sent
                else:
                    _flush(current_chunk)
                    current_chunk = sent

            # 末尾必须 flush，绝不丢弃尾部
            _flush(current_chunk)

    return chunks
