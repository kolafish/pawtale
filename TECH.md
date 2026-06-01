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
        +-- OpenAI GPT-4o image generation
```

Pawtale 当前是单仓库、前后端分离架构。后端提供项目、文档、资料、AI 和全局资料库 API；前端按项目类型分发到不同 workspace，并复用一套草稿编辑和资料编辑基础能力。

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

### AI

- 文本：DeepSeek、Claude Opus 4.6、Claude Sonnet 4.6
- 图像：OpenAI GPT-4o
- 服务边界：`AITextServiceBase` 提供 prompt 构建、响应解析和通用能力，具体模型服务只实现 `_call_llm()`

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
│   ├── projects/
│   │   ├── types.py
│   │   └── templates.py
│   ├── novel/template.py
│   ├── article/template.py
│   └── memoir/template.py
├── models/
│   ├── users.py
│   ├── models.py
│   └── projects.py
├── schemas/
│   ├── auth.py
│   ├── schemas.py
│   └── projects.py
├── routers/
│   ├── auth.py
│   ├── characters.py
│   ├── scenes.py
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
    ├── project_references.py
    ├── content_storage.py
    ├── ai_base.py
    ├── ai_factory.py
    ├── ai_text.py
    ├── ai_claude.py
    ├── ai_scene.py
    └── ai_image.py
```

### 后端边界

- `routers/projects/*` 只处理 HTTP 请求、依赖注入和 response model
- `services/projects/*` 承载项目业务编排
- `services/projects/access.py` 统一做项目归属校验
- `services/project_documents.py` 统一处理文档正文文件、数据库提交和回滚补偿
- `domain/projects/templates.py` 按项目类型分发默认模板

## 前端结构

```text
frontend/src/
├── api/index.ts
├── constants/aiModels.ts
├── domain/projectProfiles.ts
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
- `reference_items.type` 当前常用值为 `character`、`location`、`note`、`outline`
- `writing_sessions` 是写作记录能力，目前保留 API 和表结构

### 全局资料库

```text
characters
scenes
```

全局资料库独立于项目。项目资料可以从全局角色/场景导入，也可以把项目角色/地点导出回全局库。

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

POST   /api/projects/{project_id}/references/import-preview
POST   /api/projects/{project_id}/references/import-global
POST   /api/projects/{project_id}/references/{reference_id}/export-global
POST   /api/projects/{project_id}/references/{reference_id}/sync-global

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

### Global library

```text
GET/POST       /api/characters
GET/PUT/DELETE /api/characters/{id}
POST           /api/characters/{id}/avatar
POST           /api/characters/auto-complete

GET/POST       /api/scenes
GET/PUT/DELETE /api/scenes/{id}
POST           /api/scenes/init-presets
POST           /api/scenes/ai-generate
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

FastAPI 按注册顺序匹配路由。`/reorder`、`/import-global`、`/import-preview` 等字面路径必须注册在 `/{id}` 参数路径之前。

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
PYTHONFAULTHANDLER=1 venv/bin/python -m pytest -q -p no:capture
```

## 测试结构

```text
backend/tests/
├── test_auth.py
├── test_api.py
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
cd backend && PYTHONFAULTHANDLER=1 venv/bin/python -m pytest tests/test_project_*.py -q -p no:capture

# 全量
cd backend && PYTHONFAULTHANDLER=1 venv/bin/python -m pytest -q -p no:capture
```

## 环境变量

`backend/.env`：

```bash
APP_ENV=development
APP_SECRET_KEY=xxx
# DATABASE_URL=sqlite+aiosqlite:///../data/pawtale.db

DEEPSEEK_API_KEY=xxx
DEEPSEEK_BASE_URL=xxx

ANTHROPIC_AUTH_TOKEN=xxx
ANTHROPIC_BASE_URL=xxx

OPENAI_API_KEY=xxx

DEFAULT_AI_MODEL=claude-opus-4
```

`DEFAULT_AI_MODEL` 可选值：

- `claude-opus-4`
- `claude-sonnet-4`
- `deepseek`
