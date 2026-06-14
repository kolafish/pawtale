# Pawtale 回忆录小程序

这是 Pawtale 回忆录微信小程序。当前主线已切到实时语音聊天形态，详见 [../docs/MEMOIR_VOICE_CHAT_REDESIGN.md](../docs/MEMOIR_VOICE_CHAT_REDESIGN.md)。

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

## 本地运行

用微信开发者工具打开仓库根目录 `Pawtale/`。根目录的 `project.config.json` 会把 `miniprogram/` 识别为小程序根目录，并把 `cloud-functions/` 识别为云函数根目录。

实时语音聊天页面直连 `backend` 的 voice gateway。小程序不能直接连接 ASR/TTS provider，也不能携带 provider API key。用户原始语音是基础数据资产，后续转写校正、进度查看和成稿编辑都应回溯到原始音频和 transcript version。

## 新版语音聊天开发配置

`voice-start` / `voice-chat` 页面使用仓库里的 `backend` 服务，不经过 legacy 固定问答云函数：

```ts
wx.setStorageSync("pawtale_voice_backend_config", {
  httpBaseUrl: "http://192.168.1.10:8000",
  wsBaseUrl: "ws://192.168.1.10:8000",
  devEmail: "local@pawtale.local",
  devPassword: "pawtale-local",
  devDisplayName: "本地作者",
});
```

微信开发者工具本机调试可以填 `http://127.0.0.1:8000`；真机预览需要改成手机可访问的局域网地址，正式环境需要配置为 HTTPS/WSS 域名。开发态会使用这里配置的后端用户登录以取得 `pawtale_session` cookie，provider key 仍只放在 `backend/.env`。

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

## 当前小程序 API 边界

当前语音聊天入口使用：

```text
POST /api/mobile/memoir/voice-sessions
GET  /api/mobile/memoir/voice-sessions/{voice_session_id}
POST /api/mobile/memoir/voice-sessions/{voice_session_id}/stop
GET  /api/mobile/memoir/voice-sessions/{voice_session_id}/gateway
```

`home`、`workbench`、`stories`、`profile` 仍会读取部分云函数 dashboard/project/memory/output 数据，用于开发期查看进度和历史素材。固定问题、整段录音上传、问答记录、旧 review/editor 页面已经从小程序入口下线。

## 新版仍未包含

- LLM 动态追问
- Streaming TTS
- 语音聊天结果的校正页和进度页
- 云函数身份与 backend cookie 身份统一
- accepted draft 同步到 Web 正文

这些能力会在后续 PR 分别接入。
