# Pawtale Backend

FastAPI 后端，提供认证、项目写作系统、项目资料和 AI 辅助 API。

## 技术栈

- FastAPI
- async SQLAlchemy
- SQLite + aiosqlite
- Pydantic
- pytest
- DeepSeek / Claude 文本模型

## 运行

```bash
cd backend
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
PYTHONPATH=. uvicorn app.main:app --reload --port 8000
```

macOS 上如果 `python3.12` 指向 Miniforge/Conda，建议用 Homebrew Python 创建 venv：
`/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv venv`。

API 文档：

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 测试

```bash
cd backend
venv/bin/python -m pytest -q
```

常用定向测试：

```bash
venv/bin/python -m pytest tests/test_project_core.py -q
venv/bin/python -m pytest tests/test_project_structure.py -q
venv/bin/python -m pytest tests/test_project_references.py -q
venv/bin/python -m pytest tests/test_project_ai.py -q
```

## 回忆录语音网关压测

只验证 10 个不同登录用户的 WebSocket 容量，不触发真实 ASR/LLM/TTS：

```bash
cd backend
venv/bin/python scripts/loadtest_voice_gateway.py \
  --base-url https://pawtale-api.spaceflows.cn \
  --users 10 \
  --hold-seconds 120 \
  --mode capacity \
  --expect-eleventh-rejected
```

脚本会自动注册测试用户、创建 voice session、携带各自的 `pawtale_session` cookie 连接 gateway，并验证第 11 条连接返回 `voice_gateway_capacity_reached`。如果指定 `--mode asr`，脚本会发送生成的静音 PCM 帧并触发真实 ASR provider，请先确认 provider 额度和费用。

## 核心结构

```text
app/
├── main.py
├── auth.py
├── domain/
│   ├── projects/
│   ├── novel/
│   ├── article/
│   └── memoir/
├── models/
├── schemas/
├── routers/
│   ├── auth.py
│   └── projects/
└── services/
    ├── projects/
    ├── project_documents.py
    ├── content_storage.py
    └── ai_*.py
```

## 项目系统

- `Project.type` 支持 `novel`、`article`、`memoir`
- 默认模板由 `app/domain/projects/templates.py` 分发
- `/api/projects` 路由按 `core`、`structure`、`references`、`activity`、`ai` 拆分
- 项目业务编排放在 `app/services/projects/`
- 文档正文由 `content_storage.py` 存在 `data/projects/{project_id}/{document_id}.md`

## 环境变量

`backend/.env`：

```bash
APP_ENV=development
APP_SECRET_KEY=replace-with-at-least-32-random-characters
# DATABASE_URL=sqlite+aiosqlite:///../data/pawtale.db

WECHAT_MINIPROGRAM_APPID=wx71acdf73e2ba7067
WECHAT_MINIPROGRAM_SECRET=xxx
WECHAT_JSCODE2SESSION_URL=https://api.weixin.qq.com/sns/jscode2session

DEEPSEEK_API_KEY=xxx
DEEPSEEK_BASE_URL=xxx

ANTHROPIC_AUTH_TOKEN=xxx
ANTHROPIC_BASE_URL=xxx

DEFAULT_AI_MODEL=claude-opus-4
AI_TEXT_MAX_CONCURRENT_REQUESTS=3

# GitHub App 单向备份。Private key 只放后端，可用 inline 内容或文件路径二选一。
GITHUB_APP_ID=
GITHUB_APP_SLUG=
GITHUB_APP_PRIVATE_KEY=
GITHUB_APP_PRIVATE_KEY_PATH=
GITHUB_APP_SETUP_REDIRECT_URL=http://localhost:5173/
GITHUB_SYNC_DEFAULT_BRANCH=main

# AI 追问语音合成，默认 disabled；启用火山 V3 时改成 volc
TTS_PROVIDER=disabled
TTS_TIMEOUT_SECONDS=30
TTS_MAX_CONCURRENT_REQUESTS=3

# 火山/豆包语音 V3 TTS。AI 访谈和成书语音编辑使用 bidirectional WebSocket，历史消息重播和长文朗读仍可使用 unidirectional SSE。
VOLC_TTS_APP_ID=
VOLC_TTS_ACCESS_TOKEN=
VOLC_TTS_API_KEY=
VOLC_TTS_ENDPOINT=https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse
VOLC_TTS_BIDIRECTIONAL_ENDPOINT=wss://openspeech.bytedance.com/api/v3/tts/bidirection
VOLC_TTS_RESOURCE_ID=seed-tts-2.0
VOLC_TTS_SPEAKER=zh_female_vv_uranus_bigtts
VOLC_TTS_RESPONSE_FORMAT=mp3
VOLC_TTS_SAMPLE_RATE=24000
```

生产环境必须设置 `APP_ENV=production`，并使用至少 32 字符的非默认随机 `APP_SECRET_KEY`；否则后端启动会失败。

`DEFAULT_AI_MODEL` 可选：

- `claude-opus-4`
- `claude-sonnet-4`
- `deepseek`
