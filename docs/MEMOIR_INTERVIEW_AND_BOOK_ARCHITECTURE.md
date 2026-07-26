# 回忆录访谈与成书协作架构

## 文档状态

本文是回忆录产品的主架构文档，统一定义事实来源、主题讲述、成书补全、客户端缓存、请求超时和 AI 模型上下文。实时语音协议见 `MEMOIR_VOICE_CHAT_ARCHITECTURE.md`，当前物理数据结构和编辑入口见 `MEMOIR_DATA_MODEL_AND_EDITING_GUIDE.md`。

## 一、事实与状态不变量

- 原始音频、VoiceUtterance、ASR 原文和历史 TranscriptVersion 是事实来源；
- 校正、摘要、审阅和正文是派生结果，不能反向改写原始来源；
- 一次连续讲述直接作为一个 Material，同一时刻只属于一个 Section；
- 调整结构时移动 SectionMaterial，不复制或拆散原始讲述；
- 收录、隐藏、隐私、删除、冲突确认和拒答由用户或确定性领域逻辑决定；
- SectionDigest、ChapterDigest 和 BookReview 可失效、可重新生成，不是第二套事实；
- 当前素材归属、当前问题、当前章节正文和当前全书正文各自只有一套规范状态；
- 后台整理和生成失败不得导致原始音频、转写或用户修改不可见。

## 二、产品边界

### 2.1 主题讲述

主题讲述帮助用户自然地继续说下去。系统读取当前对话、最近素材、小章节摘要、话题地图和用户偏好，让 LLM 从受控动作中选择下一步。

允许动作包括：

- 继续倾听；
- 顺着当前内容追问；
- 询问人物关系、原因或结果；
- 总结确认；
- 回到已有小章节；
- 提供一个新话题；
- 暂停或结束。

话题地图只记录某个方面是否出现过素材、相关 Material / Section，以及用户是否不愿讨论。它不要求用户完成固定人生目录，也不产生“全部讲完”的结论。

### 2.2 成书补全

成书补全回答一个问题：当前内容下一步做什么，才能形成用户可以通读和确认的正文？

系统在三个层级审阅：

- Section：一个小故事、人物片段、人生阶段或观点是否能够被理解；
- Chapter：小章节之间的顺序、重点、过渡、开头和结尾是否成立；
- Book：人生阶段、主要人物、主题、篇幅和跨章衔接是否存在明显问题。

不是所有问题都需要采访：

- 缺少用户掌握且明显影响理解的内容：补讲；
- 是否收录、是否私密、内容冲突：用户确认；
- 已有素材足够，只需调整结构、去重或改写：编辑处理；
- 不影响理解的次要细节：不产生问题。

## 三、共享数据

两条链共享同一条主干：

```text
AudioFile
  → VoiceSession
  → VoiceUtterance（Material）
  → SectionMaterial
  → Section
  → Chapter
  → Book
```

`VoiceUtterance` 是规范 Material 实体。原始音频、ASR、校正文和 TranscriptVersion 始终可追溯。

主题讲述额外维护：

- 话题地图；
- InterviewBrief；
- 策略决策与评价日志；
- 用户偏好和拒答记录。

成书补全额外维护：

- SectionDigest；
- ChapterDigest；
- BookReview；
- 少量当前 CompletionIssue；
- 完整章节草稿和全书版本。

## 四、小章节

### 4.1 Section 是核心叙事单元

一个 Section 应能被相对独立地理解。支持的初始类型：

- `event`：有过程或转折的具体事件；
- `life_fragment`：生活片段；
- `person_portrait`：围绕一个人的描写；
- `life_stage_summary`：人生阶段概述；
- `viewpoint_reflection`：观点、感受或反思。

类型只影响审阅重点，不是强制写作模板。

### 4.2 素材归属不变量

- 一条 Material 同一时刻只归属于一个 Section；
- 一次回答不能被拆开后穿插到多个位置；
- Material 的章节归属由所属 Section 推导；
- 调整归属时移动 SectionMaterial，不复制原始讲述；
- Section 可以移动、排序、合并、拆分、隐藏或归档；
- 任何结构调整都不能改写原始讲述。

### 4.3 初始整理

新素材到达后，整理器可以：

- 放入已有 Section；
- 新建 Section；
- 标记需要用户选择归属；
- 识别明显重复，但不能自行删除用户内容。

