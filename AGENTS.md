# AGENTS.md

## Default Workflow For Fixing Issues (Required)

当用户要求“修某个 issue”时，默认必须按以下流程执行，不可跳步：

1. 实现 issue 修复并提交到独立分支，创建 PR（关联 issue）。
2. 在**当前线程**进行代码评审（code review），不需要启动新线程或独立 agent。
3. 根据当前线程里的 review 意见逐条修改并推送更新。
4. 重新运行相关测试（至少覆盖本次改动影响范围）。
5. 仅在以下条件全部满足后执行 merge：
   - review 意见已处理完毕（或有明确结论）；
   - 测试通过；
   - 无阻塞性问题。

## Delivery Requirements

- 最终汇报必须包含：
  - PR 链接；
  - 本次执行过的测试命令与结果；
  - 最终 merge 结果（含 commit SHA）。
  - 如涉及后端部署或小程序代码更新，必须包含部署/更新目标、验证结果和对应 commit SHA。
- 如测试因环境问题无法正常执行，必须明确说明原因、影响和替代验证方式，再等待用户确认是否继续 merge。

## Default Git Delivery Preference

除非用户明确说明“只本地提交”“不要开 PR”“不要 merge”或有测试/环境阻塞，本仓库的提交类任务默认按以下交付：

1. 从当前改动创建独立 `codex/*` 分支。
2. push 到 `origin`。
3. 创建指向远端 `main` 的 PR。
4. 在测试/验证结论明确后 merge 到 `main`。
5. 最终汇报包含 PR 链接、测试或验证结果、merge commit SHA。

如果任务过程中同步修改了 `AGENTS.md`，默认把 `AGENTS.md` 改动纳入最近一次相关 PR 一起提交、review、push 和 merge；不要把它留作未提交本地改动或另起无关 PR，除非用户明确要求拆分。

## Post-Merge Deployment Preference

- 如果本次 PR 改动包含 `backend/` 下的后端代码、依赖、配置模板、数据库迁移或部署脚本，且 PR 已成功 merge 到 `main`，默认继续把最新 `main` 自动部署到 ECS 上的 Pawtale 后端。
- 后端自动部署仅限 Pawtale 自己的部署目录、进程和端口；不得修改共享机器上已有项目、已有进程、Caddy 全局配置、防火墙、systemd、PM2 全局状态或其他服务，除非用户明确要求。
- 部署后必须做基础验证，至少包括后端健康检查、关键 API/OpenAPI 可访问性，以及本次后端改动相关的最小 smoke test；最终汇报要包含部署目标、验证结果和已部署的 commit SHA。
- 如果本次 PR 改动包含 `miniprogram/` 下的小程序代码、配置或资源，且 PR 已成功 merge 到 `main`，默认继续把小程序项目更新到最新 `main` 对应代码，并重新执行小程序相关验证。
- 小程序代码更新至少要确保本地小程序项目使用已 merge 的 commit，并执行 `miniprogram` 的类型检查和相关测试；如果需要通过微信开发者工具上传、预览、体验版、提交审核或发布，而当前环境缺少登录态、开发者工具能力或必须人工确认，必须明确说明阻塞点、影响和需要用户完成的动作。
- 如果同一个 PR 同时改 `backend/` 和 `miniprogram/`，默认在 merge 后同时执行 ECS 后端部署和小程序代码更新/验证；最终汇报要分别列出后端部署结果和小程序更新结果。
- 如果 PR 只改文档、前端静态资源或非后端/非小程序模块，默认不自动部署 ECS，也不更新小程序，除非用户明确要求。

## Long-Term Code Quality Principles

- 长期迭代定位：这是一个需要持续演进的项目，默认以长期维护质量为先，代码要清晰、稳定、可继续扩展。
- 产品边界优先：小说、公众号、回忆录必须作为清晰的产品模块演进，不把不同业务强行塞进一个万能模型。
- 共享能力下沉：认证、用户、AI provider、存储、导出、API transport 等基础能力放在 shared 层，产品模块只表达自身业务。
- 抽象必须有真实收益：只有能减少重复、隔离变化、提升测试性的抽象才允许引入，注意抽象和实现的边界，避免为了“框架感”增加样板代码。
- 实现保持精简：删除无用代码，不保留兼容壳、死分支、临时绕路、胶水代码和未使用的配置，避免啰嗦实现。
- 容错保持克制：只处理真实可预期且有明确恢复路径的异常，不写无谓、宽泛、层层包裹的防御式容错。
- 类型和 schema 明确：跨模块和 API 边界使用显式类型，避免依赖松散的字符串和任意 JSON 承载核心业务语义。
- 测试跟随边界：每次重构必须补齐对应单元测试或 API 测试，测试用例表达业务约束，而不是复刻实现细节。
- PR 小步演进：每个 PR 聚焦一个架构目标，优先保证行为可验证、diff 可 review、后续重构有清晰落点。

## Investigation And Root Cause Standard

