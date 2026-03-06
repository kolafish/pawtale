# Pawtale 技术文档

## 技术架构

```
┌─────────────────────────────────────────────────┐
│                   前端 (Web)                     │
│            React + TypeScript + Vite            │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│                 后端 (Python)                    │
│                   FastAPI                        │
├─────────────────────────────────────────────────┤
│  角色服务  │  场景服务  │  故事服务  │  媒体服务  │
│           （MVP-1 无用户服务，本地使用）          │
└─────────────────────┬───────────────────────────┘
                      │
        ┌─────────────┼─────────────┬─────────────┐
        ▼             ▼             ▼             ▼
┌──────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│   DeepSeek   │ │ Claude Opus │ │   GPT-4o    │ │    可灵     │
│  (文本生成)   │ │Claude Sonnet│ │ (图像生成)  │ │ (视频生成)  │
│   (默认)     │ │ (文本生成)  │ │             │ │             │
└──────────────┘ └─────────────┘ └─────────────┘ └─────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│                   数据库                         │
│           SQLite / PostgreSQL                   │
└─────────────────────────────────────────────────┘
```

## 模型选型

### 文本生成：多模型支持

系统支持三种 AI 模型，用户可根据需求选择：

#### Claude Opus 4.6（默认）

**选择理由：**
- 顶级创作质量
- 深度角色理解和情感表达
- 复杂剧情把控能力强

**适用场景：**
- 高质量长篇创作
- 复杂角色关系处理
- 深度情感描写

#### DeepSeek

**选择理由：**
- 中文写作能力强
- API 价格便宜（性价比最高）
- 国内直连，无需海外服务器

**适用场景：**
- 日常故事创作
- 快速原型验证
- 成本敏感的批量生成

#### Claude Sonnet 4.6

**选择理由：**
- 平衡性能与质量
- 响应速度快
- 成本适中

**适用场景：**
- 快速迭代创作
- 中等长度故事
- 日常高质量需求

**能力边界：**
| 能做到 | 做不到 |
|--------|--------|
| 中文言情写作 | 超长篇连贯性（几万字以上会丢设定） |
| 角色扮演、模拟性格语气 | 100% 还原原作角色 |
| 剧情分支生成 | - |
| 多模型切换 | - |

**应对策略：**
- 每次生成时带入角色设定摘要，保持连贯性
- 长篇故事分章节生成，每章开头重述关键设定
- 通过工厂模式实现模型切换，易于扩展新模型

### 图像生成：GPT-4o (gpt-image-1)

**选择理由：**
- 支持上传用户照片生成合影
- 可通过描述生成虚拟人物
- API 直接调用，无需自己部署
- 审核相对宽松

**能力边界：**
| 能做到 | 做不到 |
|--------|--------|
| 虚拟人物生成 | 精确控制长相一致性（每次可能略有不同） |
| 场景图、合影 | 直接生成明星真人 |
| 多种画风 | - |

### 角色一致性方案

**问题：** 同一角色多次生成会"变脸"，因为图像模型本质是根据描述随机采样。

**方案演进路径：**

#### MVP 阶段：GPT-4o 图生图

```
流程：
1. 首次生成角色"基准图"并保存
2. 后续生成时，把基准图作为参考输入
3. 模型基于参考图生成新图
```

| 一致性 | 部署难度 | 成本 | 用户等待 |
|--------|----------|------|----------|
| 70-80% | 零 | 高 | 短 |

产品策略：引导用户"生成多张选一张喜欢的"

#### Phase 2：接入 InstantID / IP-Adapter

```
流程：
1. 上传一张基准图
2. 模型提取人脸特征
3. 生成新图时注入这些特征
```

| 一致性 | 部署难度 | 成本 | 用户等待 |
|--------|----------|------|----------|
| 85-90% | 中（用 Replicate 托管）| 低 | 短 |

#### Phase 3：LoRA 训练（高级功能）

```
流程：
1. 用基准图 + 几张变体图训练专属 LoRA
2. 后续生成时加载这个 LoRA
3. 角色特征被"锁定"在模型里
```

