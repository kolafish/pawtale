# 回忆录实时语音架构

## 文档状态

本文定义回忆录小程序实时语音链路：

- VAD 与录音控制；
- Streaming ASR；
- LLM 动态回应与追问；
- Streaming TTS；
- voice session、消息和转写的持久化；
- 自由讲述、主题讲述和成书补讲共用的语音状态机。

素材、Section、章节正文、缓存、请求和成书补全边界统一见
`MEMOIR_INTERVIEW_AND_BOOK_ARCHITECTURE.md`。

## 一、产品形态

用户面对的是一次自然语音访谈：

```text
VoiceSession
  → 实时录音
  → Streaming ASR partial
  → ASR final
  → VoiceUtterance（Material）
  → LLM 选择受控策略动作
  → Streaming TTS 回应
  → 下一轮或结束
```

系统支持四种讲述方式，名称和说明统一来自 `memoir-narration-mode.ts`：

| 讲述方式 | 用户怎么使用 | guidance_mode | interview_type | session_goal |
| --- | --- | --- | --- | --- |
| 自由讲述 | 按自己的节奏讲，AI 倾听与轻提示 | free_narration | ai_interview | open_narration |
| 引导访谈 | AI 按人物、事件和话题提问 | guided_interview | ai_interview | open_narration |
| 书稿补充 | 先选待补问题，再围绕该问题补讲 | guided_interview | ai_interview | book_completion |
| 真人访谈 | 家人朋友提问，分别记录采访者和讲述者 | free_narration | human_interview | open_narration |

四项是产品入口；`guidance_mode` 表达引导强度，`interview_type` 表达谁来访谈，
`session_goal` 表达业务目标。保持现有三个显式字段，不将其混成新的后端状态。

四种讲述方式都是回忆录级默认设置，在“我的回忆录”的编辑设置页统一修改。
首页不提供临时切换，开始时读取当前回忆录保存的 `guidance_mode`、`interview_type` 与
`session_goal` 默认值。书稿补充会先进入待补问题列表，选择问题后再创建补讲会话。
开始后只在标题位置展示本次方式，不提供展开或切换入口；准备、录音、AI 回应、大字模式和保存阶段
始终沿用服务端会话字段。修改回忆录默认设置只影响之后创建的新会话，不追溯当前或历史会话。
真人转写复用普通内容区域，不用全屏层遮住方式；章节补讲的返回关系不变。

## 二、核心持久化

实时链第一层只写：

```text
audio_files
memoir_voice_sessions
memoir_chat_messages
memoir_voice_utterances
memoir_transcript_versions
```

其中：

- AudioFile 保存原始音频资产；
- VoiceSession 保存一次访谈的目标、模式和生命周期；
- ChatMessage 保存用户与 AI 的对话顺序；
- VoiceUtterance 保存一次完整用户讲述，并直接承担 Material 角色；
- TranscriptVersion 保存 ASR 原文、自动校正和人工修改的可追溯版本。

ASR partial 只用于实时显示，不作为事实落库。只有 provider final 或明确的恢复结果
才能创建用户消息、VoiceUtterance、TranscriptVersion 和音频引用。

一次讲述保持完整，不在语音层拆成多条 Claim，也不在语音层决定正文位置。

## 三、会话状态

VoiceSession 使用明确状态：

```text
created → listening ↔ speaking → stopped
                    ↘ failed
```

客户端同时维护有限的瞬时状态：

- WebSocket 连接状态；
- 录音器状态；
- ASR partial；
- 当前 assistant turn；
- TTS 播放队列；
- 自动续听计数；
- 页面是否可见。

这些状态是可以重叠的独立事实，不合并成一个覆盖所有组合的大枚举。例如流式 TTS 已经
开始播放时，assistant turn 仍可能尚未结束。页面不能分别在标题、正文和操作区解释这些
布尔值，而必须通过一个纯派生的交互阶段统一决定当前视觉焦点：录音优先，其次是已经有
内容的 assistant 回应，再其次才是尚未产出新问题的讲述整理。交互阶段不单独持久化，
避免它与连接、录音、assistant 和播放的真实状态产生双向同步。

错误也按责任边界保留：assistant 文字失败与 TTS 播放失败是不同结果；TTS 失败不能抹掉
已经完成的文字。状态文案只由交互阶段和错误派生，不能反过来作为流程判断条件。

