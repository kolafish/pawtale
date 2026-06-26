# 余主任留声机小程序

当前主线是实时语音聊天形态，详见 [../docs/MEMOIR_VOICE_CHAT_REDESIGN.md](../docs/MEMOIR_VOICE_CHAT_REDESIGN.md)。

```text
登录
 -> 微信登录或 Pawtale 账号登录
 -> 首次微信登录直接进入小程序，可在“我的”里补 Web 端邮箱密码
 -> 选择或创建回忆录
 -> 选择自由讲述或 AI 引导
 -> 创建 voice session
 -> 通过 WSS 发送录音帧
 -> Streaming ASR 实时识别并显示大字文字
 -> AI 基于当前项目上下文追问、鼓励或引导
 -> 保存 voice session、chat message、utterance、音频证据
 -> 后端持续沉淀进度快照、自动整理事件和成书素材
```

下一阶段小程序侧重点不是继续单独追“流式 TTS”，而是配合后端完成：

- AI 说话时支持用户点击麦克风抢占，立即停止 AI 播放并开始录音。
- 语音聊天展示来自后端真实项目进度的自然鼓励。
- 成书和进度页面消费后端自动归章、自动补写、章节审计和待确认事件。
- 语音编辑只负责录音、展示 AI 解释和操作预览；复杂语义判断放到后端 planner。

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

- 新用户：点击微信登录，后端用 `wx.login()` 返回的 code 换取 openid，创建 Pawtale 账号，并优先保存微信昵称和头像；Web 端邮箱和密码可稍后在“我的”里设置。
- 已有 Pawtale Web 用户：展开“使用 Pawtale 账号登录”，用 Web 端邮箱和密码进入小程序。
- 邮箱密码登录和补 Web 登录凭据时，小程序端会先校验邮箱格式，后端仍会再次校验。
- 小程序端不保存微信 AppSecret，也不保存 ASR/TTS/LLM provider key。
- ECS 后端必须在 `/opt/pawtale/backend/.env` 配置：

```bash
WECHAT_MINIPROGRAM_APPID=wx71acdf73e2ba7067
WECHAT_MINIPROGRAM_SECRET=从微信公众平台获取
WECHAT_JSCODE2SESSION_URL=https://api.weixin.qq.com/sns/jscode2session
```

## 当前页面

- `pages/login`：微信登录、已有 Pawtale 账号登录。
- `pages/home`：新版语音入口和当前回忆录入口，按当前回忆录绑定的讲述方式直接进入语音聊天。
- `pages/projects`：创建、切换、重命名、删除回忆录。
- `pages/voice-chat`：实时录音、WebSocket、partial/final transcript、AI 追问 TTS、本地快照恢复，以及下一阶段的麦克风抢占。
- `pages/transcripts`：默认查看当前回忆录的讲述记录、识别原文、整理文字、AI 引导问题，并支持问题和回答连播。
- `pages/book`：成书入口，支持章节规划、章节确认、分章成稿、来源记录查看，并逐步接入自动归章和自动补写事件。
- `pages/voice-progress`：查看当前项目会话、模块覆盖、语音段、AI 追问、累计文字，并逐步改为后端统一 progress snapshot。
- `pages/me`：账号信息、Web 登录凭据设置、当前项目统计、项目入口和 TTS 音色设置。

旧 workbench、stories、profile、固定问答和整段录音上传页面已移除。

## 检查

```bash
cd miniprogram
npm install
npm run typecheck
npm test
```

## 未完成

- 麦克风抢占：AI 播放或准备音频时，用户点击麦克风可中断 AI 并立即开始说话。
- 项目进度快照：统一“我的”、进度页、AI 访谈使用的统计口径。
- AI 进度鼓励：只播报后端给出的真实记录数、已归章数、成文字数和自动整理事件。
- 后台自动整理：新记录自动判断归入已有章节、候选新章节或待确认，并触发现有自动补写。
- 章节完整度审计：输出章节缺口和下一轮访谈建议。
- 语音编辑 planner：自然语言语音命令由后端生成可确认的操作计划。
- 结构化记忆提取、证据链浏览和更完整的成稿编辑页。
- 方言/噪声评测集、provider fallback、限流和成本监控。
