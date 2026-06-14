# 回忆录语音聊天重构方案

## 状态

这是回忆录小程序的新主线文档。此前的固定问题、整段录音上传、离线转写、整理稿确认路线只作为 legacy 参考，不再作为下一阶段产品目标。

新的产品目标是：

```text
VAD + Streaming ASR + LLM Dynamic Follow-up + Streaming TTS
实时语音输入 + 动态 AI 追问 + 实时语音回复
```

用户感知到的不是“回答第几个问题”，而是一次自然语音访谈：

- 老人直接说话，屏幕同步显示大字转写。
- AI 根据用户选择的引导强度接住上下文，动态追问或只做轻量陪伴。
- AI 回复以语音播放，同时保留文字气泡。
- 后台持续沉淀原始语音、转写、对话消息、结构化记忆和写作素材。

## 新旧版本关系

### Legacy 版本

旧版产品形态是：

```text
Question -> Answer -> Audio File -> ASR Job -> Draft -> Review
```

旧版代码只通过 Git commit 追溯，不在新版文档里维护代码路径清单。后续重构会删除或移动旧页面和旧 API，路径列表很快会失效。

```text
legacy fixed-question baseline commit:
bd3599ad3dddf382b242c47071c856d382543cac
```

需要查看旧版时，直接基于该 commit 做 Git 追溯，例如 `git show bd3599ad3dddf382b242c47071c856d382543cac`。这套代码可以保留到新链路替换完成，但不再继续扩大功能面。后续 PR 如果触碰 legacy 逻辑，应优先服务迁移，而不是继续强化固定问答模型。

### 新版本

新版产品形态是：

```text
Voice Session -> Chat Messages -> Realtime Transcript -> Memory Extraction -> Writing Output
```

新代码建议从独立入口开始，避免和 legacy 页面互相缠绕：

```text
miniprogram/pages/voice-start/                # 项目、叙述者、强/弱引导选择
miniprogram/pages/voice-chat/                 # 新版老人端实时语音聊天
miniprogram/pages/transcript-review/          # 转写校正、回听原始语音
miniprogram/pages/memoir-progress/            # 覆盖度、待追问线索、进度查看
miniprogram/services/memoir-voice-session.ts  # 小程序语音会话状态机
miniprogram/services/memoir-streaming-asr.ts  # 录音帧、WebSocket、partial/final transcript
miniprogram/services/memoir-streaming-tts.ts  # AI 回复的流式播放
miniprogram/domain/voice-chat.ts              # 新版聊天消息和实时事件类型

backend/app/routers/memoir/voice.py           # 本地/服务端 voice session API
backend/app/services/memoir/voice_gateway.py  # 对接实时 ASR/TTS provider
backend/app/services/memoir/chat_orchestrator.py

cloud-functions/memoir-api/                   # 短期可继续承载身份和云存储登记
```

Git 上建议用以下边界区分：

- `bd3599ad3dddf382b242c47071c856d382543cac`：legacy 固定问题链路的可追溯基线。
- `codex/voice-chat-redesign`：新版语音聊天主线分支。
- 第一批 PR 只做基础设施和新入口，不删除旧页面。
- 新版跑通后再单独 PR 清理 legacy 页面、旧 API 和旧文档。

## 产品体验

### 创建方式和引导模式

创建回忆录或开始一次新访谈时，先让用户选择本次访谈模式：

```text
free_narration      # 弱引导 / 自由叙述
guided_interview    # 强引导 / AI 模块化引导
```

弱引导适合已经知道自己想讲什么的人。叙述者可以按自己的时间顺序从小到大讲，也可以围绕某段经历连续讲。AI 不主动切换主题，不强行打断，只在明显停顿、听不清、信息断裂或用户请求帮助时做轻量提示，例如确认是否继续、复述刚才讲到的线索、提醒可以补充时间地点人物。

强引导适合“不知道讲什么”的人。AI 通过 system prompt 和当前覆盖度状态做模块化采访，先让用户选择今天想聊的方向，例如：

