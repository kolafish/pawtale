# Pawtale

AI 辅助内容创作工作台。当前核心是以项目为单位管理写作，支持小说、公众号文章、回忆录三个创作方向。

## 当前能力

- 项目类型：小说、公众号、回忆录
- 项目结构：Project -> DocumentGroup -> Document
- 项目内参考资料：角色、地点、笔记、大纲
- 写作编辑器：自动保存、Cmd+S、字数统计、TXT 导出
- AI 辅助：续写、单模型修改、多模型修改对比、diff review、项目内容分析
- 多模型：Claude Opus 4.6、Claude Sonnet 4.6、DeepSeek
- 登录与用户隔离：项目、资料和 API 均基于当前用户访问

## 产品方向

Pawtale 目前保留在一个代码库中，通过项目类型和 workspace 边界拆分产品体验：

- 小说创作：章节结构、大纲、人物设定、Inspector、项目分析
- 公众号创作：较短篇幅，当前复用共享草稿工作台
- 回忆录创作：小程序正在从固定问题面板改版为实时语音聊天，目标是弱引导/强引导两种访谈模式、Streaming ASR 大字转写、LLM 动态追问和 Streaming TTS 语音回复

## 项目结构

```text
Pawtale/
├── backend/                    # FastAPI + async SQLAlchemy
│   ├── app/
│   │   ├── domain/             # 项目类型和默认模板
│   │   ├── models/             # SQLAlchemy models
│   │   ├── routers/            # API routers
│   │   ├── schemas/            # Pydantic schemas
│   │   └── services/           # 业务服务和 AI 服务
│   └── tests/
├── frontend/                   # React + TypeScript + Vite
│   └── src/
│       ├── components/
│       ├── domain/
│       ├── hooks/
│       ├── stores/
│       └── api/
├── miniprogram/                # 微信小程序回忆录采集端
├── data/                       # SQLite 数据库和项目文档文件
├── PRODUCT.md                  # 产品文档
└── TECH.md                     # 技术文档
```

## 快速开始

### 后端

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

### 前端

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173

## 环境变量

后端读取 `backend/.env`：

| 变量 | 说明 |
| --- | --- |
| `APP_ENV` | 应用环境：`development` / `test` / `production`；生产环境必须显式设为 `production` |
| `APP_SECRET_KEY` | session cookie 签名密钥；生产环境必须是至少 32 字符的非默认随机值 |
| `DATABASE_URL` | 可选数据库连接；不设置时使用 `data/pawtale.db` |
| `SQLITE_JOURNAL_MODE` | SQLite journal 模式；默认 `WAL`，用于小规模并发读写 |
| `SQLITE_SYNCHRONOUS` | SQLite 同步级别；默认 `NORMAL` |
| `SQLITE_BUSY_TIMEOUT_MS` | SQLite 写锁等待时间；默认 `5000` |
| `WECHAT_MINIPROGRAM_APPID` | 微信小程序 AppID，用于后端 code2session |
| `WECHAT_MINIPROGRAM_SECRET` | 微信小程序 AppSecret，只能放后端 `.env` |
| `WECHAT_JSCODE2SESSION_URL` | 微信 code2session 接口地址 |
| `DEEPSEEK_API_KEY` | DeepSeek API key |
| `DEEPSEEK_BASE_URL` | DeepSeek API 地址 |
| `ANTHROPIC_AUTH_TOKEN` | Claude 代理认证 token |
| `ANTHROPIC_BASE_URL` | Claude 代理地址，OpenAI 兼容格式 |
| `DEFAULT_AI_MODEL` | 默认模型：`claude-opus-4` / `claude-sonnet-4` / `deepseek` |
| `GITHUB_APP_ID` | 可选，GitHub App ID，用于项目单向备份到用户授权仓库 |
| `GITHUB_APP_SLUG` | 可选，GitHub App slug，用于生成安装链接 |
| `GITHUB_APP_PRIVATE_KEY` / `GITHUB_APP_PRIVATE_KEY_PATH` | 可选，GitHub App private key；只放后端，二选一 |
| `GITHUB_APP_SETUP_REDIRECT_URL` | 可选，GitHub App 安装完成后的前端跳转地址 |
| `VOLC_ASR_API_KEY` | 豆包流式 ASR API key |
| `VOICE_GATEWAY_MAX_ACTIVE_SESSIONS` | 回忆录语音网关全局 active WebSocket 上限；默认 `10` |
| `VOICE_GATEWAY_MAX_ACTIVE_SESSIONS_PER_USER` | 单用户 active 语音网关上限；默认 `1` |
| `VOLC_ASR_RESOURCE_ID` | 豆包流式 ASR 资源 ID |
| `VOLC_ASR_WS_URL` | 豆包流式 ASR WebSocket 地址 |

## 常用命令

```bash
# 前端检查
cd frontend && npm run lint
cd frontend && npm run build

# 小程序检查
./scripts/test-miniprogram.sh

# 后端测试
cd backend && venv/bin/python -m pytest -q
```

## 文档

- [PRODUCT.md](./PRODUCT.md)
- [TECH.md](./TECH.md)
- [docs/MEMOIR_VOICE_CHAT_REDESIGN.md](./docs/MEMOIR_VOICE_CHAT_REDESIGN.md) 回忆录小程序新版主线
- [docs/MEMOIR_MINIPROGRAM_PRODUCT.md](./docs/MEMOIR_MINIPROGRAM_PRODUCT.md) legacy
- [docs/MEMOIR_MEMORY_ARCHITECTURE.md](./docs/MEMOIR_MEMORY_ARCHITECTURE.md) legacy/long-term reference
- [docs/MEMOIR_MEMORY_FIRST_V1_PLAN.md](./docs/MEMOIR_MEMORY_FIRST_V1_PLAN.md) legacy
- [miniprogram/README.md](./miniprogram/README.md)
- [backend/README.md](./backend/README.md)
- [frontend/README.md](./frontend/README.md)
