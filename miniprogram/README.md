# Pawtale 回忆录小程序

当前主线是实时语音聊天形态，详见 [../docs/MEMOIR_VOICE_CHAT_REDESIGN.md](../docs/MEMOIR_VOICE_CHAT_REDESIGN.md)。

```text
登录
 -> 选择或创建回忆录
 -> 选择自由讲述或 AI 引导
 -> 创建 voice session
 -> 通过 WSS 发送录音帧
 -> Streaming ASR 实时显示大字转写
 -> 保存 voice session、chat message、utterance、音频证据
```

## 本地运行

用微信开发者工具打开仓库根目录 `Pawtale/`。根目录的 `project.config.json` 会把 `miniprogram/` 识别为小程序根目录。

小程序直连 Pawtale backend 的 HTTP/WSS 服务，不连接 ASR/TTS provider，也不携带 provider API key。开发验证阶段 provider key 只放在 `backend/.env`。

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

## 当前页面

- `pages/login`：ECS 邮箱登录和注册。
- `pages/home`：新版语音入口和当前回忆录入口。
- `pages/projects`：创建、切换、重命名回忆录。
- `pages/voice-start`：选择弱引导自由讲述或强引导 AI 采访。
- `pages/voice-chat`：实时录音、WebSocket、partial/final transcript 和本地快照恢复。

旧 workbench、stories、profile、固定问答和整段录音上传页面已移除。

## 检查

```bash
cd miniprogram
npm install
npm run typecheck
npm test
```

## 未完成

- LLM 动态追问。
- Streaming TTS。
- 转写校正页、进度页、成稿编辑页。
- 方言/噪声评测集、provider fallback、限流和成本监控。
