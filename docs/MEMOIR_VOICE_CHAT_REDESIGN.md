# 回忆录语音聊天重构方案

## 状态

这是回忆录小程序的新主线文档。此前的固定问题、整段录音上传、离线转写、整理稿确认路线只作为 legacy 参考，不再作为下一阶段产品目标。

回忆录“事实命题、跨访谈故事、章节契约、成书缺口、补访任务和结构化正文块”的下一阶段设计统一由 [回忆录叙事架构与成书质量演进方案](MEMOIR_NARRATIVE_ARCHITECTURE.md) 约束。本文继续负责实时录音、Streaming ASR、LLM 动态追问、Streaming TTS 和 voice session 状态机；不在本文重复维护叙事领域模型和成书 PR 计划。

当前实现已经从 Streaming ASR MVP 进入语音聊天第一版闭环：

- 小程序已通过 `home` / `voice-chat` 进入 voice session，实时发送 PCM 帧并显示 ASR partial/final。
- `asr.final` 已落 `memoir_chat_messages`、`memoir_voice_utterances`、`memoir_transcript_versions` 和原始音频 `audio_files`。
- 强引导模式已接入 LLM 动态追问，assistant message 会落库并显示在语音聊天页。
- PR2 交互细节已补齐：支持开场追问、手动生成追问、重试/换个追问、跳过当前追问、用户开始新回答时清掉旧追问活跃态、结束后跳转转写记录。
- AI 追问已接入 `assistant-turn-stream`，后端使用 LLM streaming 和火山/豆包双向 TTS，按小程序播放能力切成可播放缓冲段推送。
- 小程序已接入客户端轻量 VAD：基于 PCM 能量显示音量条、计算连续静音，并可自动收尾当前句。
- 已有转写校正页和访谈进度页，可查看会话、模块覆盖、语音段、AI 追问数，并从记录继续访谈。
- 小程序已拆为首页、转写、成书、我的 tab；我的页可看当前项目统计、管理项目和设置 AI TTS 音色，项目页支持创建、切换、重命名和删除回忆录。
- 后端已新增成书章节规划、逐章成稿和全书合成 API，可基于当前转写生成章节建议、保存用户确认后的章节结构，按章节 source refs 生成/编辑/确认正文版本，并把已确认章节合成为全书稿。
- 小程序成书页已接入章节规划确认、逐章成稿、全书生成和朗读流程。用户可先确认目录，再逐章生成、编辑和确认正文；章节正文和全书稿都可生成音频朗读。
- 成书页已能检测继续录音后产生的未收录转写，并提示用户查看。章节有新增 source refs 时已有后台自动补写基础，但“新记录自动归章、自动审计、访谈中播报自动整理进展”仍属于下一阶段。

新的产品目标是：

```text
Realtime Interview Orchestration + Project Progress Events + Background AI Editing + Voice Editing Planner
实时访谈编排 + 项目进度事件 + 后台自动整理 + 智能语音编辑
```

VAD、Streaming ASR、LLM dynamic follow-up 和双向 TTS 已成为底层语音能力，不再作为下一阶段单独主线。接下来的主线是把实时语音、成书整理和语音编辑连成闭环：

用户感知到的不是“回答第几个问题”，而是一次自然语音访谈：

- 老人直接说话，屏幕同步显示大字转写。
- AI 根据用户选择的引导强度接住上下文，动态追问、轻量陪伴，并在合适时用真实进度做鼓励。
- AI 回复以语音播放，同时保留文字气泡。
- 用户可以在 AI 说话时抢占麦克风，打断当前 AI 语音并直接开口。
- 后台持续沉淀原始语音、转写、对话消息、结构化记忆和写作素材，并自动完成可解释的归章、补写和章节完整度评估。

## 新旧版本关系

### Legacy 版本

旧版产品形态是：

```text
Question -> Answer -> Audio File -> ASR Job -> Draft -> Review
```

旧版代码只通过 Git commit 追溯，不在新版文档里维护代码路径清单。小程序固定问答、问答记录、旧 review/editor 页面已下线；需要查看旧版时以 baseline commit 为准。

```text
legacy fixed-question baseline commit:
bd3599ad3dddf382b242c47071c856d382543cac
```

需要查看旧版时，直接基于该 commit 做 Git 追溯，例如 `git show bd3599ad3dddf382b242c47071c856d382543cac`。后续 PR 如果触碰 legacy 逻辑，应优先服务迁移或删除，而不是继续强化固定问答模型。

### 新版本

新版产品形态是：

```text
Voice Session -> Chat Messages -> Realtime Transcript -> Memory Extraction -> Writing Output
```

当前小程序代码已从独立入口进入新版链路，避免和 legacy 页面互相缠绕：

