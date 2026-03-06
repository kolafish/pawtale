# Pawtale - 是我的CP呀

AI 辅助内容创作平台，降低内容创作的门槛。

## 项目结构

```
Pawtale/
├── backend/          # FastAPI 后端
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── routers/
│   │   └── services/
│   ├── tests/
│   └── requirements.txt
├── frontend/         # React 前端
│   └── src/
│       ├── components/
│       ├── hooks/
│       ├── stores/
│       ├── constants/
│       ├── api/
│       └── types/
├── data/             # SQLite 数据库
├── PRODUCT.md        # 产品文档
└── TECH.md           # 技术文档
```

## 快速开始

### 1. 启动后端

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API keys

# 启动服务
PYTHONPATH=. uvicorn app.main:app --reload --port 8000
```

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

### 3. 访问应用

打开浏览器访问 http://localhost:5173

## 已实现功能

- [x] 角色创建和管理（含深度字段：核心性格、致命弱点、成长弧线等）
- [x] 场景库（预设 + 自定义 + AI 生成）
- [x] 多模型支持（DeepSeek / Claude Opus 4.6 / Claude Sonnet 4.6）
- [x] 故事大纲系统（引导式 4 步创建向导）
- [x] 故事生成与剧情分支选择
- [x] 角色头像生成（GPT-4o）
- [x] 双创作模式
  - 自由写作：全宽编辑器 + 可折叠侧边栏（提炼/资源/作品）
  - 互动创作：左栏 Tab（角色/场景/大纲/故事）+ 右栏故事预览
- [x] 写作增强功能：自动保存、字数统计、Cmd+S、AI 续写分隔符、导出 .txt
- [x] AI 自动提炼（角色/场景/大纲），可一键存入全局库
- [x] 故事中动态场景切换和角色管理

## 环境变量

| 变量 | 说明 |
|------|------|
| DEEPSEEK_API_KEY | DeepSeek API 密钥 |
| DEEPSEEK_BASE_URL | DeepSeek API 地址 |
| ANTHROPIC_AUTH_TOKEN | Claude API 认证令牌（可选） |
| ANTHROPIC_BASE_URL | Claude API 地址（可选） |
| OPENAI_API_KEY | OpenAI API 密钥（用于图像生成） |
| DEFAULT_AI_MODEL | 默认 AI 模型（默认 `claude-opus-4`；可选 `deepseek` / `claude-opus-4` / `claude-sonnet-4`） |

## 文档

- [产品文档](./PRODUCT.md)
- [技术文档](./TECH.md)
