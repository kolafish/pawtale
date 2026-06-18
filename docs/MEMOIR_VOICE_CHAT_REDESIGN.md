# 回忆录语音聊天重构方案

## 状态

这是回忆录小程序的新主线文档。此前的固定问题、整段录音上传、离线转写、整理稿确认路线只作为 legacy 参考，不再作为下一阶段产品目标。

当前实现已经从 Streaming ASR MVP 进入语音聊天第一版闭环：

- 小程序已通过 `home` / `voice-chat` 进入 voice session，实时发送 PCM 帧并显示 ASR partial/final。
- `asr.final` 已落 `memoir_chat_messages`、`memoir_voice_utterances`、`memoir_transcript_versions` 和原始音频 `audio_files`。
- 强引导模式已接入 LLM 动态追问，assistant message 会落库并显示在语音聊天页。
- PR2 交互细节已补齐：支持开场追问、手动生成追问、重试/换个追问、跳过当前追问、用户开始新回答时清掉旧追问活跃态、结束后跳转转写记录。
- AI 追问已接入 assistant-message 驱动的 TTS 音频生成和播放；第一版是短句音频快速生成后播放，接口按后续 streaming TTS 形态保留 provider 抽象。
- 小程序已接入客户端轻量 VAD：基于 PCM 能量显示音量条、计算连续静音，并可自动收尾当前句。
- 已有转写校正页和访谈进度页，可查看会话、模块覆盖、语音段、AI 追问数，并从记录继续访谈。
- 小程序已拆为首页、转写、成书、我的 tab；我的页可看当前项目统计、管理项目和设置 AI TTS 音色，项目页支持创建、切换、重命名和删除回忆录。
- 后端已新增成书章节规划、逐章成稿和全书合成 API，可基于当前转写生成章节建议、保存用户确认后的章节结构，按章节 source refs 生成/编辑/确认正文版本，并把已确认章节合成为全书稿。
- 小程序成书页已接入章节规划确认、逐章成稿、全书生成和朗读流程。用户可先确认目录，再逐章生成、编辑和确认正文；章节正文和全书稿都可生成音频朗读。
- 成书页已能检测继续录音后产生的未收录转写，并提示用户查看。新录音不会自动改动已确认章节和全书，后续再接入“归入章节”和“补充成稿”流程。

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
miniprogram/pages/login/                      # 微信登录、Pawtale 账号登录、首次设 Web 登录凭据
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
miniprogram/services/tts-settings.ts          # 小程序本地 TTS 音色偏好
miniprogram/domain/voice-chat.ts              # 新版聊天消息和实时事件类型
backend/app/routers/memoir/book.py            # 成书章节规划、章节稿、全书稿和朗读 mobile API
backend/app/services/memoir/book_plans.py     # 章节规划生成、确认和 source ref 校验
backend/app/services/memoir/book_drafts.py    # 逐章成稿、版本追加和正文确认
backend/app/services/memoir/book_outputs.py   # 全书稿合成、版本追加和全书朗读
backend/app/services/memoir/book_audio.py     # 章节/全书 TTS 文本分片、缓存和音频落库

backend/app/routers/memoir/voice.py           # 本地/服务端 voice session API
backend/app/services/memoir/voice_gateway.py  # WebSocket voice gateway + Streaming ASR
backend/app/services/memoir/voice_sessions.py # voice session detail/list/progress
backend/app/services/memoir/voice_followups.py # LLM dynamic follow-up
backend/app/services/memoir/voice_tts.py      # assistant message TTS
backend/app/services/memoir/voice_transcripts.py # 转写校正和 AI 忠实清理
```

Git 上建议用以下边界区分：

- `bd3599ad3dddf382b242c47071c856d382543cac`：legacy 固定问题链路的可追溯基线。
- `codex/voice-chat-redesign`：新版语音聊天主线分支。
- 第一批 PR 已完成 Streaming ASR、voice session 数据和小程序新入口。
- 第二批 PR 已完成 LLM 动态追问、小程序追问交互、assistant TTS 播放、客户端自动收尾和进度页。
- 第三批 PR 已完成自动续听、TTS 文本播放进度、转写自动校正、AI 问题与回答连播、主 tab 拆分和我的页项目/TTS 设置。
- 小程序 legacy 固定问答页面、旧非实时录音上传代码和微信云函数链路已清理。
- 登录链路已迁移为统一 Pawtale 账号：已有 Web 用户用邮箱密码登录小程序，新用户用微信登录后设置 Web 端邮箱密码。

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

进入语音聊天页后默认由 AI 先说第一句。弱引导的开场只做开放邀请，让叙述者按自己的顺序开始；强引导的开场围绕用户选定模块给一个具体、容易回答的问题。

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
- 我的页：查看账号、当前项目统计、管理回忆录项目，并选择 AI TTS 音色。
- 成书页：把确认后的素材组织为章节，逐章生成以用户叙述为主的正文，确认后合成为全书稿；章节和全书都支持语音朗读。

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

当前第一版采用客户端轻量 VAD，不引入服务端断句模型：

- 小程序在 `RecorderManager.onFrameRecorded` 中按 16-bit PCM 计算 RMS 能量。
- 页面显示音量条和连续静音时长。
- 默认开启自动收尾：录音超过最小时长后，连续静音超过阈值会自动停止当前句并发送 `audio.end`。
- 用户可在页面关闭自动收尾，继续使用手动开始/结束。

这不是上线态完整 VAD。后续仍需要评估：

- 服务端 VAD：用于更稳定的 utterance 边界、插话打断和异常恢复。
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
guidance_intent       # opening / follow_up / encourage_continue / clarify / switch_module / ask_detail / summarize
next_module
memory_hints
open_question_hints
safety_flags
```