| 一致性 | 部署难度 | 成本 | 用户等待 |
|--------|----------|------|----------|
| 95%+ | 高 | 中 | 长（首次训练）|

定位：付费用户的"角色定制"服务

### 视频生成：可灵

**选择理由：**
- 国内 API 直连
- 支持图生视频
- 效果在国内产品中领先

**能力边界：**
| 能做到 | 做不到 |
|--------|--------|
| 5-10秒图生视频 | 长视频 |
| 人物简单动作 | 复杂剧情、多人互动 |
| 虚拟人物视频 | 口型对话同步 |

**应对策略：**
- 产品定位为"名场面片段"而非完整视频
- 引导用户生成适合短视频的场景

## 法律风险规避

### 已规避

| 风险点 | 规避方式 |
|--------|----------|
| 明星肖像权 | 只生成虚拟角色，不使用真人明星 |
| 明星声音权 | 不涉及声音克隆 |
| 角色版权 | 用户参考设定创建新角色，不直接使用原作角色名 |

### 用户协议要点

- 用户上传照片仅限本人
- 生成内容仅供个人娱乐
- 禁止用于商业用途或侵犯他人权益
- 平台不对用户创建的角色设定负责

## 技术栈

### 后端
- **语言**: Python 3.11+
- **框架**: FastAPI
- **数据库**: SQLite（MVP）→ PostgreSQL（生产）
- **ORM**: SQLAlchemy
- **认证**: JWT

### 前端
- **框架**: React 19 + TypeScript
- **构建**: Vite
- **样式**: TailwindCSS
- **状态管理**: Zustand
- **请求**: TanStack Query
- **图标**: Lucide React

### 部署
- **服务器**: 国内云服务器即可（所有 API 都支持国内直连）
- **容器**: Docker
- **反向代理**: Nginx

## 目录结构

```
Pawtale/
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI 入口
│   │   ├── config.py         # 配置管理
│   │   ├── database.py       # 数据库连接
│   │   ├── models/           # 数据库模型
│   │   │   └── models.py     # Character, Scene, Story, StoryOutline 等
│   │   ├── schemas/          # Pydantic 模型
│   │   │   └── schemas.py
│   │   ├── routers/          # API 路由
│   │   │   ├── characters.py
│   │   │   ├── scenes.py
│   │   │   ├── stories.py    # 含编辑器工作区 API
│   │   │   └── outlines.py   # 故事大纲 + 引导式创建
│   │   └── services/         # 业务逻辑
│   │       ├── ai_factory.py # AI 服务工厂（多模型）
│   │       ├── ai_base.py    # AI 服务抽象基类
│   │       ├── ai_text.py    # DeepSeek 文本生成
│   │       ├── ai_claude.py  # Claude 文本生成
│   │       ├── ai_scene.py   # AI 场景生成
│   │       └── ai_image.py   # GPT-4o 图像生成
│   ├── tests/
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── Layout.tsx              # 顶层布局（模式切换）
│       │   ├── WritingWorkspace.tsx     # 自由写作模式容器
│       │   ├── EditorMain.tsx          # 编辑器 + 工具栏 + 状态栏
│       │   ├── WritingSidebar.tsx       # 可折叠右侧边栏
│       │   ├── WorkspaceHome.tsx        # 新建/打开作品首页
│       │   ├── ExtractionPanel.tsx      # 提炼编辑 + 存入库
│       │   ├── ResourceLinker.tsx       # 资源关联面板
│       │   ├── StoryPreview.tsx         # 故事预览/分支选择
│       │   ├── CharacterPanel.tsx       # 角色管理
│       │   ├── ScenePanel.tsx           # 场景管理
│       │   ├── OutlinePanel.tsx         # 大纲管理
│       │   ├── OutlineGuidedWizard.tsx  # 引导式创建向导
│       │   └── StoryListPanel.tsx       # 故事列表
│       ├── hooks/
│       │   └── useWorkspaceState.ts     # 写作工作区状态 Hook
│       ├── stores/
│       │   └── appStore.ts              # Zustand 全局状态
│       ├── constants/
│       │   └── aiModels.ts
│       ├── api/
│       │   └── index.ts
│       └── types/
│           └── index.ts
├── data/
│   └── pawtale.db            # SQLite 数据库
├── PRODUCT.md                # 产品文档
├── TECH.md                   # 技术文档（本文件）
└── README.md
```

