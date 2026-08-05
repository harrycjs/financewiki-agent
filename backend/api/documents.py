"""
文档管理接口
"""
from fastapi import APIRouter, UploadFile, File, HTTPException
from typing import List, Optional
import json
import uuid
from datetime import datetime
from pathlib import Path

from ..database import execute_query, get_db
from ..config import settings

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    """上传文档"""
    from ..services.document_service import DocumentService
    from ..core.embedding.embedding_service import EmbeddingService
    from ..queue.producer import QueueProducer

    # 验证文件类型
    allowed_types = ['.pdf', '.docx', '.md', '.txt']
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_types:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {file_ext}")

    # 生成文档ID
    doc_id = str(uuid.uuid4())

    # 保存文件
    file_path = f"./data/documents/{doc_id}{file_ext}"
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    # 解析文档
    doc_service = DocumentService()
    parsed_content = await doc_service.parse(file_path, file_ext)

    # 保存到数据库
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO documents (id, filename, file_type, file_size, file_path, content, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, file.filename, file_ext, len(content), file_path,
             parsed_content, json.dumps({"pages": 1}))
        )
        conn.commit()

    # 入队异步处理（embedding、知识图谱提取）
    producer = QueueProducer()
    await producer.enqueue_document(doc_id, file_path, parsed_content)

    return {
        "id": doc_id,
        "filename": file.filename,
        "file_type": file_ext,
        "file_size": len(content),
        "message": "文档上传成功，正在处理中..."
    }


@router.get("")
async def get_documents():
    """获取文档列表"""
    rows = execute_query(
        """SELECT id, filename, file_type, file_size, entities_count, relations_count,
                  created_at, updated_at
           FROM documents
           ORDER BY created_at DESC"""
    )
    return [
        {
            "id": row[0],
            "filename": row[1],
            "file_type": row[2],
            "file_size": row[3],
            "entities_count": row[4],
            "relations_count": row[5],
            "created_at": row[6],
            "updated_at": row[7]
        }
        for row in rows
    ]


@router.get("/{doc_id}")
async def get_document(doc_id: str):
    """获取文档详情"""
    rows = execute_query(
        """SELECT id, filename, file_type, file_size, content, metadata,
                  entities_count, relations_count, created_at, updated_at
           FROM documents
           WHERE id = ?""",
        (doc_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="文档不存在")

    row = rows[0]
    return {
        "id": row[0],
        "filename": row[1],
        "file_type": row[2],
        "file_size": row[3],
        "content": row[4],
        "metadata": json.loads(row[5]) if row[5] else {},
        "entities_count": row[6],
        "relations_count": row[7],
        "created_at": row[8],
        "updated_at": row[9]
    }


@router.delete("/{doc_id}")
async def delete_document(doc_id: str):
    """删除文档及关联数据"""
    # 获取文档信息
    rows = execute_query("SELECT file_path FROM documents WHERE id = ?", (doc_id,))
    if not rows:
        raise HTTPException(status_code=404, detail="文档不存在")

    file_path = rows[0][0]

    # 删除文件
    if Path(file_path).exists():
        Path(file_path).unlink()

    # 删除数据库记录
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM relations WHERE doc_id = ?", (doc_id,))
        cursor.execute("DELETE FROM entities WHERE doc_id = ?", (doc_id,))
        cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.commit()

    # 删除向量数据
    from ..core.rag.vector_store import QdrantVectorStore
    vector_store = QdrantVectorStore()
    await vector_store.delete_by_doc_id(doc_id)

    return {"message": "文档已删除"}


@router.get("/{doc_id}/entities")
async def get_document_entities(doc_id: str):
    """获取文档提取的实体"""
    rows = execute_query(
        """SELECT id, name, type, attributes, created_at
           FROM entities
           WHERE doc_id = ?
           ORDER BY created_at DESC""",
        (doc_id,)
    )
    return [
        {
            "id": row[0],
            "name": row[1],
            "type": row[2],
            "attributes": json.loads(row[3]) if row[3] else {},
            "created_at": row[4]
        }
        for row in rows
    ]


@router.get("/{doc_id}/relations")
async def get_document_relations(doc_id: str):
    """获取文档提取的关系"""
    rows = execute_query(
        """SELECT r.id, r.source_id, r.target_id, r.relation, r.weight,
                  e1.name as source_name, e2.name as target_name
           FROM relations r
           JOIN entities e1 ON r.source_id = e1.id
           JOIN entities e2 ON r.target_id = e2.id
           WHERE r.doc_id = ?
           ORDER BY r.weight DESC""",
        (doc_id,)
    )
    return [
        {
            "id": row[0],
            "source_id": row[1],
            "target_id": row[2],
            "relation": row[3],
            "weight": row[4],
            "source_name": row[5],
            "target_name": row[6]
        }
        for row in rows
    ]


@router.post("/{doc_id}/reprocess")
async def reprocess_document(doc_id: str):
    """重新解析并入库文档

    适用场景：
    - 旧版本下入队时把内容截断到 5000 字符
    - 上游 PDF 解析库升级后老文档想重跑
    - 知识图谱不完整时手动触发重建

    行为：
    1. 按 file_path 重新解析源文件得到完整内容
    2. 覆盖 documents.content 为新解析结果（保证完整入库）
    3. 清空旧 entities / relations
    4. 清空旧向量（in-memory 或 Qdrant）
    5. 重新入队（chunk + embedding + KG 全量重建）
    """
    rows = execute_query(
        "SELECT filename, file_type, file_path FROM documents WHERE id = ?",
        (doc_id,)
    )
    if not rows:
        raise HTTPException(status_code=404, detail="文档不存在")

    filename, file_type, file_path = rows
    if not Path(file_path).exists():
        raise HTTPException(status_code=404, detail=f"源文件已丢失: {file_path}")

    from ..services.document_service import DocumentService
    from ..queue.producer import QueueProducer

    # 1. 重新解析
    doc_service = DocumentService()
    parsed_content = await doc_service.parse(file_path, file_type)

    # 2. 覆盖 DB 中的 content（确保完整入库，不截断）
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE documents
               SET content = ?, entities_count = 0, relations_count = 0, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (parsed_content, doc_id)
        )
        # 3. 清空旧实体和关系
        cursor.execute("DELETE FROM relations WHERE doc_id = ?", (doc_id,))
        cursor.execute("DELETE FROM entities WHERE doc_id = ?", (doc_id,))
        conn.commit()

    # 4. 清空旧向量
    from ..core.rag.vector_store import QdrantVectorStore
    vector_store = QdrantVectorStore()
    await vector_store.delete_by_doc_id(doc_id)

    # 5. 重新入队（完整内容，不截断）
    producer = QueueProducer()
    await producer.enqueue_document(doc_id, file_path, parsed_content)

    return {
        "id": doc_id,
        "filename": filename,
        "file_type": file_type,
        "new_content_length": len(parsed_content),
        "message": "文档已重新解析并入队处理",
    }