### Streaming TTS

当前已实现 assistant message 驱动的第一版 TTS：

```text
assistant message
 -> backend TTS provider
 -> audio_files
 -> 小程序下载并播放
```

后端新增 TTS provider 抽象，环境变量为 `TTS_PROVIDER`、`TTS_API_KEY`、`TTS_BASE_URL`、`TTS_MODEL`、`TTS_VOICE`、`TTS_RESPONSE_FORMAT`。默认 `TTS_PROVIDER=disabled`，没有配置时小程序仍显示 AI 文字追问，不阻塞 ASR 和 LLM。当前支持 `openai_compatible` 和 `stepfun`；`TTS_PROVIDER=stepfun` 时优先读取 `STEPFUN_API_KEY`、`STEPFUN_BASE_URL`、`STEPFUN_TTS_MODEL`、`STEPFUN_TTS_VOICE`，默认走阶跃 `/v1/audio/speech` 非流式短句合成。

小程序语音聊天页在 AI 追问生成后会请求 TTS，自动播放；播放时优先按后端返回的逐字时间轴高亮已讲到的文字，播放完成且 voice gateway 可录音时自动进入下一轮收听。用户开始说话、跳过追问、结束页面时会取消未完成合成或停止播放，避免旧追问音频和新回答冲突。

为了让高亮块跟随真实语音位置移动，后端支持 TTS 后置强制对齐。默认 `TTS_ALIGNMENT_PROVIDER=disabled` 时不做文字高亮对齐；配置 `TTS_ALIGNMENT_PROVIDER=elevenlabs` 并提供 `ELEVENLABS_API_KEY` 或 `TTS_ALIGNMENT_API_KEY` 后，后端会把已合成的追问音频和原始追问文本送到 forced alignment，返回逐字 `start_ms` / `end_ms` 时间轴，保存到 `memoir_chat_messages.metadata_json.tts_alignment`，并随 TTS response 返回给小程序。小程序播放时只按该时间轴定位当前字，不再按播放百分比估算；开启 alignment provider 且 key 可用后，已有但没有逐字时间轴的 TTS 音频会在下次请求时重新生成并替换旧音频。

后续真正的 Streaming TTS 仍要把 provider WebSocket/SSE 音频流直接接到播放队列，并与 LLM streaming 输出联动。当前第一版刻意保持为“短句生成后播放”：AI 追问文本较短，非流式音质和稳定性更可控，先保证 ASR、持久化、追问和播放闭环。

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
- `audio_files`：可复用，仍保存原始音频资产。
- `memoir_transcript_versions`：继续复用，用于 ASR 原文、方言修正、用户编辑和最终确认。
- memory/output 相关表继续保留，消费新版 chat message 和 transcript evidence。

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

LLM 和 TTS 当前不走 WebSocket 事件：

- LLM 追问通过 `POST /api/mobile/memoir/voice-sessions/{voice_session_id}/follow-up` 生成 assistant message。
- TTS 通过 `POST /api/mobile/memoir/voice-sessions/{voice_session_id}/messages/{message_id}/tts` 生成并关联 `audio_file_id`。
- 音频播放通过 `GET /api/mobile/memoir/audio-files/{audio_file_id}/playback` 下载。

