# Pawtale 技术文档

## 架构概览

```text
React 19 + TypeScript + Vite
TailwindCSS + Zustand + TanStack Query
        |
        | /api
        v
FastAPI + async SQLAlchemy
SQLite + file-backed document content
        |
        +-- DeepSeek
        +-- Claude Opus 4.6 / Sonnet 4.6

WeChat Mini Program
        |
        +-- backend HTTP APIs + voice gateway WebSocket
        +-- streaming ASR/TTS + chat orchestration
```

Pawtale 当前是单仓库、前后端分离架构。后端提供项目、文档、项目资料和 AI API；前端按项目类型分发到不同 workspace，并复用一套草稿编辑和资料编辑基础能力。

回忆录小程序不是 Web 工作台的移动版。当前代码已切到实时语音聊天形态，引入弱引导/强引导两种访谈模式，由 backend voice gateway 承接麦克风帧、Streaming ASR、LLM 动态追问和 Streaming TTS。

回忆录新版主线见 [docs/MEMOIR_VOICE_CHAT_REDESIGN.md](docs/MEMOIR_VOICE_CHAT_REDESIGN.md)。旧的固定问题和 memory-first v1 文档仅作为 legacy 背景和长期原则参考。

## 技术栈

### 后端

- Python 3.11+
- FastAPI
- SQLAlchemy async ORM
- SQLite + aiosqlite
- Pydantic
- pytest

### 前端

- React 19
- TypeScript
- Vite
- TailwindCSS
- Zustand
- TanStack Query
- Lucide React
- @dnd-kit

### 微信小程序

- 微信开发者工具
- TypeScript
- ECS/Backend HTTP API
- WSS voice gateway

### AI

- 文本：DeepSeek、Claude Opus 4.6、Claude Sonnet 4.6
- 服务边界：`AITextServiceBase` 定义 provider 合约和薄代理；prompt/use-case 逻辑在 `ai_story.py`、`ai_revision.py`、`ai_project_analysis.py`

## 后端结构

```text
backend/app/
├── main.py
├── auth.py
├── config.py
├── database.py
├── migrations/
│   └── auth.py
├── domain/
│   ├── references.py
│   ├── projects/
│   │   ├── types.py
│   │   └── templates.py
│   ├── novel/template.py
│   ├── article/template.py
│   └── memoir/template.py
├── models/
│   ├── users.py
│   └── projects.py
├── schemas/
│   ├── auth.py
│   └── projects.py
├── routers/
│   ├── auth.py
│   └── projects/
│       ├── core.py
│       ├── structure.py
│       ├── references.py
│       ├── activity.py
│       └── ai.py
└── services/
    ├── projects/
    │   ├── access.py
    │   ├── core.py
    │   ├── structure.py
    │   ├── references.py
    │   ├── activity.py
    │   └── ai.py
    ├── project_documents.py
    ├── content_storage.py
    ├── ai_base.py
    ├── ai_story.py
    ├── ai_revision.py
    ├── ai_project_analysis.py
    ├── ai_factory.py
    ├── ai_text.py
    └── ai_claude.py
```

### 后端边界

- `routers/projects/*` 只处理 HTTP 请求、依赖注入和 response model
- `services/projects/*` 承载项目业务编排
- `services/projects/access.py` 统一做项目归属校验
- `services/project_documents.py` 统一处理文档正文文件、数据库提交和回滚补偿
- `domain/projects/templates.py` 按项目类型分发默认模板
- `domain/references.py` 统一定义 Reference 类型和值清洗

回忆录 memory-first 能力继续放在 `app/services/memoir/` 下演进。第一期计划新增：

```text
app/services/memoir/
├── source_artifacts.py
├── transcript_versions.py
├── memory_extraction.py
├── memory_repository.py
├── memory_resolution.py
├── coverage.py
├── question_planner.py
├── session_summary.py
└── outputs.py
```

这些 service 复用现有认证、项目归属校验、AI provider 和数据库层，不引入独立后端框架。