## API 设计

### 角色
```
GET    /api/characters                    # 获取角色列表
POST   /api/characters                    # 创建角色
GET    /api/characters/{id}               # 获取角色详情
PUT    /api/characters/{id}               # 更新角色
DELETE /api/characters/{id}               # 删除角色
POST   /api/characters/{id}/avatar        # 生成角色头像
POST   /api/characters/auto-complete      # AI 自动补全角色设定
```

### 场景
```
GET    /api/scenes                        # 获取场景列表
POST   /api/scenes                        # 创建场景
GET    /api/scenes/{id}                   # 获取场景详情
PUT    /api/scenes/{id}                   # 更新场景
DELETE /api/scenes/{id}                   # 删除场景
POST   /api/scenes/init-presets           # 初始化预设场景
POST   /api/scenes/ai-generate            # AI 生成场景
```

### 故事
```
GET    /api/stories                       # 获取故事列表
GET    /api/stories/{id}                  # 获取故事详情
POST   /api/stories/generate              # 生成故事（首段）
POST   /api/stories/{id}/choose           # 选择分支继续生成
PUT    /api/stories/{id}                  # 更新故事
DELETE /api/stories/{id}                  # 删除故事
PUT    /api/stories/{id}/favorite         # 切换收藏
POST   /api/stories/{id}/switch-scene     # 故事中切换场景
POST   /api/stories/{id}/update-characters # 故事中添加/移除角色
PUT    /api/stories/{id}/nodes/{nodeId}   # 编辑节点
POST   /api/stories/{id}/nodes/{nodeId}/regenerate        # 重新生成节点
POST   /api/stories/{id}/nodes/{nodeId}/revise            # 润色节点
POST   /api/stories/{id}/nodes/{nodeId}/regenerate-choices # 重新生成分支选项
POST   /api/stories/{id}/polish           # 批量润色章节
GET    /api/stories/{id}/export           # 导出故事
```

### 编辑器工作区
```
POST   /api/stories/workspace             # 创建写作工作区
GET    /api/stories/{id}/workspace        # 获取工作区
PUT    /api/stories/{id}/workspace        # 更新工作区（标题/正文/模型）
POST   /api/stories/{id}/workspace/ai-continue    # AI 续写
POST   /api/stories/{id}/workspace/extract        # AI 自动提炼
PUT    /api/stories/{id}/workspace/extracted       # 手动修改提炼结果
```

### 故事大纲
```
GET    /api/outlines                      # 获取大纲列表
POST   /api/outlines                      # 创建大纲
GET    /api/outlines/{id}                 # 获取大纲详情
PUT    /api/outlines/{id}                 # 更新大纲
DELETE /api/outlines/{id}                 # 删除大纲
POST   /api/outlines/{id}/characters      # 添加角色配置
PUT    /api/outlines/{id}/characters/{cid} # 更新角色配置
DELETE /api/outlines/{id}/characters/{cid} # 移除角色配置
POST   /api/outlines/{id}/scenes          # 添加场景配置
PUT    /api/outlines/{id}/scenes/{sid}    # 更新场景配置
DELETE /api/outlines/{id}/scenes/{sid}    # 移除场景配置
POST   /api/outlines/{id}/generate        # 基于大纲生成故事
POST   /api/outlines/{id}/ai-enhance      # AI 增强大纲
GET    /api/outlines/templates            # 获取大纲模板
POST   /api/outlines/guided-create        # 引导式 4 步创建
```

## 数据库模型