```text
miniprogram/pages/login/                      # 微信登录、Pawtale 账号登录
miniprogram/pages/home/                       # 新版语音入口和当前回忆录入口
miniprogram/pages/projects/                   # 回忆录项目创建、切换、重命名、删除
miniprogram/pages/voice-chat/                 # 新版老人端实时语音聊天
miniprogram/pages/transcripts/                # 语音与转写校正
miniprogram/pages/book/                       # 成书入口、章节规划和分章成稿入口
miniprogram/pages/voice-progress/             # 访谈记录和进度
miniprogram/pages/me/                         # 账号、项目统计和 TTS 音色设置
miniprogram/services/request.ts               # ECS HTTP + cookie session
miniprogram/services/auth.ts                  # wx.login、backend cookie session、账号凭据设置
miniprogram/services/memoir-voice-api.ts      # voice session API
miniprogram/services/memoir-streaming-asr.ts  # 录音帧、WebSocket、partial/final transcript
miniprogram/services/voice-assistant-turn-runtime.ts # assistant turn 播放、取消和后续麦克风抢占
miniprogram/services/tts-settings.ts          # 小程序本地 TTS 音色偏好
miniprogram/domain/voice-chat.ts              # 新版聊天消息和实时事件类型
backend/app/routers/memoir/book.py            # 成书章节规划、章节稿、全书稿和朗读 mobile API
backend/app/services/memoir/book_plans.py     # 章节规划生成、确认和 source ref 校验
backend/app/services/memoir/book_drafts.py    # 逐章成稿、版本追加和正文确认
backend/app/services/memoir/book_outputs.py   # 全书稿合成、版本追加和全书朗读
backend/app/services/memoir/book_audio.py     # 章节/全书 TTS 文本分片、缓存和音频落库
backend/app/services/memoir/book_auto_revision.py # 已归章新增 source 的后台章节补写

backend/app/routers/memoir/voice.py           # 本地/服务端 voice session API
backend/app/services/memoir/voice_gateway.py  # WebSocket voice gateway + Streaming ASR
backend/app/services/memoir/voice_sessions.py # voice session detail/list/progress
backend/app/services/memoir/voice_chat_context.py # voice session 上下文读取和 turn index
backend/app/services/memoir/voice_assistant_turn_stream.py # LLM + TTS 流式 assistant turn
backend/app/services/memoir/voice_followups.py # no-answer prompt
backend/app/services/memoir/voice_tts.py      # assistant message TTS / Streaming TTS
backend/app/services/memoir/voice_transcripts.py # 转写校正和 AI 忠实清理

# 下一阶段计划新增或收敛的后端模块
backend/app/services/memoir/progress_snapshot.py # 当前项目进度快照和统计口径
backend/app/services/memoir/automation_events.py # 后台自动整理事件和 AI 可播报摘要
backend/app/services/memoir/source_router.py     # 新记录自动归章 / 候选新章 / 暂缓处理判断
backend/app/services/memoir/book_quality_audit.py # 章节完整度、缺口和访谈引导提示
backend/app/services/memoir/voice_edit_planner.py # 语音编辑自然语言 -> 操作计划 -> 预览确认
```

Git 上建议用以下边界区分：

- `bd3599ad3dddf382b242c47071c856d382543cac`：legacy 固定问题链路的可追溯基线。
- `codex/voice-chat-redesign`：新版语音聊天主线分支。
- 第一批 PR 已完成 Streaming ASR、voice session 数据和小程序新入口。
- 第二批 PR 已完成 LLM 动态追问、小程序追问交互、assistant TTS 播放、客户端自动收尾和进度页。
- 第三批 PR 已完成自动续听、TTS 文本播放进度、转写自动校正、AI 问题与回答连播、主 tab 拆分和我的页项目/TTS 设置。
- 当前下一批 PR 不再围绕“补 Streaming TTS”拆分，而是围绕麦克风抢占、项目进度上下文、后台自动整理和语音编辑 planner 继续演进。
- 小程序 legacy 固定问答页面、旧非实时录音上传代码和微信云函数链路已清理。
- 登录链路已迁移为统一 Pawtale 账号：已有 Web 用户用邮箱密码登录小程序，新用户用微信登录后直接进入小程序，Web 端邮箱密码可稍后在“我的”里设置。

## 产品体验

### 创建方式和引导模式

创建回忆录或开始一次新访谈时，先让用户选择本次访谈模式：

```text
free_narration      # 弱引导 / 自由叙述
guided_interview    # 强引导 / AI 模块化引导
```

以上是当前已实现的会话模式。后续成书驱动访谈不会新建第二套语音技术链路，而是在现有 `interview_type`、`guidance_mode` 和 voice session 基础上按叙事架构文档引入 `session_goal=open_narration/book_completion` 与持久 `followup_task`。`guidance_mode` 继续表达交互强度，`session_goal` 表达业务目标；当前 `guided_interview` 的主题菜单继续作为冷启动和引导策略使用，不承担章节完整度状态本身。

弱引导适合已经知道自己想讲什么的人。叙述者可以按自己的时间顺序从小到大讲，也可以围绕某段经历连续讲。AI 不主动切换主题，不强行打断，只在明显停顿、听不清、信息断裂或用户请求帮助时做轻量提示，例如确认是否继续、复述刚才讲到的线索、提醒可以补充时间地点人物。

强引导适合“不知道讲什么”的人。AI 通过 system prompt 和当前覆盖度状态做模块化采访，先让用户选择今天想聊的方向，例如：

- 人：家人、朋友、老师、同事、对自己影响很大的人。
- 事：开心、难过、遗憾、转折、冒险、现在还常想起的事。
- 地点：老家、学校、单位、住过的地方、常去的地方。
- 时间段：童年、求学、工作、婚育、退休、重大变故。
- 主题：爱好、性格、价值观、家庭传统、时代变化。

选定模块后，AI 一次只问一个具体问题，问题要围绕可讲述的场景，而不是让用户填写抽象信息。例如“那我们先聊一件你印象里最开心的事，它大概发生在哪一年？”强引导也要允许用户随时跑题；跑题内容仍然保存，只是后端更新当前模块和覆盖度。

进入语音聊天页后默认由 AI 先说第一句。每次新进入访谈的第一句都先做轻量主持人开场：根据当前 TTS 音色自称“老余”（男声）或“小余”（女声），简单说明这是一次慢慢留下回忆的访谈，不是答题或考问，并提醒叙述者可以想一会儿、跳过或换方向。弱引导的开场给一个开放入口，让叙述者按自己的顺序开始；强引导的开场先给主题菜单，让叙述者选择方向，不直接切入上次很细的历史细节。回访时只能用一句宽泛概括提醒上次进度，并同时提供“接着上次大方向讲”或“换主题”的选择。

### 首屏

小程序进入后直接进入语音访谈，不再先展示固定问题卡片。

页面核心区域：

