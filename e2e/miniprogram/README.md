# 老余口述小程序 E2E

这里存放微信开发者工具和真机端到端测试。现有 `miniprogram/test/` 继续负责
page method、view model、service 和状态机测试；本目录只覆盖真实小程序运行时、
页面绑定、导航以及跨服务主链路。

## 本地准备

1. 安装并登录微信开发者工具。
2. 在顶部菜单“设置 → 安全设置”中开启“服务端口”。
3. 复制 `.env.e2e.local.example` 为 `.env.e2e.local`，填写专用测试账号。
4. 不要把账号、密码、微信票据或 provider key 提交到仓库。

服务端口由微信开发者工具管理，不写入测试配置。自动化 WebSocket 默认使用
`9420`，可通过 `PAWTALE_E2E_AUTOMATION_PORT` 覆盖。

## 运行

从仓库根目录执行：

```bash
./scripts/test-miniprogram-e2e.sh devtools
```

失败产物写入 `artifacts/`。测试只清理本轮创建并记录 ID 的数据，不会按名称模糊
删除，也不会修改测试账号中原有回忆录。
