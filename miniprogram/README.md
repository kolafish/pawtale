# Pawtale 回忆录小程序

这是 Pawtale 回忆录微信小程序。当前覆盖文字回答流程，并支持在问题页录音转写：

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

小程序端只走微信云开发路径：JSON API 通过 `wx.cloud.callFunction` 请求 `memoir-api`，录音先上传到云存储，再由云函数登记音频元数据。

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

## 当前 API

当前小程序只调用这些后端 API：

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
POST /api/mobile/memoir/audio-files/{audio_file_id}/transcribe
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

- accepted draft 同步到 Web 正文

这些能力会在后续 PR 分别接入。