## 五、分层语义摘要

### 5.1 SectionDigest

SectionDigest 读取有序 Material 和必要的当前正文，输出：

- 简短摘要；
- 当前表达重点；
- 已经讲清的内容；
- 最值得处理的少量问题；
- 对应 Material ID；
- 输入签名。

不同 Section 类型使用不同审阅维度。事件关注背景、过程、转折和影响；人物关注关系、特点和意义；观点关注触发背景和个人理解。

### 5.2 ChapterDigest

ChapterDigest 主要读取有序 SectionDigest，并按需读取原文，判断：

- 顺序和时间线；
- 重复与重点分散；
- 小章节之间的过渡；
- 开头和结尾；
- 背景、人物和指代是否清楚；
- 是否需要合并、拆分或移动 Section。

### 5.3 BookReview

BookReview 主要读取 ChapterDigest，并对具体疑点按需读取章节或素材，判断：

- 人生阶段是否存在明显空白；
- 跨章内容是否重复或断裂；
- 重要人物是否突然出现或消失；
- 各阶段和主题篇幅是否失衡；
- 全书开头、结尾和主题线是否成立。

Digest 和 Review 都是可重新生成的派生数据，不是事实来源。

## 六、增量问题

审阅可以返回一到三个候选问题，但系统只向用户展示一个当前问题。问题优先级综合考虑：

- 是否影响读者理解；
- 是否关系到重要内容；
- 回答后能否明显改善章节；
- 当前是否适合询问；
- 是否已经问过、解决、跳过或拒绝。

CompletionIssue 只保存真正进入处理流程的问题，不保存庞大的潜在问题池。至少记录：

- scope：section / chapter / book；
- 对应对象；
- 自然语言问题；
- 内部原因和优先级；
- 处理方式：interview / manual_review；
- fingerprint；
- 状态和处理结果；
- 相关 Material；
- 创建时的输入签名。

用户回答后：

```text
回答成为普通 Material
  → 归入当前或新 Section
  → 更新 SectionDigest
  → 更新 ChapterDigest
  → 关闭已解决 Issue
  → 再选择下一个最有价值的问题
```

“不记得”“以后再说”“不愿讨论”和“已有其他素材回答”都是明确结果，不能通过换一种问法持续追问。

## 七、用户可见流程

小程序保持简单路径：

```text
补全本书
  → 每章一个下一步
  → 章节工作台
  → 必要时进入一次语音补讲
  → 生成或更新完整章节正文
  → 通读、修改、确认
```

Section 在章节工作台内展示和调整，不额外增加必须经过的页面。

章节状态使用一个读模型推导：

- `not_started`：尚未整理；
- `preparing`：正在整理或生成；
- `needs_input`：需要补讲或确认一件事；
- `draft_ready`：正文可通读、修改和确认；
- `confirmed`：本章已确认；
- `failed`：本次处理失败，原始素材未丢失。

前台不展示 Digest、fingerprint、scope、置信度等后台术语。

## 八、LLM 与领域逻辑边界

LLM 可以：

- 决定素材适合哪个 Section；
- 建议新建、合并、拆分、移动 Section；
- 生成摘要和编辑建议；
- 判断下一步最值得问什么；
- 选择访谈策略动作；
- 根据允许素材生成候选正文。

确定性领域逻辑负责：

- project 和用户权限隔离；
- 原始素材不可变；
- 一条 Material 只有一个当前 Section；
- 用户收录、隐私、拒答和删除决定；
- 输入签名、过期结果和并发发布；
- 正文 Material allowlist；
- 章节确认和全书组装条件。

LLM 不能：

- 创造用户没有讲过的事实；
- 把模糊记忆写成确定事实；
- 自动解决内容冲突；
- 覆盖用户排除或隐私决定；
- 删除素材；
- 因固定模板缺项而强制追问。

## 九、客户端数据与一致性

后端数据库始终是事实源；本地缓存只是按用户和项目隔离、可丢弃的完整展示快照。

