"""
对话接口
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import json
import uuid
from datetime import datetime

from ..database import execute_query, get_db

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    """聊天消息"""
    role: str
    content: str
    sources: Optional[List[str]] = None


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str
    session_id: Optional[str] = None
    model: Optional[str] = None
    top_k: int = 10


class SessionCreate(BaseModel):
    """创建会话"""
    title: Optional[str] = None
    model: Optional[str] = None


def _ensure_session_row(session_id: str, model: str, first_message: str = None):
    """确保 sessions 表里有一行；不存在则用首条消息生成标题

    这是关键修复：之前 /api/chat 只往 chat_history 写，sessions 表没有对应行，
    导致侧边栏 /api/chat/sessions 永远列不出这个会话。
    """
    title = None
    if first_message:
        # 标题：截取首条消息前 30 字，去空白
        title = first_message.strip().replace("\n", " ")[:30]
        if len(first_message.strip()) > 30:
            title += "..."

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, title FROM sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                """INSERT INTO sessions (id, title, model, created_at, updated_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                (session_id, title or f"对话 {datetime.now().strftime('%Y-%m-%d %H:%M')}", model)
            )
        else:
            # 已有行：刷新 updated_at；标题保留原值（除非原值是默认"对话 xxxx"且现在有首条消息）
            cursor.execute(
                """UPDATE sessions
                   SET updated_at = CURRENT_TIMESTAMP,
                       title = CASE
                           WHEN title IS NULL OR title LIKE '对话 %' OR title = '' THEN ?
                           ELSE title
                       END,
                       model = COALESCE(NULLIF(?, ''), model)
                   WHERE id = ?""",
                (title, model, session_id)
            )
        conn.commit()


def _write_chat_history(session_id: str, user_msg: str, ai_msg: str, sources: list):
    """把一轮对话写入 chat_history（SQLite 是记忆的 source of truth）"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO chat_history (session_id, role, content, sources)
               VALUES (?, ?, ?, ?)""",
            (session_id, "user", user_msg, json.dumps([]))
        )
        cursor.execute(
            """INSERT INTO chat_history (session_id, role, content, sources)
               VALUES (?, ?, ?, ?)""",
            (session_id, "assistant", ai_msg, json.dumps(sources or []))
        )
        # 再次刷新 updated_at，使列表按最近活跃排序
        cursor.execute(
            "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,)
        )
        conn.commit()


@router.post("")
async def chat(request: ChatRequest):
    """发送消息，获取AI回复"""
    from ..core.rag.retriever import TripleRetriever
    from ..core.rag.generator import ResponseGenerator
    from ..core.memory import get_memory_manager
    from ..core.cache.cache_service import CacheService

    # 获取或创建会话
    session_id = request.session_id or str(uuid.uuid4())
    model_name = request.model or "deepseek"

    # ★ 修复：每次 /api/chat 都确保 sessions 表里有对应行
    _ensure_session_row(session_id, model_name, first_message=request.message)

    # 三层记忆统一入口（单例，持有唯一的 Redis 连接与向量库实例）
    memory = get_memory_manager()

    # 检查缓存（cache key 加入 skills_sig，技能切换自动失效）
    cache = CacheService()
    from ..core.rag.skill_resolver import get_resolver
    skills_sig = get_resolver().get_active_skills_signature()
    cached_result = await cache.get_query_cache(
        request.message,
        model_name,
        request.top_k,
        skills_sig
    )

    if cached_result:
        # 缓存命中也必须落历史与记忆，否则同一问题问第二次会话就断了
        answer = cached_result["answer"]
        sources = cached_result.get("sources", [])
        _write_chat_history(session_id, request.message, answer, sources)
        memory.schedule_write(session_id, request.message, answer, sources)

        async def cached_stream():
            yield json.dumps({"type": "session_id", "session_id": session_id})
            yield "\n"
            yield json.dumps({"type": "delta", "content": answer})
            yield "\n"
            yield json.dumps({"type": "done", "sources": sources, "cached": True})
            yield "\n"

        return StreamingResponse(cached_stream(), media_type="text/event-stream")

    # 执行检索
    retriever = TripleRetriever()
    results = await retriever.retrieve(request.message, request.top_k)

    # 组装三层记忆上下文（内含 95% 安全阀同步压缩）
    context_block = await memory.assemble_context(session_id, request.message)

    generator = ResponseGenerator()
    sources = [r.get("id") for r in results if r.get("id")]

    async def agent_stream():
        """真流式：边生成边下发，工具调用同步暴露给前端

        落库放在 finally 里——客户端中途断开时（GeneratorExit）也能把已生成的
        部分答案存下来，不至于历史里出现一条只有提问没有回答的记录。
        """
        answer = ""
        used_tools = False

        yield json.dumps({"type": "session_id", "session_id": session_id})
        yield "\n"

        try:
            async for event in generator.run_agent_stream(
                query=request.message,
                context=context_block,
                documents=results,
            ):
                kind = event["type"]
                if kind == "delta":
                    yield json.dumps({"type": "delta", "content": event["content"]})
                    yield "\n"
                elif kind == "tool_call":
                    yield json.dumps({
                        "type": "tool_call",
                        "name": event["name"],
                        "arguments": event["arguments"],
                    })
                    yield "\n"
                elif kind == "tool_result":
                    yield json.dumps({
                        "type": "tool_result",
                        "name": event["name"],
                        "preview": event["preview"],
                        "ok": event["ok"],
                    })
                    yield "\n"
                elif kind == "final":
                    answer = event["content"]
                    used_tools = event["used_tools"]

            yield json.dumps({"type": "done", "sources": sources})
            yield "\n"

        except Exception as e:
            print(f"⚠️ agent 流式生成失败: {type(e).__name__}: {e}")
            yield json.dumps({"type": "error", "message": f"{type(e).__name__}: {e}"})
            yield "\n"

        finally:
            if answer:
                _write_chat_history(session_id, request.message, answer, sources)
                memory.schedule_write(session_id, request.message, answer, sources)
                # 调过工具的回答不进缓存：搜索结果、文件内容都是时效性的，
                # 缓存下来下次命中会返回过期信息
                if not used_tools:
                    await cache.set_query_cache(
                        request.message,
                        model_name,
                        request.top_k,
                        {"answer": answer, "sources": sources},
                        skills_sig,
                    )
            # agent 可能用 bash 在 skills 目录里新建/修改了技能；
            # scanner 有缓存，下一轮对话必须重新扫描才能看到
            if used_tools:
                try:
                    from ..core.skills.scanner import get_scanner
                    get_scanner().invalidate_cache()
                except Exception as e:
                    print(f"⚠️ 扫描器缓存失效失败: {e}")

    return StreamingResponse(agent_stream(), media_type="text/event-stream")


