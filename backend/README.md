# Pawtale Backend

## 技术栈

- FastAPI + async SQLAlchemy + aiosqlite
- 多模型 AI 支持：DeepSeek、Claude Opus 4.6、Claude Sonnet 4.6
- OpenAI GPT-4o 图像生成

## 安装依赖

```bash
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 配置环境变量

创建 `.env` 文件：

```bash
# AI 文本生成服务
DEEPSEEK_API_KEY=your-deepseek-key
DEEPSEEK_BASE_URL=https://api.deepseek.com

# Claude API 配置（可选，用于多模型支持）
ANTHROPIC_AUTH_TOKEN=your-claude-token
ANTHROPIC_BASE_URL=https://api.anthropic.com

# 图像生成服务
OPENAI_API_KEY=your-openai-key

# 默认 AI 模型
DEFAULT_AI_MODEL=deepseek  # 可选: deepseek, claude-opus-4, claude-sonnet-4
```

## 运行

```bash
uvicorn app.main:app --reload --port 8000
```

## 测试

```bash
# 运行所有测试
pytest -v

# 运行特定测试文件
pytest tests/test_api.py -v
pytest tests/test_new_features.py -v
pytest tests/test_frontend.py -v

# 运行特定测试
pytest tests/test_api.py::TestCharacterAPI::test_create_character -v
```

## 核心功能

### 多模型 AI 支持
- 支持 DeepSeek、Claude Opus 4.6、Claude Sonnet 4.6
- 工厂模式实现，易于扩展
- 用户可在创建大纲和生成故事时选择模型

### 引导式创建
- 4步向导流程：故事目标 → 角色张力 → 核心冲突 → AI 生成预览
- API: `POST /api/outlines/guided-create`

### 故事大纲系统
- 角色配置（类型、称呼、关系）
- 场景配置（可用场景、起始场景）
- 故事参数（情绪、风格、冲突、结局）

## API 文档

启动后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
