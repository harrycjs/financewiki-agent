"""
中期记忆模块（基于SQLite）
"""
import json
from typing import List, Dict, Any
import sqlite3

from ...config import settings
from ...database import get_db


class MidTermMemory:
    """中期记忆：基于SQLite的历史问答"""

    def __init__(self):
        self.db_path = settings.SQLITE_DB_PATH

    async def save(self, user_msg: str, ai_msg: str, sources: List[str] = None):
        """保存问答对"""
        with get_db() as conn:
            cursor = conn.cursor()
            # 保存到chat_history表
            cursor.execute(
                """INSERT INTO chat_history (session_id, role, content, sources)
                   VALUES (?, ?, ?, ?)""",
                ("mid_term", "user", user_msg, json.dumps([]))
            )
            cursor.execute(
                """INSERT INTO chat_history (session_id, role, content, sources)
                   VALUES (?, ?, ?, ?)""",
                ("mid_term", "assistant", ai_msg, json.dumps(sources or []))
            )
            conn.commit()

    async def search_similar(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """搜索相似的历史问答"""
        with get_db() as conn:
            cursor = conn.cursor()
            # 简单的关键词匹配
            cursor.execute(
                """SELECT role, content, sources, created_at
                   FROM chat_history
                   WHERE session_id = 'mid_term' AND content LIKE ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (f"%{query}%", limit)
            )
            rows = cursor.fetchall()

        return [
            {
                "role": row[0],
                "content": row[1],
                "sources": json.loads(row[2]) if row[2] else [],
                "created_at": row[3]
            }
            for row in rows
        ]

    async def get_user_preferences(self) -> Dict[str, Any]:
        """获取用户偏好"""
        # 可以扩展：分析历史问答提取用户偏好
        return {}

    async def cleanup(self, days: int = 30):
        """清理过期数据"""
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """DELETE FROM chat_history
                   WHERE session_id = 'mid_term'
                   AND created_at < datetime('now', ?)""",
                (f"-{days} days",)
            )
            conn.commit()