- 顶部：当前访谈主题、引导模式、连接状态、暂停/结束。
- 中部：大字实时转写区。
- 底部：语音按钮、AI 播放状态、最近几条聊天消息。

### 核心页面

新版至少需要这些页面或等价视图：

- 开始页：选择回忆录项目、叙述者、引导模式，并进入或继续一个 voice session。
- 语音聊天页：实时录音、Streaming ASR 大字显示、AI 语音回复、麦克风抢占、暂停/继续/结束。
- 转写校正页：查看每段原始语音、ASR 原文、编辑后文本和版本记录，支持家人或编辑校正。
- 进度页：查看当前项目已讲述记录、已归章记录、成文字数、章节完整度、自动整理事件，以及 AI 发现的待追问问题。
- 我的页：查看账号、当前项目统计、管理回忆录项目，并选择 AI TTS 音色。
- 成书页：把确认后的素材组织为章节，逐章生成以用户叙述为主的正文，确认后合成为全书稿；章节和全书都支持语音朗读，并展示后台自动归章、补写和待确认素材。

### 补全成书

“补全成书”先进入全书概览，不直接把用户送进某一道后台问题。概览只展示每章当前处于“可以补充、正在补充、需要确认、正在整理、目前完整”中的哪一种状态，以及最多三个自然语言的补充方向；不向用户展示 Story、Claim、slot、Contract 等内部模型名、内部计数或质量规则。

用户从概览主动选择一章后，补访继续复用标准 `voice-chat` 页面和同一条 `VAD + Streaming ASR + LLM Dynamic Follow-up + Streaming TTS` 链路。补访不是第二套录音、转写或 TTS 实现；实时转写、AI 朗读、重听、换个问法、打断、断线恢复和原始音频留存都沿用 AI 访谈能力。补访只额外携带目标章节和当前补充任务，用于素材自动归章及后台质量核对。

每个补充任务必须明确答案用途：补充新的叙事内容、解释已有内容的关系，或核实/纠正已有事实。问题文案必须与用途一致，不能把“解释这件事为何出现在本章”泛化成“事情为什么这样发展”。答案用途是后端写作契约，不向用户展示内部枚举；后续处理无法明确判断用户是在补内容、纠正理解还是只作说明时，必须询问用户，不能自动选择一种用途。

内部章节检查可以保留细粒度缺项，但这些缺项不能一一变成用户问题。产品层只为章节核心故事生成一次“完整讲一段”的任务，引导用户按记得的顺序讲事情如何开始、有哪些人、发生了什么、结果和后来影响；顺带提到的非核心故事不自动形成追问队列。用户完成一次有效讲述后，本轮 AI 只确认内容已保存，不继续沿细节追问，也不自动切换下一题；后台任务队列自动重新检查该章并刷新概览。用户返回全书概览后再决定补哪一章，手动换题也只能在当前章节内进行。

补访素材在录音固化时快照绑定补充任务和目标章节。后续异步处理复用素材路由的一次结构化判断，把回答解释为“可直接形成叙事事实的内容、对已有内容的解释、事实确认或纠正、上述用途的混合”，并把各用途对应的原文片段记录在任务与讲述的关系上。原始音频和完整转写始终保留，不能被解释结果覆盖。

只有明确的叙事内容片段可以进入 Story/Claim 摄取；解释和事实处理作为成书编辑上下文保存，不能被当成新增段落直接拼入正文。混合回答只摄取其中明确的叙事片段。系统无法准确判断或拆分时，必须把该回答置为待用户确认，暂停自动归章、叙事摄取和任务完成；用户确认用途后再沿原后台任务链继续，不能用默认分类兜底。补访上下文一旦快照绑定，异步处理不得重新猜测目标章节。

章节初稿、手动重写和后台自动修订必须消费同一个类型化写作输入：`narrative_sources` 只包含允许写进正文的口述片段，`editorial_contexts` 只包含解释和事实确认/纠正，并通过 task、utterance、transcript version 保留来源链。混合回答的完整转写不能作为正文来源，只能把已确认的直接叙述片段放入 `narrative_sources`；解释和纠错片段进入 `editorial_contexts`。编辑上下文可以要求修改、删除或重新理解旧正文，但不能被照抄、转述或当成新事件补进章节。写作模型应先理解全部叙事来源，再围绕章节主线取舍，不能以“每条原始素材都必须出现”为目标机械拼接。

### 用户说话

```text
开始说话
 -> VAD 或按钮进入 listening
 -> Streaming ASR 返回 partial transcript
 -> 大字区域显示实时文字
 -> ASR final 后固化成用户消息
 -> 保存原始音频和 transcript version
```

partial transcript 只用于屏幕显示和本地状态，不作为事实依据落库。final transcript 才进入业务数据。

用户说出的所有语音都属于基础数据资产。原始音频不能被 AI 摘要、转写修正或最终文章覆盖；任何结构化记忆和写作产物都只能引用它。推荐规则：

- 原始音频按 session 和 utterance 保存，记录 `audio_file_id`、`start_ms`、`end_ms`。
- ASR 原文、用户/家人校正文、最终确认文本都作为 transcript version 保存。
- AI 提取的人、事、地点、主题必须通过 evidence 指回 transcript version 和音频时间段。
- 用户删除或隐藏产出稿时，不默认删除原始音频资产，除非明确执行素材删除。

### AI 开场和追问

```text
新 voice session 且暂无用户消息
 -> LLM 根据引导模式生成 AI 开场
 -> 保存 assistant chat message
 -> TTS 播放 AI 开场

用户 final transcript
 -> LLM 根据近期对话、项目进度、自动整理事件、章节审计提示和敏感度生成追问
 -> 保存 assistant chat message
 -> Streaming TTS 播放 assistant message
 -> 页面显示 AI 文字气泡
```

AI 追问不再从固定模板题库取下一题。模板只允许作为冷启动或服务不可用时的兜底。

