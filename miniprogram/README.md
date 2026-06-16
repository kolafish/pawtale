# 余主任留声机小程序

当前主线是实时语音聊天形态，详见 [../docs/MEMOIR_VOICE_CHAT_REDESIGN.md](../docs/MEMOIR_VOICE_CHAT_REDESIGN.md)。

```text
登录
 -> 微信登录或 Pawtale 账号登录
 -> 首次微信登录设置 Web 端邮箱密码
 -> 选择或创建回忆录
 -> 选择自由讲述或 AI 引导
 -> 创建 voice session
 -> 通过 WSS 发送录音帧
 -> Streaming ASR 实时显示大字转写
 -> 保存 voice session、chat message、utterance、音频证据
```

## 本地运行

用微信开发者工具打开仓库根目录 `Pawtale/`。根目录的 `project.config.json` 会把 `miniprogram/` 识别为小程序根目录。

小程序直连 Pawtale backend 的 HTTP/WSS 服务，不连接 ASR/TTS provider，也不携带 provider API key。开发验证阶段 provider key 和微信小程序 AppSecret 只放在 `backend/.env`。

## 后端配置

默认地址在 `miniprogram/config.ts`：

```ts
export const BACKEND_HTTP_BASE_URL = "https://pawtale-api.spaceflows.cn";
export const BACKEND_WS_BASE_URL = "wss://pawtale-api.spaceflows.cn";
```

开发者工具可临时覆盖运行时配置：

```ts
wx.setStorageSync("pawtale_backend_config", {
  httpBaseUrl: "http://127.0.0.1:8000",
  wsBaseUrl: "ws://127.0.0.1:8000",
});
```

真机预览必须使用手机可访问的 HTTPS/WSS 域名，并在微信公众平台配置 request/socket 合法域名。

## 登录链路

- 新用户：点击微信登录，后端用 `wx.login()` 返回的 code 换取 openid，创建 Pawtale 账号，再要求设置 Web 端可用的邮箱和密码。
- 已有 Pawtale Web 用户：展开“使用 Pawtale 账号登录”，用 Web 端邮箱和密码进入小程序。
- 小程序端不保存微信 AppSecret，也不保存 ASR/TTS/LLM provider key。
- ECS 后端必须在 `/opt/pawtale/backend/.env` 配置：

```bash
WECHAT_MINIPROGRAM_APPID=wx71acdf73e2ba7067
WECHAT_MINIPROGRAM_SECRET=从微信公众平台获取
WECHAT_JSCODE2SESSION_URL=https://api.weixin.qq.com/sns/jscode2session
```

## 当前页面

- `pages/login`：微信登录、已有 Pawtale 账号登录、首次微信登录后设置 Web 登录凭据。
- `pages/home`：新版语音入口和当前回忆录入口。
- `pages/projects`：创建、切换、重命名、删除回忆录。
- `pages/voice-start`：选择弱引导自由讲述或强引导 AI 采访。
- `pages/voice-chat`：实时录音、WebSocket、partial/final transcript、AI 追问 TTS 和本地快照恢复。
- `pages/transcripts`：查看原始语音、ASR 原文、校正文、AI 引导问题，并支持问题和回答连播。
- `pages/voice-progress`：查看会话、模块覆盖、语音段、AI 追问和累计文字。
- `pages/me`：账号信息、当前项目统计、项目入口和 TTS 音色设置。

旧 workbench、stories、profile、固定问答和整段录音上传页面已移除。

## 检查

```bash
cd miniprogram
npm install
npm run typecheck
npm test
```

## 未完成

- 真正低延迟 Streaming TTS、插话打断和断线续传。
- 结构化记忆提取、证据链浏览和成稿编辑页。
- 方言/噪声评测集、provider fallback、限流和成本监控。
