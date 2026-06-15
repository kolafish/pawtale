# 回忆录微信小程序产品规划

> Legacy 文档：本文描述的是上一版“固定问题 + 整段录音上传 + 离线 ASR + 整理稿确认”的小程序规划。回忆录小程序已转向实时语音聊天重构，新实现以 [MEMOIR_VOICE_CHAT_REDESIGN.md](MEMOIR_VOICE_CHAT_REDESIGN.md) 为准。旧云函数代码已删除，旧版只通过 baseline commit 追溯；本文仅保留为迁移背景参考。

## 产品定位

回忆录微信小程序是 Pawtale 的回忆录采集端，不是现有 Web 工作台的移动版。

小程序只服务一个场景：系统用问题引导用户回忆，用户主要通过语音回答，AI 将回答转写、整理、追问，逐步沉淀成可编辑的回忆录片段。

小程序不提供小说入口，不提供公众号入口，也不承载桌面端的章节树、资料库、Inspector、多模型对比等复杂能力。

长期产品形态不是把小程序做成聊天机器人，而是让小程序成为回忆录的低门槛采集端。后台会演进为记忆优先的聊天式人生采访系统：原始材料、转写版本、结构化人物/事件/地点/主题记忆、证据链、覆盖度地图、问题规划和写作产物分层处理。完整长期架构见 [MEMOIR_MEMORY_ARCHITECTURE.md](MEMOIR_MEMORY_ARCHITECTURE.md)，第一期落地计划见 [MEMOIR_MEMORY_FIRST_V1_PLAN.md](MEMOIR_MEMORY_FIRST_V1_PLAN.md)。

## 产品边界

### 小程序负责

- 引导用户进入回忆录访谈流程
- 展示当前问题
- 支持语音回答和文字回答
- 展示语音转写结果
- 展示 AI 整理后的回忆录片段
- 支持用户编辑回答和整理后的正文
- 支持继续下一问、跳过、重录
- 将确认后的内容沉淀到回忆录项目

### Web 端负责

- 管理完整项目
- 深度编辑和整理回忆录正文
- 导出和后续发布能力
- 更复杂的结构化管理

### 明确不做

- 不把小说、公众号入口放进小程序
- 不复刻 Web 的 Binder、章节树、Inspector
- 不在小程序里暴露 AI 模型选择和 debug 信息
- 不把一次问答直接塞进 `documents` 当正文使用
- 不为了兼容 Web UI 引入复杂的移动端胶水逻辑

## 核心用户流程

```text
进入小程序
 -> 微信登录
 -> 找到或创建默认回忆录项目
 -> 获取当前访谈会话
 -> 展示系统问题
 -> 用户语音回答
 -> 上传音频
 -> 云函数转写
 -> AI 整理成文
 -> 用户查看反馈
 -> 用户编辑或确认
 -> 进入下一问
```

这个流程的核心资产不是单篇文章，而是连续的访谈记录。最终正文可以由确认后的片段生成或汇总到 Web 端继续编辑。

## 信息架构

```text
Memoir Project
├── Interview Session
│   ├── Question
│   │   └── Answer
│   │       ├── Audio File
│   │       └── Draft
│   └── Current Progress
└── Final Documents
```

### 概念定义

- `Memoir Project`：用户的回忆录项目，复用现有 `projects.type = memoir`
- `Interview Session`：一次主题访谈，例如“童年”“父母”“一次重要选择”
- `Question`：系统当前提出的问题，可以来自模板或 AI 追问
- `Answer`：用户对某个问题的回答，包含音频、转写、编辑文本
- `Draft`：AI 根据回答整理出的回忆录正文片段
- `Final Documents`：确认后的内容最终沉淀到现有文档系统，供 Web 端继续编辑

## 数据组织

现有 `projects` 继续作为顶层资产。小程序新增回忆录访谈表，不直接复用 `documents` 存储问答过程。

