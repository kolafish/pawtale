# Pawtale

Pawtale 是一个以项目为单位的 AI 内容创作系统。仓库包含：

- Web 写作工作台：小说、公众号文章和回忆录编辑；
- 微信小程序“老余口述”：低门槛语音访谈、讲述记录和回忆录成书；
- 微信小程序“小鱼口述”：持续语音输入、文章编辑和无模板 AI 辅助；
- FastAPI 后端：认证、项目数据、AI/语音 provider 编排、回忆录素材与成书服务。

## 当前产品

### Web 工作台

- 生产 Web 仅允许已启用密码登录的管理员使用邮箱密码，开发和测试环境支持邮箱注册与登录；
- 项目列表、重命名、删除和项目级导出；
- 小说/公众号：分组与文档树、资料 Binder、自动保存、AI 续写、修改对比和项目分析；
- 回忆录：素材校对、问答关系、方言词表、章节目录、章节稿、全书预览与 PDF/TXT 导出；
- GitHub App 单向项目备份；
- 管理员用户行为审计。

### “老余口述”小程序

访客可从首页书卡阅读已开放的示例回忆录。登录后，用户从首页原位开始讲述，点击回忆录书卡进入书稿，从“全部回忆”查看记录，右上角进入设置。

```text
登录并选择回忆录
  → 同意语音授权
  → AI 访谈 / 真人访谈 / 成书补讲
  → Streaming ASR 实时转写
  → AI 访谈中由 LLM 回应并流式朗读
  → 保存原始音频、讲述和转写版本
  → 搜索、回听和校正讲述记录
  → 后台整理目录和章节正文
  → 目录确认后，按就绪章节自动组装可读书稿
  → 阅读、修改和导出
```

小程序还提供语音编辑、语音搜索、图片素材、分享视频、AI/TTS 偏好和大字模式。

### “小鱼口述”小程序

小鱼以一篇文章为一个 `article` project，提供文章列表、键盘编辑、持续口述、revision 自动保存，以及“接着写、按要求改、检查文章”三项无固定模板的 AI 辅助。它不创建访谈、音频素材或回忆录成书数据。

## 仓库结构

```text
Pawtale/
├── backend/                    # FastAPI、SQLAlchemy、后台任务和 provider 集成
├── frontend/                   # React Web 工作台
├── miniprogram/                # “老余口述”回忆录小程序
├── xiaoyu-miniprogram/         # “小鱼口述”文章小程序
├── docs/                       # 当前架构和测试文档
├── scripts/                    # 仓库级验证脚本
├── data/                       # 本地运行数据（不作为产品文档）
├── PRODUCT.md                  # 当前产品功能与用户动线
└── TECH.md                     # 当前技术架构与代码边界
```

## 快速开始

### 后端

先准备支持 `pg_trgm`、`vector` 和 `pg_search` 的 PostgreSQL 开发实例，并确保迁移账号可创建这些扩展。CI 使用的 ParadeDB 镜像见[后端工作流](./.github/workflows/backend.yml)。

```bash
cd backend
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
createdb --template=template0 pawtale
PYTHONPATH=. alembic upgrade head
PYTHONPATH=. uvicorn app.main:app --reload --port 8000
```

`DATABASE_URL` 使用 `postgresql+asyncpg`。服务启动只校验 schema，不会自动修改数据库。

### Web 前端

```bash
cd frontend
npm install
npm run dev
```

访问 <http://localhost:5173>。开发服务器通过 `/api` 代理到本地后端。

### 微信小程序

```bash
cd miniprogram
npm install
npm run typecheck
npm test
```

使用微信开发者工具打开仓库根目录；`project.config.json` 已将 `miniprogram/` 配置为小程序根目录。开发和发布约束见 [miniprogram/README.md](./miniprogram/README.md)。

小鱼口述单独验证：

```bash
cd xiaoyu-miniprogram && npm install && npm run typecheck && npm test
```

使用微信开发者工具打开 `xiaoyu-miniprogram/`，配置和边界见 [xiaoyu-miniprogram/README.md](./xiaoyu-miniprogram/README.md)。

## 常用验证

```bash
# 从仓库根目录运行，后端使用专用 ParadeDB 测试库
TEST_POSTGRES_DATABASE_URL='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/pawtale_test' \
  bash scripts/test-backend.sh

# Web
npm --prefix frontend ci && bash scripts/test-frontend.sh

# 老余小程序
npm --prefix miniprogram ci && npm --prefix miniprogram run typecheck && npm --prefix miniprogram test
```

真机语音、后台切换、权限和播放体验不能只依赖单元测试，合入相关改动前还应执行 [小程序真机 Smoke](./docs/MINIPROGRAM_REAL_DEVICE_SMOKE.md)。

## 文档

- [产品功能与用户动线](./PRODUCT.md)
- [技术架构与开发边界](./TECH.md)
- [小程序说明](./miniprogram/README.md)
- [小鱼口述小程序说明](./xiaoyu-miniprogram/README.md)
- [Web 前端说明](./frontend/README.md)
- [后端说明](./backend/README.md)
- [回忆录事实、访谈、成书与请求架构](./docs/MEMOIR_INTERVIEW_AND_BOOK_ARCHITECTURE.md)
- [回忆录实时语音架构](./docs/MEMOIR_VOICE_CHAT_ARCHITECTURE.md)
- [回忆录重要数据结构、来源、展示与编辑指南](./docs/MEMOIR_DATA_MODEL_AND_EDITING_GUIDE.md)
- [AI provider 架构](./backend/docs/AI_ARCHITECTURE.md)