持久状态以后端为准。页面恢复、前后台切换或断线重连时，客户端重新读取
VoiceSession 和消息，不根据客户端缓存的 UI 状态猜测服务端结果。

首页保存当前 VoiceSession 的入口参数，实时状态由 voice-session 组件实例持有。首页刷新
不会重建正在展示的组件；用户点“首页”或“结束讲述”时，组件复用同一收尾流程，结束
streaming 并等待后端会话确认为 stopped 后才收起和刷新首页。收尾失败时留在讲述界面明确
提示，不引入可恢复的隐式“暂停”状态。完成事件携带 session id，旧实例事件不能结束
下一次讲述。账号或项目改变时清除旧入口并卸载组件。

组件卸载后保留其失效标记直到实例被回收，迟到的授权或会话响应不能重新连接。
普通续讲和话题讲述通过用户、项目隔离的导航目标回到首页；章节补讲仍返回原章节。
首页嵌入时由首页刷新故事进度，组件不再重复轮询进度通知。

## 四、录音与 Streaming ASR

### 4.1 开始录音

开始前依次确认：

1. 当前页面仍有效；
2. 用户已完成语音授权和微信隐私授权；
3. VoiceSession 未停止；
4. gateway 已连接或可重新连接；
5. 当前 assistant 播放已停止或被用户打断。

录音帧通过 WebSocket 发送，客户端 VAD 只负责音量展示和自动收尾建议，不替代
provider final。

### 4.2 最终结果

收到 ASR final 后：

1. 服务端保存音频；
2. 创建或更新 ChatMessage；
3. 创建 VoiceUtterance；
4. 创建 TranscriptVersion；
5. 返回稳定 ID；
6. 再进入 assistant turn。

同一 provider final 必须幂等，重连或重复事件不能创建两份 Material。

### 4.3 恢复

如果音频已保存但转写未完成，记录页只展示“等待转写”，允许用户显式恢复。恢复流程
不能伪造空文本，也不能把失败吞成成功。

### 4.4 语音编辑命令

故事、小节和讲述文字的语音编辑复用同一个 `voice-text-editor` 和临时 dictation gateway，
不为首次修改、已有方案反馈或不同页面分别维护录音状态机。状态归属固定如下：

| 层级 | 唯一职责 |
| --- | --- |
| 页面 | 加载、AI 任务、修改方案等业务状态 |
| `voice-text-editor` | `idle / connecting / listening / stopping` 交互阶段 |
| Streaming ASR 客户端 | WebSocket、原生录音器和 ASR 协议事实 |
| 后端 gateway | `ready / listening / finishing / stopped / failed` 协议生命周期 |

页面不能复制连接或录音状态，也不能为同一个可见编辑流程交替挂载多个语音编辑器。
页面恢复已有方案时，先完成业务状态读取，再挂载唯一的编辑器；首次修改和继续调整只改变
文案、提交目标和 busy 状态。所有 Streaming ASR gateway 必须先释放用户容量租约，再发送
`session.stopped`，使客户端收到停止确认后可以安全进入下一次编辑或访谈。

短语音输入只有两种产品模式，但共用上述状态机：编辑命令进入页面后自动收音、停顿后自动
提交；需要逐项核对的补全回答只把语音转成文字，由用户确认后保存。模式由组件属性决定，
页面不得据此复制连接、录音、收尾状态。连续访谈仍统一复用 `voice-session`；它保留 TTS、
打断、持久化会话等正交事实，但连接过程只以 `VoiceChatConnectionStatus` 为唯一事实源，
不再另设与 `status === "connecting"` 重复的布尔状态。

语音编辑已确定要移动的完整小节时，预览只列出来源章节、小节标题和建议的新章名；
用户点确认按钮后才执行。模型不得把确认或未指定章名本身当作需要再次语音回答的歧义。

## 五、LLM 策略层

LLM 接收经过整理的 InterviewBrief，而不是直接读取所有数据库表。Brief 可以包含：

- 最近对话；
- 最近 Material；
- 当前 Section 摘要；
- 话题地图相关项；
- 当前 CompletionIssue；
- conversation momentum；
- 用户偏好与拒答记录；
- 安全约束；
- 本轮可用的候选动作。

