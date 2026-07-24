# Pawtale 当前技术架构

## 系统概览

```text
React Web ───────────────┐
                         ├─ HTTP / session cookie ─┐
WeChat Mini Program ─────┤                         │
                         └─ WebSocket voice ───────┤
                                                   v
                                      FastAPI application
                                      ├─ project services
                                      ├─ memoir domain services
                                      ├─ editing/share-video workers
                                      ├─ SQLite + file storage
                                      └─ AI / ASR / TTS providers
```

Pawtale 是单仓库、单后端的多产品系统。认证、用户、项目、AI transport、存储和导出属于共享层；小说、公众号和回忆录在 domain、service、API read model 和前端 workspace 层保持边界。

## 技术栈

| 模块 | 当前技术 |
| --- | --- |
| 后端 | Python 3.12、FastAPI、async SQLAlchemy、Pydantic、SQLite/aiosqlite、pytest |
| Web | React 19、TypeScript 5.9、Vite 7、TailwindCSS 4、Zustand、TanStack Query、Vitest |
| 小程序 | 微信原生小程序、TypeScript、Node test runner |
| 文本 AI | Claude Fable 5 / Opus 4.8 / Sonnet 4.6 兼容代理、DeepSeek V4、火山方舟 Doubao Seed 2.0 |
| 语音 | 火山流式 ASR、火山双向/单向 TTS、StepFun 批量 ASR |
| 导出 | TXT/Markdown/ZIP 项目导出，回忆录 TXT/PDF，分享视频后台渲染 |

## 后端边界

```text
backend/app/
├── main.py                         # 应用生命周期、路由和后台 worker
├── config.py                       # 运行配置和生产环境校验
├── database.py
├── migrations/                     # runtime schema 校验与迁移
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

## API 分区

### 共享 API

- `/api/auth/*`：邮箱注册登录、微信登录/绑定、当前用户和凭据设置；
- `/api/projects/*`：项目、分组、文档、资料、活动、导出、AI 和 GitHub 同步；
- `/api/audit/*`：客户端事件写入和管理员聚合查询；
- `/api/github/*`：GitHub App 安装入口。

### 回忆录 API

- `/api/mobile/memoir/*`：小程序身份、回忆录项目、语音会话、记录、进度、书稿、图片和导出；
- `/api/mobile/memoir/*/gateway`：录音/Streaming ASR 与 assistant turn WebSocket；
- `/api/memoir/*`：Web 编辑器使用的素材、问答关系、Section、章节 pipeline、CompletionIssue 和 Review API。

移动端 read model 与 Web 编辑 read model可以不同，但必须共享同一套持久化真相和领域服务。

## 认证与权限

- Web 和小程序使用服务端 session cookie；
- 微信登录由后端使用 `wx.login()` code 换取 openid，小程序不持有 AppSecret；
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
- InterviewPolicyDecision / Evaluation；
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
  → 用户编辑并确认
  → 组装 BookOutputVersion
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
├── app.json                        # 页面注册和四个 Tab
├── config.ts
├── domain/                         # 语音和 API 领域类型
├── services/                       # transport、播放、设置和页面辅助
├── pages/                          # 页面 view model 与交互
└── test/                           # Node 行为测试
```

小程序页面按职责分组：

- 入口：home、login、projects、project-edit、me；
- 语音：voice-authorization、voice-chat、speaker-transcription；
- 素材：transcripts、transcript-detail、topic-map、voice-progress；
- 成书：book、book-completion、chapter-workspace、book-export、book-reader、image-assets。

页面测试优先调用 page method、view model 和 service mock，断言用户可观察结果；WXML/WXSS 只在路由、绑定或配置本身是失败边界时做轻量检查。

## AI 与 provider

- `AITextServiceBase` 定义文本 provider 合约；
- `AIServiceFactory` 支持 Claude Fable 5、Opus 4.8、Sonnet 4.6、DeepSeek Flash/Pro 和 Doubao Seed；
- provider 并发由共享 limiter 控制；
- 回忆录访谈策略、素材整理、章节生成和全书文案各自拥有用例 service；
- 流式 ASR/TTS 和离线 ASR 使用独立协议适配器；
- provider 失败必须保留原始数据并返回显式失败状态。

具体 provider 接入见 [AI_ARCHITECTURE.md](./backend/docs/AI_ARCHITECTURE.md)。

## 数据库和迁移

- 默认数据库为仓库根目录 `data/pawtale.db`；
- SQLite 默认使用 WAL、`NORMAL` synchronous 和 busy timeout；
- schema 变更通过 `backend/scripts/migrate_runtime_schema.py` 显式执行；
- 应用启动调用 `assert_runtime_schema`，不隐式创建或迁移表；
- 迁移默认创建运行时备份，可在全新环境显式使用 `--skip-backup`。

## 验证

```bash
# 后端
cd backend
venv/bin/python -m pytest -q

# Web
cd frontend
npm run lint
npm test
npm run build

# 小程序
cd miniprogram
npm run typecheck
npm test
```

语音、权限、后台切换、滚动、音频播放和大字模式还需要开发者工具或真机 smoke。当前清单见 [MINIPROGRAM_REAL_DEVICE_SMOKE.md](./docs/MINIPROGRAM_REAL_DEVICE_SMOKE.md)。

运行配置以 [backend/.env.example](./backend/.env.example) 和 [backend/README.md](./backend/README.md) 为准，不在多个文档重复维护完整变量列表。