- 人：家人、朋友、老师、同事、对自己影响很大的人。
- 事：开心、难过、遗憾、转折、冒险、现在还常想起的事。
- 地点：老家、学校、单位、住过的地方、常去的地方。
- 时间段：童年、求学、工作、婚育、退休、重大变故。
- 主题：爱好、性格、价值观、家庭传统、时代变化。

选定模块后，AI 一次只问一个具体问题，问题要围绕可讲述的场景，而不是让用户填写抽象信息。例如“那我们先聊一件你印象里最开心的事，它大概发生在哪一年？”强引导也要允许用户随时跑题；跑题内容仍然保存，只是后端更新当前模块和覆盖度。

### 首屏

小程序进入后直接进入语音访谈，不再先展示固定问题卡片。

页面核心区域：

- 顶部：当前访谈主题、引导模式、连接状态、暂停/结束。
- 中部：大字实时转写区。
- 底部：语音按钮、AI 播放状态、最近几条聊天消息。

### 核心页面

新版至少需要这些页面或等价视图：

- 开始页：选择回忆录项目、叙述者、引导模式，并进入或继续一个 voice session。
- 语音聊天页：实时录音、Streaming ASR 大字显示、AI 语音回复、暂停/继续/结束。
- 转写校正页：查看每段原始语音、ASR 原文、编辑后文本和版本记录，支持家人或编辑校正。
- 进度页：查看已聊过的人、事、地点、时间段、主题覆盖度，以及 AI 发现的待追问问题。
- 产出编辑页：把确认后的素材组织为章节、片段或故事稿，仍然能回溯到语音证据。

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

### AI 追问

```text
用户 final transcript
 -> LLM 根据近期对话、长期记忆、覆盖度和敏感度生成追问
 -> 保存 assistant chat message
 -> Streaming TTS 播放 assistant message
 -> 页面显示 AI 文字气泡
```

AI 追问不再从固定模板题库取下一题。模板只允许作为冷启动或服务不可用时的兜底。

强引导和弱引导的差异主要体现在 prompt policy，而不是两套完全不同的技术链路：

- 弱引导：`system prompt` 要约束 AI 少问、短问、只在停顿或上下文断裂时补位。
- 强引导：`system prompt` 要约束 AI 按模块推进、一次一个问题、持续更新覆盖度和待追问线索。
- 两种模式都要把用户最新 final transcript 作为事实输入，把 AI 回复保存为 assistant message。

## 技术架构

### 总链路

```text
Mini Program RecorderManager
 -> Voice Client WebSocket
 -> Pawtale Voice Gateway
 -> Streaming ASR Provider
 -> partial/final transcript
 -> Chat Orchestrator
 -> LLM dynamic follow-up
 -> Streaming TTS Provider
 -> Mini Program audio playback
```

Provider API key 不进入小程序。小程序只连接 Pawtale 自己的 voice gateway。

### VAD

第一版不强依赖复杂 VAD，可以先采用按钮控制开始/停止。实时转写链路稳定后再补：

- 客户端轻量 VAD：基于音量或能量判断 UI 状态。
- 服务端 VAD：决定 utterance 边界、打断 AI 播放、提交 LLM。
- Provider VAD：如果实时语音服务提供 server VAD，可以优先复用。

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

旧的 StepFun 整段音频转写不再保留为产品代码路径。后续如果需要做 ASR 质量对照，应通过独立评测脚本或 provider adapter 测试完成，不能重新接回小程序的固定问答/整段转写流程。

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
```

输出应是可追踪的 assistant message，而不是直接播放一次临时文本。

LLM 输出建议带结构化元数据，便于后续进度页和追问策略复用：

```text
assistant_text
guidance_intent       # encourage_continue / clarify / switch_module / ask_detail / summarize
next_module
memory_hints
open_question_hints
safety_flags
```

### Streaming TTS

当前旧版 TTS 是完整生成音频后播放，后续需要改成：

```text
assistant message text stream
 -> TTS audio stream
 -> 小程序边接收边播放
```

如果小程序端流式播放复杂，第一版可以先用“快速生成短句音频后播放”，但接口和数据模型按 assistant message 设计，不再按固定 `question/draft/output` source 设计。

## 数据模型方向

新模型以 chat message 为主，不再以 question 为主。

```text
memoir_voice_sessions
├─ id
├─ project_id
├─ memoir_session_id
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