AI 可以把承接、认可、鼓励、进度提醒和下一问组合成一次自然回复，但进度提醒必须来自后端结构化快照或自动事件，不能让 LLM 自己编造。例子：

- “您已经留下 22 条讲述，其中 18 条整理进章节了。”
- “刚才新增的 3 条已经补进《岁首欢聚记》，这一章内容更完整了。”
- “这一章人物细节已经比较清楚了，接下来可以问一问当时的地点或声音气味。”

强引导和弱引导的差异主要体现在 prompt policy，而不是两套完全不同的技术链路：

- 弱引导：`system prompt` 要约束 AI 少问、短问、只在停顿或上下文断裂时补位。
- 强引导：`system prompt` 要约束 AI 按模块推进、一次一个问题、持续使用进度和章节审计线索。
- 两种模式都要把用户最新 final transcript 作为事实输入，把 AI 回复保存为 assistant message。

## 技术架构

### 总链路

```text
Mini Program RecorderManager
 -> Voice Client WebSocket
 -> Pawtale Voice Gateway
 -> Streaming ASR Provider
 -> partial/final transcript
 -> Chat Orchestrator + Progress Snapshot + Audit Hints
 -> Streaming LLM assistant turn
 -> Streaming TTS Provider
 -> Mini Program audio playback

Background workers
 -> Source Router
 -> Book Auto Revision
 -> Book Quality Audit
 -> Automation Events
 -> Progress Snapshot
```

Provider API key 不进入小程序。小程序只连接 Pawtale 自己的 voice gateway。

### VAD

当前第一版采用客户端轻量 VAD，不引入服务端断句模型：

- 小程序在 `RecorderManager.onFrameRecorded` 中按 16-bit PCM 计算 RMS 能量。
- 页面显示音量条和连续静音时长。
- 默认开启自动收尾：录音超过最小时长后，连续静音超过阈值会自动停止当前句并发送 `audio.end`。
- 用户可在页面关闭自动收尾，继续使用手动开始/结束。

这不是上线态完整 VAD。后续仍需要评估：

- 服务端 VAD：用于更稳定的 utterance 边界、自动插话打断和异常恢复。
- Provider VAD：如果实时语音服务提供 server VAD，可以优先复用。
- 方言、远场、背景噪声下的阈值评测和动态阈值。

### Streaming ASR

当前已验证豆包流式 ASR 可用：

```text
VOLC_ASR_WS_URL=wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
VOLC_ASR_RESOURCE_ID=volc.seedasr.sauc.duration
```

验证结果：

- WebSocket upgrade 成功。
- 16k mono PCM 帧可识别。
- 真实 m4a 样例转 PCM 后可返回 final transcript。

StepFun 整段音频转写不接入实时语音聊天主链，也不恢复旧版固定问答后的自动整段转写。素材详情允许用户对单条已有音频显式触发离线增强：火山与 StepFun 双路识别后由 LLM 校验；籍贯匹配到已支持方言时才启用对应方言规则。任务必须异步执行、保留状态并防止覆盖人工编辑，评测和批量处理仍通过独立脚本完成。

### LLM Orchestrator

LLM 不直接接原始音频。它消费结构化上下文：

```text
guidance_mode
current_guidance_module
recent_messages
confirmed_memories
open_questions
coverage_state
current_topic
elder_profile
project_progress_snapshot
recent_automation_events
chapter_audit_hints
```

输出应是可追踪的 assistant message，而不是直接播放一次临时文本。

LLM 输出建议带结构化元数据，便于后续进度页和追问策略复用：

```text
assistant_text
guidance_intent       # opening / follow_up / acknowledge / encourage_continue / reflect_summary / transition / repair
progress_update       # optional, only from structured backend progress or automation events
next_module
memory_hints
open_question_hints
audit_hint_used
safety_flags
```

### Streaming TTS

当前存在两条 TTS 播放路径：

```text
历史 assistant message / no-answer prompt
 -> backend TTS provider
 -> audio_files
 -> 小程序下载并播放

AI 下一轮追问
 -> backend assistant-turn-stream WebSocket
 -> LLM streaming 输出文本 delta
 -> backend bidirectional TTS WebSocket 接收文本流并返回音频流
 -> 后端按小程序播放器能力切成可播放缓冲段
 -> 小程序顺序播放临时音频片段
 -> turn.completed 后落库 assistant message / audio_file
```

后端 TTS 接入收敛为火山/豆包语音 V3，环境变量为 `TTS_PROVIDER=volc`、`VOLC_TTS_APP_ID`、`VOLC_TTS_ACCESS_TOKEN`、`VOLC_TTS_API_KEY`、`VOLC_TTS_RESOURCE_ID`、`VOLC_TTS_SPEAKER`、`VOLC_TTS_RESPONSE_FORMAT`、`VOLC_TTS_BIDIRECTIONAL_ENDPOINT`。默认 `TTS_PROVIDER=disabled`，没有配置时小程序仍显示 AI 文字追问，不阻塞 ASR 和 LLM。AI 访谈和成书语音编辑使用 bidirectional TTS；历史 assistant message 重播、目录/章节/全书朗读等非实时音频仍可走 unidirectional SSE 并读取字幕时间戳。

小程序语音聊天页的下一轮 AI 追问使用 `assistant-turn-stream`，前端收到文本 delta 后即时展示，收到音频分片完成事件后顺序播放；播放完成且 voice gateway 可录音时自动进入下一轮收听。历史 assistant message 重播和 no-answer prompt 仍使用 message TTS 下载路径。用户开始说话、跳过追问、结束页面时会取消未完成合成或停止播放，避免旧追问音频和新回答冲突。

