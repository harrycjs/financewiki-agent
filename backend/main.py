"""
金融投研知识库问答Agent - 主应用
"""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import settings
from .database import init_database
from .api import chat, documents, models, knowledge_graph, skills


# 应用生命周期
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    print("🚀 启动金融投研知识库问答Agent...")

    # 初始化数据库
    init_database()

    # 初始化向量数据库
    from .core.rag.vector_store import QdrantVectorStore
    vector_store = QdrantVectorStore()
    await vector_store.init_collection()

    # 初始化三层记忆（Redis 探测 + Qdrant chat_memory collection + 长期抽取 LLM 能力）
    from .core.memory import get_memory_manager
    await get_memory_manager().init()

    # 预热 embedding 模型：长期/中期记忆抽取依赖它，避免首次用户请求时冷启动卡顿
    try:
        from .core.rag.embedding_service import EmbeddingService
        EmbeddingService()._load_local_model()
        print("✅ Embedding 模型已预热")
    except Exception as e:
        print(f"⚠️ Embedding 模型预热失败（仍可用，长期/中期抽取将降级）: {e}")

    # 加载知识图谱
    from .core.knowledge_graph.builder import KnowledgeGraphBuilder
    kg_builder = KnowledgeGraphBuilder()
    kg_builder.load_from_db()
    print("✅ 知识图谱已加载")

    # 启动队列消费者
    from .queue.worker import QueueWorker, document_handler
    worker = QueueWorker()
    worker.register_handler("document", document_handler)

    # 在后台启动消费者（仅在Redis模式下）
    worker_task = None
    if not worker.use_memory:
        worker_task = asyncio.create_task(worker.start())
        print("✅ 队列消费者已启动")
    else:
        print("✅ 队列使用内存模式（任务直接处理）")

    # 初始化默认模型配置
    _init_default_models()

    print("✅ 应用启动完成！")
    print(f"📊 访问地址: http://localhost:{settings.APP_PORT}")
    print(f"📚 API文档: http://localhost:{settings.APP_PORT}/docs")

    yield

    # 关闭时
    print("🛑 正在关闭应用...")
    if worker_task:
        await worker.stop()
        worker_task.cancel()
    print("✅ 应用已关闭")


def _init_default_models():
    """初始化默认模型配置"""
    from .database import execute_query, get_db
    from .config import settings
    import uuid

    # 检查是否已有配置
    rows = execute_query("SELECT COUNT(*) FROM model_configs")
    if rows[0][0] > 0:
        return

    # 创建默认模型配置
    default_models = [
        ("zhipu", "GLM-4", settings.ZHIPU_API_KEY, settings.ZHIPU_API_BASE),
        ("deepseek", "DeepSeek", settings.DEEPSEEK_API_KEY, settings.DEEPSEEK_API_BASE),
        ("kimi", "Kimi", settings.KIMI_API_KEY, settings.KIMI_API_BASE),
        ("minimax", "MiniMax", settings.MINIMAX_API_KEY, settings.MINIMAX_API_BASE),
    ]

    with get_db() as conn:
        cursor = conn.cursor()
        for provider, name, api_key, api_base in default_models:
            cursor.execute(
                """INSERT INTO model_configs (id, name, provider, api_key, api_base, is_active)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), name, provider, api_key, api_base,
                 1 if provider == "deepseek" else 0)
            )
        conn.commit()

    print("✅ 默认模型配置已创建")


# 创建FastAPI应用
app = FastAPI(
    title=settings.APP_NAME,
    description="金融投研知识库问答Agent - 基于三路召回检索的智能问答系统",
    version="1.0.0",
    lifespan=lifespan
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由（先注册所有 API，确保优先级高于 catch-all / mount）
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(models.router)
app.include_router(knowledge_graph.router)
app.include_router(skills.router)


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "app": settings.APP_NAME}


@app.get("/api/stats")
async def get_stats():
    """获取系统统计"""
    from .database import execute_query

    # 文档统计
    doc_count = execute_query("SELECT COUNT(*) FROM documents")[0][0]

    # 实体统计
    entity_count = execute_query("SELECT COUNT(*) FROM entities")[0][0]

    # 关系统计
    relation_count = execute_query("SELECT COUNT(*) FROM relations")[0][0]

    # 队列统计
    from .queue.producer import QueueProducer
    producer = QueueProducer()
    queue_lengths = await producer.get_all_queue_lengths()

    return {
        "documents": doc_count,
        "entities": entity_count,
        "relations": relation_count,
        "queues": queue_lengths
    }


# 静态文件服务
from starlette.staticfiles import StaticFiles
from starlette.responses import FileResponse
from starlette.routing import Mount
import mimetypes

# 添加MIME类型
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('application/json', '.json')

frontend_path = Path(__file__).parent.parent / "frontend" / "dist"

# 自定义静态文件类，确保正确的MIME类型
class CustomStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        # 根据文件扩展名设置正确的MIME类型
        if hasattr(response, 'path'):
            file_path = response.path
            if file_path.endswith('.js'):
                response.headers['content-type'] = 'application/javascript'
            elif file_path.endswith('.css'):
                response.headers['content-type'] = 'text/css'
        return response

# 静态文件 + SPA catch-all
# 注意：用 @app.get 替代 app.mount，避免 mount 在 Starlette 里抢匹配
if frontend_path.exists():
    from fastapi.responses import FileResponse

    @app.get("/assets/{file_path:path}", include_in_schema=False)
    async def serve_assets(file_path: str):
        """服务 dist/assets/ 下的静态资源"""
        full_path = frontend_path / "assets" / file_path
        if full_path.is_file():
            return FileResponse(full_path)
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="asset not found")

    @app.get("/", include_in_schema=False)
    async def serve_root():
        """服务根路径"""
        return FileResponse(frontend_path / "index.html")

    # SPA catch-all: 任意非 /api 路径都返回 index.html
    # 必须放在所有 API 路由之后，确保 API 优先匹配
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_catch_all(full_path: str):
        # 排除 API（如果到这里说明 API 路由都没匹配上，返回 404 而不是 SPA 页面）
        if full_path.startswith("api/"):
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not found")
        # 其他路径返回 index.html（SPA 前端路由处理）
        index_file = frontend_path / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="index.html not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.DEBUG
    )
