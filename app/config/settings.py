"""
应用配置管理

使用 pydantic-settings 从 .env 文件和环境变量加载配置。
禁止在代码中硬编码密钥、地址等敏感信息。

所有配置项按职责分组，通过注释分隔。
保持扁平结构，不做嵌套 Model——避免当前阶段不必要的抽象。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ============================================================
    # Application
    # ============================================================
    app_name: str = "论文智答"
    app_version: str = "0.1.0"
    debug: bool = True

    # ============================================================
    # Server
    # ============================================================
    host: str = "127.0.0.1"
    port: int = 8000

    # ============================================================
    # Database — SQLite (Phase 3 启用)
    # ============================================================
    database_url: str = "sqlite+aiosqlite:///data/graphrag.db"

    # ============================================================
    # LLM — 大语言模型 (Phase 5-7 启用)
    # ============================================================
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"

    # ============================================================
    # Embedding — 向量嵌入模型 (Phase 5 启用)
    # ============================================================
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"

    # ============================================================
    # Storage — 数据与日志存储路径
    # ============================================================
    data_dir: str = "data"
    log_dir: str = "logs"

    # ============================================================
    # Parser — 文档解析 (Phase 4)
    # ============================================================
    parser_chunk_size: int = 500
    parser_chunk_overlap: int = 50

    # ============================================================
    # Paper Assistant
    # ============================================================
    paper_chunk_size: int = 1200
    paper_chunk_overlap: int = 150

    # ============================================================
    # Retrieval — 检索参数 (Phase 5)
    # ============================================================
    retrieval_top_k: int = 5
    retrieval_vector_weight: float = 0.7
    retrieval_keyword_weight: float = 0.3

    # ============================================================
    # Memory — 会话记忆 (Phase 6)
    # ============================================================
    short_memory_size: int = 10

    # ============================================================
    # Research — 文献搜索下载 Agent
    # ============================================================
    research_top_k: int = 20
    research_search_timeout: float = 15.0
    research_download_delay: float = 4.0
    research_proxy: str = ""
    vpn_portal_url: str = "https://vpn.swjtu.edu.cn"
    unpaywall_email: str = ""

    # ============================================================
    # Logging
    # ============================================================
    log_level: str = "DEBUG"


# 全局单例，项目内统一通过此实例读取配置
settings = Settings()
