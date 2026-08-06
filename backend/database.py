"""
数据库初始化和管理模块
"""
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from .config import settings


def init_database():
    """初始化SQLite数据库"""
    db_path = settings.SQLITE_DB_PATH
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # 文档表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_size INTEGER,
                file_path TEXT NOT NULL,
                content TEXT,
                metadata TEXT,
                entities_count INTEGER DEFAULT 0,
                relations_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 实体表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                attributes TEXT,
                doc_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
            )
        """)

        # 关系表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS relations (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                doc_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_id) REFERENCES entities(id) ON DELETE CASCADE,
                FOREIGN KEY (target_id) REFERENCES entities(id) ON DELETE CASCADE,
                FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
            )
        """)

        # 对话历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sources TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 会话表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                model TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 模型配置表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS model_configs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider TEXT NOT NULL,
                api_key TEXT,
                api_base TEXT,
                is_active BOOLEAN DEFAULT 0,
                config TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ==================== 记忆系统 ====================

        # 短期记忆摘要（主存 Redis，此表为兜底 / 可审计副本）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS short_term_summaries (
                session_id TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 中期记忆：跨会话历史问答原文（向量索引在 Qdrant chat_memory）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mid_term_qa (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                user_msg TEXT NOT NULL,
                ai_msg TEXT NOT NULL,
                sources TEXT,
                embedding_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 长期记忆：LLM 抽取的结构化事实（embedding 以 JSON 存 blob 列）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS long_term_facts (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                fact TEXT NOT NULL,
                category TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                embedding_blob TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 压缩事件审计：观测 80% 阈值触发频率与压缩收益
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS compression_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                pre_tokens INTEGER NOT NULL,
                post_tokens INTEGER NOT NULL,
                compressed_turns INTEGER NOT NULL,
                trigger TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 创建索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_entities_doc_id ON entities(doc_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relations_doc_id ON relations(doc_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_history_session ON chat_history(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_history_created ON chat_history(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mid_term_session ON mid_term_qa(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mid_term_created ON mid_term_qa(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_long_term_category ON long_term_facts(category)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_long_term_updated ON long_term_facts(updated_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_compression_session ON compression_events(session_id)")

        conn.commit()
        print(f"✅ 数据库初始化完成: {db_path}")


@contextmanager
def get_db():
    """获取数据库连接"""
    conn = sqlite3.connect(settings.SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def execute_query(query: str, params: tuple = ()):
    """执行查询"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor.fetchall()


def execute_many(query: str, params_list: list):
    """批量执行"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.executemany(query, params_list)
        conn.commit()


def execute_update(query: str, params: tuple = ()) -> int:
    """执行 INSERT/UPDATE/DELETE，返回受影响行数"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor.rowcount
