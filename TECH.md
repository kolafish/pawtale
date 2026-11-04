# Pawtale 当前技术架构

## 系统概览

```text
React Web ───────────────┐
Laoyu Mini Program ──────┼─ HTTP / session cookie ─┐
Xiaoyu Mini Program ─────┤                          │
                         └─ WebSocket voice ────────┤
                                                   v
                                      FastAPI application
                                      ├─ project services
                                      ├─ memoir domain services
                                      ├─ editing/share-video workers
                                      ├─ PostgreSQL + file storage
                                      └─ AI / ASR / TTS providers
```

Pawtale 是单仓库、单后端的多产品系统。认证、用户、项目、AI transport、存储和导出属于共享层；小说、公众号和回忆录在 domain、service、API read model 和前端 workspace 层保持边界。

## 技术栈

| 模块 | 当前技术 |
| --- | --- |
| 后端 | Python 3.12、FastAPI、async SQLAlchemy、Pydantic、PostgreSQL/asyncpg、Alembic、pytest |
| Web | React 19、TypeScript 5.9、Vite 7、TailwindCSS 4、Zustand、TanStack Query、Vitest |
| 小程序 | 微信原生小程序、TypeScript、Node test runner |
| 文本 AI | DeepSeek 和火山方舟，产品模型见 [AI provider 架构](./backend/docs/AI_ARCHITECTURE.md) |
| 语音 | 火山流式 ASR、火山双向/单向 TTS、StepFun 批量 ASR |
| 导出 | TXT/Markdown/ZIP 项目导出，回忆录 TXT/PDF，分享视频后台渲染 |

## 后端边界

```text
backend/app/
├── main.py                         # 应用生命周期、路由和后台 worker
├── config.py                       # 运行配置和生产环境校验
├── database.py
├── migrations/                     # PostgreSQL runtime schema 校验
├── domain/                         # 项目类型、模板和稳定领域类型
├── models/                         # SQLAlchemy 持久化模型
├── schemas/                        # HTTP/WebSocket 边界类型
├── routers/
│   ├── auth.py
│   ├── audit.py
│   ├── github.py
│   ├── projects/
│   └── memoir/
└── services/
    ├── projects/                   # 通用项目编排
    ├── memoir/                     # 回忆录语音、素材、章节和成书
    ├── ai_*.py                     # provider transport 和通用用例
    ├── content_storage.py
    └── project_documents.py
```

约定：

- router 只处理协议、认证依赖、输入输出和错误映射；
- service 承载业务编排和事务边界；
- schema 显式表达跨模块数据，不使用任意 JSON 承载核心状态；
- project access 和 memoir access 在 service 入口统一校验；
- AI provider 子类只实现 transport，产品 prompt 和用例放在独立 service；
- 后台任务使用数据库 job、lease、heartbeat 和幂等发布，不依赖进程内队列保存业务真相。

## 时间与日期

- Web、老余和小鱼小程序的用户可见时间统一为北京时间（UTC+08:00），不跟随设备时区；日期筛选、审计日统计、账单周期和导出日期也采用北京时间。
- 数据库时间戳继续以 UTC 保存；无时区后缀的后端时间戳按 UTC 解析，有显式时区的时间戳保留其绝对时刻，再转换为北京时间展示。
- Web 与老余小程序分别复用 `frontend/src/utils/time.ts` 和 `miniprogram/services/time.ts`；后端审计根据 `occurred_at` 计算北京时间日期，不信任客户端的本地 `event_date`。
- 登录过期、缓存有效期、后台任务调度及音视频相对时长使用绝对时间或经过时长，不做八小时平移。

## API 分区

### 共享 API

- `/api/auth/*`：微信登录/绑定、当前用户与邮箱认证。生产环境仅开放已启用密码登录的管理员邮箱登录，注册和设置凭据仅限非生产环境；
- `/api/projects/*`：项目、分组、文档、资料、活动、导出、AI 和 GitHub 同步；
- `/api/audit/*`：客户端事件写入和管理员聚合查询；
- `/api/github/*`：GitHub App 安装入口。

### 回忆录 API

