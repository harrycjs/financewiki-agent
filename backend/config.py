"""
配置管理模块
"""
import os
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import List, Optional


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

    # ==================== 记忆系统 ====================
    # 短期记忆：当前会话原文窗口（Redis，SQLite 兜底回填）
    MEMORY_SHORT_TERM_TTL: int = 86400          # 24小时；miss 时从 chat_history 回填
    MEMORY_SHORT_TERM_ANCHOR_RENDER: int = 0    # 0 = 渲染全部锚点原文；>0 只渲染最近 N 条

    # 中期记忆：跨会话历史问答向量召回
    MEMORY_MID_TERM_COLLECTION: str = "chat_memory"
    MEMORY_MID_TERM_TOP_K: int = 5
    MEMORY_MID_TERM_SCORE_THRESHOLD: float = 0.6
    MEMORY_MID_TERM_SNIPPET_CHARS: int = 150

    # 长期记忆：LLM 抽取的结构化事实
    MEMORY_LONG_TERM_TOP_K: int = 5
    MEMORY_LONG_TERM_SCORE_THRESHOLD: float = 0.5
    MEMORY_LONG_TERM_DEDUP_THRESHOLD: float = 0.92   # 余弦相似度超过则合并而非新增
    MEMORY_LONG_TERM_SNIPPET_CHARS: int = 100
    MEMORY_ENABLE_LONG_TERM_EXTRACT: bool = True     # 出问题可一键关闭 LLM 抽取

    # ==================== 上下文压缩 ====================
    COMPRESSION_CONTEXT_WINDOW: int = 200_000    # 统一上下文窗口
    COMPRESSION_TRIGGER_RATIO: float = 0.8       # 后台主通道触发线
    COMPRESSION_HARD_RATIO: float = 0.95         # 请求路径同步安全阀
    COMPRESSION_ANCHOR_RECENT_TURNS: int = 3     # 保留最近 N 轮原文不压缩
    COMPRESSION_SUMMARY_MAX_TOKENS: int = 2000
    COMPRESSION_LLM_TEMPERATURE: float = 0.2

    # ==================== Agent 工具 ====================
    # 工具沙箱根目录（多根）：
    # - WORKSPACE_ROOT：用户文件沙箱，bash 默认 cwd，新文件写到这
    # - SKILLS_ROOT：技能目录；skill-creator 等的 scripts/ 必须可读可执行，
    #                agent 创建新 skill 时也要能写到这里
    WORKSPACE_ROOT: str = "./data/workspace"
    SKILLS_ROOT: str = "./backend/skills"
    AGENT_MAX_STEPS: int = 8         # 工具循环最大轮数，防止 LLM 打转烧 token
    BASH_TIMEOUT: int = 30           # bash 单条命令超时（秒）
    BASH_MAX_OUTPUT: int = 8000      # bash / read_file 回灌给 LLM 的最大字符数
    TAVILY_API_KEY: Optional[str] = None
    TAVILY_API_BASE: str = "https://api.tavily.com"

    @property
    def allowed_roots(self) -> List[Path]:
        """工具沙箱允许的根目录列表。所有 read/write 都必须落在其中之一内。"""
        roots: List[Path] = []
        for p in (self.WORKSPACE_ROOT, self.SKILLS_ROOT):
            try:
                roots.append(Path(p).resolve())
            except Exception:
                pass
        return roots

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# 创建全局配置实例
settings = Settings()

# 确保数据目录存在
Path(settings.SQLITE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
Path("./data/documents").mkdir(parents=True, exist_ok=True)
Path("./data/knowledge_graph").mkdir(parents=True, exist_ok=True)
Path(settings.WORKSPACE_ROOT).mkdir(parents=True, exist_ok=True)
Path(settings.SKILLS_ROOT).mkdir(parents=True, exist_ok=True)
Path("./logs").mkdir(parents=True, exist_ok=True)