- `memoir_questions`：legacy 固定问题；新版只作冷启动兜底或迁移参考。
- `memoir_answer_sessions` / `memoir_answer_segments`：legacy 采集草稿；新版不继续扩展。
- `memoir_audio_files`：可复用，仍保存原始音频资产。
- `memoir_transcript_versions`：继续复用，用于 ASR 原文、方言修正、用户编辑和最终确认。
- memory/output 相关表继续保留，消费新版 chat message 和 transcript evidence。

## Realtime Event Contract

小程序和 voice gateway 之间使用事件协议，不直接暴露 provider 协议。

第一版后端网关路径：

```text
GET /api/mobile/memoir/voice-sessions/{voice_session_id}/gateway
```

鉴权沿用 `pawtale_session` cookie。小程序端后续通过 WebSocket header 携带同一个 session cookie，provider key 只保存在后端 `.env`。

```text
client -> server
session.start
audio.frame
audio.end
session.stop
tts.cancel

server -> client
session.ready
asr.partial
asr.final
assistant.delta
assistant.final
tts.audio
tts.done
session.error
session.closed
```

PR3 已实现的最小子集：

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
asr.final          # { "text": "...", "start_ms": 0, "end_ms": 2400, "provider_payload": {...} }
session.error
```

LLM 和 TTS 相关事件仍是后续阶段目标，PR3 不生成 `assistant.*` 或 `tts.*`。

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

### First Version Scope: Production-shaped Streaming ASR

第一版不是一次性 demo，而是按最终上线模块形状实现最小闭环。

第一版必须做：

- 新增独立 voice session API 和 realtime event contract。
- 新增 voice gateway WebSocket，所有 provider key 只在后端。
- 小程序新增 `voice-start` 和 `voice-chat` 第一版入口。
- 小程序发送 16k mono PCM 音频帧，服务端转发给豆包 Streaming ASR。
- 页面展示 `asr.partial` 大字实时转写，`asr.final` 固化成用户消息。
- 保存 voice session、chat message、utterance、原始音频或可回溯的音频片段引用。
- 支持 `free_narration` / `guided_interview` 两种模式字段，但第一版不做复杂 AI 引导。
- 明确错误状态：录音权限、WebSocket 失败、provider 失败、网络中断。
- 删除 legacy 非实时 ASR 产品代码，避免新旧 ASR 路径并行。

第一版明确不做：

- LLM 自动追问。
- Streaming TTS。
- 自动 VAD 断句和用户插话打断。
- 多 ASR provider fallback。
- 断线续传和 session resume。
- 转写校正页、进度页和成稿编辑页的完整实现。
- 方言/噪声系统评测和监控大盘。

第一版和上线版的差距：

- 上线版需要 VAD、断线恢复、限流、成本监控、provider fallback、质量评测集和更完整的编辑校正链路。
- 上线版需要把 `asr.final` 进一步接入 LLM 动态追问、Streaming TTS、memory extraction 和 transcript version 管理。
- 第一版只保证实时转写主链路和数据骨架正确，后续能力都在这个骨架上迭代。

### PR 拆分建议

第一版建议拆成 5 个 PR，避免一个 PR 同时改前端、长连接、provider 和数据迁移：

1. PR 1: 删除 legacy 非实时 ASR 代码
   - 删除 StepFun 整段转写 service、ASR job 路由、`audio-files/{id}/transcribe`。
   - 小程序 legacy 页面停止调用离线转写，只保留录音保存和手工补文字。
   - 更新文档和测试，确认没有 provider key 暴露。

2. PR 2: 后端 voice session 数据骨架
   - 新增 voice session、chat message、utterance schema/model/migration。
   - 新增创建/停止 voice session 的 HTTP API。
   - 暴露最小消息和 utterance 写入 API，供后续 gateway 复用。
   - 不接 provider，只做持久化和权限边界测试。

3. PR 3: Voice gateway + 豆包 Streaming ASR adapter
   - 新增 WebSocket gateway 和 provider adapter。
   - 实现 `session.start`、`audio.frame`、`audio.end`、`asr.partial`、`asr.final`、`session.error`。
   - 用本地音频样例跑集成验证。

4. PR 4: 小程序 `voice-start` / `voice-chat` MVP
   - 新增模式选择、录音帧发送、大字转写和错误状态。
   - 真机验证 partial 延迟和 final 固化。

5. PR 5: 持久化闭环和第一版验收
   - 将 `asr.final` 落 chat message / utterance。
   - 保存原始音频或音频片段引用。
   - 补齐 API/小程序测试和 README 操作说明。

### Phase 1: Streaming ASR MVP

目标：先完成“边说边出大字”，同时把 voice session 和原始语音资产模型打牢。

- 新增 voice session API。
- 新增 voice gateway WebSocket。
- 小程序新增开始页和 `voice-chat` 页面，支持选择 `free_narration` / `guided_interview`。
- `RecorderManager.onFrameRecorded` 发送音频帧。
- 豆包 ASR 返回 partial/final。
- final transcript 落 `memoir_chat_messages` 或临时兼容表。
- 停止后保存完整音频并关联 transcript。
- legacy 非实时 ASR endpoint 和 ASR job 不再保留。

不做：

- AI 自动追问。
- 流式 TTS。
- 复杂 VAD。
- 删除全部旧页面。

### Phase 2: Guidance Modes and LLM Dynamic Follow-up

目标：final transcript 后按强/弱引导策略生成下一句 AI 追问。

- 新增 `chat_orchestrator`。
- 增加两套 system prompt policy：自由叙述陪伴、模块化强引导采访。
- 引入 recent messages 和 memory context。
- 强引导先支持人、事、地点、时间段、主题五类模块。
- assistant message 落库。
- 前端显示 AI 气泡。

### Phase 3: Streaming TTS

目标：AI 回复可边生成边播放。

- assistant message 驱动 TTS。
- 小程序支持播放队列和打断。
- 用户插话时暂停或取消 TTS。

### Phase 4: Editing, Progress and Memory-first Integration

目标：语音聊天结果沉淀为回忆录资产，并提供可校正、可查看进度的页面。

- 从 chat messages 提取 person/event/place/theme。
- evidence 指向 transcript version 和 audio span。
- 生成 session summary、open questions、story fragments。
- 新增转写校正页，支持回听原始语音并保存校正文。
- 新增进度页，展示人物、事件、地点、时间段、主题覆盖度。

### Phase 5: Legacy Cleanup

目标：新版稳定后清理旧链路。

- 移除固定问题主入口。
- 旧 `question/answer/draft` 页面下线或转为只读迁移入口。
- 删除不再使用的 TTS source type。
- 更新旧文档或归档。

## 验收标准

Streaming ASR MVP 必须满足：

- 真机上麦克风开始后 1 秒内出现 partial transcript。
- final transcript 能稳定落库。
- 断网或 provider 错误能给出明确 UI 状态。
- 小程序端不包含 provider key。
- 旧链路在迁移完成前不被破坏。

完整语音聊天版本必须满足：

- 用户可以连续讲述，不需要理解固定题目。
- 用户可以在开始访谈时选择弱引导或强引导。
- AI 追问来自上下文，不依赖模板题库。
- 强引导能按人、事、地点、时间段、主题推进；弱引导不会频繁打断自主叙述。
- 每个 AI 输出都能追踪到输入消息和 prompt context。
- 原始音频、ASR 原文、修正文、用户确认文本分层保存。
- 家人/编辑端能追溯事实来源。

## 旧文档归档说明

以下文档描述的是上一版或中间态，不再作为新版实现目标：

- `docs/MEMOIR_MINIPROGRAM_PRODUCT.md`
- `docs/MEMOIR_MEMORY_FIRST_V1_PLAN.md`
- `docs/MEMOIR_MEMORY_ARCHITECTURE.md`

后续实现以本文档为主。旧文档中仍有价值的部分包括：证据链、结构化记忆、覆盖度地图、写作产物分层。这些能力会迁移到新版 chat message 和 transcript evidence 模型上。
