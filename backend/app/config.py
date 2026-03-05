from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path

# 获取 backend 目录的绝对路径
BACKEND_DIR = Path(__file__).parent.parent.resolve()
DATA_DIR = BACKEND_DIR.parent / "data"


class Settings(BaseSettings):
    app_env: str = "development"
    app_secret_key: str = "dev-secret-key-change-in-production"
    database_url: str = f"sqlite+aiosqlite:///{DATA_DIR}/pawtale.db"

    # AI API Keys
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    openai_api_key: str = ""

    # Claude API Keys
    anthropic_auth_token: str = ""
    anthropic_base_url: str = "https://cursor.scihub.edu.kg/api"

    # Default AI Model
    default_ai_model: str = "deepseek"  # deepseek / claude-opus-4 / claude-sonnet-4

    class Config:
        env_file = str(BACKEND_DIR / ".env")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
