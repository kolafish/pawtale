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
    local_default_user_email: str = "local@pawtale.local"
    local_default_user_password: str = "pawtale-local"
    local_default_user_display_name: str = "本地作者"

    # AI API Keys
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"

    # Claude API Keys
    anthropic_auth_token: str = ""
    anthropic_base_url: str = "https://cursor.scihub.edu.kg/api"

    # Default AI Model
    default_ai_model: str = "claude-opus-4"  # deepseek / claude-opus-4 / claude-sonnet-4

    class Config:
        env_file = str(BACKEND_DIR / ".env")
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