## 前端结构

```text
frontend/src/
├── api/index.ts
├── constants/aiModels.ts
├── domain/
│   ├── projectProfiles.ts
│   └── references.ts
├── stores/appStore.ts
├── types/index.ts
├── utils/
├── hooks/
│   ├── useProjectState.ts
│   ├── useReferenceEditor.ts
│   ├── useAIRevision.ts
│   └── project/
│       ├── useDocumentDraft.ts
│       ├── useProjectStructure.ts
│       ├── useProjectReferences.ts
│       └── useDraftRevision.ts
└── components/
    ├── Layout.tsx
    ├── WritingWorkspace.tsx
    ├── ProjectHome.tsx
    ├── Binder.tsx
    ├── binder/
    │   ├── ManuscriptTree.tsx
    │   ├── ReferenceSections.tsx
    │   └── useBinderDrag.ts
    ├── workspaces/
    │   ├── WorkspaceShell.tsx
    │   ├── ProjectDraftWorkspace.tsx
    │   ├── NovelWorkspace.tsx
    │   ├── ArticleWorkspace.tsx
    │   └── MemoirWorkspace.tsx
    ├── EditorMain.tsx
    ├── AIAssistPanel.tsx
    ├── DiffReviewView.tsx
    ├── Inspector.tsx
    ├── CharacterEditor.tsx
    ├── NoteEditor.tsx
    └── OutlineEditor.tsx
```

### 前端边界

- `WritingWorkspace` 根据 `currentProject.type` 选择产品 workspace
- `WorkspaceShell` 只负责布局：Binder、移动端目录、产品面板、右侧面板、弹窗槽位
- `ProjectDraftWorkspace` 负责共享草稿工作台装配
- `useProjectState` 是 workspace facade，组合文档草稿、结构、资料、AI 续写和导出
- `useDocumentDraft` 管理文档内容、快照、auto-save、Cmd+S 和切换文档
- `useProjectStructure` 管理分组/文档 CRUD 与排序
- `useProjectReferences` 管理项目资料 CRUD 与选中
- `useDraftRevision` 连接当前草稿和 AI 修改流程
- `useAIRevision` 只负责 AI 修改的 diff/review 状态
- 项目资料只通过 Binder + 主编辑区创建和编辑；Inspector 只负责文档属性、文档关联资料和项目信息

## 小程序语音聊天结构

```text
miniprogram/
├── config.ts
├── services/
│   ├── request.ts
│   ├── auth.ts                      # ECS cookie session
│   ├── memoir.ts                    # project and memoir HTTP APIs
│   ├── memoir-voice-api.ts          # voice session API helpers
│   └── memoir-streaming-asr.ts      # recorder frames + WebSocket transcript
└── pages/
    ├── login/                       # ECS email login/register
    ├── home/                        # voice entry
    ├── projects/                    # memoir project management
    ├── voice-start/                 # guidance mode selection
    └── voice-chat/                  # realtime transcript + voice chat

backend/app/routers/memoir/
└── voice.py                         # voice session HTTP + WebSocket gateway
```

边界约定：

- `voice-start` 负责选择弱引导/强引导，`voice-chat` 只管理语音会话、实时字幕、聊天消息和播放状态。
- Editing：`transcript-review` 负责回听原始语音、校正 ASR 文本和保存 transcript version；`memoir-progress` 负责展示人、事、地点、时间段、主题覆盖度。
- Provider key 只能在后端或 voice gateway，不能进入小程序。

## 数据模型

### 项目系统

```text
users
└─ id, email, password_hash, created_at, updated_at

projects
├─ id, user_id, type
├─ title, description
├─ word_count_target, daily_word_goal
└─ created_at, updated_at

document_groups
├─ id, project_id
├─ name, sort_order
└─ created_at, updated_at

documents
├─ id, group_id
├─ name, content
├─ word_count, synopsis, status, label
├─ word_count_target, sort_order
└─ created_at, updated_at

reference_items
├─ id, project_id
├─ type, name, content, metadata_json
├─ sort_order
└─ created_at, updated_at

document_references
├─ document_id
└─ reference_id

writing_sessions
├─ id, project_id
├─ date, words_written
└─ created_at
```

