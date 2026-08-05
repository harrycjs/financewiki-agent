"""
长期记忆模块
"""
from typing import List, Dict, Any

from ...database import execute_query


class LongTermMemory:
    """长期记忆：基于数据库的用户画像和核心观点"""

    def __init__(self):
        pass

    async def get_user_profile(self) -> Dict[str, Any]:
        """获取用户画像"""
        # 从历史数据中提取用户画像
        # 可以扩展：使用LLM分析历史对话提取用户偏好、关注领域等

        # 统计用户关注的领域
        rows = execute_query(
            """SELECT content FROM chat_history
               WHERE role = 'user'
               ORDER BY created_at DESC
               LIMIT 100"""
        )

        # 简单的关键词统计
        keywords = {}
        for row in rows:
            content = row[0]
            # 提取关键词（简化版）
            for word in ["股票", "基金", "债券", "期货", "外汇", "黄金",
                         "科技", "金融", "消费", "医疗", "新能源"]:
                if word in content:
                    keywords[word] = keywords.get(word, 0) + 1

        return {
            "focus_areas": sorted(keywords.keys(), key=lambda x: keywords[x], reverse=True)[:5],
            "total_queries": len(rows)
        }

    async def save_insight(self, insight: str, category: str):
        """保存核心洞察"""
        # 可以扩展：保存LLM生成的重要洞察
        pass

    async def get_insights(self, category: str = None) -> List[str]:
        """获取核心洞察"""
        # 可以扩展：获取保存的洞察
        return []