后续如果要真正做低延迟 Streaming TTS，可以在现有 message/audio 数据模型不变的前提下扩展 `assistant.*` 和 `tts.*` WebSocket 事件。

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
- 小程序新增 `home` 和 `voice-chat` 第一版入口。
- 小程序发送 16k mono PCM 音频帧，服务端转发给豆包 Streaming ASR。
- 页面展示 `asr.partial` 大字实时转写，`asr.final` 固化成用户消息。
- 小程序保存 voice session 本地快照，并能从后端详情回放已固化 transcript。
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
   - 小程序不再保留旧固定问答录音上传入口。
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

4. PR 4: 小程序 `home` / `voice-chat` MVP
   - 新增模式选择、录音帧发送、大字转写和错误状态。
   - 真机验证 partial 延迟和 final 固化。

5. PR 5: 持久化闭环和第一版验收
   - 将 `asr.final` 落 chat message / utterance。
   - 保存原始音频或音频片段引用。
   - 补齐 API/小程序测试和 README 操作说明。

PR5 后第一版已经具备的闭环：

- 小程序从 `home` 创建 voice session，并进入 `voice-chat`。
- 小程序录音为 16k mono PCM frame，通过 Pawtale voice gateway 发送。
- 后端 provider adapter 返回 `asr.partial` / `asr.final`。
- `asr.partial` 只用于页面大字实时显示。
- `asr.final` 会创建 `memoir_chat_messages` 用户消息、`memoir_voice_utterances` 和 voice-session 归属的 `memoir_transcript_versions`。
- 当前 utterance 的 PCM 音频帧会保存为 `AudioFile`，并通过 `audio_file_id` 关联到 message / utterance。
- `voice-chat` 会优先恢复本地 voice session 快照，再异步读取后端详情，避免返回页面后丢失已固化文字。

Streaming ASR PR5 后当时仍未完成、不能按上线态理解的缺口：

- 小程序已经统一为 backend cookie 身份；仍需要在微信公众平台配置正式 request/socket 合法域名。
- 微信登录后端 API 已完成；ECS `/opt/pawtale/backend/.env` 仍需要配置真实 `WECHAT_MINIPROGRAM_SECRET` 后，真机微信登录才能通过 code2session。
- 原始音频按 final utterance 粗粒度保存，尚未做更精确的 VAD 时间切片和断线续传。
- voice session 快照只保证已固化 transcript 的页面回放，不等同于断线续传；录音帧上传中的状态仍不可恢复。
- 低延迟 Streaming TTS、用户插话打断和断线续传尚未接入；LLM follow-up、AI 回复落库和短句 TTS 播放已在第二批完成第一版。
- 转写校正页和进度页已有第一版；成稿编辑页、结构化记忆页和证据链浏览仍未迁移到新版 voice session 数据。
- 还没有方言/噪声评测集、provider fallback、限流和成本监控。

### 当前第二批 PR 状态

在 Streaming ASR 第一版闭环之后，第二批按长期迭代方向继续补齐语音聊天能力：

1. AI 追问后端和小程序基础接入：已完成。`asr.final` 后可调用 LLM 生成 assistant message，并在语音聊天页展示 AI 追问。
2. PR2 交互细节：已完成。页面支持开场追问、手动生成追问、重试追问、换个追问、跳过追问；用户开始新回答时旧追问不再保持活跃；结束访谈后可直接进入转写记录页。
3. Assistant TTS：已完成第一版。assistant message 可生成音频并关联 `audio_file_id`，小程序自动播放并支持重播；没有配置 TTS provider 时降级为文字追问。
4. 客户端轻量 VAD / 自动收尾：已完成第一版。小程序显示音量条、连续静音状态，并可自动结束当前句；用户可以关闭自动收尾。
5. 访谈记录与进度页：已完成第一版。后端提供 voice session list/progress API，小程序可查看会话、模块覆盖、语音段、AI 追问和字数，并能从记录继续访谈。

当前语音聊天第一版仍不是最终上线态。后续重点是 memory extraction、结构化证据链、章节/片段产出编辑、断线恢复、provider fallback、质量评测和成本监控。

### 当前第三批 PR 状态

这批主要补齐语音聊天的连续体验和小程序页面结构：

1. 自动续听：已完成。AI TTS 自动播放完后，如果 WebSocket 和录音状态允许，会自动开始下一轮收听。
2. TTS 文本进度：已完成。播放 AI 问题时，页面会按 forced alignment 时间轴显示已读和当前文字；没有时间轴时不做文字高亮。
3. 转写自动校正：已完成。`asr.final` 保存时会同步生成忠实去口语化校正文；转写记录页默认展示校正文，仍保留 ASR 原文。
4. AI 问题与回答连播：已完成。转写记录项显示对应 AI 引导问题；播放时优先播放问题音频，再播放用户回答音频。
5. 主 tab 拆分：已完成。小程序拆为首页、语音、转写、我的四个主入口。
6. 我的页项目和 TTS 设置：已完成。我的页展示当前项目统计、TTS 音色设置；项目页支持删除回忆录，并保留至少一本项目。

