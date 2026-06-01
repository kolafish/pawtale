# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**Pawtale** 是一个 AI 辅助的小说写作工具，定位类似 Scrivener / Ulysses。以项目为单位管理写作，支持两层文档树（分组 → 文档）、项目内参考资料系统、全局角色/场景资料库、AI 续写等功能。

## 技术栈

- **后端**: FastAPI + async SQLAlchemy + aiosqlite，SQLite 数据库 (`data/pawtale.db`)
- **前端**: React 19 + TypeScript + Vite，TailwindCSS，Zustand 状态管理，TanStack Query
- **AI 服务**:
  - 文本生成：Claude Opus 4.6（默认）、Claude Sonnet 4.6、DeepSeek（多模型工厂模式）
  - 图像生成：OpenAI GPT-4o

## Python 虚拟环境

后端使用 Python 虚拟环境，位于 `backend/venv/`。运行后端代码时必须使用虚拟环境的 Python：

```bash
# 虚拟环境 Python 路径
/Users/jin/Desktop/Pawtale/backend/venv/bin/python

# 运行测试 (使用绝对路径避免 shell 环境问题)
PYTHONPATH=/Users/jin/Desktop/Pawtale/backend /Users/jin/Desktop/Pawtale/backend/venv/bin/python -m pytest /Users/jin/Desktop/Pawtale/backend/tests -v

# 验证模块导入
/Users/jin/Desktop/Pawtale/backend/venv/bin/python -c "import sys; sys.path.insert(0, '/Users/jin/Desktop/Pawtale/backend'); from app.main import app; print('OK')"
```

## 开发命令

```bash
# 后端 (端口 8000)
cd backend && PYTHONPATH=. uvicorn app.main:app --reload --port 8000

# 前端 (端口 5173，自动代理 /api 到后端)
cd frontend && npm run dev

# TypeScript 检查（必须用绝对路径，npx 有 gvm 冲突）
/Users/jin/Desktop/Pawtale/frontend/node_modules/.bin/tsc --noEmit --project /Users/jin/Desktop/Pawtale/frontend/tsconfig.app.json

# Vite 构建
/Users/jin/Desktop/Pawtale/frontend/node_modules/.bin/vite build --outDir /tmp/pawtale-build /Users/jin/Desktop/Pawtale/frontend

# 运行所有后端测试
PYTHONPATH=/Users/jin/Desktop/Pawtale/backend /Users/jin/Desktop/Pawtale/backend/venv/bin/python -m pytest /Users/jin/Desktop/Pawtale/backend/tests -v

# 数据迁移（旧数据 → 新项目格式）
cp data/pawtale.db data/pawtale.db.bak
PYTHONPATH=backend backend/venv/bin/python backend/scripts/migrate_to_projects.py
```

## 环境变量

后端需要在 `backend/.env` 中配置:
```
# Claude API（通过代理，OpenAI 兼容格式）
ANTHROPIC_AUTH_TOKEN=xxx       # cr_ 开头的 token
ANTHROPIC_BASE_URL=xxx         # 代理地址

# DeepSeek API
DEEPSEEK_API_KEY=xxx
DEEPSEEK_BASE_URL=xxx

# 图像生成
OPENAI_API_KEY=xxx

# 默认 AI 模型
DEFAULT_AI_MODEL=claude-opus-4  # 可选: deepseek, claude-opus-4, claude-sonnet-4
```

## 架构要点

### 项目系统（核心）

数据层级：**Project → DocumentGroup → Document**（两层树形结构）

- 项目加载时只返回文档元数据（name, word_count, status），不含 content
- 文档内容按需加载（点击 Binder 中的文档时请求）
- 参考资料（ReferenceItem）绑定到项目，可通过 document_references 关联到文档
- 新表通过 `Base.metadata.create_all` 自动创建，无需手动迁移

### 前端布局

```
Layout (h-screen flex flex-col)
├─ Header: [Pawtale] [写作 | 全局资料库] ... [默认模型▾]
└─ Main (flex-1 flex overflow-hidden)
   ├─ 写作视图: WritingWorkspace
   │   ├─ 无项目 → ProjectHome（项目卡片网格）
   │   └─ 有项目 → 产品 workspace（novel/article/memoir）
   │       └─ ProjectDraftWorkspace → Binder (250px) + EditorMain (flex-1) + 可选产品面板
   └─ 全局资料库视图: CharacterPanel / ScenePanel（Tab 切换）
```

### 状态管理

- **Zustand (`appStore.ts`)**: `currentProject`, `currentDocumentId`, `defaultAiModel`, `isGenerating`
- **`useProjectState` hook**: 文档内容、auto-save (3s debounce)、Cmd+S、CRUD 操作、AI 续写、导出
- **TanStack Query**: 项目列表、参考资料列表、文档关联等服务端数据

### AI 功能

**AI 续写**：
- 用户在编辑器中写作，点击"AI 续写"按钮
- 可输入续写指令引导 AI 方向
- AI 生成内容追加到文档末尾
- 多模型支持：Claude Opus 4.6 / Sonnet 4.6 / DeepSeek，通过工厂模式切换