```text
projects
└─ type = memoir

memoir_sessions
├─ id
├─ project_id
├─ title
├─ status              # active / completed
├─ current_question_id
└─ created_at, updated_at

memoir_questions
├─ id
├─ session_id
├─ prompt
├─ source              # template / ai_generated
├─ sort_order
├─ status              # pending / answered / skipped
└─ created_at

memoir_answers
├─ id
├─ question_id
├─ audio_file_id
├─ transcript_raw
├─ transcript_edited
├─ status              # uploaded / transcribing / drafted / confirmed
├─ duration_seconds
└─ created_at, updated_at

memoir_drafts
├─ id
├─ answer_id
├─ content_raw
├─ content_edited
├─ status              # draft / accepted
└─ created_at, updated_at

audio_files
├─ id
├─ user_id
├─ storage_path
├─ mime_type
├─ size_bytes
├─ duration_seconds
└─ created_at

wechat_identities
├─ id
├─ user_id
├─ openid
├─ unionid
└─ created_at
```

### 保存策略

- 音频文件存对象存储或本地 file storage，数据库只存 `storage_path` 和元数据
- 转写原文保存在 `transcript_raw`
- 用户修改后的回答保存在 `transcript_edited`
- AI 原始整理结果保存在 `content_raw`
- 用户确认或编辑后的正文保存在 `content_edited`
- 只有确认后的片段才进入最终正文生成流程

这样可以保留完整过程，也避免把临时转写、追问、重录状态污染现有文档模型。

## 云端模块规划

小程序上线链路使用微信云开发，不依赖云托管容器：

```text
miniprogram
 -> wx.cloud.callFunction("memoir-api")
 -> 云数据库
 -> 云存储
 -> StepFun ASR
 -> DeepSeek
```

本地 FastAPI 继续用于 Web 工作台和本地小程序调试。小程序云端能力独立放在 `cloud-functions/memoir-api`，避免把所有平台差异塞进 FastAPI 或小程序页面。

## FastAPI 本地模块规划

```text
backend/app/
├── models/memoir.py
├── schemas/memoir.py
├── routers/
│   ├── mobile/wechat.py
│   └── memoir/mobile.py
└── services/memoir/
    ├── interview.py
    ├── answers.py
    ├── transcription.py
    └── drafting.py
```

### 模块职责

- `mobile/wechat.py`：微信登录，openid/unionid 和现有用户体系绑定
- `memoir/mobile.py`：小程序专用回忆录 API
- `services/memoir/interview.py`：访谈会话、问题选择、下一问逻辑
- `services/memoir/answers.py`：回答保存、编辑、状态流转
- `services/memoir/transcription.py`：语音转写 provider 抽象
- `services/memoir/drafting.py`：将回答整理成回忆录片段

FastAPI 本地链路应该复用现有认证、用户、AI provider、文件存储能力，但回忆录访谈业务保持独立边界。云函数链路复用同一 API contract 和领域状态，不直接复用 SQLite model。

## API 规划

```text
POST /api/mobile/wechat/login

GET  /api/mobile/memoir/project
GET  /api/mobile/memoir/me

GET  /api/mobile/memoir/sessions/current
POST /api/mobile/memoir/sessions

GET  /api/mobile/memoir/questions/next
POST /api/mobile/memoir/questions/{question_id}/skip

POST /api/mobile/memoir/answers/audio
GET  /api/mobile/memoir/answers/{answer_id}
PATCH /api/mobile/memoir/answers/{answer_id}

POST /api/mobile/memoir/answers/{answer_id}/draft
PATCH /api/mobile/memoir/drafts/{draft_id}
POST /api/mobile/memoir/drafts/{draft_id}/accept
```

### 语音处理链路

```text
小程序录音
 -> 上传音频到云存储
 -> 云函数创建 audio_file
 -> 云函数调用转写服务写入 transcript_raw
 -> 云函数调用 AI 整理服务写入 memoir_drafts
 -> 小程序轮询 answer 状态
 -> 用户进入反馈页
```

