# Pawtale 技术文档

## 技术架构

```
┌─────────────────────────────────────────────────┐
│                   前端 (Web)                     │
│         React 19 + TypeScript + Vite            │
│     TailwindCSS + Zustand + TanStack Query      │
└─────────────────────┬───────────────────────────┘
                      │ /api (Vite proxy)
                      ▼
┌─────────────────────────────────────────────────┐
│                 后端 (Python)                    │
│          FastAPI + async SQLAlchemy             │
├─────────────────────────────────────────────────┤
│  项目服务  │  角色服务  │  场景服务  │  AI 服务  │
└─────────────────────┬───────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
┌──────────────┐ ┌─────────────┐ ┌─────────────┐
│   DeepSeek   │ │ Claude Opus │ │   GPT-4o    │
│  (文本生成)   │ │Claude Sonnet│ │ (图像生成)  │
└──────────────┘ │ (文本生成)  │ └─────────────┘
                 └─────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│              SQLite (aiosqlite)                  │
│                data/pawtale.db                   │
└─────────────────────────────────────────────────┘
```

## 技术栈

### 后端
- **语言**: Python 3.11+
- **框架**: FastAPI
- **数据库**: SQLite + aiosqlite（异步）
- **ORM**: SQLAlchemy (async)
- **虚拟环境**: `backend/venv/`

### 前端
- **框架**: React 19 + TypeScript
- **构建**: Vite
- **样式**: TailwindCSS
- **状态管理**: Zustand
- **请求**: TanStack Query
- **图标**: Lucide React

### AI 服务
- **文本生成**: DeepSeek、Claude Opus 4.6、Claude Sonnet 4.6（工厂模式切换）
- **图像生成**: GPT-4o（角色头像）

## 数据模型

### 项目系统（新，主要使用）

```
projects                    # 顶层项目容器
├─ id (UUID PK), title, description
├─ word_count_target, daily_word_goal
└─ created_at, updated_at

document_groups             # 分组（类似"卷"或"部分"）
├─ id (UUID PK), project_id FK
├─ name, sort_order
└─ created_at, updated_at

documents                   # 文档（写作内容的最小单元）
├─ id (UUID PK), group_id FK
├─ name, content TEXT
├─ synopsis, status (draft/revision/done), label
├─ word_count_target, sort_order
└─ created_at, updated_at

reference_items             # 项目内参考资料
├─ id (UUID PK), project_id FK
├─ type (character/location/note)
├─ name, content TEXT
├─ metadata_json JSON       # 按类型存不同字段
├─ sort_order
└─ created_at, updated_at

document_references         # 文档-资料关联（多对多）
├─ document_id FK
└─ reference_id FK

writing_sessions            # 写作记录
├─ id (UUID PK), project_id FK
├─ date DATE, words_written INT
└─ created_at
```

### 全局资料库（旧，保留使用）

- **characters**: 全局角色库（name, gender, age, identity, personality, background 等）
- **scenes**: 全局场景库（name, category, description, location, mood_keywords 等）

### 旧表（保留但不再被前端使用）

- stories, story_characters, story_workspaces, story_outlines, outline_characters, outline_scenes

## API 设计

### 项目 API（`/api/projects`，主要使用）

```
# 项目 CRUD
GET    /api/projects                              # 列出所有项目
POST   /api/projects                              # 创建项目（自动建默认分组+文档）
GET    /api/projects/{id}                          # 获取项目（含 groups, docs 元数据, references）
PUT    /api/projects/{id}                          # 更新项目设置
DELETE /api/projects/{id}                          # 删除项目

# 分组
POST   /api/projects/{id}/groups                   # 新建分组
PUT    /api/projects/{id}/groups/reorder           # 排序分组 ⚠️ 必须在 /{gid} 之前注册
PUT    /api/projects/{id}/groups/{gid}             # 重命名分组
DELETE /api/projects/{id}/groups/{gid}             # 删除分组

# 文档
POST   /api/projects/{id}/groups/{gid}/documents   # 新建文档
PUT    /api/projects/{id}/documents/reorder         # 排序 ⚠️ 必须在 /{did} 之前注册
GET    /api/projects/{id}/documents/{did}           # 获取文档内容
PUT    /api/projects/{id}/documents/{did}           # 更新文档
DELETE /api/projects/{id}/documents/{did}           # 删除文档

# 参考资料
GET    /api/projects/{id}/references               # 列出参考资料
POST   /api/projects/{id}/references               # 新建
PUT    /api/projects/{id}/references/{rid}          # 更新
DELETE /api/projects/{id}/references/{rid}          # 删除
POST   /api/projects/{id}/references/import-global  # 从全局库导入

# 文档-资料关联
GET    /api/projects/{id}/documents/{did}/references
PUT    /api/projects/{id}/documents/{did}/references

# 写作记录
GET    /api/projects/{id}/sessions
POST   /api/projects/{id}/sessions

# 导出
GET    /api/projects/{id}/export                   # 导出 TXT

# AI 续写
POST   /api/projects/{id}/documents/{did}/ai-continue
```

### 全局资料库 API（保留）

```
# 角色
GET/POST           /api/characters
GET/PUT/DELETE     /api/characters/{id}
POST               /api/characters/{id}/avatar
POST               /api/characters/auto-complete

# 场景
GET/POST           /api/scenes
GET/PUT/DELETE     /api/scenes/{id}
POST               /api/scenes/init-presets
POST               /api/scenes/ai-generate
```

### 旧 API（保留但不再被前端使用）