### Phase 1: Streaming ASR MVP

目标：先完成“边说边出大字”，同时把 voice session 和原始语音资产模型打牢。

- 新增 voice session API。
- 新增 voice gateway WebSocket。
- 小程序新增开始页和 `voice-chat` 页面，支持选择 `free_narration` / `guided_interview`。
- `RecorderManager.onFrameRecorded` 发送音频帧。
- 豆包 ASR 返回 partial/final。
- final transcript 落 `memoir_chat_messages` 或临时兼容表。
- 停止后保存完整音频并关联 transcript。
- 页面离开再进入同一个 voice session 时，能通过本地快照和后端详情恢复已固化文字。
- legacy 非实时 ASR endpoint 和 ASR job 不再保留。

不做：

- AI 自动追问。
- 流式 TTS。
- 复杂 VAD。
- 转写校正、进度和成稿编辑页面。

### Phase 2: Guidance Modes and LLM Dynamic Follow-up

目标：final transcript 后按强/弱引导策略生成下一句 AI 追问。第一版已完成。

- 已新增 `voice_followups.py`，按 `free_narration` / `guided_interview` 生成追问。
- 已支持 recent messages，上下文来自当前 voice session 的 chat messages。
- 强引导先支持人、事、地点、时间段、主题五类模块。
- assistant message 已落库，并在前端显示 AI 追问。
- PR2 交互细节已补齐：开场追问、生成/重试/换一个、跳过、结束后查看转写记录。

下一步需要把 confirmed memories、open questions、coverage state 纳入 prompt context。

### Phase 3: Streaming TTS

目标：AI 回复可边生成边播放。第一版已完成“短句生成后播放”的生产形状，尚未做到真正流式音频播放。

- assistant message 驱动 TTS 已完成。
- 小程序支持自动播放、重播、生成失败降级文字。
- 小程序支持基于 forced alignment 的播放文字进度和 TTS 播放后自动续听。
- 用户开始说话、跳过追问、退出页面时会取消未完成合成或停止播放。

下一步是接入真实 streaming TTS provider，把 provider 音频流接到播放队列，并补齐插话打断。

### Phase 4: Editing, Progress and Memory-first Integration

目标：语音聊天结果沉淀为回忆录资产，并提供可校正、可查看进度的页面。

- 转写校正页已完成第一版，支持回听原始语音、编辑 ASR 转写、AI 忠实清理口语稿。
- final transcript 保存时已自动生成忠实校正文，减少转写记录页的手动清理成本。
- 转写记录项已显示 AI 引导问题，并支持问题音频与回答音频顺序播放。
- 进度页已完成第一版，展示会话、模块覆盖、语音段、AI 追问、字数，并支持继续访谈。
- 从 chat messages 提取 person/event/place/theme 仍未完成。
- evidence 指向 transcript version 和 audio span 的结构化浏览仍未完成。
- session summary、open questions、story fragments 和成稿编辑仍未完成。

### Phase 5: Legacy Cleanup

目标：新版稳定后清理旧链路。

- 固定问题主入口已移除。
- 小程序旧 `workbench`、`stories`、`profile`、固定问答、旧 review/editor 页面已下线。
- 旧 answer-session 草稿 store、旧非实时录音上传 service 已删除。
- 小程序端旧 dashboard/stories/review 缓存、类型和 API wrapper 已删除。
- Backend mobile API 仅保留身份、项目管理和 voice session 链路；旧固定访谈、离线整理、stories、coverage、outputs mobile endpoints 已下线。
- 微信云函数链路已删除；旧版只能通过 baseline commit 追溯。
- 后续删除不再使用的 TTS source type，并把进度、校正、成稿编辑迁移到 voice session 数据。

## 验收标准

Streaming ASR MVP 必须满足：

- 真机上麦克风开始后 1 秒内出现 partial transcript。
- final transcript 能稳定落库。
- 离开并重新进入同一个 voice session 后，已 final 的 transcript 不丢失。
- 断网或 provider 错误能给出明确 UI 状态。
- 小程序端不包含 provider key 或微信 AppSecret。
- 旧链路不再作为产品代码路径保留。

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