- 已访问页面可以先展示缓存并后台刷新；
- 缓存信封包含 schema version、cached_at、scope 和 payload；
- 删除字段、修改类型或改变业务语义时提升缓存 schema，不能宽泛合并未知旧数据；
- 写操作先提交服务端，再更新或精确失效缓存；
- 请求超时或断网时写结果属于未知，先查询服务端状态，不能直接重发；
- 较早的响应不能覆盖较新的刷新或用户写入；
- 项目级 server revision 只用于快照刷新，不替代实体版本、事务或任务状态；
- `401` 只允许一次重新认证和一次原请求重试，`403` 不触发重新登录。

## 十、请求、任务与模型上下文

小程序 HTTP 使用共享 profile：

| Profile | 客户端上限 | 适用范围 |
| --- | ---: | --- |
| `telemetry` | 8 秒 | 不阻塞用户操作、失败可丢弃或稍后补发的审计事件 |
| `interactive` | 30 秒 | 普通读取和状态查询 |
| `mutation` | 60 秒 | 保存、移动、确认和任务创建 |
| `generation` | 10 分钟 | 暂时仍为同步协议的生成操作 |

后端文本模型使用共享 profile：

| Profile | Provider 上限 | 适用范围 |
| --- | ---: | --- |
| `realtime` | 15 秒 | 实时访谈策略等快速决策 |
| `interactive` | 60 秒 | 搜索解析、标题和轻量归类 |
| `generation` | 180 秒 | 摘要、审阅、规划和正文生成步骤 |

业务页面和业务 service 只选择 profile，不复制秒数。ASR、TTS、图片、上传下载和 WebSocket 的独立协议时限集中在对应 transport 或 provider 配置中。

后台和传输使用以下全局截止时间：

| 操作 | 上限 | 超时处理 |
| --- | ---: | --- |
| 通用编辑任务 | 60 分钟 | 取消当前步骤，任务失败落库，保留已提交检查点，用户可重试或续跑 |
| 分享视频任务 | 30 分钟 | 取消渲染与心跳，清理未完成文件，失败落库，不自动重复付费生成 |
| 书稿导出渲染 | 4 分钟 | 返回明确的 `504`；导出无写入且可安全重试 |
| 小程序普通媒体传输 | 1 分钟 | 显示下载或上传失败，允许用户重试 |
| 小程序书稿导出传输 | 5 分钟 | 覆盖后端渲染和下载，显示明确失败 |

这些数值只定义在后端 Settings 或小程序 transport；业务代码不得另写毫秒数。任务租约只是防止多个 worker 同时执行，心跳续租不能绕过任务总截止时间。

超时层级满足：

```text
provider deadline < backend operation deadline < proxy deadline < client deadline
```

成本高、耗时长、分多阶段或不能安全重试的操作必须使用可恢复任务：

```text
创建任务
  → 返回 job ID
  → 持久化阶段和进度
  → 客户端轮询或订阅
  → 页面恢复时继续查询
```

Provider 超时表示结果未知，不能因为客户端没有收到结果就自动重复付费调用。只有确定请求未被接受或业务明确幂等时才允许自动重试。多阶段生成每完成一个昂贵步骤立即提交，后续失败只重做失败步骤。

章节检查和章节正文生成共用一个可恢复任务。检查按小节和整章报告阶段；正文严格按当前 Section 顺序逐节调用模型，每节成功后持久化正文、素材引用和输入签名检查点。失败后只有在输入签名、模型和操作类型都未变化时才能续跑，已完成小节不再调用模型；任务状态接口不向客户端返回检查点正文。

用户选择的模型沿完整链路传递：

```text
用户选择
  → 客户端 ai_model
  → API schema
  → workflow / job payload
  → AI factory
  → provider
```

生成和失败记录至少保存 requested model、resolved model、provider、timeout、elapsed 和 error type。结构化输出额外记录 finish reason 和响应长度，但不持久化包含用户素材的原始响应。

## 十一、最终数据结构

```text
memoir_voice_sessions
memoir_voice_utterances
memoir_transcript_versions

memoir_sections
memoir_section_materials
memoir_section_digests
memoir_chapter_digests
memoir_book_reviews
memoir_completion_issues

memoir_book_plans
memoir_book_plan_chapters
memoir_book_chapter_drafts
memoir_book_chapter_draft_versions
memoir_book_outputs
memoir_book_output_versions

memoir_interview_policy_decisions
memoir_interview_policy_evaluations
memoir_editing_jobs
```

系统拥有一套素材归属、一套分层摘要、一套当前问题和一套章节正文。