- `/api/stories/*` — 互动故事相关
- `/api/outlines/*` — 故事大纲相关

## 目录结构

```
Pawtale/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── config.py            # 配置管理
│   │   ├── database.py          # 数据库连接
│   │   ├── models/
│   │   │   ├── models.py        # 旧模型 (Character, Scene, Story 等)
│   │   │   └── projects.py      # 新模型 (Project, DocumentGroup, Document 等)
│   │   ├── schemas/
│   │   │   ├── schemas.py       # 旧 schema
│   │   │   └── projects.py      # 新 schema
│   │   ├── routers/
│   │   │   ├── characters.py    # 全局角色库
│   │   │   ├── scenes.py        # 全局场景库
│   │   │   ├── projects.py      # 项目系统（主要）
│   │   │   ├── stories.py       # 旧互动故事（保留）
│   │   │   └── outlines.py      # 旧大纲（保留）
│   │   └── services/
│   │       ├── ai_factory.py    # AI 服务工厂
│   │       ├── ai_base.py       # AI 服务基类
│   │       ├── ai_text.py       # DeepSeek 文本生成
│   │       ├── ai_claude.py     # Claude 文本生成
│   │       ├── ai_scene.py      # AI 场景生成
│   │       └── ai_image.py      # GPT-4o 图像生成
│   ├── scripts/
│   │   └── migrate_to_projects.py  # 数据迁移脚本
│   └── tests/
│       ├── test_api.py          # 旧 API 测试
│       ├── test_projects.py     # 项目 API 测试 (23 tests)
│       ├── test_services.py     # AI 服务测试
│       ├── test_frontend.py     # 前端编译测试
│       └── test_new_features.py # 多模型等功能测试
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── Layout.tsx          # 顶层布局（写作/全局资料库切换）
│       │   ├── WritingWorkspace.tsx # 写作模式容器
│       │   ├── ProjectHome.tsx     # 项目列表/创建
│       │   ├── Binder.tsx          # 左侧文档树 (250px)
│       │   ├── EditorMain.tsx      # 编辑器 + 工具栏 + 状态栏
│       │   ├── Inspector.tsx       # 右侧浮层面板（文档/资料/项目）
│       │   ├── ReferenceSheet.tsx  # 参考资料编辑表单
│       │   ├── CharacterPanel.tsx  # 全局角色库管理
│       │   ├── ScenePanel.tsx      # 全局场景库管理
│       │   └── AIDebugPanel.tsx    # AI 调试信息展示
│       ├── hooks/
│       │   └── useProjectState.ts  # 项目状态 Hook（auto-save, CRUD, AI）
│       ├── stores/
│       │   └── appStore.ts         # Zustand 全局状态
│       ├── constants/
│       │   └── aiModels.ts         # AI 模型选项
│       ├── api/
│       │   └── index.ts            # API 客户端（projectsApi, charactersApi, scenesApi）
│       └── types/
│           └── index.ts            # TypeScript 类型定义
├── data/
│   └── pawtale.db                  # SQLite 数据库
├── PRODUCT.md                      # 产品文档
├── TECH.md                         # 技术文档（本文件）
├── CLAUDE.md                       # Claude Code 指引
└── README.md
```

## 关键设计决策

### 项目加载策略
- `GET /api/projects/{id}` 返回 groups 和 documents **元数据**（name, word_count, status），但**不含 content**
- 文档内容通过 `GET /api/projects/{id}/documents/{did}` 按需加载
- 避免大项目一次性加载所有文档内容

### 路由注册顺序
- FastAPI 路由匹配是顺序的，`/reorder` 等字面路径必须在 `/{id}` 参数路径**之前**注册
- 否则 `reorder` 会被当作 `id` 参数值，导致 422 错误

### 自动保存机制
- 3 秒 debounce 自动保存（`useProjectState.ts`）
- Cmd+S 立即保存
- 切换文档时自动保存当前文档
- 保存状态显示：已保存 / 保存中 / 未保存

### 数据迁移
- `backend/scripts/migrate_to_projects.py` 将旧 StoryWorkspace 数据转换为新 Project
- 旧表保留不删除，旧后端路由保留不删除
- 新表通过 `Base.metadata.create_all` 自动创建

## 环境变量

```bash
# backend/.env

# AI 文本生成
DEEPSEEK_API_KEY=xxx
DEEPSEEK_BASE_URL=xxx

# Claude API（通过代理）
ANTHROPIC_AUTH_TOKEN=xxx       # cr_ 开头的 token
ANTHROPIC_BASE_URL=xxx         # 代理地址，使用 OpenAI 兼容格式

# 图像生成
OPENAI_API_KEY=xxx

# 默认 AI 模型
DEFAULT_AI_MODEL=claude-opus-4  # 可选: deepseek, claude-opus-4, claude-sonnet-4
```

## 开发命令

```bash
# 后端
cd backend && PYTHONPATH=. uvicorn app.main:app --reload --port 8000

# 前端（自动代理 /api → localhost:8000）
cd frontend && npm run dev

# TypeScript 检查（必须用绝对路径，npx 有 gvm 冲突）
/Users/jin/Desktop/Pawtale/frontend/node_modules/.bin/tsc --noEmit --project frontend/tsconfig.app.json

# 后端测试
PYTHONPATH=backend backend/venv/bin/python -m pytest backend/tests -v

# 数据迁移
cp data/pawtale.db data/pawtale.db.bak
PYTHONPATH=backend backend/venv/bin/python backend/scripts/migrate_to_projects.py
```
