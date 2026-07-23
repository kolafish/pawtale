# Pawtale

Pawtale 是一个以项目为单位的 AI 内容创作系统。仓库包含：

- Web 写作工作台：小说、公众号文章和回忆录编辑；
- 微信小程序“余主任留声机”：低门槛语音访谈、讲述记录和回忆录成书；
- FastAPI 后端：认证、项目数据、AI/语音 provider 编排、回忆录素材与成书服务。

## 当前产品

### Web 工作台

- 邮箱注册、登录和基于用户/项目的访问隔离；
- 项目列表、重命名、删除和项目级导出；
- 小说/公众号：分组与文档树、资料 Binder、自动保存、AI 续写、修改对比和项目分析；
- 回忆录：素材校对、问答关系、方言词表、章节目录、章节稿、全书预览与 PDF/TXT 导出；
- GitHub App 单向项目备份；
- 管理员用户行为审计。

### 微信小程序

小程序以“首页、记录、书稿、我的”四个一级入口组织完整回忆录动线：

```text
登录并选择回忆录
  → 同意语音授权
  → 自由讲述 / AI 引导 / 人工访谈 / 成书补讲
  → Streaming ASR 实时转写
  → LLM 动态回应 + Streaming TTS
  → 保存原始音频、讲述和转写版本
  → 搜索、回听和校正讲述记录
  → 生成章节规划并逐章补全
  → 确认章节稿
  → 生成、阅读和导出全书
```

小程序还提供话题地图、语音搜索、图片素材、分享视频、多人实时转写、AI/TTS 偏好和大字模式。

## 仓库结构

```text
Pawtale/
├── backend/                    # FastAPI、SQLAlchemy、后台任务和 provider 集成
├── frontend/                   # React Web 工作台
├── miniprogram/                # 微信小程序
├── docs/                       # 当前架构和测试文档
├── scripts/                    # 仓库级验证脚本
├── data/                       # 本地运行数据（不作为产品文档）
├── PRODUCT.md                  # 当前产品功能与用户动线
└── TECH.md                     # 当前技术架构与代码边界
```

## 快速开始

### 后端

```bash
cd backend
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
PYTHONPATH=. python scripts/migrate_runtime_schema.py
PYTHONPATH=. uvicorn app.main:app --reload --port 8000
```

已有本地数据库时，迁移脚本默认先在 `data/backups/` 创建备份。服务启动只校验 schema，不会自动修改数据库。

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

## 常用验证

```bash
# Web
cd frontend && npm run lint && npm test && npm run build

# 小程序
cd miniprogram && npm run typecheck && npm test

# 后端
cd backend && venv/bin/python -m pytest -q
```

真机语音、后台切换、权限和播放体验不能只依赖单元测试，合入相关改动前还应执行 [小程序真机 Smoke](./docs/MINIPROGRAM_REAL_DEVICE_SMOKE.md)。

## 文档

- [产品功能与用户动线](./PRODUCT.md)
- [技术架构与开发边界](./TECH.md)
- [小程序说明](./miniprogram/README.md)
- [Web 前端说明](./frontend/README.md)
- [后端说明](./backend/README.md)
- [回忆录事实、素材与成书架构](./docs/MEMOIR_NARRATIVE_ARCHITECTURE.md)
- [回忆录访谈与成书协作架构](./docs/MEMOIR_INTERVIEW_AND_BOOK_ARCHITECTURE.md)
- [回忆录实时语音架构](./docs/MEMOIR_VOICE_CHAT_ARCHITECTURE.md)
- [AI provider 架构](./backend/docs/AI_ARCHITECTURE.md)