### Character
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| name | String | 角色名 |
| gender | String | 性别 |
| age | Integer | 年龄 |
| identity | String | 身份 |
| appearance | Text | 外貌描述 |
| personality | JSON | 性格标签 |
| speaking_style | Text | 说话风格 |
| background | Text | 背景故事 |
| inspiration | Text | 灵感来源 |
| avatar_url | String | 头像 URL |
| base_image_url | String | 基准图 URL |
| is_preset | Boolean | 是否预设角色 |
| role_type | String | 角色类型 (protagonist/supporting/antagonist) |
| core_trait | String | 核心性格驱动力 |
| life_goal | String | 目标追求 |
| fatal_flaw | String | 致命弱点 |
| growth_hint | String | 成长弧线提示 |
| habits | JSON | 行为习惯 |
| secret | String | 秘密 |
| created_at | DateTime | 创建时间 |

### Scene
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| name | String | 场景名 |
| category | String | 分类 |
| description | Text | 场景描述 |
| location | String | 地点 |
| time_weather | String | 时间/天气 |
| mood_keywords | JSON | 氛围关键词 |
| is_preset | Boolean | 是否预设场景 |
| dramatic_potential | String | 戏剧潜力 |
| sensory_details | JSON | 感官细节 (visual/auditory/olfactory) |
| props | JSON | 可能的道具/元素 |
| created_at | DateTime | 创建时间 |

### Story
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| title | String | 标题 |
| outline_id | UUID | 关联大纲 |
| scene_id | UUID | 关联场景 |
| mood | String | 情绪标签 |
| writing_style | String | 文字风格 |
| chapter_length | String | 章节长度 |
| ai_model | String | AI 模型 |
| nodes | JSON | 剧情节点树 |
| current_node_id | String | 当前所在节点 |
| current_path | JSON | 当前阅读路径 |
| editor_content | Text | 编辑器正文内容 |
| extracted_characters | JSON | 提炼的角色 |
| extracted_scenes | JSON | 提炼的场景 |
| extracted_outline_points | JSON | 提炼的大纲 |
| extraction_notes | JSON | 一致性提醒 |
| is_favorite | Boolean | 是否收藏 |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |
| characters | M2M | 关联角色（多对多） |

**nodes 结构示例：**
```json
{
  "node_1": {
    "title": "咖啡馆偶遇",
    "content": "你在咖啡馆看到他和另一个女生在一起...",
    "word_count": 120,
    "ai_model": "claude-opus-4",
    "scene_id": "...",
    "scene_name": "咖啡馆",
    "present_characters": [{ "character_id": "...", "name": "陆辰" }],
    "choices": [
      { "label": "A. 转身离开", "next_node": "node_2" },
      { "label": "B. 走过去打招呼", "next_node": "node_3" }
    ]
  }
}
```

### StoryOutline
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| title | String | 大纲标题 |
| mood | String | 情绪基调 |
| writing_style | String | 文字风格 |
| chapter_length | String | 章节长度 |
| central_conflict | String | 核心冲突 |
| ending_type | String | 结局类型 (HE/BE/OE) |
| plot_hints | JSON | 剧情走向提示 |
| forbidden_elements | JSON | 禁止元素 |
| ai_model | String | AI 模型选择 |
| story_goal | String | 故事目标 |
| character_tension | String | 角色张力 |
| creation_stage | String | 创建阶段 (draft/confirmed) |
| is_active | Boolean | 是否激活 |
| outline_characters | Rel | 角色配置列表 |
| outline_scenes | Rel | 场景配置列表 |

## 环境变量

```bash
# AI 文本生成服务
DEEPSEEK_API_KEY=your-deepseek-key
DEEPSEEK_BASE_URL=https://api.deepseek.com

# Claude API 配置（可选，用于多模型支持）
ANTHROPIC_AUTH_TOKEN=your-claude-token
ANTHROPIC_BASE_URL=https://api.anthropic.com

# 图像生成服务
OPENAI_API_KEY=your-openai-key

# 默认 AI 模型
DEFAULT_AI_MODEL=claude-opus-4    # 可选: deepseek, claude-opus-4, claude-sonnet-4
```

## 待解决技术问题

- [ ] 视频生成异步处理：轮询 vs WebSocket 通知
- [ ] 图片/视频存储：本地 vs 对象存储（OSS）
