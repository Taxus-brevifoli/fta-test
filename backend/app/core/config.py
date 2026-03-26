from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # LLM
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    llm_embedding_model: str = "text-embedding-v4"
    llm_rerank_model: str = ""

    # 服务
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # CORS
    frontend_origin: str = "http://localhost:5173"

    # 上传目录
    upload_dir: str = "uploads"


settings = Settings()