- 调查 bug、线上异常、真机问题、用户反馈或不稳定行为时，默认目标是查到可验证根因，而不是先加兜底、重试、超时、吞错或宽泛兼容来掩盖问题。
- 给出修复前必须尽量建立完整因果链：触发入口、关键状态变化、失败组件、错误传播路径、为什么会表现为用户看到的现象，以及为什么该修复能阻断这条链路。
- 没有查到根因时必须明确说“尚未查到根因”，列出已排除项、仍需验证的假设和下一步证据，不得把猜测、经验判断或兜底策略表述成根因结论。
- 兜底只能作为辅助措施使用，例如提升可观测性、让失败显式暴露、保护用户数据或临时止血；如果加入兜底，必须同时说明它解决的只是症状、风险或诊断问题，不能替代根因修复。
- 对 ASR/TTS/LLM、WebSocket、录音、播放、权限、后台切换等状态机问题，优先追真实事件序列、provider payload、客户端状态迁移、原生日志和最小复现；不要只根据 UI 最终状态推断原因。
- 回归测试要锁定根因链路中的关键失败条件，优先使用能复现真实事件顺序或数据形态的样本；不要只测试新增兜底文案或宽泛错误处理。

## Testing Strategy

- 测试优先覆盖真实风险：provider 协议解析、WebSocket/ASR/TTS 状态机、持久化/API 边界、权限与跨项目隔离、数据迁移、用户可观察的业务结果。
- 真实 bug 必须沉淀最小回归样本；ASR/TTS/LLM 相关问题优先使用脱敏后的真实 provider payload、事件序列或录音时序建模，不只手写理想数据。
- 小程序页面测试优先调用 view model、page method、service mock 和状态 patch，断言用户可观察结果；避免直接用 WXML/WXSS 正则锁 class、文案、字号、颜色、间距和布局细节。
- 只有当路由注册、隐私声明、配置文件或模板绑定本身就是失败边界时，才允许读取 JSON/WXML/WXSS 做轻量断言；不要用这类测试替代真机/开发者工具 smoke。
- UI 视觉、大字模式、滚动、播放进度、后台退出、麦克风权限等体验问题，单元测试只能覆盖状态逻辑；合入前应补充人工或自动化 smoke 验证，并在最终汇报说明是否执行。
- 删除低价值测试优先于维护它：如果一个测试只证明实现细节存在、不能复现真实问题、经常阻碍合理重排，就应删除或改写成业务行为/回归测试。

## Memoir Voice Chat Redesign Context

- 回忆录小程序新版主线文档是 `docs/MEMOIR_VOICE_CHAT_REDESIGN.md`，后续回忆录小程序改造默认以它为准。
- 旧版固定问题、整段录音上传、离线 ASR、整理稿确认链路只通过 commit `bd3599ad3dddf382b242c47071c856d382543cac` 追溯，不在文档里维护旧代码路径清单。
- 旧版回忆录文档已删除；需要查看 legacy 背景时通过上述 baseline commit 做 Git 追溯。
- 新版产品形态是 `VAD + Streaming ASR + LLM Dynamic Follow-up + Streaming TTS`，以 voice session、chat message、realtime transcript、audio evidence 为核心，不再继续强化固定问答模型。
- 新版创建/开始访谈时要区分 `free_narration` 弱引导和 `guided_interview` 强引导：弱引导支持叙述者按时间线自主讲述，强引导由 AI 按人物、事件、地点、时间段、主题等模块推进。
- 用户原始语音是回忆录基础数据资产。ASR 原文、校正文、结构化记忆和最终文章都必须能回溯到原始音频、transcript version 和音频时间段。
- 小程序端不能保存或暴露 ASR/TTS/LLM provider key。开发验证阶段 provider key 统一放在 `backend/.env`，不要写入 tracked config、文档示例或小程序代码。

## Shared ECS Deployment Guardrails

- ECS `115.190.174.247` 是共享机器，不是 Pawtale 专用部署环境。第一原则：不要动已经存在的进程、Web 服务、项目目录或全局配置，除非用户明确要求。
- 推荐 SSH 连接方式：`ssh pawtale-ecs`。本机 `~/.ssh/config` 应将该 alias 指向 `root@115.190.174.247`，并使用 `~/.ssh/pawtale_codex_115_190_174_247`；原始连接方式是 `ssh root@115.190.174.247`。不要把服务器密码或私钥内容写入仓库或文档。
- 相关域名是 `spaceflows.cn`。当前已知 Caddy 映射：`www.spaceflows.cn -> 127.0.0.1:5173`，`dev-api.spaceflows.cn -> 127.0.0.1:8081`，`api.spaceflows.cn -> 127.0.0.1:8082`，`dev-admin.spaceflows.cn -> 127.0.0.1:9527`，`admin.spaceflows.cn -> 127.0.0.1:9728`。
- 已有服务包括 Caddy、Redis、PM2 管理的 `spaceflows-web`，以及 `/root/project/juju_find_*`、`/root/project/space-flows-web` 等项目。部署 Pawtale 时应使用独立目录、独立进程名、独立端口和可回滚配置。
- 修改 Caddy、PM2、systemd、防火墙、证书、DNS、全局 Node/Python 环境或已有项目之前，必须先说明影响范围并获得用户确认。
