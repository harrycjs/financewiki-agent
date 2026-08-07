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


def chunk_document(content: str, max_chunk_len: int = 600, similarity_threshold: float = 0.55, window: int = 3) -> list:
    """语义分块：滑动窗口 + 相似度落差检测

    切分策略：
    1. 句子级切分（中英文句末标点）
    2. 批量 embedding（复用 cache）
    3. 计算相邻句子的局部平均相似度（窗口平滑）
    4. 相似度骤降处 = 语义边界
    5. 兜底：单 chunk 超长时按字符硬切，保证不丢尾部

    参数：
      max_chunk_len        单片硬上限（适配 embedding 模型 token 限制）
      similarity_threshold 相似度低于此值视为切点（0~1，越大切得越细）
      window               滑动窗口大小（句数，越大越平滑）
    """
    import re
    import uuid
    import asyncio

    # 工具函数：硬切（兜底用）
    def _hard_split(text: str, limit: int) -> list:
        if len(text) <= limit:
            return [text]
        return [text[i:i+limit] for i in range(0, len(text), limit) if text[i:i+limit]]

    # 工具函数：把 chunk 包成 dict
    def _wrap(content: str) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "type": "semantic",
            "content": content,
            "metadata": {"level": "semantic", "split_method": "cosine_drop"}
        }

    # 工具函数：合并 PDF 碎片化行
    # PyPDF2 抽出来的文本特点：
    #   1. 单 \n 是 PDF 视觉换行（一个句子被切成多行）
    #   2. \n\n 是真正的段落/页面分隔
    #   3. 每页顶部重复页眉："XXX公司2025年年度报告全文 12"
    # 直接按 \n 分句会得到 14000+ 条噪声句。正确做法：
    #   - 只按 \n\n 切段落（PDF 一页一段）
    #   - 段内单 \n 折成空串（合并视觉换行）
    #   - 去掉重复的页眉/页码噪声
    def _merge_frag_lines(text: str) -> str:
        # 1. 先按行剥页眉/页脚噪声（仅删行本身，保留 \n\n 边界）
        cleaned_lines = []
        for line in text.split("\n"):
            s = line.strip()
            # 匹配页眉："报告全文12"（标题 + 页码同行）
            if re.match(r"^.{4,40}?(年度报告全文|年报全文)\s*\d+\s*$", s):
                continue
            # 匹配纯页眉：只有"报告全文"无页码（页码可能在下一行）
            if re.match(r"^.{4,40}?(年度报告全文|年报全文)\s*$", s):
                continue
            # 匹配纯页码 / 空白
            if not s or re.match(r"^\d{1,4}$", s):
                continue
            cleaned_lines.append(line)
        text = "\n".join(cleaned_lines)

        # 2. 按 \n\n 切成段（PDF 页面分隔）
        paras = text.split("\n\n")
        merged_paras = []
        for p in paras:
            # 段内所有单 \n 折成空串（合并视觉换行）
            collapsed = re.sub(r"\s*\n\s*", "", p).strip()
            collapsed = re.sub(r"[ \t]+", " ", collapsed)
            if collapsed and len(collapsed) >= 10:
                merged_paras.append(collapsed)
        return "\n\n".join(merged_paras)

    # 短文本直接返回
    text = content.strip()
    if len(text) <= max_chunk_len:
        return [_wrap(text)] if text else []

    # 0. 先合并 PDF 碎片行
    text = _merge_frag_lines(text)
    if len(text) <= max_chunk_len:
        return [_wrap(text)] if text else []

    # 1. 句子级切分（中英文句末标点；不再按 \n 切，由 _merge_frag_lines 处理过段落边界）
    raw_sentences = re.split(r'(?<=[。！？；.!?])\s*', text)
    # 过滤过短噪声（页码、单字等残留）
    sentences = [s.strip() for s in raw_sentences if len(s.strip()) >= 8]

    # 2. 太短（句子数 < 窗口+1）不切
    if len(sentences) <= window + 1:
        return _hard_split(text, max_chunk_len) and [_wrap(c) for c in _hard_split(text, max_chunk_len)] or []

    # 3. 同步包一层：让旧的同步调用也能跑（不依赖外部异步上下文时）
    return _semantic_chunk_sync(
        sentences, max_chunk_len, similarity_threshold, window, _hard_split, _wrap
    )


