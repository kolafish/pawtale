# Pawtale 回忆录小程序

这是 Pawtale 回忆录微信小程序的本地开发骨架。当前覆盖文字回答流程，并支持在问题页录音转写：

```text
进入小程序
 -> 本地开发登录 / 云函数 openid
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

1. 启动后端：

```bash
cd backend
venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

2. 用微信开发者工具打开仓库根目录 `Pawtale/`。根目录的 `project.config.json` 会把 `miniprogram/` 识别为小程序根目录，并把 `cloud-functions/` 识别为云函数根目录。

3. 在微信开发者工具里关闭本地调试的合法域名校验，或确保 `http://127.0.0.1:8000` 可访问。

4. 进入首页后，小程序会自动尝试本地开发登录：

```text
email: miniprogram-dev@example.com
password: password123
```

如果用户不存在，会自动注册；如果已存在，会登录。

## 类型检查

```bash
cd miniprogram
npm install
npm run typecheck
```

## 微信云开发配置

小程序 AppID 已配置为 `wx71acdf73e2ba7067`。

`config.ts` 集中管理请求目标：

```ts
export const API_TRANSPORT = "cloudFunction";
export const LOCAL_API_BASE_URL = "http://127.0.0.1:8000";
export const CLOUD_ENV_ID = "cloud1-d6gmxffp0ea48aa1a";
export const CLOUD_FUNCTION_NAME = "memoir-api";
```

真机调试和发布使用：

```ts
export const API_TRANSPORT = "cloudFunction";
```

云端路线使用微信云函数、云数据库和云存储，不使用云托管容器。切到 `cloudFunction` 后，JSON API 会通过 `wx.cloud.callFunction` 请求 `memoir-api`，录音会先上传到云存储，再由云函数登记音频元数据。

如需临时连接本地 FastAPI 调试，再改回：

```ts
export const API_TRANSPORT = "local";
```

## 当前 API

当前小程序只调用这些后端 API：

```text
GET  /api/mobile/memoir/me
GET  /api/mobile/memoir/project
GET  /api/mobile/memoir/sessions/current
GET  /api/mobile/memoir/questions/next
GET  /api/mobile/memoir/summary
GET  /api/mobile/memoir/stories
GET  /api/mobile/memoir/stories/{answer_id}
POST /api/mobile/memoir/questions/{question_id}/skip
POST /api/mobile/memoir/audio-files
POST /api/mobile/memoir/audio-files/{audio_file_id}/transcribe
POST /api/mobile/memoir/answers
PATCH /api/mobile/memoir/answers/{answer_id}
POST /api/mobile/memoir/answers/{answer_id}/draft
PATCH /api/mobile/memoir/drafts/{draft_id}
POST /api/mobile/memoir/drafts/{draft_id}/accept
```

`POST /api/mobile/memoir/answers` 支持纯文本回答，也支持带上刚上传录音的
`audio_file_id` 和 `duration_seconds`，用于后续播放原话或重录。

## 当前不包含

- accepted draft 同步到 Web 正文

这些能力会在后续 PR 分别接入。
