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

#### DeepSeek（默认）

**选择理由：**
- 中文写作能力强
- API 价格便宜（性价比最高）
- 国内直连，无需海外服务器

**适用场景：**
- 日常故事创作
- 快速原型验证
- 成本敏感的批量生成

#### Claude Opus 4.6

**选择理由：**
- 顶级创作质量
- 深度角色理解和情感表达
- 复杂剧情把控能力强

**适用场景：**
- 高质量长篇创作
- 复杂角色关系处理
- 深度情感描写

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
- **框架**: React 18 + TypeScript
- **构建**: Vite
- **UI 组件**: Shadcn/ui（基于 Radix）
- **样式**: TailwindCSS
- **状态管理**: Zustand
- **请求**: TanStack Query

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
│   │   ├── models/           # 数据库模型
│   │   │   ├── user.py
│   │   │   ├── character.py
│   │   │   ├── scene.py
│   │   │   └── story.py
│   │   ├── schemas/          # Pydantic 模型
│   │   ├── routers/          # API 路由
│   │   │   ├── auth.py
│   │   │   ├── characters.py
│   │   │   ├── scenes.py
│   │   │   ├── stories.py
│   │   │   └── media.py
│   │   ├── services/         # 业务逻辑
│   │   │   ├── ai_text.py    # 文本生成服务
│   │   │   ├── ai_image.py   # 图像生成服务
│   │   │   └── ai_video.py   # 视频生成服务
│   │   └── utils/            # 工具函数
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   └── (待创建)
├── data/
│   └── pawtale.db            # SQLite 数据库
├── PRODUCT.md                # 产品文档
├── TECH.md                   # 技术文档（本文件）
└── README.md
```

## API 设计

### 认证
```
POST /api/auth/register    # 注册
POST /api/auth/login       # 登录
POST /api/auth/refresh     # 刷新 token
```

### 角色
```
GET    /api/characters           # 获取角色列表
POST   /api/characters           # 创建角色
GET    /api/characters/{id}      # 获取角色详情
PUT    /api/characters/{id}      # 更新角色
DELETE /api/characters/{id}      # 删除角色
POST   /api/characters/{id}/avatar  # 生成角色头像
```

### 场景
```
GET    /api/scenes               # 获取场景列表
POST   /api/scenes               # 创建场景
GET    /api/scenes/{id}          # 获取场景详情
```

### 故事
```
POST   /api/stories/generate     # 生成故事（首段）
POST   /api/stories/{id}/branch  # 生成分支选项
POST   /api/stories/{id}/choose  # 选择分支继续生成
GET    /api/stories              # 获取故事列表
GET    /api/stories/{id}         # 获取故事详情
PUT    /api/stories/{id}         # 更新故事
DELETE /api/stories/{id}         # 删除故事
```

### 媒体
```
POST   /api/media/image          # 生成图像
POST   /api/media/video          # 生成视频
GET    /api/media/{id}           # 获取媒体文件
GET    /api/media/{id}/status    # 获取生成状态（视频生成是异步的）
```

## 数据库模型

### User
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| email | String | 邮箱 |
| password_hash | String | 密码哈希 |
| nickname | String | 昵称 |
| created_at | DateTime | 创建时间 |

### Character
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| user_id | UUID | 所属用户 |
| name | String | 角色名 |
| age | Integer | 年龄 |
| identity | String | 身份 |
| appearance | Text | 外貌描述 |
| personality | JSON | 性格标签 |
| speaking_style | Text | 说话风格 |
| background | Text | 背景故事 |
| inspiration | Text | 灵感来源（参考的影视剧角色） |
| avatar_url | String | 头像 URL |
| base_image_url | String | 基准图 URL（用于保持一致性） |
| is_preset | Boolean | 是否预设角色 |
| created_at | DateTime | 创建时间 |

### Scene
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| user_id | UUID | 所属用户（null 为预设）|
| name | String | 场景名 |
| category | String | 分类（初遇/暧昧/表白等）|
| description | Text | 场景描述 |
| location | String | 地点 |
| time_weather | String | 时间/天气 |
| mood_keywords | JSON | 氛围关键词 |
| is_preset | Boolean | 是否预设场景 |

### Story
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| user_id | UUID | 所属用户（MVP-1 可为空） |
| title | String | 标题 |
| character_ids | JSON | 相关角色 ID |
| scene_id | UUID | 场景 ID |
| mood | String | 情绪标签 |
| nodes | JSON | 剧情节点树（见下方结构） |
| current_node_id | String | 当前所在节点 |
| is_favorite | Boolean | 是否收藏 |
| created_at | DateTime | 创建时间 |

**nodes 结构示例：**
```json
{
  "node_1": {
    "content": "你在咖啡馆看到他和另一个女生在一起...",
    "choices": [
      { "label": "A. 转身离开", "next_node": "node_2" },
      { "label": "B. 走过去打招呼", "next_node": "node_3" },
      { "label": "C. 找个角落观察", "next_node": "node_4" }
    ]
  },
  "node_2": {
    "content": "你转身离开，心里五味杂陈...",
    "choices": [...]
  }
}
```

### Media
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| user_id | UUID | 所属用户 |
| type | String | 类型（image/video） |
| story_id | UUID | 关联故事（可选） |
| character_id | UUID | 关联角色（可选） |
| url | String | 文件 URL |
| status | String | 状态（pending/processing/completed/failed） |
| created_at | DateTime | 创建时间 |

## 环境变量

```bash
# 应用配置
APP_ENV=development
APP_SECRET_KEY=your-secret-key

# 数据库
DATABASE_URL=sqlite:///./data/pawtale.db

# AI 模型 API
DEEPSEEK_API_KEY=your-deepseek-key
OPENAI_API_KEY=your-openai-key
KLING_API_KEY=your-kling-key
```

## 待解决技术问题

- [ ] 视频生成异步处理：轮询 vs WebSocket 通知
- [ ] 图片/视频存储：本地 vs 对象存储（OSS）
