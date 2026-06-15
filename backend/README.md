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
```

生产环境必须设置 `APP_ENV=production`，并使用至少 32 字符的非默认随机 `APP_SECRET_KEY`；否则后端启动会失败。

`DEFAULT_AI_MODEL` 可选：

- `claude-opus-4`
- `claude-sonnet-4`
- `deepseek`