说明：

- `projects.type` 当前支持 `novel`、`article`、`memoir`
- `documents.content` 保留数据库兼容字段，正文实际由 `content_storage.py` 存到 `data/projects/{project_id}/{document_id}.md`
- `reference_items.type` 由 `domain/references.py` 约束，当前支持 `character`、`location`、`note`、`outline`
- `writing_sessions` 是写作记录能力，目前保留 API 和表结构

### 回忆录 Voice-chat 数据层规划

Legacy 采集流程当前仍有这些表：

```text
memoir_sessions
memoir_questions
memoir_answers
memoir_drafts
audio_files
```

新版语音聊天主线以这些对象为中心：

```text
memoir_voice_sessions
memoir_chat_messages
memoir_voice_utterances
memoir_audio_files
memoir_transcript_versions
```

`memoir_voice_sessions` 必须记录 `guidance_mode`，取值为 `free_narration` 或 `guided_interview`。弱引导允许叙述者按时间线自主讲述；强引导由 LLM 按人物、事件、地点、时间段、主题等模块推进。

长期记忆层继续保留以下方向，但 evidence 应指向 chat message、transcript version 和 audio span：

```text
memoir_source_artifacts
memoir_transcript_versions
memoir_person_memories
memoir_event_memories
memoir_place_memories
memoir_theme_memories
memoir_memory_links
memoir_memory_evidence
memoir_open_questions
memoir_coverage_topics
memoir_coverage_snapshots
memoir_question_candidates
memoir_session_summaries
memoir_outputs
memoir_output_versions
```

设计原则：

- 原始材料、转写版本、结构化记忆和写作产物是不同生命周期对象。
- 用户语音是回忆录基础数据资产；原始音频和音频时间段不能被 AI 摘要、校正文或最终文章覆盖。
- 每条 AI 提取的 memory 默认是 `draft`，必须有 `memoir_memory_evidence` 指回 chat message、transcript version 和 audio span。
- `memoir_questions` 保留为 legacy 固定问题或服务不可用时的兜底，不作为新版实时访谈主模型。
- 新版待追问线索优先沉淀到 `memoir_open_questions`、chat message 元数据和 coverage state；`memoir_question_candidates` 只在后续确有规划器收益时再复用或改造。
- 覆盖度地图由 `memoir_coverage_topics` 和 `memoir_coverage_snapshots` 表达，第一期以固定人生维度为主。

## API

### Auth

```text
POST /api/auth/register
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
```

### Projects

```text
GET    /api/projects
POST   /api/projects
GET    /api/projects/{project_id}
PUT    /api/projects/{project_id}
DELETE /api/projects/{project_id}
```

### Project structure

```text
POST   /api/projects/{project_id}/groups
PUT    /api/projects/{project_id}/groups/reorder
PUT    /api/projects/{project_id}/groups/{group_id}
DELETE /api/projects/{project_id}/groups/{group_id}

POST   /api/projects/{project_id}/groups/{group_id}/documents
PUT    /api/projects/{project_id}/documents/reorder
GET    /api/projects/{project_id}/documents/{document_id}
PUT    /api/projects/{project_id}/documents/{document_id}
DELETE /api/projects/{project_id}/documents/{document_id}
```

### Project references

```text
GET    /api/projects/{project_id}/references
POST   /api/projects/{project_id}/references
PUT    /api/projects/{project_id}/references/{reference_id}
DELETE /api/projects/{project_id}/references/{reference_id}

GET    /api/projects/{project_id}/documents/{document_id}/references
PUT    /api/projects/{project_id}/documents/{document_id}/references
```

### Activity and export

