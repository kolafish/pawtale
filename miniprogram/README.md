# Pawtale 回忆录小程序

这是 Pawtale 回忆录微信小程序的本地开发骨架。当前覆盖文字回答流程，并支持在问题页录音转写：

```text
进入小程序
 -> 本地开发登录
 -> 获取默认回忆录项目
 -> 获取当前问题
 -> 录音上传
 -> 语音转写并编辑回答
 -> AI 校正口述稿
 -> 查看反馈
 -> 编辑
 -> 确认并继续下一问
```

## 本地运行

1. 启动后端：

```bash
cd backend
venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

2. 用微信开发者工具打开 `miniprogram/` 目录。

3. 在微信开发者工具里关闭本地调试的合法域名校验，或确保 `http://127.0.0.1:8000` 可访问。

4. 进入首页后，小程序会自动尝试本地开发登录：

```text
email: miniprogram-dev@example.com
password: password123
```

如果用户不存在，会自动注册；如果已存在，会登录。

## 当前 API

当前小程序只调用这些后端 API：

```text
GET  /api/mobile/memoir/project
GET  /api/mobile/memoir/sessions/current
GET  /api/mobile/memoir/questions/next
POST /api/mobile/memoir/questions/{question_id}/skip
POST /api/mobile/memoir/audio-files
POST /api/mobile/memoir/audio-files/{audio_file_id}/transcribe
POST /api/mobile/memoir/answers
PATCH /api/mobile/memoir/answers/{answer_id}
POST /api/mobile/memoir/answers/{answer_id}/draft
PATCH /api/mobile/memoir/drafts/{draft_id}
POST /api/mobile/memoir/drafts/{draft_id}/accept
```

## 当前不包含

- 微信 openid/unionid 登录
- accepted draft 同步到 Web 正文
- 云托管部署配置

这些能力会在后续 PR 分别接入。
