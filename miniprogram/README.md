# Pawtale 回忆录小程序

这是 Pawtale 回忆录微信小程序。当前代码仍保留 legacy 固定问答流程；下一阶段会整体改版为实时语音聊天形态，详见 [../docs/MEMOIR_VOICE_CHAT_REDESIGN.md](../docs/MEMOIR_VOICE_CHAT_REDESIGN.md)。

新版目标流程：

```text
进入小程序
 -> 选择弱引导自由叙述，或强引导 AI 采访
 -> 创建 voice session
 -> 用户直接说话
 -> Streaming ASR 实时显示大字转写
 -> LLM 根据上下文动态追问
 -> Streaming TTS 播放 AI 回复
 -> 对话消息、原始音频和转写证据落库
```

当前 legacy 流程：

```text
进入小程序
 -> 云函数 openid
 -> 获取默认回忆录项目
 -> 获取当前问题
 -> 录音上传
 -> 语音转写并编辑回答
 -> AI 校正口述稿
 -> 查看问答记录
 -> 编辑
 -> 确认并继续下一问
```

## 本地运行

用微信开发者工具打开仓库根目录 `Pawtale/`。根目录的 `project.config.json` 会把 `miniprogram/` 识别为小程序根目录，并把 `cloud-functions/` 识别为云函数根目录。

当前 legacy 页面只走微信云开发路径：JSON API 通过 `wx.cloud.callFunction` 请求 `memoir-api`，录音先上传到云存储，再由云函数登记音频元数据。

新版实时语音聊天会新增 voice gateway。小程序不能直接连接 ASR/TTS provider，也不能携带 provider API key。用户原始语音是基础数据资产，后续转写校正、进度查看和成稿编辑都应回溯到原始音频和 transcript version。

## 新版语音聊天开发配置

PR4 开始新增 `voice-start` / `voice-chat` 页面。当前实时语音链路直连仓库里的 `backend` 服务，不经过 legacy 云函数：

```ts
export const VOICE_BACKEND_HTTP_BASE_URL = "http://127.0.0.1:8000";
export const VOICE_BACKEND_WS_BASE_URL = "ws://127.0.0.1:8000";
```

微信开发者工具本机调试可以使用默认值；真机预览需要改成手机可访问的局域网地址，正式环境需要配置为 HTTPS/WSS 域名。开发态会使用后端本地默认用户登录以取得 `pawtale_session` cookie，provider key 仍只放在 `backend/.env`。

## 检查

```bash
cd miniprogram
npm install
npm run typecheck
npm test
```

## 微信云开发配置

小程序 AppID 已配置为 `wx71acdf73e2ba7067`。

`config.ts` 集中管理云开发目标：

```ts
export const CLOUD_ENV_ID = "cloud1-d6gmxffp0ea48aa1a";
export const CLOUD_FUNCTION_NAME = "memoir-api";
```

## 当前 Legacy API

当前 legacy 小程序页面调用这些后端 API。新版 voice-chat 页面会使用新的 voice session 和 realtime event contract，不继续扩展固定问答 API：

```text
GET  /api/mobile/memoir/me
PATCH /api/mobile/memoir/me
GET  /api/mobile/memoir/dashboard
GET  /api/mobile/memoir/project
GET  /api/mobile/memoir/projects
POST /api/mobile/memoir/projects
PATCH /api/mobile/memoir/projects/{project_id}
POST /api/mobile/memoir/projects/{project_id}/activate
GET  /api/mobile/memoir/sessions/current
GET  /api/mobile/memoir/questions/next
POST /api/mobile/memoir/questions/{question_id}/skip
GET  /api/mobile/memoir/stories
GET  /api/mobile/memoir/stories/{answer_id}
POST /api/mobile/memoir/answers
PATCH /api/mobile/memoir/answers/{answer_id}
DELETE /api/mobile/memoir/answers/{answer_id}
POST /api/mobile/memoir/answers/{answer_id}/draft
PATCH /api/mobile/memoir/drafts/{draft_id}
POST /api/mobile/memoir/drafts/{draft_id}/accept
POST /api/mobile/memoir/audio-files
GET  /api/mobile/memoir/memories/{people|events|places|themes}
PATCH /api/mobile/memoir/memories/{memory_type}/{memory_id}
GET  /api/mobile/memoir/open-questions
PATCH /api/mobile/memoir/open-questions/{question_id}
GET  /api/mobile/memoir/coverage
POST /api/mobile/memoir/coverage/recalculate
GET  /api/mobile/memoir/question-candidates
POST /api/mobile/memoir/question-candidates/generate
POST /api/mobile/memoir/question-candidates/{candidate_id}/select
GET  /api/mobile/memoir/outputs
```

`POST /api/mobile/memoir/answers` 支持纯文本回答，也支持带上刚上传录音的
`audio_file_id` 和 `duration_seconds`，用于后续播放原话或重录。

## 当前不包含

- voice gateway
- Streaming ASR 实时字幕
- LLM 动态追问
- Streaming TTS
- accepted draft 同步到 Web 正文

这些能力会在后续 PR 分别接入。