为了让高亮块跟随真实语音位置移动，后端请求火山 V3 时开启 `audio_params.enable_subtitle=true`，读取 `sentence.words[].startTime/endTime` 原生字幕时间戳，转换为逐字/词 `start_ms` / `end_ms` 时间轴，保存到 `memoir_chat_messages.metadata_json.tts_alignment`，并随 TTS response 返回给小程序。小程序播放时只按该时间轴定位当前字，不再按播放百分比估算；已有旧 provider 音频不会继续复用，会在下次请求时按火山 V3 重新生成。

当前流式体验的边界是：provider 已使用“流式文本输入 + 流式音频输出”，但小程序播放器仍需要临时文件，因此后端会把连续音频流切成短缓冲段推送；用户插话打断、断线续播和真正追加式播放器仍属于后续增强。

### Realtime Turn Orchestration And Barge-in

语音聊天的实时编排由小程序 `VoiceAssistantTurnRuntime`、`voice-chat` 页面状态机和后端 `assistant-turn-stream` 共同负责。下一阶段优先实现手动麦克风抢占：

```text
AI speaking / preparing audio
 -> user taps microphone
 -> miniprogram stops current playback and highlight timeline
 -> client sends turn.cancel with reason=interrupted_by_user
 -> backend stops LLM/TTS generation and marks turn as interrupted
 -> miniprogram starts RecorderManager immediately
 -> voice gateway continues normal ASR flow
```

第一版只做用户主动点击抢占。自动 VAD 抢占需要确认设备外放不会被麦克风误识别成用户说话，不能和手动抢占混在同一个 PR。

中断后的 assistant turn 不应作为完整有效 AI 回复参与后续 prompt。后端可以记录中断元数据，用于排查状态机问题和避免旧音频继续落库，但不能把半句话当作用户听完的追问。

### Project Progress Snapshot And Automation Events

项目进度不能由小程序临时拼，也不能让 LLM 自己推断。后端需要提供当前项目的结构化快照：

```text
ProjectProgressSnapshot
├─ valid_utterance_count
├─ assigned_utterance_count
├─ pending_utterance_count
├─ chapter_count
├─ confirmed_chapter_count
├─ draft_word_count
├─ recent_automation_events
└─ chapter_progress_items
```

后台自动操作必须写入事件，供成书页、进度页和 AI 访谈共同消费：

```text
BookAutomationEvent
├─ event_type            # source_assigned / source_held / chapter_auto_revised / audit_completed
├─ project_id
├─ chapter_id
├─ utterance_ids
├─ summary_text
├─ confidence
├─ status                # pending / completed / failed / needs_review
└─ created_at
```

AI 访谈只允许播报这些结构化快照和事件里的事实。为了避免机械刷数字，prompt policy 应要求进度鼓励“有变化才说、一次只说一个重点、说完仍回到一个具体问题”。

### Background Source Routing And Auto Editing

现有 `book_auto_revision.py` 负责“章节已有新增 source refs 后如何补写正文”。下一阶段缺的是前置判断：新记录到底应该进入哪一章，还是暂缓处理。

建议新增 `source_router.py`，输入是新讲述记录、现有章节标题和 source 摘要、最近几条归章事件、当前项目画像；输出是结构化决策：

```text
SourceRoutingDecision
├─ action                # assign_to_chapter / create_candidate_chapter / hold_for_review / ignore_low_value
├─ target_chapter_id
├─ candidate_title
├─ utterance_ids
├─ confidence
├─ rationale
└─ evidence_summary
```

只有高置信度 `assign_to_chapter` 才自动写入章节 source refs，并触发已有自动补写任务。明显切换话题、信息过短或不确定时只能进入待确认或候选章节，不自动删除原始记录。

### Book Quality Audit

后台审计不是生成漂亮评分，而是给 AI 访谈和成书页提供下一步动作建议。语义审计、素材卡片、章节卡片、章节素材关系结构化的连续 PR 计划见 [MEMOIR_SEMANTIC_AUDIT_REFACTOR_PLAN.md](MEMOIR_SEMANTIC_AUDIT_REFACTOR_PLAN.md)。建议 `book_quality_audit.py` 输出：

- 每章 source 数、已成文字数、待补写 source 数。
- 缺口：时间、地点、人物、事件经过、情绪、结果、时代背景。
- 重复或相似 source 提示，供重新生成时合并相同事件。
- 下一轮访谈建议问题，供 `voice_followup_ai.py` 选择性使用。

审计结果应该是版本化快照，避免每次打开页面都重新调用 LLM，也方便解释 AI 为什么问某个方向。

### Voice Editing Planner

语音编辑不应继续依赖小程序端关键词或过窄的固定命令。后端应提供“自然语言 -> 操作计划 -> 用户确认 -> 执行”的 planner：

```text
user voice command
 -> context retrieval: chapter order, chapter titles, source record numbers, pending sources
 -> action plan candidates
 -> preview
 -> confirm
 -> executor
```

动作应是显式类型，而不是自由文本：

```text
assign_source_to_chapter
move_chapter
rename_chapter
merge_chapters
split_chapter
rewrite_chapter
ask_clarification
```

小程序只负责展示 AI 解释、操作预览和确认按钮；复杂语义判断、章节匹配和执行校验放在后端。

## 数据模型方向

新模型以 chat message 为主，不再以 question 为主。

```text
memoir_voice_sessions
├─ id
├─ project_id
├─ status                 # created / listening / speaking / stopped / failed
├─ guidance_mode          # free_narration / guided_interview
├─ current_guidance_module
├─ provider
├─ input_format
├─ started_at
├─ stopped_at
└─ error_message

memoir_chat_messages
├─ id
├─ project_id
├─ voice_session_id
├─ role                   # user / assistant / system
├─ content_text
├─ content_status         # partial / final / corrected
├─ audio_file_id
├─ transcript_version_id
├─ parent_message_id
├─ guidance_intent
├─ turn_index
├─ created_at
└─ updated_at

memoir_voice_utterances
├─ id
├─ voice_session_id
├─ chat_message_id
├─ audio_file_id
├─ transcript_raw
├─ transcript_edited
├─ start_ms
├─ end_ms
├─ provider
├─ provider_payload_json
└─ created_at
```

