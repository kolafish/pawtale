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
│   └── requirements.txt
├── frontend/         # React 前端
│   └── src/
│       ├── components/
│       ├── stores/
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
uvicorn app.main:app --reload --port 8000
```

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

### 3. 访问应用

打开浏览器访问 http://localhost:5173

## MVP-1 功能

- [x] 角色创建和管理
- [x] 预设场景库
- [x] 故事生成（DeepSeek）
- [x] 剧情分支选择
- [x] 角色头像生成（GPT-4o）
- [x] 双栏 UI 布局
- [x] 故事保存和查看

## 环境变量

| 变量 | 说明 |
|------|------|
| DEEPSEEK_API_KEY | DeepSeek API 密钥 |
| OPENAI_API_KEY | OpenAI API 密钥（用于图像生成）|

## 文档

- [产品文档](./PRODUCT.md)
- [技术文档](./TECH.md)