```text
GET  /api/projects/{project_id}/sessions
POST /api/projects/{project_id}/sessions
GET  /api/projects/{project_id}/export
```

### AI

```text
POST /api/projects/{project_id}/documents/{document_id}/ai-continue
POST /api/projects/{project_id}/documents/{document_id}/ai-revise
POST /api/projects/{project_id}/documents/{document_id}/ai-revise-multi
POST /api/projects/{project_id}/analyze
POST /api/projects/{project_id}/analyze/apply
```

## 关键设计决策

### 一个仓库，产品边界内聚

小说、公众号、回忆录目前共享基础写作系统。差异通过以下层表达：

- 后端 `ProjectType` 和默认模板
- 前端 `projectProfiles.ts`
- 前端 `NovelWorkspace` / `ArticleWorkspace` / `MemoirWorkspace`
- Binder 资料分区配置

### 文档内容按需加载

项目详情返回文档元数据，不返回正文。正文通过文档详情接口按需加载，避免大项目打开时拉取全部内容。

### 自动保存

- 文档编辑 3 秒 debounce 自动保存
- Cmd+S 立即保存
- 切换文档或资料前保存当前脏草稿
- AI diff review 期间暂停自动保存，避免未确认修改落盘

### 路由注册顺序

FastAPI 按注册顺序匹配路由。`/reorder` 等字面路径必须注册在 `/{id}` 参数路径之前。

### 文件存储和事务补偿

文档正文写入文件系统，数据库保存元数据。文档创建、更新、删除时通过 `project_documents.py` 做文件操作和数据库事务协调；失败时尽量补偿文件状态。

## 开发原则

- 保持 `useProjectState` 作为 facade，不把所有业务继续塞回单个 hook
- 产品差异进入 workspace/profile/template，避免在共享组件里堆条件分支
- 业务逻辑放 service/hook，组件尽量负责装配和展示
- 删除不用的旧实现，不保留无意义兼容层
- 抽象必须减少重复或稳定边界，不为了抽象而抽象
- 新增能力应补充对应测试或更新现有测试边界

## 开发命令

```bash
# 后端开发
cd backend
PYTHONPATH=. uvicorn app.main:app --reload --port 8000

# 前端开发
cd frontend
npm run dev

# 前端检查
cd frontend
npm run lint
npm run build

# 后端测试
cd backend
venv/bin/python -m pytest -q
```

## 测试结构

```text
backend/tests/
├── test_auth.py
├── test_ai_base.py
├── test_services.py
├── test_new_features.py
├── test_frontend.py
├── test_binder_rendering.py
├── test_project_core.py
├── test_project_structure.py
├── test_project_documents.py
├── test_project_references.py
├── test_project_activity.py
└── test_project_ai.py
```

常用验证组合：

```bash
# 前端变更
cd frontend && npm run lint && npm run build

# 项目 API 全量
cd backend && venv/bin/python -m pytest tests/test_project_*.py -q

# 全量
cd backend && venv/bin/python -m pytest -q
```

## 环境变量

`backend/.env`：

```bash
APP_ENV=development
APP_SECRET_KEY=replace-with-at-least-32-random-characters
# DATABASE_URL=sqlite+aiosqlite:///../data/pawtale.db

DEEPSEEK_API_KEY=xxx
DEEPSEEK_BASE_URL=xxx

ANTHROPIC_AUTH_TOKEN=xxx
ANTHROPIC_BASE_URL=xxx

DEFAULT_AI_MODEL=claude-opus-4

VOLC_ASR_API_KEY=xxx
VOLC_ASR_RESOURCE_ID=volc.seedasr.sauc.duration
VOLC_ASR_WS_URL=wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
```

生产环境必须设置 `APP_ENV=production`，并使用至少 32 字符的非默认随机 `APP_SECRET_KEY`。

`DEFAULT_AI_MODEL` 可选值：

- `claude-opus-4`
- `claude-sonnet-4`
- `deepseek`
