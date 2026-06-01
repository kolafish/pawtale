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
- **文本生成**: DeepSeek、Claude Opus 4.6、Claude Sonnet 4.6（工厂模式 + 模板方法模式）
- **图像生成**: GPT-4o（角色头像）
- **架构**: `AITextServiceBase` 基类包含所有 prompt 构建和响应解析，子类只实现 `_call_llm()`

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

### 全局资料库

- **characters**: 全局角色库（name, gender, age, identity, personality, background 等）
- **scenes**: 全局场景库（name, category, description, location, mood_keywords 等）

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
POST   /api/projects/{id}/references/import-preview # 导入前重复检测
POST   /api/projects/{id}/references/{rid}/export-global  # 导出到全局库
POST   /api/projects/{id}/references/{rid}/sync-global    # 从全局库同步最新数据

# 文档-资料关联
GET    /api/projects/{id}/documents/{did}/references
PUT    /api/projects/{id}/documents/{did}/references

# 写作记录
GET    /api/projects/{id}/sessions
POST   /api/projects/{id}/sessions

# 导出
GET    /api/projects/{id}/export                   # 导出 TXT

# AI 辅助
POST   /api/projects/{id}/documents/{did}/ai-continue    # AI 续写
POST   /api/projects/{id}/documents/{did}/ai-revise      # AI 修改（返回 diff）
POST   /api/projects/{id}/documents/{did}/ai-revise-multi # AI 多模型对比修改
POST   /api/projects/{id}/analyze                         # AI 分析提取人物/大纲/设定
POST   /api/projects/{id}/analyze/apply                   # 应用分析结果到项目
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

## 目录结构

```
Pawtale/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── config.py            # 配置管理
│   │   ├── database.py          # 数据库连接
│   │   ├── domain/              # 产品类型和默认项目模板
│   │   ├── models/
│   │   │   ├── models.py        # 全局角色/场景模型
│   │   │   └── projects.py      # 项目写作模型 (Project, DocumentGroup, Document 等)
│   │   ├── schemas/
│   │   │   ├── schemas.py       # 全局资料库 schema
│   │   │   └── projects.py      # 项目写作 schema
│   │   ├── routers/
│   │   │   ├── characters.py    # 全局角色库
│   │   │   ├── scenes.py        # 全局场景库
│   │   │   └── projects/        # 项目系统路由（core/structure/references/activity/ai）
│   │   └── services/
│   │       ├── ai_factory.py    # AI 服务工厂
│   │       ├── ai_base.py       # AI 服务基类（模板方法，含所有 prompt 和解析逻辑）
│   │       ├── ai_text.py       # DeepSeek 文本生成（仅 _call_llm）
│   │       ├── ai_claude.py     # Claude 文本生成（仅 _call_llm + 响应格式兼容）
│   │       ├── ai_scene.py      # AI 场景生成
│   │       ├── content_storage.py # 文档文件存储
│   │       ├── project_documents.py # 项目文档服务（事务补偿 + 响应构造）
│   │       ├── project_references.py # 项目/全局资料转换 helper
│   │       ├── projects/        # 项目业务编排服务
│   │       └── ai_image.py      # GPT-4o 图像生成
│   └── tests/
│       ├── conftest.py          # 测试配置（内存数据库 + dependency_overrides）
│       ├── test_api.py          # 全局资料库 API 测试
│       ├── test_project_*.py    # 项目 API 分组测试
│       ├── test_ai_base.py      # AI 基类测试 (51 tests，覆盖 helper + 模板方法)
│       ├── test_services.py     # AI 服务测试
│       ├── test_binder_rendering.py  # Binder 渲染测试
│       ├── test_frontend.py     # 前端编译测试
│       └── test_new_features.py # 多模型等功能测试
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── Layout.tsx          # 顶层布局（写作/全局资料库切换）
│       │   ├── WritingWorkspace.tsx # 写作模式容器
│       │   ├── ProjectHome.tsx     # 项目列表/创建
│       │   ├── Binder.tsx          # 左侧 Binder 装配层
│       │   ├── binder/             # Binder 子组件和 hook（文档树、资料分区、拖拽重排）
│       │   ├── EditorMain.tsx      # 编辑器 + 工具栏 + 状态栏
│       │   ├── AIAssistPanel.tsx   # AI 续写/修改 入口面板
│       │   ├── DiffReviewView.tsx  # AI 修改 diff 对比视图
│       │   ├── Inspector.tsx       # 右侧浮层面板（文档/资料/项目）+ 全局库导入（含重复检测）
│       │   ├── ReferenceSheet.tsx  # 参考资料编辑表单
│       │   ├── CharacterEditor.tsx # 角色编辑器（含导出到全局库）
│       │   ├── NoteEditor.tsx      # 笔记/地点编辑器（地点支持导出到全局库）
│       │   ├── OutlineEditor.tsx   # 大纲编辑器
│       │   ├── AnalyzeDialog.tsx   # AI 分析提取对话框
│       │   ├── ErrorBoundary.tsx   # React 错误边界
│       │   ├── CharacterPanel.tsx  # 全局角色库管理
│       │   ├── ScenePanel.tsx      # 全局场景库管理
│       │   └── AIDebugPanel.tsx    # AI 调试信息展示
│       ├── domain/
│       │   └── projectProfiles.ts  # 前端产品类型配置（创建入口、结构单位、Binder 分区）
│       ├── hooks/
│       │   ├── useProjectState.ts  # 项目状态 Hook（auto-save, CRUD, AI, 拖拽排序）
│       │   ├── useAIRevision.ts    # AI 修改 Hook（选段/全文修改，diff 对比）
│       │   └── useReferenceEditor.ts # 参考资料编辑 Hook（auto-save, 同步/导出全局库）
│       ├── stores/
│       │   └── appStore.ts         # Zustand 全局状态
│       ├── constants/
│       │   └── aiModels.ts         # AI 模型选项
│       ├── utils/
│       │   └── numberUtils.ts      # 中文数字转换
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

### AI 服务架构（模板方法模式）

```
AITextServiceBase (ai_base.py)
├─ 抽象接口: _call_llm(), provider_name, model_name
├─ 公共方法: auto_complete_character(), generate_story(), revise_chapter(), analyze_project_content()
└─ 共享 helper: _extract_json(), _build_char_descriptions(), _parse_story_response() 等

DeepSeekTextService (ai_text.py, ~65行)    ← 原生 system/user 分离
ClaudeTextService (ai_claude.py, ~125行)   ← system 合并到 user（代理限制）
```

- 新增模型只需实现 `_call_llm()` + 两个属性
- 工厂模式：`get_ai_service(model)` 返回对应服务实例

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

### 数据持久化
- 新表通过 `Base.metadata.create_all` 自动创建
- 文档正文存放在 `data/projects/{project_id}/{document_id}.md`
- 项目文档创建、更新、删除通过 `project_documents.py` 统一处理文件与数据库提交
- `/api/projects/*` 的 HTTP 路由只保留请求/响应边界，业务编排放在 `services/projects/`

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

```