旧表的定位：

- `memoir_questions` / `memoir_sessions` / `memoir_answers` / `memoir_drafts`：legacy 固定问答链路；当前代码不再保留，只通过 baseline commit 追溯。
- 旧 memory-first 结构化记忆、覆盖度、问题候选和旧输出链路：当前代码不再保留；后续如恢复此能力，需要重新挂到 chat message、voice utterance 和 transcript evidence 上。
- `audio_files`：可复用，仍保存原始音频资产。
- `memoir_transcript_versions`：继续复用，用于 ASR 原文、方言修正、用户编辑和最终确认。
- `memoir_book_*`：保留为当前成书规划、章节草稿和整书输出链路。

### Realtime Voice Schema Boundary

实时语音链路的第一层持久化边界只认以下表：

```text
audio_files
memoir_voice_sessions
memoir_chat_messages
memoir_voice_utterances
memoir_transcript_versions
```

这组表必须和 SQLAlchemy model 的列保持一致。应用启动时可以修复已经明确识别的安全漂移，例如早期开发库缺少 `memoir_transcript_versions.voice_session_id`；修复后必须执行严格 schema 断言，缺表、缺列或多余列都应让后端启动失败，避免把错误延迟到用户录音结束才暴露。

开发阶段如果不需要保留旧小程序数据，可以手动重建这组表：

```bash
cd backend
PYTHONPATH=. python scripts/rebuild_memoir_voice_schema.py --describe-only
PYTHONPATH=. python scripts/rebuild_memoir_voice_schema.py --yes
```

`--describe-only` 只打印模型期望列和当前数据库实际列；`--yes` 会删除并重建上述实时语音表，会清空 voice session、chat message、utterance、transcript version 以及 `audio_files` 元数据。ECS 上执行前必须先做数据库和音频文件备份，并确认不需要保留这部分数据。

### Transcript Search And Version Retention

当前阶段继续使用 SQLite 作为主库，但转写搜索不能继续依赖普通 `LIKE` 扫描。后端启动迁移会为 `memoir_voice_utterances` 建立 SQLite FTS5 虚拟表：

```text
memoir_voice_utterances_fts
├─ transcript_raw
└─ transcript_edited
```

FTS 使用 `trigram` tokenizer，适合中文三字以上片段检索；两字或更短的中文关键词仍保留普通 `LIKE` fallback，避免“外婆”“老师”这类常见短词搜不到。FTS 表通过 SQLite trigger 跟随 `memoir_voice_utterances` 的 insert/update/delete 自动同步。

`memoir_transcript_versions` 当前只保留必要追溯窗口：每条转写记录、每种 `version_type` 最多保留最近 3 个版本。写入新版本时必须设置 `source_artifact_id = utterance.id`，再裁剪同一 `source_artifact_id + version_type` 下更旧的版本。`memoir_voice_utterances.transcript_raw` 和 `transcript_edited` 仍保存当前可展示文本，成书证据链继续引用当前有效转写记录和音频时间段。叙事架构落地 `ClaimEvidence` 后，被 Claim、DraftBlock 历史版本或正式书稿引用的 transcript version 必须 pin 并排除在裁剪范围之外，具体由叙事架构文档约束。

### PostgreSQL Migration Roadmap

SQLite 只作为当前阶段的轻量主库。随着多用户、连续录音、成书生成和搜索量增长，PostgreSQL 迁移是后续重要架构任务，不能等到 SQLite 写锁、备份恢复和搜索性能成为线上故障后再处理。

迁移目标：

- 主业务表从 `/opt/pawtale/data/pawtale.db` 迁移到 PostgreSQL，保留明确的 schema migration 流程。
- 转写搜索从 SQLite FTS5 迁移到 PostgreSQL full-text search、`pg_trgm`，或独立搜索服务；中文搜索质量需要单独评估分词策略。
- 音频本体继续不进入关系数据库，后续迁到 OSS/S3 类对象存储；数据库只保存 `audio_files` 元数据和对象 key。
- 迁移前必须补齐数据导出、校验、回滚和只读演练脚本，至少校验用户、项目、voice session、utterance、transcript version、book plan/source ref 和 audio file 引用完整性。
- PostgreSQL 上线后再考虑行级权限、备份加密、敏感文本字段加密和审计日志，不把这些能力继续绑在单机 SQLite 文件权限上。

### Realtime ASR Smoke Check

部署后先跑不调用真实 provider 的本地 smoke，确认 `.env`、schema 和 Volc 协议帧构造正常：

```bash
cd backend
PYTHONPATH=. python scripts/smoke_realtime_asr.py
```

脚本只输出配置是否存在，不打印 provider key。需要做真实豆包 ASR 冒烟时，传入一段 16kHz mono `pcm_s16le` 文件：

```bash
cd backend
PYTHONPATH=. python scripts/smoke_realtime_asr.py --pcm-file /path/to/sample.pcm
```

ECS 部署后建议顺序：

1. 先备份 `/opt/pawtale/backend` 和 `/opt/pawtale/data/pawtale.db`。
2. 同步 backend 代码，保留 ECS 上的 `/opt/pawtale/backend/.env`。
3. 重启 `pawtale-backend`。
4. 请求 `https://pawtale-api.spaceflows.cn/health`。
5. 在 ECS 上执行 `cd /opt/pawtale/backend && PYTHONPATH=/opt/pawtale/backend/.pythondeps:/opt/pawtale/backend python3 scripts/smoke_realtime_asr.py`，确认 schema 和 provider 配置。

## Realtime Event Contract

小程序和 voice gateway 之间使用事件协议，不直接暴露 provider 协议。

第一版后端网关路径：

