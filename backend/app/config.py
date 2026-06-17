from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path

# 获取 backend 目录的绝对路径
BACKEND_DIR = Path(__file__).parent.parent.resolve()
DATA_DIR = BACKEND_DIR.parent / "data"
DEFAULT_APP_SECRET_KEY = "dev-secret-key-change-in-production"
MIN_PRODUCTION_SECRET_LENGTH = 32
VALID_APP_ENVS = {"development", "test", "production"}


class Settings(BaseSettings):
    app_env: str = "development"
    app_secret_key: str = DEFAULT_APP_SECRET_KEY
    database_url: str = f"sqlite+aiosqlite:///{DATA_DIR}/pawtale.db"
    sqlite_journal_mode: str = "WAL"
    sqlite_synchronous: str = "NORMAL"
    sqlite_busy_timeout_ms: int = 5000
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

    # GitHub App one-way project backup
    github_app_id: str = ""
    github_app_slug: str = ""
    github_app_private_key: str = ""
    github_app_private_key_path: str = ""
    github_api_base_url: str = "https://api.github.com"
    github_web_base_url: str = "https://github.com"
    github_app_setup_redirect_url: str = "http://localhost:5173/"
    github_sync_default_branch: str = "main"

    # VolcEngine / Doubao Streaming ASR
    volc_asr_api_key: str = ""
    volc_asr_app_key: str = ""
    volc_asr_access_key: str = ""
    volc_asr_resource_id: str = "volc.seedasr.sauc.duration"
    volc_asr_ws_url: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
    volc_asr_connect_timeout_seconds: float = 10.0

    # Assistant TTS
    tts_provider: str = "disabled"
    tts_api_key: str = ""
    tts_base_url: str = "https://api.openai.com/v1"
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"
    tts_response_format: str = "mp3"
    tts_timeout_seconds: float = 30.0
    tts_alignment_provider: str = "disabled"
    tts_alignment_api_key: str = ""
    tts_alignment_base_url: str = "https://api.elevenlabs.io/v1"
    tts_alignment_timeout_seconds: float = 30.0
    elevenlabs_api_key: str = ""
    elevenlabs_base_url: str = "https://api.elevenlabs.io/v1"

    # StepFun TTS. STEPFUN_API_KEY is kept separate from the generic TTS key
    # so existing StepFun credentials can be reused by setting TTS_PROVIDER=stepfun.
    stepfun_api_key: str = ""
    stepfun_base_url: str = "https://api.stepfun.ai/v1"
    stepfun_tts_model: str = "step-tts-2"
    stepfun_tts_voice: str = "cixingnansheng"

    class Config:
        env_file = str(BACKEND_DIR / ".env")
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


def normalized_app_env(settings: Settings) -> str:
    return settings.app_env.strip().lower()


def is_production_environment(settings: Settings | None = None) -> bool:
    current_settings = settings or get_settings()
    return normalized_app_env(current_settings) == "production"


def validate_runtime_settings(settings: Settings | None = None) -> None:
    current_settings = settings or get_settings()
    app_env = normalized_app_env(current_settings)
    if app_env not in VALID_APP_ENVS:
        valid_envs = ", ".join(sorted(VALID_APP_ENVS))
        raise RuntimeError(f"APP_ENV must be one of: {valid_envs}")

    if app_env != "production":
        return

    secret = current_settings.app_secret_key.strip()
    if not secret or secret == DEFAULT_APP_SECRET_KEY:
        raise RuntimeError("APP_SECRET_KEY must be set to a non-default value when APP_ENV=production")
    if len(secret) < MIN_PRODUCTION_SECRET_LENGTH:
        raise RuntimeError(
            f"APP_SECRET_KEY must be at least {MIN_PRODUCTION_SECRET_LENGTH} characters when APP_ENV=production"
        )
