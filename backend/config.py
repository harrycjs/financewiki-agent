"""
配置管理模块
"""
import os
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """应用配置"""

    # 应用配置
    APP_NAME: str = "FinanceWiki Agent"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    DEBUG: bool = True

    # Qdrant配置
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_GRPC_PORT: int = 6334

    # Redis配置
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None

    # SQLite配置
    SQLITE_DB_PATH: str = "./data/finance_agent.db"

    # 大模型API配置
    ZHIPU_API_KEY: Optional[str] = None
    ZHIPU_API_BASE: str = "https://open.bigmodel.cn/api/paas/v4"

    DEEPSEEK_API_KEY: Optional[str] = None
    DEEPSEEK_API_BASE: str = "https://api.deepseek.com"

    KIMI_API_KEY: Optional[str] = None
    KIMI_API_BASE: str = "https://api.moonshot.cn/v1"

    MINIMAX_API_KEY: Optional[str] = None
    MINIMAX_API_BASE: str = "https://api.minimax.chat/v1"

    # Embedding配置
    EMBEDDING_PROVIDER: str = "local"  # local, zhipu, deepseek
    EMBEDDING_MODEL: str = "BAAI/bge-large-zh-v1.5"  # 本地模型
    EMBEDDING_DIMENSION: int = 1024  # bge-large-zh-v1.5维度

    # 检索配置
    RETRIEVAL_TOP_K: int = 10
    CACHE_TTL: int = 3600  # 1小时
    EMBEDDING_CACHE_TTL: int = 86400  # 24小时

    # 日志配置
    LOG_LEVEL: str = "INFO"

    # 默认模型
    DEFAULT_MODEL: str = "deepseek"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# 创建全局配置实例
settings = Settings()

# 确保数据目录存在
Path(settings.SQLITE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
Path("./data/documents").mkdir(parents=True, exist_ok=True)
Path("./data/knowledge_graph").mkdir(parents=True, exist_ok=True)
Path("./logs").mkdir(parents=True, exist_ok=True)
