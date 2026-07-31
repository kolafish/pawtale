# “老余口述”微信小程序

小程序是 Pawtale 的回忆录讲述与移动端成书界面。

当前共注册 19 个页面：4 个一级导航页、4 个账号与项目页、4 个语音与素材页、6 个成书页和 1 个偏好设置页。

当前语音和成书架构分别见：

- [实时语音架构](../docs/MEMOIR_VOICE_CHAT_ARCHITECTURE.md)
- [事实、访谈、成书与请求架构](../docs/MEMOIR_INTERVIEW_AND_BOOK_ARCHITECTURE.md)

## 当前用户动线

```text
微信登录 / Pawtale 账号登录
  → 创建或选择回忆录
  → 同意语音授权
  → 自由讲述、AI 引导或人工访谈
  → 实时录音、Streaming ASR、AI 回应和 Streaming TTS
  → 保存讲述记录、原始音频和转写版本
  → 搜索、回听、校正和分享
  → 生成章节目录并逐章补全
  → 确认章节、生成全书
  → 阅读和导出
```

## 一级导航

- `pages/home`：当前回忆录、继续讲述和折叠后的其他讲述方式；
- `pages/transcripts`：讲述记录、文字/语音搜索和转写恢复；
- `pages/book`：章节、成书进度、补全入口、阅读和导出入口；
- `pages/me`：账号、回忆录管理、AI/TTS 设置和大字模式。

## 功能页面

### 账号与项目

- `pages/login`：微信登录，或展开使用 Pawtale 邮箱账号；
- `pages/projects`：创建、切换、重命名和删除回忆录；
- `pages/project-edit`：回忆录名称、叙述者资料和默认讲述方式；
- `pages/voice-authorization`：语音、转写、音频留存和 AI 处理的明确授权。

### 语音与素材

- `pages/voice-chat`：录音、partial/final 转写、动态追问、流式播放、自动续听、用户打断和断线恢复；
- `pages/voice-progress`：汇总当前回忆录的讲述数据、最近会话和聊过的人生话题，并记录“够了/不适合/不想聊”等用户决定；
- `pages/transcript-detail`：回听、阅读/编辑切换、人工校正、重新识别、素材位置和低频修复；
- `pages/transcript-media`：从成书文字选择片段，生成插图或带原音字幕的分享视频；

### 成书

- `pages/book-completion`：整本书和每章的下一步；
- `pages/revision-workbench`：确认整书或小节修改方案，并跟踪后台修改结果；
- `pages/chapter-workspace`：Section、素材、补讲问题、章节正文、版本和确认；
- `pages/image-assets`：生成图片、章节插图/图录归属和素材状态；
- `pages/book-export`：阅读预览、文件导出，以及折叠后的成书信息、封面和版本设置；
- `pages/book-reader`：沉浸式阅读、整书朗读、目录跳转、返回顶部和历史版本。

### 偏好设置

- `pages/voice-settings`：自动打断、文字整理、AI 模型，以及聊天/书稿朗读偏好。

## 本地运行

```bash
cd miniprogram
npm install
npm run typecheck
npm test
```

使用微信开发者工具打开仓库根目录。`project.config.json` 已将 `miniprogram/` 设为小程序根目录。

默认后端地址在 `config.ts`：

```ts
export const BACKEND_HTTP_BASE_URL = "https://pawtale-api.spaceflows.cn";
export const BACKEND_WS_BASE_URL = "wss://pawtale-api.spaceflows.cn";
```

开发者工具可临时覆盖：

```ts
wx.setStorageSync("pawtale_backend_config", {
  httpBaseUrl: "http://127.0.0.1:8000",
  wsBaseUrl: "ws://127.0.0.1:8000",
});
```

真机必须使用手机可访问的 HTTPS/WSS 地址，并在微信公众平台配置合法域名。

## 登录和安全

- 微信登录：小程序将 `wx.login()` code 交给后端，后端调用 code2session；
- Pawtale 登录：使用已有邮箱和密码；
- 微信新用户可以先进入小程序，之后在“我的”里补充 Web 登录凭据；
- session cookie 由后端管理；
- 微信 AppSecret、ASR/TTS/LLM key 不得写入小程序、tracked 配置或文档示例；
- 语音授权版本同时保存在本地和后端审计记录中。

## 状态与恢复

- 服务端 VoiceSession 和消息是持久状态真相；
- ASR partial 只用于页面实时显示；
- provider final、音频、VoiceUtterance 和 TranscriptVersion 由后端幂等保存；
- 页面隐藏时停止录音和 AI 播放，重新显示后从后端刷新；
- 音频已经保存但转写失败时展示可恢复状态，不伪造空记录；
- 阅读和编辑模式分离，未保存编辑在退出前确认；
- 大字模式保存在本地，并在主要页面统一应用。

## 测试策略

`test/` 使用 Node test runner 和页面 harness，主要覆盖：

- page method、view model、service mock 和用户可观察结果；
- 语音事件顺序、录音资源释放、自动续听和 AI 打断；
- 搜索、转写编辑、成书状态、版本和返回路径；
- 登录、隐私授权、分享、设置与大字模式。

WXML/WXSS 只在路由、配置或模板绑定本身是失败边界时做轻量断言。录音权限、真机后台切换、播放进度、滚动和视觉层级仍需 [真机 Smoke](../docs/MINIPROGRAM_REAL_DEVICE_SMOKE.md)。

## 发布边界

代码合并后默认只更新本地小程序代码并执行类型检查和测试。除非当前任务明确要求，不自动调用微信开发者工具上传、生成体验版、提交审核或发布。
