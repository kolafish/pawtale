from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from pathlib import Path

# 获取 backend 目录的绝对路径
BACKEND_DIR = Path(__file__).parent.parent.resolve()
DATA_DIR = BACKEND_DIR.parent / "data"
DEFAULT_APP_SECRET_KEY = "dev-secret-key-change-in-production"
MIN_PRODUCTION_SECRET_LENGTH = 32
VALID_APP_ENVS = {"development", "test", "production"}
DEFAULT_AI_TEXT_MODEL = "claude-opus-4-8"
VALID_AI_TEXT_MODELS = {
    "deepseek",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "volc-doubao-pro",
    "doubao-seed-2-0-pro-260215",
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-sonnet-4",
    "claude-sonnet-4-6",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        extra="ignore",
    )

    app_env: str = "development"
    app_secret_key: str = DEFAULT_APP_SECRET_KEY
    database_url: str = f"sqlite+aiosqlite:///{DATA_DIR}/pawtale.db"
    sqlite_journal_mode: str = "WAL"
    sqlite_synchronous: str = "NORMAL"
    sqlite_busy_timeout_ms: int = 5000
    local_default_user_email: str = "local@pawtale.local"
    local_default_user_password: str = "pawtale-local"
    local_default_user_display_name: str = "本地作者"
    admin_emails: str = ""

    # WeChat Mini Program auth
    wechat_miniprogram_appid: str = ""
    wechat_miniprogram_secret: str = ""
    wechat_xiaoyu_miniprogram_appid: str = ""
    wechat_xiaoyu_miniprogram_secret: str = ""
    wechat_jscode2session_url: str = "https://api.weixin.qq.com/sns/jscode2session"

    # AI API Keys
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_thinking_type: str = "disabled"

    # VolcEngine Ark API Keys
    volc_ark_api_key: str = ""
    volc_ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    volc_ark_model: str = "doubao-seed-2-0-pro-260215"
    volc_ark_thinking_type: str = "disabled"
    volc_ark_image_model: str = "doubao-seedream-5-0-lite-260128"
    volc_ark_image_size: str = "2K"
    volc_ark_image_output_format: str = "png"
    volc_ark_image_response_format: str = "b64_json"
    volc_ark_image_timeout_seconds: float = 180.0
    volc_ark_image_max_concurrent_requests: int = 1
    memoir_share_video_ai_background_enabled: bool = False
    memoir_share_video_scene_planner_enabled: bool = True
    memoir_share_video_scene_planner_model: str = "doubao-seed-2-0-pro-260215"

    # Claude API Keys
    anthropic_auth_token: str = ""
    anthropic_base_url: str = "https://blankapi.com"

    # Default AI Model
    default_ai_model: str = DEFAULT_AI_TEXT_MODEL
    ai_text_max_concurrent_requests: int = 3
    ai_text_realtime_timeout_seconds: float = 15.0
    ai_text_interactive_timeout_seconds: float = 60.0
    ai_text_generation_timeout_seconds: float = 180.0

    # StepFun batch ASR for user-triggered material reprocessing and offline tools.
    stepfun_api_key: str = ""
    stepfun_base_url: str = "https://api.stepfun.com"
    stepfun_asr_model: str = "stepaudio-2.5-asr"
    stepfun_asr_timeout_seconds: float = 120.0

    # Memoir editing worker
    memoir_chapter_revision_delay_seconds: int = 300
    memoir_quality_audit_delay_seconds: int = 120
    memoir_editing_worker_enabled: bool = True
    memoir_editing_poll_seconds: int = 1
    memoir_editing_batch_size: int = 5
    memoir_editing_lease_seconds: int = 300
    memoir_editing_heartbeat_seconds: int = 30
    memoir_editing_job_deadline_seconds: float = 3600.0
    memoir_interview_policy_shadow_enabled: bool = False
    memoir_interview_policy_shadow_model: str = ""
    memoir_interview_policy_active_enabled: bool = False
    memoir_interview_policy_active_rollout_percent: int = 0
    memoir_interview_policy_active_model: str = ""
    memoir_interview_policy_outcome_model: str = ""
    memoir_topic_map_shadow_enabled: bool = False
    memoir_topic_map_shadow_model: str = ""
    memoir_share_video_worker_enabled: bool = True
    memoir_share_video_poll_seconds: int = 2
    memoir_share_video_batch_size: int = 1
    memoir_share_video_lease_seconds: int = 900
    memoir_share_video_heartbeat_seconds: int = 30
    memoir_share_video_job_deadline_seconds: float = 1800.0
    memoir_share_video_max_attempts: int = 3
    memoir_share_video_retry_seconds: int = 10
    memoir_book_export_deadline_seconds: float = 240.0

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
    voice_gateway_max_active_sessions: int = 10
    voice_gateway_max_active_sessions_per_user: int = 1

    # Assistant TTS
    tts_provider: str = "disabled"
    tts_timeout_seconds: float = 30.0
    tts_max_concurrent_requests: int = 3
    volc_tts_app_id: str = ""
    volc_tts_access_token: str = ""
    volc_tts_api_key: str = ""
    volc_tts_endpoint: str = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
    volc_tts_bidirectional_endpoint: str = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
    volc_tts_resource_id: str = "seed-tts-2.0"
    volc_tts_speaker: str = "zh_female_vv_uranus_bigtts"
    volc_tts_response_format: str = "mp3"
    volc_tts_sample_rate: int = 24000

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

    default_ai_model = current_settings.default_ai_model.strip().lower()
    if default_ai_model not in VALID_AI_TEXT_MODELS:
        raise RuntimeError(
            f"DEFAULT_AI_MODEL is not supported: {default_ai_model}"
        )

    if app_env != "production":
        return

    secret = current_settings.app_secret_key.strip()
    if not secret or secret == DEFAULT_APP_SECRET_KEY:
        raise RuntimeError("APP_SECRET_KEY must be set to a non-default value when APP_ENV=production")
    if len(secret) < MIN_PRODUCTION_SECRET_LENGTH:
        raise RuntimeError(
            f"APP_SECRET_KEY must be at least {MIN_PRODUCTION_SECRET_LENGTH} characters when APP_ENV=production"
        )
