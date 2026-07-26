# CLAUDE.md

本仓库的开发流程、测试要求、部署约束和长期架构原则统一以 [AGENTS.md](./AGENTS.md) 为准。不要在本文件维护另一套可能漂移的规则。

开始工作前请阅读：

- [README.md](./README.md)：项目入口和常用命令；
- [PRODUCT.md](./PRODUCT.md)：当前功能和用户动线；
- [TECH.md](./TECH.md)：当前代码边界；
- [回忆录主架构文档](./docs/MEMOIR_INTERVIEW_AND_BOOK_ARCHITECTURE.md)：回忆录事实、访谈、成书、缓存与请求不变量。

常用验证：

```bash
cd backend && venv/bin/python -m pytest -q
cd frontend && npm run lint && npm test && npm run build
cd miniprogram && npm run typecheck && npm test
```

回忆录 provider key、微信 AppSecret 和 GitHub App private key 只保存在后端运行环境，不得写入仓库或小程序。