MVP 可以先用同步处理或 FastAPI background task。后续如果转写耗时和并发上来，再引入队列。

## 小程序前端规划

建议新增独立目录：

```text
miniprogram/
├── app.ts
├── app.json
├── app.wxss
├── pages/
│   ├── home/
│   ├── question/
│   ├── feedback/
│   └── editor/
├── services/
│   ├── request.ts
│   ├── upload.ts
│   ├── auth.ts
│   └── memoir.ts
├── stores/
│   └── session.ts
└── domain/
    └── memoir.ts
```

小程序前端不复用 React 组件。可以复用的是 API contract、领域概念和后端服务。

## 页面设计

### Home 页

用途：进入当前回忆录访谈。

主要信息：

- 当前回忆录项目标题
- 当前访谈主题
- 已完成问题数量
- 最近一个已整理片段
- 主操作：继续回答

Home 可以很轻，MVP 甚至可以登录后直接进入问题页。

### 问题页

用途：完成一个问题的回答。

页面结构：

```text
[ 当前主题 ]

系统问题
你小时候最常去的一个地方是哪里？
那里有什么声音、气味，或者你现在还记得的细节？

00:00

[ 开始回答 / 停止录音 ]

[ 文字回答 ]       [ 跳过 ]
```

交互状态：

- `idle`：展示问题，等待回答
- `recording`：展示录音时长和停止按钮
- `uploading`：上传音频
- `transcribing`：转写中
- `drafting`：整理中
- `error`：可重试或重录

### 反馈页

用途：让用户确认系统是否理解正确。

页面结构：

```text
回答已整理

转写结果
“我小时候经常去外婆家……”

整理成文
“外婆家的院子，是我童年里最清晰的地方……”

[ 编辑 ]       [ 继续下一问 ]
```

反馈页不展示复杂编辑控件，只展示结果和两个主要操作。

### 编辑页

用途：让用户修改转写文本或 AI 整理后的正文。

页面结构：

```text
我的回答
[ 多行文本框 ]

整理后的正文
[ 多行文本框 ]

[ 保存 ]       [ 保存并继续 ]
```

编辑页保持轻量，不做长文排版编辑器。

## 问题生成策略

MVP 先用模板问题，保证体验稳定：

- 童年居住地
- 重要的人
- 一次难忘的事件
- 当时的感受
- 后来怎么看这件事

后续再加入 AI 追问：

- 根据回答补充时间、地点、人物
- 根据情绪线索追问细节
- 根据已完成片段发现缺口
- 控制问题数量，避免无限聊天

问题生成应由后端负责，前端只消费 `next question`。

## MVP 范围

第一版只需要完成闭环：

1. 小程序微信登录
2. 获取或创建默认回忆录项目
3. 获取当前问题
4. 支持文字回答
5. 支持语音上传
6. 保存转写文本
7. AI 整理成一个短片段
8. 反馈页确认
9. 进入下一问

语音转写如果暂时没有稳定 provider，可以先保留接口和状态，用文字回答验证完整数据流。

## 后续迭代

### 第二阶段

- 接入稳定语音转写
- 支持重录
- 支持跳过问题
- 支持 AI 追问
- 支持访谈主题选择

### 第三阶段

- 将已确认片段汇总为章节
- Web 端展示小程序采集结果
- 支持导出
- 支持家人协作补充

## 实施顺序

建议按小 PR 推进：

1. 新增回忆录访谈数据模型和 schema
2. 新增小程序微信登录 API
3. 新增 memoir session/question/answer/draft API，先支持文字回答
4. 新增音频上传和转写抽象
5. 新建 `miniprogram/`，完成问题页、反馈页、编辑页
6. 接入 AI 整理成文
7. 支持确认片段同步到现有 `documents`

每个 PR 都应该有对应测试，优先覆盖 API 行为、状态流转和用户归属隔离。
