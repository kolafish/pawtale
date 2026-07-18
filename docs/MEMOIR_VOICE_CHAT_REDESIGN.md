# 回忆录实时语音聊天架构

## 文档状态

本文是回忆录小程序实时语音链路的当前基线，负责：

- VAD 与录音控制；
- Streaming ASR；
- LLM 动态回应与追问；
- Streaming TTS；
- voice session、消息和转写的持久化；
- 自由讲述、主题讲述和成书补讲共用的语音状态机。

素材、Section、章节正文和成书补全边界见
`MEMOIR_NARRATIVE_ARCHITECTURE.md`；主题访谈和成书补全协作见
`MEMOIR_STRUCTURED_INTERVIEW_AND_BOOK_COMPLETION_PLAN.md`。

旧固定问答链只通过 commit
`bd3599ad3dddf382b242c47071c856d382543cac` 追溯。当前代码不保留旧页面、
旧表、兼容接口或双写。

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

同一套语音技术链支持三类目标：

- `open_narration`：自由讲述，AI 以倾听和轻提示为主；
- `guided_interview`：主题讲述，AI 根据话题地图选择候选方向；
- `book_completion`：成书补讲，只围绕一个当前 CompletionIssue。

`guidance_mode` 表达引导强度，`session_goal` 表达业务目标。两者不能混成一套
隐式状态。

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

持久状态以后端为准。页面恢复、前后台切换或断线重连时，客户端重新读取
VoiceSession 和消息，不根据旧 UI 状态猜测服务端结果。

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

用户点击麦克风时可以打断正在生成或播放的 AI：

1. 客户端立即停止本地播放；
2. 发送取消原因；
3. 后端停止 LLM/TTS；
4. 中断的半句话不进入后续对话上下文；
5. 录音器开始接收用户讲话。

TTS 播放失败不能删除已经完成的文字回复，但必须显式展示播放失败，并允许用户重听。

## 七、自由讲述与主题讲述

自由讲述以用户当前内容为主。AI 不主动按固定人生目录切换主题，只在停顿、指代不清、
用户请求帮助或自然转折时轻量回应。

主题讲述使用话题地图作为候选方向：

- 某方面是否出现过 Material；
- 相关 Material / Section；
- 用户是否明确不愿讨论。

话题地图不维护 `not_started → sufficient` 的强覆盖状态，也不要求用户逐项填满。LLM
根据当前对话状态决定继续当前内容还是提供新方向。

## 八、成书补讲

成书补讲复用同一 voice-chat 页面和 assistant turn，只额外绑定：

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

语音层只产出可追溯 Material。后续链路是：

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

小程序记录页不提供旧式“手工归章”入口。结构调整统一在书稿的章节/Section 工作区完成，
避免多个页面同时修改素材归属。

## 十、主要代码边界

小程序：

```text
pages/voice-chat/                         # 实时访谈页面
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