候选动作由系统限制，例如：

- 继续倾听；
- 沿当前内容追问；
- 询问人物关系；
- 询问原因或结果；
- 总结确认；
- 回到已有 Section；
- 提供新话题；
- 暂停或结束。

模型选择动作并生成自然回应，领域逻辑决定动作是否允许。模型不能确认事实、改变隐私
决定、删除素材、覆盖用户排除项或自动解决矛盾。

策略决策记录输入快照、候选动作、选中动作、原因、生成问题、用户结果、policy version
和可选人工评价。日志用于 prompt 评估，不成为业务事实。

## 六、Assistant Turn 与 TTS

assistant turn 通过单一流返回：

```text
turn.started
text.delta*
audio.format
audio.frame*
audio.done
turn.completed
```

失败或取消使用：

```text
turn.failed
turn.cancelled
```

`turn.completed` 是本轮 AI 消息完成的唯一成功信号。客户端不能把 text delta 或部分音频
当作完整消息。

首次问候、欢迎回来、项目进度和访谈问题都作为同一轮结构化上下文交给 LLM，由一次文字
生成统一组织，再进入同一条 TTS 流；服务端不先播放固定进度前缀再拼接模型问题。语音
自动播报只读取尚未播放的 automation event，不把每轮实时计算的项目快照直接当作新
消息；讲述数量到达里程碑时写入一条语音专用 event，客户端播放成功后用现有
`voice-played` 回执消费。用户主动重听已保存消息不属于新的自动播报。

用户点击麦克风时可以打断正在生成或播放的 AI：

1. 客户端立即停止本地播放；
2. 发送取消原因；
3. 后端停止 LLM/TTS；
4. 中断的半句话不进入后续对话上下文；
5. 录音器开始接收用户讲话。

自动打断复用已认证的语音 gateway。小程序发送 `barge_in.start/frame/stop`（含
`monitor_id`），音频格式仍为 16 kHz、单声道 PCM16；监听帧不送 ASR，也不写入音频证据。
ECS 后端加载随代码固定、校验 SHA256 的 Silero ONNX 模型，共享权重，每次监听独立
维护模型状态。前 640 ms 标定当前播放音量，之后连续 160 ms 的人声概率 ≥ 0.8 且音量
超过播放基线时发送 `barge_in.speech`。音量门槛只用于播放回声标定，不再使用固定
0.035/0.07 响度门槛或 450/700 ms 发声等待。

小程序保留最近 2 秒监听音频。服务器返回讲话起点（含 160 ms 前置音频），客户端裁掉
更早的播放内容，立即取消 AI，并在授权同步期间继续缓存。正式录音复用同一个录音器，
先发送 `session.start`，再按顺序补送缓存和后续音频，不停止再重开麦克风。旧 monitor
事件不能触发新播放；检测响应过晚而起点已不在缓存中、准备超过 10 秒、后台退出或
授权失败时，明确停止/提示，不发送缺失句首的缓存。

该播放基线不是声学回声消除：首次标定期内的讲话、外放音量改变、强回声和小声插话
仍需要真机验证。真实往返延迟也会增加打断时间，160 ms 是模型确认窗口，不是总延迟。
模型固定在 `backend/app/services/asr/models/`，在现有后端进程内使用 CPU 推理，无需
额外服务、端口或云端模型 API；启动失败或模型校验失败会阻止后端启动。

TTS 播放失败不能删除已经完成的文字回复，但必须显式展示播放失败，并允许用户重听。

## 七、自由讲述与主题讲述

两种模式给 AI 的上下文不同（`voice_interviewer_prompt.py`）：

| 上下文 | 自由讲述 | 主题讲述（引导访谈） |
|---|---|---|
| 本次会话最近 12 条对话 | 有 | 有 |
| 以前讲过的相关原文（记忆激活） | 不检索 | 有 |
| 讲述者资料：用户填的性别、年龄、家乡 | 不要 | 有 |
| 书稿概况：章节名和每章讲述段数 | 不要 | 有 |
| 人生话题：一行列出各项和三档状态（从某个话题进来时附这一项的说明） | 不要 | 有 |
| 上次讲到的章节 | 仅开场 | 仅开场 |
| 进度事实 | 仅开场，且有未播报的新进展时 | 同左 |

