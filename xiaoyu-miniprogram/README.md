# “小鱼口述”微信小程序

小鱼口述是 Pawtale 的轻量文章创作界面。它与 Web、老余口述共享同一套后端认证、用户、`article` project、document、AI provider 和存储能力，但不使用回忆录访谈、素材或成书模型。

## 用户动线

```text
微信登录
  → 新建或打开一篇文章
  → 键盘输入 / 持续口述
  → revision 自动保存
  → AI 接着写 / 按要求改 / 检查文章
```

- 一篇文章对应一个 `article` project 和它的默认 document；
- 持续口述只把临时音频流送往共享 ASR gateway，最终只合并文字；
- AI 根据当前正文判断文体、目的和语气，不套固定文章或公众号模板；
- AI 修改先展示候选，用户确认后才写入正文；
- article review 只检查表达、结构和可核实性提示，不执行回忆录事实完整性检查。

## 本地验证

```bash
cd xiaoyu-miniprogram
npm install
npm run typecheck
npm test
```

使用微信开发者工具直接打开 `xiaoyu-miniprogram/`。发布前需要：

1. `project.config.json` 已配置小鱼测试 AppID；正式发布前换成对应的正式 AppID；
2. 在微信公众平台配置 `https://pawtale-api.spaceflows.cn` 和对应 WSS 合法域名；
3. 在后端 `.env` 配置 `WECHAT_XIAOYU_MINIPROGRAM_APPID` 与 `WECHAT_XIAOYU_MINIPROGRAM_SECRET`；
4. 完成麦克风和录音用途的隐私保护指引声明。

AppSecret、ASR key 和 LLM key 只能保存在后端，不能写入小程序。

## 数据与失败边界

- 后端数据库和 document 正文是事实源，小程序不另建文章缓存或数据表；
- 保存、AI 续写、AI 修改采用都携带 `content_revision`，409 时停止覆盖并要求重新载入；
- partial 只在页面实时显示，final 合并后走普通 document 保存；
- dictation gateway 不保存音频、不创建 VoiceSession，也不产生访谈消息；
- 高成本 AI 请求超时后不自动重试，用户需要刷新确认结果。

真机登录、录音权限、前后台切换、断网和 AI 主链路见 [小程序真机 Smoke](../docs/MINIPROGRAM_REAL_DEVICE_SMOKE.md)。

代码合并后默认只更新本地代码并执行验证，不自动上传、生成体验版、提交审核或发布。
