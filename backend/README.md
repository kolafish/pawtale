# Pawtale Backend

FastAPI 后端，统一提供账号认证、项目写作系统、回忆录语音与成书、AI provider、导出、后台任务和审计 API。

## 运行

```bash
cd backend
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env

createdb pawtale
PYTHONPATH=. alembic upgrade head
PYTHONPATH=. uvicorn app.main:app --reload --port 8000
```

- `DATABASE_URL` 必须使用 `postgresql+asyncpg`；
- 生产环境必须显式设置 `DATABASE_URL`；
- schema 迁移统一由 Alembic 管理；
- API 启动只执行运行时配置和 schema 校验，不自动建表或迁移。

## 管理员权限

管理员权限只由数据库中的 `users.role = 'admin'` 决定。注册、微信登录和设置邮箱
都不会授予管理员权限；旧的 `ADMIN_EMAILS` 配置不再生效。管理员角色由可信运维
按已核实的用户 ID 显式设置。升级前若曾依赖邮箱名单，需先核实并设置已有管理员
的持久化角色，不能把新注册的同名邮箱自动作为管理员。

## 数据库迁移

Alembic 必须连接独立的开发数据库，不能使用生产数据库生成 migration 或执行测试。

```bash
export DATABASE_URL='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/pawtale_dev'
PYTHONPATH=. alembic upgrade head
PYTHONPATH=. alembic check
```

后端 CI 通过 `TEST_POSTGRES_DATABASE_URL` 验证 baseline 能完整升级、与 SQLAlchemy
metadata 一致、降级并再次升级。生产变更先备份，再显式运行 `alembic upgrade head`，
最后重启 API 并执行 schema、数据行数和外键验证。

本地入口：

- 健康检查：<http://localhost:8000/health>
- OpenAPI：<http://localhost:8000/docs>
- ReDoc：<http://localhost:8000/redoc>

## 测试

纯逻辑和 provider 协议测试不需要数据库，也不会执行 Alembic：

```bash
venv/bin/python -m pytest -q tests/test_ai_base.py tests/test_volc_streaming_asr.py tests/test_database_transactions.py
```

数据库/API 测试通过 `test_db` 显式依赖 `postgres_schema`，迁移测试直接使用该 fixture。运行完整测试仍必须配置独立的 ParadeDB 测试库，包含 `pg_search` 等运行时扩展；缺少配置或扩展会失败，不会跳过集成测试。

```bash
cd backend
venv/bin/ruff check app tests scripts
TEST_POSTGRES_DATABASE_URL='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/pawtale_test' \
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

## 同条口述更正验证

转写整理会处理同一条记录内目标明确、给出确定新说法的年份、人名、地点等更正，并去掉已执行的更正指令。复用原有一次整理调用，仅增加提示词长度；原始录音和 ASR 原文保留。该能力适用于新记录的自动整理和已有记录主动重新整理，不自动批量重处理历史记录。

当前范围以后台一条 VoiceUtterance 为界。停顿导致录音分成多条时，不跨条修改；不确定、未完成的更正和故事里的引语保留原意。此入口不执行删稿、隐私、章节调整或风格指令。

以下评测使用合成口述样本，显式调用所选付费 provider，需要配置其凭据和有效的 PostgreSQL DATABASE_URL（不会读写数据库）：

```bash
PYTHONPATH=. venv/bin/python scripts/eval_memoir_self_correction.py \
  --ai-model deepseek-flash --output /tmp/memoir-self-correction.json
```

可用 `--case correct_relationship` 缩小范围，或用 `--polish-level medium` / `high` 验证其他整理档位。脚本保存实际输出和模型信息；片段检查通过后仍需人工核对人物关系、时间归属和事实完整性，不能把样本通过率当成线上准确率。

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

- 应用与 PostgreSQL；
- 微信小程序登录；
- DeepSeek、Doubao 文本模型；
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

DeepSeek 按[官方高峰/闲时规则](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)估算费用：中国法定节假日和所有周末全天半价，周末调休上班也不切换为高峰。节假日历来自 `chinesecalendar`，当前固定版本覆盖至 2026 年；国务院公布次年安排后，应升级依赖并验证节假日、调休和时段边界。日历未覆盖年份的工作日高峰候选时段会保留调用用量、记录未知费用并告警，不猜测单价扣款；已知的晚间和周末闲时仍正常计价。

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