@router.get("/history")
async def get_history(session_id: str, limit: int = 50):
    """获取对话历史（按时间正序：user → assistant → user → assistant）

    关键：用 id ASC 而不是 created_at DESC，
    因为同一秒内连续插入的多条消息 created_at 相同，DESC 在 SQLite 中不稳定，
    会导致"成对"内部顺序也乱（assistant 排到 user 前面）。
    id 是自增主键，严格反映插入顺序 = 时间顺序。
    """
    rows = execute_query(
        """SELECT role, content, sources, created_at
           FROM chat_history
           WHERE session_id = ?
           ORDER BY id ASC
           LIMIT ?""",
        (session_id, limit)
    )
    return [
        {
            "role": row[0],
            "content": row[1],
            "sources": json.loads(row[2]) if row[2] else [],
            "created_at": row[3]
        }
        for row in rows
    ]


@router.get("/sessions")
async def get_sessions():
    """获取会话列表"""
    rows = execute_query(
        """SELECT id, title, model, created_at, updated_at
           FROM sessions
           ORDER BY updated_at DESC"""
    )
    return [
        {
            "id": row[0],
            "title": row[1],
            "model": row[2],
            "created_at": row[3],
            "updated_at": row[4]
        }
        for row in rows
    ]


@router.post("/sessions/recover-orphans")
async def recover_orphan_sessions():
    """把 chat_history 里存在、但 sessions 表里没有的 session_id 全部救回来

    背景：旧版 /api/chat 不写 sessions 行，导致历史消息对应的会话在侧边栏不可见。
    本接口会扫描 chat_history，对每个孤儿 session_id：
    - 从首条 user 消息截取前 30 字生成标题
    - 在 sessions 表插入一行，updated_at 用该 session_id 的最后一条消息时间
    - 可重复运行（已存在的 session_id 会被跳过）
    """
    orphans = execute_query(
        """SELECT DISTINCT h.session_id,
                  (SELECT content FROM chat_history
                   WHERE session_id = h.session_id AND role = 'user'
                   ORDER BY created_at ASC LIMIT 1) AS first_user_msg,
                  (SELECT MAX(created_at) FROM chat_history
                   WHERE session_id = h.session_id) AS last_msg_at,
                  (SELECT COUNT(*) FROM chat_history
                   WHERE session_id = h.session_id) AS msg_count
           FROM chat_history h
           WHERE NOT EXISTS (SELECT 1 FROM sessions s WHERE s.id = h.session_id)"""
    )

    recovered = []
    skipped = 0
    with get_db() as conn:
        cursor = conn.cursor()
        for sid, first_user_msg, last_msg_at, msg_count in orphans:
            cursor.execute("SELECT 1 FROM sessions WHERE id = ?", (sid,))
            if cursor.fetchone():
                skipped += 1
                continue

            title = None
            if first_user_msg:
                title = first_user_msg.strip().replace("\n", " ")[:30]
                if len(first_user_msg.strip()) > 30:
                    title += "..."
            title = title or f"对话 {datetime.now().strftime('%Y-%m-%d %H:%M')}"

            cursor.execute(
                """INSERT INTO sessions (id, title, model, created_at, updated_at)
                   VALUES (?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), COALESCE(?, CURRENT_TIMESTAMP))""",
                (sid, title, "deepseek", last_msg_at, last_msg_at)
            )
            recovered.append({
                "id": sid,
                "title": title,
                "msg_count": msg_count,
                "last_msg_at": last_msg_at,
            })

        conn.commit()

    return {
        "recovered_count": len(recovered),
        "skipped_count": skipped,
        "recovered": recovered,
    }


@router.post("/sessions")
async def create_session(request: SessionCreate):
    """创建新会话"""
    session_id = str(uuid.uuid4())
    title = request.title or f"对话 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    model = request.model or "deepseek"

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO sessions (id, title, model)
               VALUES (?, ?, ?)""",
            (session_id, title, model)
        )
        conn.commit()

    return {"id": session_id, "title": title, "model": model}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话：DB + 三层记忆（短期/中期）一并清理"""
    from ..core.memory import get_memory_manager
    memory = get_memory_manager()
    await memory.forget_session(session_id)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chat_history WHERE session_id = ?", (session_id,))
        cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
    return {"message": "会话已删除"}


@router.get("/memory/stats")
async def memory_stats(session_id: Optional[str] = None):
    """三层记忆运行状态（用于观测）"""
    from ..core.memory import get_memory_manager
    memory = get_memory_manager()
    return await memory.stats(session_id)
