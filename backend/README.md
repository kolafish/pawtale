# Pawtale Backend

FastAPI 后端，统一提供账号认证、项目写作系统、回忆录语音与成书、AI provider、导出、后台任务和审计 API。

## 运行

```bash
cd backend
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env

PYTHONPATH=. python scripts/migrate_runtime_schema.py
PYTHONPATH=. uvicorn app.main:app --reload --port 8000
```

- 数据库默认位于仓库根目录 `data/pawtale.db`；
- 迁移脚本对已有数据库默认先创建备份；
- 全新数据库可显式使用 `--skip-backup`；
- API 启动只执行运行时配置和 schema 校验，不自动建表或迁移。

本地入口：

- 健康检查：<http://localhost:8000/health>
- OpenAPI：<http://localhost:8000/docs>
- ReDoc：<http://localhost:8000/redoc>

## 测试

```bash
cd backend
venv/bin/python -m pytest -q
```

常用范围：

```bash
# 通用项目系统
venv/bin/python -m pytest tests/test_project_*.py -q

# 回忆录语音
venv/bin/python -m pytest \
  tests/test_memoir_voice_sessions.py \
  tests/test_memoir_voice_transcripts.py \
  tests/test_memoir_voice_assistant_turn_stream.py -q

# 回忆录章节和成书
venv/bin/python -m pytest \
  tests/test_memoir_sections.py \
  tests/test_memoir_chapter_pipeline.py \
  tests/test_memoir_book_outputs.py -q
```

## 应用结构

```text
app/
├── main.py
├── config.py
├── database.py
├── migrations/
├── domain/
├── models/
├── schemas/
├── routers/
│   ├── auth.py
│   ├── audit.py
│   ├── github.py
│   ├── projects/
│   └── memoir/
└── services/
    ├── projects/
    ├── memoir/
    ├── ai_base.py
    ├── ai_factory.py
    ├── ai_text.py
    ├── ai_claude.py
    └── ai_volc.py
```

### 通用项目服务

- `routers/projects/`：项目、结构、资料、活动、导出、GitHub 同步和 AI；
- `services/projects/`：项目业务编排与权限；
- `project_documents.py`：文档文件与数据库事务补偿；
- `content_storage.py`：正文文件存储；
- `domain/projects/`：项目类型和默认模板。

### 回忆录服务

- `voice*.py`：VoiceSession、gateway、final 持久化、动态回应、TTS 和恢复；
- `material*.py` / `transcript*.py`：素材、转写版本、校对和批量处理；
- `sections.py` / `section_digests.py`：素材归属与 Section 派生摘要；
- `chapter*.py` / `completion*.py`：章节 pipeline、下一步和补讲；
- `book*.py`：章节稿、全书版本、图片、朗读和导出；
- `editing_jobs.py`：后台整理 job、lease 和 worker；
- `share_video*.py`：分享视频场景、背景、对齐和渲染；
- `narrative/`：访谈策略、话题地图和成书领域决策。

详细边界见仓库根目录 [TECH.md](../TECH.md)。

## 后台 Worker

`app.main` 根据配置启动：

- 回忆录 editing worker；
- 分享视频 worker。

任务使用数据库持久化、lease、heartbeat 和重试。关闭 worker 不影响 API 读取原始音频和转写，但会暂停依赖后台处理的派生结果。

## 运行配置

完整变量和安全注释以 [.env.example](./.env.example) 与 `app/config.py` 为准。主要分组包括：

- 应用与 SQLite；
- 微信小程序登录；
- Claude、DeepSeek、Doubao 文本模型；
- 火山流式 ASR 和 TTS；
- StepFun 批量 ASR；
- 回忆录 editing/share-video worker；
- 访谈策略和话题地图 rollout；
- GitHub App 单向备份。

生产环境要求：

```bash
APP_ENV=production
APP_SECRET_KEY=<至少 32 字符的非默认随机值>
```

微信 AppSecret、AI/ASR/TTS key 和 GitHub App private key 只能放在部署环境的 `backend/.env`，不得进入小程序或 tracked 文件。

## 语音网关容量检查

只验证多个登录用户的 WebSocket 容量、不触发真实 provider：

```bash
venv/bin/python scripts/loadtest_voice_gateway.py \
  --base-url https://pawtale-api.spaceflows.cn \
  --users 10 \
  --hold-seconds 120 \
  --mode capacity \
  --expect-eleventh-rejected
```

`--mode asr` 会发送音频帧并触发真实 ASR，执行前需确认 provider 额度和费用。