- `/api/mobile/memoir/*`：小程序身份、回忆录项目、语音会话、记录、进度、书稿、图片和导出；
- `/api/mobile/memoir/*/gateway`：录音/Streaming ASR 与 assistant turn WebSocket；
- `/api/memoir/*`：Web 编辑器使用的素材、问答关系、Section、章节 pipeline、CompletionIssue 和 Review API。

移动端 read model 与 Web 编辑 read model可以不同，但必须共享同一套持久化真相和领域服务。

### 文章口述与 AI

- `/api/projects/{project_id}/documents/{document_id}/dictation`：article owner 专用的临时 Streaming ASR gateway，不保存音频或访谈 session；
- `/ai-continue`：按 revision 原子追加续写；
- `/ai-revise`：返回候选修改，不直接覆盖正文；
- `/ai-review`：返回动态 `{excerpt, issue, suggestion}`，不执行回忆录事实完整性检查。

## 认证与权限

- Web 和小程序使用服务端 session cookie；
- 微信登录由后端使用 `wx.login()` code 换取 openid，小程序不持有 AppSecret；
- 老余与小鱼按 AppID 区分 identity provider，并在 UnionID 可用时关联同一用户；
- 每个项目 API 都校验 owner、编辑权限或显式只读权限；
- 回忆录音频、素材、章节、图片、导出和后台 job 均绑定 `project_id`；
- `APP_ENV=production` 时必须使用至少 32 字符的非默认 `APP_SECRET_KEY`；
- provider key 和 GitHub private key 只从 `backend/.env` 读取。

## 通用项目系统

```text
User
└─ Project (novel | article | memoir)
   ├─ DocumentGroup
   │  └─ Document
   ├─ ReferenceItem
   ├─ DocumentReference
   ├─ WritingSession
   └─ optional ProjectGitSync
```

- 项目详情默认返回结构元数据，正文按文档详情接口加载；
- 正文文件保存在 `data/projects/{project_id}/{document_id}.md`；
- 数据库保存文档元数据，文件系统保存规范正文；
- `project_documents.py` 协调文件操作与数据库事务补偿；
- 默认模板由项目类型分发，不在共享组件中堆叠产品条件。

## 回忆录数据主干

```text
AudioFile
  → MemoirVoiceSession
  → MemoirChatMessage
  → MemoirVoiceUtterance
  → MemoirTranscriptVersion
  → MemoirSectionMaterial
  → MemoirSection
  → MemoirBookPlanChapter
  → MemoirBookChapterDraftVersion
  → MemoirBookOutputVersion
```

辅助派生对象包括：

- SectionDigest、ChapterDigest、BookReview；
- CompletionIssue；
- EditingJob、AutomationEvent；
- ImageAsset、BookImagePlacement；
- 分享视频 job 和 output；
- 话题框架与 Material 关联。

原始音频和 provider final 是事实入口。摘要、话题状态、章节审计、AI 正文和全书输出都是可重建的派生层。

## 实时语音状态机

```text
created → listening ↔ speaking → stopped
                    ↘ failed
```

小程序通过 voice gateway 发送录音帧并接收 partial/final 转写。只有 final 或显式恢复结果进入持久化。assistant turn 使用统一事件流：

```text
turn.started
text.delta*
audio.format
audio.frame*
audio.done
turn.completed | turn.failed | turn.cancelled
```

客户端负责录音器、页面可见性、播放队列和即时反馈；后端负责会话真相、final 幂等、AI 策略、TTS 编排和持久化。用户点击麦克风可以中断正在生成或播放的 AI，并进入下一次录音。

## 素材到成书

```text
VoiceUtterance
  → 归入一个 Section
  → 更新 Section/Chapter 摘要
  → 生成或调整章节规划
  → 评估一个当前 CompletionIssue
  → 生成完整章节正文版本
  → 目录确认后自动组装就绪章节，保留人工修改
  → BookOutputVersion
  → 阅读、TXT、PDF
```

章节正文只可使用该章节当前 allowlist 中的素材。Material 归属、输入签名、版本、用户确认和全书生成条件由确定性领域逻辑控制。

## 后台任务

应用生命周期可启动两个 worker：

- memoir editing worker：素材整理、章节修订、质量审查和相关自动化事件；
- share-video worker：场景规划、背景准备、语音/字幕对齐和视频渲染。