```text
GET /api/mobile/memoir/voice-sessions/{voice_session_id}/gateway
```

鉴权沿用 `pawtale_session` cookie。小程序端通过 `wx.login()` 获取一次性 code，后端用 `WECHAT_MINIPROGRAM_APPID` 和 `WECHAT_MINIPROGRAM_SECRET` 调用微信 code2session 换取 openid，并把 openid 绑定到 Pawtale 用户。小程序端后续通过 WebSocket header 携带同一个 session cookie；微信 AppSecret 和 provider key 都只保存在后端 `.env`。

```text
client -> server
session.start
audio.frame
audio.end
session.stop

server -> client
session.ready
asr.partial
asr.final
session.error
session.closed
```

当前 WebSocket 已实现的 ASR 子集：

```text
client -> server
session.start
audio.frame        # { "audio": "<base64 pcm_s16le_16000_mono bytes>" }
audio.end
session.stop

server -> client
session.ready
session.started
audio.ended
asr.partial        # { "text": "...", "start_ms": 0, "end_ms": 1200, "provider_payload": {...} }
asr.final          # { "text": "...", "message_id": "...", "utterance_id": "...", "audio_file_id": "...", "provider_payload": {...} }
session.error
```

AI 下一轮追问当前走单独的 WebSocket：

- 路径：`/api/mobile/memoir/voice-sessions/{voice_session_id}/assistant-turn-stream`。
- 客户端发送 `turn.start` / `turn.cancel`。
- 服务端返回 `turn.ready`、`turn.started`、`text.delta`、`text.segment`、`audio.chunk`、`audio.segment_done`、`turn.completed`、`turn.cancelled`、`turn.failed`。
- `turn.completed` 后端已完成 assistant message 和最终音频落库；小程序用它替换流式临时展示行。

历史消息重播和 no-answer prompt 仍使用 `POST /api/mobile/memoir/voice-sessions/{voice_session_id}/messages/{message_id}/tts` 生成并关联 `audio_file_id`，再通过 `GET /api/mobile/memoir/audio-files/{audio_file_id}/playback` 下载播放。

`session.start` 必须包含引导模式，后端据此选择 prompt policy：

```json
{
  "type": "session.start",
  "project_id": "proj_...",
  "guidance_mode": "guided_interview",
  "narrator_id": "elder_..."
}
```

事件数据示例：

```json
{
  "type": "asr.partial",
  "voice_session_id": "vs_...",
  "utterance_id": "utt_...",
  "text": "我年轻的时候",
  "sequence": 12
}
```

```json
{
  "type": "asr.final",
  "voice_session_id": "vs_...",
  "utterance_id": "utt_...",
  "message_id": "msg_...",
  "text": "我年轻的时候在厂里工作。",
  "start_ms": 1200,
  "end_ms": 4800
}
```

## 实施顺序

### 已完成基础

下面这些能力已经进入当前主线，不再作为下一阶段 PR 目标重复规划：

- Streaming ASR、voice session、chat message、utterance、transcript version 和原始音频证据链。
- LLM 动态追问、assistant message 落库、assistant-turn-stream 和双向 TTS 第一版。
- 小程序语音聊天、自动续听、TTS 字幕高亮、转写记录、成书页、我的页当前项目统计和 TTS 设置。
- legacy 固定问题、整段录音上传、微信云函数和旧小程序页面已清理。

下一阶段不继续围绕“是否流式”拆 PR，而是围绕实时访谈体验、项目进度上下文、后台自动整理和语音编辑能力拆分。

### 新阶段 PR 拆分

1. PR 1: Project Progress Snapshot And Automation Events
   - 新增项目进度快照服务，统一有效讲述记录、已归章记录、待归章记录、章节数、成文字数等统计口径。
   - 新增后台自动整理事件模型/服务/API，先支持只读事件和最近事件摘要。
   - 不改变 AI 追问和成书行为，为后续激励播报、自动归章和审计提供基础。

2. PR 2: Manual Microphone Barge-in
   - AI 播放或准备音频时，用户点击麦克风可打断当前 assistant turn。
   - 小程序停止播放和高亮，发送 `turn.cancel reason=interrupted_by_user`，随后立即开始录音。
   - 后端明确 `interrupted` 终态，取消 LLM/TTS 生成，不把半句 AI 回复作为完整追问写入后续上下文。

3. PR 3: Progress-aware AI Encouragement
   - `voice_followup_ai.py` 消费 PR 1 的 progress snapshot 和 recent automation events。
   - AI 可以把承接、认可、进度提醒和下一问组合成自然回复。
   - 进度鼓励必须 sparse：有变化才说，一次只说一个真实事实，不能牺牲“一次只问一个问题”。

4. PR 4: Source Router For New Records
   - 新增新记录归章判断：进入已有章节、候选新章节、暂缓处理、低价值保留。
   - 高置信度才自动写入章节 source refs；不确定或明显换话题时进入待确认。
   - 所有判断写入 automation event，保证能解释和回溯。

5. PR 5: Auto Assignment To Auto Revision
   - 把 PR 4 的高置信度归章结果接到现有 `book_auto_revision.py`。
   - 自动归章后触发章节补写，并把补写完成事件暴露给成书页和 AI 访谈。
   - 支持 AI 播报“新增记录已补进某章”这类具体进展。

6. PR 6: Book Quality Audit
   - 后台生成章节完整度和项目进度审计快照。
   - 输出每章缺口、重复素材、待补问题和下一轮访谈建议。
   - `voice_followup_ai.py` 可以选择性使用审计提示，引导用户补足人物、地点、事件经过、情绪和结果。

7. PR 7: Voice Editing Planner V2
   - 后端提供自然语言语音编辑 planner，把用户表达转成 typed action plan。
   - 小程序展示操作预览，用户确认后再执行。
   - 支持归章记录、移动章节、合并章节、拆分章节、重命名、重写和需要澄清。

