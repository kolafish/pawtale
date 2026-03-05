# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**Pawtale** 是一个 AI 驱动的互动言情小说创作平台，用户可以创建角色、选择场景、生成分支式互动故事。

## 技术栈

- **后端**: FastAPI + async SQLAlchemy + aiosqlite，SQLite 数据库 (`data/pawtale.db`)
- **前端**: React 19 + TypeScript + Vite，TailwindCSS，Zustand 状态管理，TanStack Query
- **AI 服务**:
  - 文本生成：DeepSeek API、Claude Opus 4.6、Claude Sonnet 4.6（多模型支持）
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

# 运行所有后端测试
cd backend && pytest -v

# 运行单个测试文件
cd backend && pytest tests/test_api.py -v

# 运行单个测试函数
cd backend && pytest tests/test_api.py::test_create_character -v
```

## 环境变量

后端需要在 `backend/.env` 中配置:
```
# AI 文本生成服务
DEEPSEEK_API_KEY=xxx           # DeepSeek API 密钥
DEEPSEEK_BASE_URL=xxx          # DeepSeek API 地址

# Claude API 配置（可选，用于多模型支持）
ANTHROPIC_AUTH_TOKEN=xxx       # Claude API 认证令牌
ANTHROPIC_BASE_URL=xxx         # Claude API 地址

# 图像生成服务
OPENAI_API_KEY=xxx             # OpenAI API 密钥

# 默认 AI 模型
DEFAULT_AI_MODEL=deepseek      # 可选: deepseek, claude-opus-4, claude-sonnet-4
```

## 架构要点

### 多模型 AI 支持
系统支持多个 AI 模型进行文本生成，通过工厂模式实现：
- **DeepSeek**: 默认模型，性价比高
- **Claude Opus 4.6**: 高质量创作，适合复杂剧情
- **Claude Sonnet 4.6**: 平衡性能与质量

用户可在创建大纲和生成故事时选择 AI 模型。工厂模式位于 `backend/app/services/ai_factory.py`。

### 引导式创建流程
4步向导帮助用户创建故事大纲：
1. **故事目标**: 用户描述想要的故事类型和读者感受，选择情绪基调和 AI 模型
2. **角色张力**: 描述角色关系和核心张力，可选择已有角色
3. **核心冲突**: 定义剧情的核心冲突点
4. **AI 生成预览**: AI 根据前三步输入生成完整大纲草稿，用户确认后创建

API 端点: `POST /api/outlines/guided-create`

### 核心概念：故事大纲 (StoryOutline)
故事大纲是管理角色、场景和故事配置的核心容器。大纲包含：
- **角色配置** (OutlineCharacter): 角色在故事中的类型（主角/配角/反派）、称呼、关系
- **场景配置** (OutlineScene): 可用场景列表、起始场景标记
- **故事参数**: 情绪基调、文字风格、章节长度、核心冲突、结局类型、剧情提示、禁止元素
- **AI 模型选择**: 指定用于生成故事的 AI 模型
- **引导流程字段**: story_goal（故事目标）、character_tension（角色张力）、creation_stage（创建阶段）

### 角色深度系统
角色除基本信息外，支持深度字段：
- `role_type`: 角色类型 (protagonist/supporting/antagonist)
- `core_trait`: 底色 - 核心性格驱动力
- `life_goal`: 目标追求
- `fatal_flaw`: 致命弱点
- `growth_hint`: 成长弧线提示
- `habits`: 行为习惯
- `secret`: 秘密（可作为剧情转折点）

### 场景增强
场景支持 AI 生成和增强字段：
- `dramatic_potential`: 戏剧潜力
- `sensory_details`: 感官细节 (视觉/听觉/嗅觉)
- `props`: 可能的道具/元素

### 故事节点树结构
故事使用树形节点结构存储在 `Story.nodes` JSON 字段中。每个节点包含：
- `title`, `content`, `word_count`, `choices` (分支选项)
- `scene_id`, `scene_name`: 当前场景信息
- `present_characters`: 当前在场角色
- `scene_transition`: 场景切换信息
- `character_changes`: 角色进出变化

`current_node_id` 和 `current_path` 跟踪当前阅读位置。

### 故事中的动态操作
- **场景切换**: 通过 `/api/stories/{id}/switch-scene` 在故事进行中切换场景
- **角色管理**: 通过 `/api/stories/{id}/update-characters` 添加/移除角色

### 前端状态同步
修改故事后需要同时更新 Zustand store 中的 `currentStory` 和 `currentNode` 状态。`StoryPreview.tsx` 是核心组件，负责故事渲染、分支选择、场景切换和角色管理。

### 数据库迁移
无自动迁移。修改模型后需删除 `data/pawtale.db` 重建，可用 `data/backup.json` 备份恢复数据。

### API 代理
前端 Vite 配置将 `/api` 请求代理到 `localhost:8000`，开发时前后端可独立运行。

## 关键文件

### 后端
- `backend/app/models/models.py` - 数据模型 (Character, Scene, Story, StoryOutline, OutlineCharacter, OutlineScene)
- `backend/app/routers/stories.py` - 故事 API（生成、分支、编辑、场景切换、角色管理）
- `backend/app/routers/outlines.py` - 故事大纲 API（CRUD、角色/场景配置、引导式创建、基于大纲生成故事）
- `backend/app/routers/scenes.py` - 场景 API（CRUD、AI 生成场景）
- `backend/app/services/ai_factory.py` - AI 服务工厂（多模型支持）
- `backend/app/services/ai_base.py` - AI 服务抽象基类
- `backend/app/services/ai_text.py` - DeepSeek 文本生成服务
- `backend/app/services/ai_claude.py` - Claude 文本生成服务（Opus 4.6 & Sonnet 4.6）
- `backend/app/services/ai_scene.py` - AI 场景生成服务
- `backend/app/services/ai_image.py` - GPT-4o 图像生成服务

### 前端
- `frontend/src/components/StoryPreview.tsx` - 故事预览/生成核心组件（含场景切换器、角色管理器）
- `frontend/src/components/OutlinePanel.tsx` - 故事大纲管理面板
- `frontend/src/components/OutlineGuidedWizard.tsx` - 引导式创建向导（4步流程）
- `frontend/src/components/CharacterPanel.tsx` - 角色管理（含深度字段编辑）
- `frontend/src/components/ScenePanel.tsx` - 场景管理（含 AI 生成）
- `frontend/src/stores/appStore.ts` - Zustand 全局状态
- `frontend/src/api/index.ts` - API 客户端（charactersApi, scenesApi, storiesApi, outlinesApi）

### 测试
- `backend/tests/test_api.py` - API 端点测试（角色、场景、故事、健康检查）
- `backend/tests/test_services.py` - AI 服务测试
- `backend/tests/test_frontend.py` - 前端编译测试（TypeScript 类型检查、Vite 构建）
- `backend/tests/test_new_features.py` - 新功能测试（多模型支持、引导式创建、AI 模型选择）