**AI 修改**：
- 选中文本或全文，点击"AI 修改"按钮
- 输入修改意见（如"改得更诗意"、"缩短到 300 字"）
- AI 返回修改建议（不自动保存，由用户决定是否接受）
- 支持 diff 对比查看修改前后差异

**AI 服务架构**：
- 工厂模式：`get_ai_service(model)` 返回对应服务实例
- 统一接口：`AITextServiceBase` 定义所有 AI 方法
- Claude 代理限制：system role 被忽略 → 合并到 user message
- DeepSeek：原生 API，支持 system role（但保持简短）
- 详见 `backend/docs/AI_ARCHITECTURE.md`

### API 路由注册顺序

**重要**：FastAPI 路由按注册顺序匹配。字面路径（如 `/reorder`）必须在参数路径（如 `/{id}`）之前注册，否则字面量会被当作参数值。

### 全局资料库

保留旧的 Character 和 Scene 表及 API，通过顶部 Tab 切换到"全局资料库"视图访问。项目可从全局库导入角色/场景作为项目内参考资料。

### 旧表清理

旧 stories/outlines 工作区已经移除。当前数据库只保留项目系统、全局资料库和用户相关表，不要重新引入 `stories`、`story_outlines` 等旧表。

## 关键文件

### 后端
- `backend/app/models/projects.py` - 项目系统模型（Project, DocumentGroup, Document, ReferenceItem, WritingSession）
- `backend/app/schemas/projects.py` - 项目系统 Pydantic schemas
- `backend/app/domain/` - 产品类型和默认项目模板（novel/article/memoir）
- `backend/app/routers/projects/` - 项目 API 端点（按 core/structure/references/activity/ai 拆分）
- `backend/app/services/projects/` - 项目业务编排（权限校验、文档树、参考资料、统计导出、AI 工作流）
- `backend/app/models/models.py` - 全局资料库模型（Character, Scene）
- `backend/app/routers/characters.py` - 全局角色库 API
- `backend/app/routers/scenes.py` - 全局场景库 API
- `backend/app/services/ai_factory.py` - AI 服务工厂
- `backend/app/services/ai_base.py` - AI 服务抽象基类
- `backend/app/services/ai_claude.py` - Claude 文本生成服务
- `backend/app/services/ai_text.py` - DeepSeek 文本生成服务
- `backend/docs/AI_ARCHITECTURE.md` - AI 服务架构完整文档
- `backend/scripts/migrate_to_projects.py` - 数据迁移脚本

### 前端
- `frontend/src/components/Layout.tsx` - 顶层布局（写作 / 全局资料库视图切换）
- `frontend/src/components/WritingWorkspace.tsx` - 写作模式路由（ProjectHome 或产品 workspace）
- `frontend/src/components/workspaces/ProjectDraftWorkspace.tsx` - Binder + Editor + AI 修改的共享草稿工作台
- `frontend/src/components/workspaces/NovelWorkspace.tsx` - 小说工作台（共享草稿工作台 + Inspector + AI 分析）
- `frontend/src/components/workspaces/ArticleWorkspace.tsx` - 公众号工作台（共享草稿工作台，正文/素材入口）
- `frontend/src/components/workspaces/MemoirWorkspace.tsx` - 回忆录工作台（共享草稿工作台 + 问题驱动面板）
- `frontend/src/components/ProjectHome.tsx` - 项目列表/创建页面
- `frontend/src/components/Binder.tsx` - 左侧 Binder 装配层（项目标题、菜单、拖拽上下文）
- `frontend/src/components/binder/` - Binder 子组件和 hook（文档树、资料分区、拖拽重排）
- `frontend/src/components/EditorMain.tsx` - 编辑器（工具栏 + textarea + AI 续写 + 状态栏）
- `frontend/src/components/Inspector.tsx` - 右侧浮层面板（文档/资料/项目 Tab）
- `frontend/src/components/ReferenceSheet.tsx` - 参考资料编辑表单
- `frontend/src/domain/projectProfiles.ts` - 前端产品类型配置（创建入口、结构单位、Binder 分区）
- `frontend/src/hooks/useProjectState.ts` - 项目状态 facade（组合文档草稿、结构、资料、AI 续写、导出）
- `frontend/src/hooks/project/useDocumentDraft.ts` - 文档草稿状态（加载、快照、auto-save、Cmd+S、切换文档）
- `frontend/src/hooks/project/useProjectStructure.ts` - 项目结构操作（分组/文档 CRUD 和排序）
- `frontend/src/stores/appStore.ts` - Zustand 全局状态
- `frontend/src/api/index.ts` - API 客户端（projectsApi, charactersApi, scenesApi）
- `frontend/src/types/index.ts` - TypeScript 类型定义
- `frontend/src/components/CharacterPanel.tsx` - 全局角色库管理
- `frontend/src/components/ScenePanel.tsx` - 全局场景库管理

### 测试
- `backend/tests/test_project_*.py` - 项目 API 分组测试（core/structure/references/activity/ai）
- `backend/tests/test_api.py` - 旧 API 端点测试（角色、场景、故事）
- `backend/tests/test_services.py` - AI 服务测试
- `backend/tests/test_frontend.py` - 前端编译测试（TypeScript + Vite）
- `backend/tests/test_new_features.py` - 多模型等功能测试