### 第一批详细计划

第一批只做 PR 1、PR 2、PR 3。目标是先把“AI 能被打断”和“AI 能基于真实进度鼓励用户”做稳，不急着自动改书。

#### PR 1: Project Progress Snapshot And Automation Events

后端：

- 新增 `progress_snapshot.py`，从当前项目读取 voice utterances、book plan、chapter source refs、chapter drafts 和 confirmed outputs，输出 `ProjectProgressSnapshot`。
- 新增 `automation_events.py`，提供事件写入和最近事件查询。第一版可以只支持 service 层和 mobile read API，不需要先做复杂后台 worker。
- 在 schema 层定义明确字段，避免把核心统计藏在任意 JSON 里。
- 我的页、进度页和 AI 访谈后续都用同一份统计，不能各自重新拼。

小程序：

- 先不改主要 UI，只在 API 类型和必要页面数据模型里接入新的 progress snapshot。
- 如果已有页面统计字段和新快照冲突，以新快照为准。

测试：

- 后端单元/API 测试覆盖有效记录、已归章记录、待归章记录、成文字数、删除记录不计入统计。
- 小程序测试覆盖 API 字段解析和现有项目统计页面不破坏。

验收：

- 同一个项目的“我的页统计、进度页统计、AI 可用统计”来自同一后端口径。
- 可以返回最近自动事件，即使第一版事件为空。

#### PR 2: Manual Microphone Barge-in

后端：

- 扩展 `assistant-turn-stream` 的 cancel 语义，区分用户跳过、页面退出和麦克风抢占。
- `interrupted_by_user` 不产生完整 assistant message，或只保存不会进入追问上下文的中断元数据。
- 确保 LLM/TTS 任务、音频 chunk 和落库动作在取消后停止。

小程序：

- `VoiceAssistantTurnRuntime` 增加 `interrupt(reason)`，统一停止播放、清理高亮、关闭当前 turn socket。
- `voice-chat` 页在 AI speaking / preparing audio 状态下点击麦克风，直接走 interrupt + startRecording。
- UI 状态从“播放中/思考中”切到“正在听你说”，避免出现播放和录音并存。

测试：

- runtime 单测覆盖播放中 interrupt、音频准备中 interrupt、重复 interrupt。
- 页面单测覆盖 AI speaking 时点击麦克风会取消 turn 并启动录音。
- 后端测试覆盖 `turn.cancel reason=interrupted_by_user` 后不会发 `turn.completed`。

验收：

- 真机上 AI 没说完时点麦克风，AI 立刻停止，麦克风开始录。
- 下一轮 AI 不引用被用户打断的半句内容作为已完成追问。

#### PR 3: Progress-aware AI Encouragement

后端：

- `voice_followup_ai.py` 增加 progress snapshot 和 automation event summary 输入。
- prompt 明确允许多意图组合：`acknowledge + encourage_continue + follow_up`，但最终仍只问一个具体问题。
- 进度事实必须来自结构化输入，模型不能生成未给出的数字、章节名或自动整理结果。
- 新增输出元数据，例如 `progress_update`、`audit_hint_used`，方便后续观察 AI 是否过度播报。

小程序：

- 语音聊天页无需新增复杂 UI，只正常展示和播放 AI 文本。
- 如果后端返回 progress metadata，先保留在 message metadata，不强制展示独立标签。

测试：

- 后端 prompt/context 构造测试：有进度、有自动事件、无进度三种输入。
- 基线 eval 增加少量用例：AI 能自然鼓励、不编造数字、不连续多问。
- 小程序现有 assistant turn 流测试确认新增 metadata 不破坏播放。

验收：

- AI 偶尔能说出真实进度或自动整理进展。
- 没有进度变化时，AI 不机械重复统计。
- 输出仍符合回忆录访谈口吻，一次只落到一个具体问题。

## 验收标准

实时语音基础链路继续必须满足：

- 真机上麦克风开始后 1 秒内出现 partial transcript。
- final transcript 能稳定落库。
- 离开并重新进入同一个 voice session 后，已 final 的 transcript 不丢失。
- 断网或 provider 错误能给出明确 UI 状态。
- 小程序端不包含 provider key 或微信 AppSecret。
- 旧链路不再作为产品代码路径保留。

下一阶段第一批必须满足：

- AI 说话或准备音频时，用户点击麦克风可以立即打断并开始录音。
- 被打断的 AI 半句不会作为完整追问进入后续上下文。
- 当前项目统计只有一套后端口径，删除记录、待归章记录、已归章记录和成文字数都可解释。
- AI 的进度鼓励只引用后端给出的真实快照或事件，不编造记录数、章节名或完成度。
- AI 鼓励不能替代访谈目标；每轮回复仍应自然落到一个具体、容易回答的问题。

完整语音聊天版本必须满足：

- 用户可以连续讲述，不需要理解固定题目。
- 用户可以在开始访谈时选择弱引导或强引导。
- AI 追问来自上下文，不依赖模板题库。
- 强引导能按人、事、地点、时间段、主题推进；弱引导不会频繁打断自主叙述。
- 每个 AI 输出都能追踪到输入消息和 prompt context。
- 原始音频、ASR 原文、修正文、用户确认文本分层保存。
- 家人/编辑端能追溯事实来源。
- 后台自动归章、补写、审计和语音编辑动作都能解释来源、置信度和用户确认状态。

## Legacy 追溯

旧版文档和产品代码不再保留在当前工作树中。需要查看固定问题、离线 ASR、memory-first 中间态或旧写作产物设计时，直接通过 legacy baseline commit 做 Git 追溯。

后续实现以本文档为主。旧方案中仍有价值的部分包括：证据链、结构化记忆、覆盖度地图、写作产物分层。这些能力会迁移到新版 chat message 和 transcript evidence 模型上。