def _semantic_chunk_sync(sentences, max_chunk_len, threshold, window, _hard_split, _wrap):
    """同步入口：跑异步 embedding 计算"""
    import asyncio
    try:
        # 看是否已有事件循环
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 已经在异步上下文里：构造任务跑不动，退化为窗口级硬切
            return _fallback_chunk(sentences, max_chunk_len, _hard_split, _wrap)
        else:
            return loop.run_until_complete(_semantic_chunk_async(
                sentences, max_chunk_len, threshold, window, _hard_split, _wrap
            ))
    except RuntimeError:
        # 没有事件循环 → 新建一个
        return asyncio.run(_semantic_chunk_async(
            sentences, max_chunk_len, threshold, window, _hard_split, _wrap
        ))


async def _semantic_chunk_async(sentences, max_chunk_len, threshold, window, _hard_split, _wrap):
    """异步核心：批量 embedding + 找相似度落差"""
    import numpy as np
    from ..core.embedding.embedding_service import EmbeddingService

    # 1. 批量 embedding（带缓存）
    es = EmbeddingService()
    embs = await es.embed_batch(sentences)
    # embs 是 list[list[float]]，转 numpy 加速计算
    embs_arr = np.array(embs, dtype=np.float32)  # (N, dim)

    # 2. 计算相邻 N 句的局部平均余弦相似度（向量化矩阵运算，O(N²) 一次性算出）
    n = len(sentences)
    norms = np.linalg.norm(embs_arr, axis=1, keepdims=True)
    normalized = embs_arr / (norms + 1e-9)
    sim_matrix = normalized @ normalized.T  # (N, N)

    # 构造邻居 mask：[i-window, i+window] 内（不含自身）
    idx = np.arange(n)
    mask = np.zeros((n, n), dtype=bool)
    for offset in range(-window, window + 1):
        if offset == 0:
            continue
        j = idx + offset
        valid = (j >= 0) & (j < n)
        mask[idx[valid], j[valid]] = True

    # 每行的均值相似度（i = n-1 没用到，只取前 n-1）
    neighbor_counts = mask.sum(axis=1)
    sims = (sim_matrix * mask).sum(axis=1) / np.maximum(neighbor_counts, 1)
    sims = sims[: n - 1].tolist()

    # 3. 找切点（相似度 < threshold）
    chunks_text: list = []
    current = [sentences[0]]
    for i, sim in enumerate(sims):
        if sim < threshold:
            chunks_text.append("".join(current))
            current = []
        current.append(sentences[i + 1])
    if current:
        chunks_text.append("".join(current))

    # 4. 兜底：任何超长 chunk 按 max_chunk_len 硬切
    final_chunks = []
    for c in chunks_text:
        if len(c) > max_chunk_len:
            final_chunks.extend(_hard_split(c, max_chunk_len))
        else:
            final_chunks.append(c)

    return [_wrap(c) for c in final_chunks]


def _fallback_chunk(sentences, max_chunk_len, _hard_split, _wrap):
    """降级方案：在已有事件循环里跑不了嵌套 loop 时，按简单句子级累计"""
    chunks_text: list = []
    current = ""
    for s in sentences:
        if len(current) + len(s) > max_chunk_len:
            if current:
                chunks_text.append(current)
            current = s
        else:
            current += s
    if current:
        chunks_text.append(current)

    # 任何超长硬切
    final = []
    for c in chunks_text:
        if len(c) > max_chunk_len:
            final.extend(_hard_split(c, max_chunk_len))
        else:
            final.append(c)
    return [_wrap(c) for c in final]