worker 开关、轮询、batch、lease、heartbeat 和重试参数都来自 `Settings`。任务结果发布前必须再次校验项目、输入签名和 job lease。

## Web 前端结构

```text
frontend/src/
├── api/index.ts
├── stores/appStore.ts
├── domain/
├── hooks/
└── components/
    ├── ProjectHome.tsx
    ├── workspaces/
    │   ├── NovelWorkspace.tsx
    │   ├── ArticleWorkspace.tsx
    │   └── MemoirWorkspace.tsx
    ├── memoir/
    └── audit/
```

- `WritingWorkspace` 按项目类型分发 workspace；
- 小说和公众号复用 `ProjectDraftWorkspace`；
- 回忆录使用独立的素材、目录、章节稿和全书工作区；
- Zustand 保存当前项目、当前文档和默认模型；
- TanStack Query 管理服务端查询、缓存和 mutation；
- 文档草稿和回忆录编辑都在离开前保护未保存内容。

## 小程序结构

```text
miniprogram/
├── app.json                        # 页面注册，启动页为 home
├── config.ts
├── domain/                         # 语音和 API 领域类型
├── services/                       # transport、播放、设置和页面辅助
├── pages/                          # 页面 view model 与交互
└── test/                           # Node 行为测试
```

`xiaoyu-miniprogram/` 是独立的轻量发布单元，只包含文章列表、编辑器、dictation transport 和 article AI API；两套小程序共享后端合同，不互相导入页面或业务 service。

小程序页面按职责分组：

- 入口：home、login、projects、project-edit、me（设置）；
- 语音：voice-authorization、voice-chat、story-voice-edit（首页语音编辑）；
- 素材：transcripts、transcript-detail、transcript-media、voice-progress（讲述概览）；
- 成书：book、book-completion、chapter-workspace、book-export、book-reader、image-assets。

页面测试优先调用 page method、view model 和 service mock，断言用户可观察结果；WXML/WXSS 只在路由、绑定或配置本身是失败边界时做轻量检查。

## AI 与 provider

- `AITextServiceBase` 定义文本 provider 合约；
- `AIServiceFactory` 按产品模型选择文本 provider。普通用户请求使用默认的 `deepseek-flash`，管理员可选择其他受支持的模型；
- provider 并发由共享 limiter 控制；
- 回忆录访谈策略、素材整理、章节生成和全书文案各自拥有用例 service；
- 流式 ASR/TTS 和离线 ASR 使用独立协议适配器；
- provider 失败必须保留原始数据并返回显式失败状态。

具体 provider 接入见 [AI_ARCHITECTURE.md](./backend/docs/AI_ARCHITECTURE.md)。

## 数据库和迁移

- 数据库只支持 PostgreSQL，异步驱动为 asyncpg；
- schema 变更通过 Alembic revision 显式执行；
- 应用启动调用 PostgreSQL runtime schema 断言，不隐式创建或迁移表；
- 生产迁移先备份，再执行 `PYTHONPATH=. alembic upgrade head`，最后核对数据和外键。

## 验证

```bash
# 从仓库根目录运行，后端使用专用 ParadeDB 测试库
TEST_POSTGRES_DATABASE_URL='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/pawtale_test' \
  bash scripts/test-backend.sh

# Web
npm --prefix frontend ci && bash scripts/test-frontend.sh

# 老余小程序
npm --prefix miniprogram ci && npm --prefix miniprogram run typecheck && npm --prefix miniprogram test

# 小鱼小程序
npm --prefix xiaoyu-miniprogram ci && npm --prefix xiaoyu-miniprogram run typecheck && npm --prefix xiaoyu-miniprogram test
```

语音、权限、后台切换、滚动、音频播放和大字模式还需要开发者工具或真机 smoke。当前清单见 [MINIPROGRAM_REAL_DEVICE_SMOKE.md](./docs/MINIPROGRAM_REAL_DEVICE_SMOKE.md)。

运行配置以 [backend/.env.example](./backend/.env.example) 和 [backend/README.md](./backend/README.md) 为准，不在多个文档重复维护完整变量列表。
