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

    # WeChat Mini Program auth
    wechat_miniprogram_appid: str = ""
    wechat_miniprogram_secret: str = ""
    wechat_jscode2session_url: str = "https://api.weixin.qq.com/sns/jscode2session"

    # AI API Keys
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"

    # Claude API Keys
    anthropic_auth_token: str = ""
    anthropic_base_url: str = "https://cursor.scihub.edu.kg/api"

    # Default AI Model
    default_ai_model: str = "claude-opus-4"  # deepseek / claude-opus-4 / claude-sonnet-4

    # VolcEngine / Doubao Streaming ASR
    volc_asr_api_key: str = ""
    volc_asr_app_key: str = ""
    volc_asr_access_key: str = ""
    volc_asr_resource_id: str = "volc.seedasr.sauc.duration"
    volc_asr_ws_url: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
    volc_asr_connect_timeout_seconds: float = 10.0

    class Config:
        env_file = str(BACKEND_DIR / ".env")
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