自由讲述以用户当前内容为主：AI 只接住刚才的内容，请叙述者接着讲，不问具体细节，
不推荐话题。

人生话题是一条按人生先后排的时间线（家世与出身、童年、求学与少年、工作与闯荡、成家与儿女、如今的生活、想留下的话）。人、转折、难处不单独设项，prompt 里交代每段人生都可以聊到。

主题讲述把人生话题（话题地图）作为整本书的提纲：开场给两个具体方向，一个接着上次，
一个是还没聊的话题；从“讲述进度”页某个话题进入时，从那个话题开头。讲述中，一个故事
讲完、叙述者说记不清，或一个话题讲得差不多时，AI 可以换一个人或一件事，或从人生话题
里提一个可以拒绝的新方向。叙述者明确换方向时，以他最新一句为准，不再用关键词识别。

讲述中途不播报进度；进度事件留到下一次开场。

话题地图不要求用户逐项填满。每个话题只统计置信度不低于 0.6 的主要匹配（或人工关联），
按讲述段数分三档：还没聊（0）、聊过一点（1–2）、聊了不少（3 段及以上）。

## 八、成书补讲

成书补讲复用同一 voice-session 组件和 assistant turn，只额外绑定：

- `session_goal = book_completion`；
- 当前 CompletionIssue；
- 目标 Section / Chapter；
- 问题创建时的输入签名。

一次只处理一个问题。用户完成有效回答后：

1. 回答保存为普通 VoiceUtterance；
2. 整理器把完整 Material 放入一个 Section；
3. 更新相关 Digest；
4. Issue 标记为已回答并重新评估；
5. 本轮语音结束，不自动追加细节题；
6. 用户回到章节工作台生成或更新完整章节正文。

用户选择“不记得”“以后再说”或“不愿讨论”时，本轮立即结束并记录明确结果，不能继续
换一种问法追问。

## 九、与成书链路的边界

语音层只产出可追溯 Material。成书链路是：

```text
VoiceUtterance
  → SectionMaterial
  → Section
  → SectionDigest
  → ChapterDigest
  → CompletionIssue / 完整章节正文
  → BookOutput
```

归属和正文生成不在 WebSocket 回调里同步执行，而通过独立领域服务和 Job 完成。失败时
原始音频和转写仍然可见。

结构调整统一在书稿的章节/Section 工作区完成，避免多个页面同时修改素材归属。

## 十、主要代码边界

小程序：

```text
components/voice-session/                 # 实时访谈界面与实例级录音、播放生命周期
pages/home/                               # 普通讲述原位展开、保存后刷新故事
pages/voice-chat/                         # 章节补讲入口与返回原任务
pages/transcripts/                        # Material 列表
pages/transcript-detail/                  # 转写、音频和人工校正
pages/book-completion/                    # 全书补全概览
pages/chapter-workspace/                  # 单章下一步、正文和确认
services/memoir-streaming-asr.ts          # gateway 客户端
services/voice-assistant-turn-stream.ts   # assistant turn 流
services/voice-assistant-turn-runtime.ts  # 流式音频播放与取消
domain/voice-chat.ts                      # API 和事件类型
```

后端：

```text
routers/memoir/voice_gateway.py
services/memoir/voice_final_persistence.py
services/memoir/voice_sessions.py
services/memoir/voice_followup_ai.py
services/memoir/voice_transcripts.py
services/memoir/sections.py
services/memoir/material_organization.py
services/memoir/narrative/interview_policy_runtime.py
```

## 十一、测试与验证

自动化测试优先覆盖：

- provider payload 和事件顺序；
- final 幂等持久化；
- 音频、转写和 Material 追溯；
- 断线恢复；
- AI 播放中断；
- 页面隐藏后的录音和播放释放；
- book completion 回答后立即结束；
- 拒答结果不会触发重复追问；
- 跨项目权限隔离。

真机 smoke 至少验证：

- 首次授权；
- 连续说话、停顿和自动收尾；
- AI 语音播放与打断；
- 前后台切换；
- 网络断开与恢复；
- 回答一个成书补讲问题后返回章节工作台。

Provider key 只放在 `backend/.env`，不得进入小程序、tracked 配置或文档示例。
