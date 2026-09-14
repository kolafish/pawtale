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
- 后端启动只断言 runtime schema，不自动执行迁移。改动数据库结构、枚举 CHECK 约束或迁移代码时，备份后必须在 `backend/` 显式运行 `PYTHONPATH=. alembic upgrade head`，再重启并核对数据行数、约束和外键完整性。
- 部署后必须做基础验证，至少包括后端健康检查、关键 API/OpenAPI 可访问性，以及本次后端改动相关的最小 smoke test；最终汇报要包含部署目标、验证结果和已部署的 commit SHA。
- 如果本次 PR 改动包含 `miniprogram/` 下的小程序代码、配置或资源，且 PR 已成功 merge 到 `main`，默认继续把小程序项目更新到最新 `main` 对应代码，并重新执行小程序相关验证。
- 小程序代码更新至少要确保本地小程序项目使用已 merge 的 commit，并执行 `miniprogram` 的类型检查和相关测试。
- 小程序默认交付止于本地代码更新与验证。开发完成后不要自动调用微信开发者工具 CLI 或 UI 执行上传、预览、体验版、提交审核或发布；这些操作由用户手动完成。只有用户在当前任务中明确要求时才执行。
- 如果同一个 PR 同时改 `backend/` 和 `miniprogram/`，默认在 merge 后同时执行 ECS 后端部署和小程序代码更新/验证；最终汇报要分别列出后端部署结果和小程序更新结果。
- 如果 PR 只改文档、前端静态资源或非后端/非小程序模块，默认不自动部署 ECS，也不更新小程序，除非用户明确要求。

## Long-Term Code Quality Principles

- 长期迭代定位：这是一个需要持续演进的项目，默认以长期维护质量为先，代码要清晰、稳定、可继续扩展。
- 产品边界优先：小说、公众号、回忆录必须作为清晰的产品模块演进，不把不同业务强行塞进一个万能模型。
- 共享能力下沉：认证、用户、AI provider、存储、导出、API transport 等基础能力放在 shared 层，产品模块只表达自身业务。
- 抽象必须有真实收益：只有能减少重复、隔离变化、提升测试性的抽象才允许引入，注意抽象和实现的边界，避免为了“框架感”增加样板代码。
- 实现保持精简：删除无用代码，不保留兼容壳、死分支、临时绕路、胶水代码和未使用的配置，避免啰嗦实现。
- 新增编辑或语音功能前，先检查现有页面、组件和调用链，优先扩展、复用已有能力；已有页面能承担的流程不另写一套页面，新增页面须有明确业务差异。
- 语音复用必须保留完整体验，包括 ASR 实时转写、VAD 停顿自动结束、等待最终识别结果、手动结束和离页清理；缺失能力优先在共享组件补齐，让所有使用入口一起受益，避免各页面重复实现录音和识别逻辑。
- 开发沟通保持简短，开发计划默认说明关键实现和需要几个 PR 即可；用户未要求时不自动生成冗长设计文档或正式预估。
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
- 不为架构文档或开发约束本身增加静态扫描、源码正则、未来字段枚举或未来模型枚举测试；测试只覆盖当前功能、真实数据边界和可复现回归风险。
- UI 视觉、大字模式、滚动、播放进度、后台退出、麦克风权限等体验问题，单元测试只能覆盖状态逻辑；合入前应补充人工或自动化 smoke 验证，并在最终汇报说明是否执行。
- 删除低价值测试优先于维护它：如果一个测试只证明实现细节存在、不能复现真实问题、经常阻碍合理重排，就应删除或改写成业务行为/回归测试。

## Memoir Product Architecture Context

- 回忆录小程序实时语音架构以 `docs/MEMOIR_VOICE_CHAT_ARCHITECTURE.md` 为准。
- 回忆录事实来源、素材归属、项目级访谈、成书、缓存、请求和 AI 模型上下文统一以 `docs/MEMOIR_INTERVIEW_AND_BOOK_ARCHITECTURE.md` 为准；当前物理数据结构和编辑入口见 `docs/MEMOIR_DATA_MODEL_AND_EDITING_GUIDE.md`。
- 后端数据库始终是事实源；本地缓存必须按用户和项目隔离，持久化结构变化时明确提升缓存 schema、迁移或失效，不能宽泛合并未知旧数据。
- 小程序 HTTP 只使用共享 `telemetry / interactive / mutation / generation` timeout profile；后端文本模型只使用共享 `realtime / interactive / generation` profile。业务页面和业务 service 不得复制秒数。
- Provider timeout 必须短于后端操作、代理和客户端 deadline；超时后的生成结果属于未知，不能自动重复高成本调用。成本高、分多阶段、耗时长或不能安全重试的生成默认设计为可恢复的异步任务，并持久化阶段和进度。
- 后台任务总截止时间统一由后端 Settings 管理：通用编辑任务 60 分钟、分享视频 30 分钟、书稿导出 4 分钟；小程序媒体传输统一由 transport 管理，普通媒体 1 分钟、书稿导出 5 分钟。租约心跳不能替代任务总截止时间，业务代码不得复制这些数值。
- 超时或失败记录至少包含 requested model、resolved model、provider、timeout、elapsed 和 error type；用户看到明确中文状态，不能用空错误或英文兜底掩盖根因。
- 用户选择的 AI 模型必须沿客户端、API schema、workflow 和 provider 完整传递，并记录 requested / resolved model；不能因中间层漏传而静默换用默认模型。
- 产品形态是 `VAD + Streaming ASR + LLM Dynamic Follow-up + Streaming TTS`，以 voice session、chat message、realtime transcript、audio evidence 为核心。
- 创建/开始访谈时要区分 `free_narration` 弱引导和 `guided_interview` 强引导：弱引导支持叙述者按时间线自主讲述，强引导由 AI 按人物、事件、地点、时间段、主题等模块推进。
- 用户原始语音是回忆录基础数据资产。ASR 原文、校正文、结构化记忆和最终文章都必须能回溯到原始音频、transcript version 和音频时间段。
- 小程序端不能保存或暴露 ASR/TTS/LLM provider key。开发验证阶段 provider key 统一放在 `backend/.env`，不要写入 tracked config、文档示例或小程序代码。

## Shared ECS Deployment Guardrails

- ECS `115.190.174.247` 是共享机器，不是 Pawtale 专用部署环境。第一原则：不要动已经存在的进程、Web 服务、项目目录或全局配置，除非用户明确要求。
- 推荐 SSH 连接方式：`ssh pawtale-ecs`。本机 `~/.ssh/config` 应将该 alias 指向 `root@115.190.174.247`，并使用 `~/.ssh/pawtale_codex_115_190_174_247`；原始连接方式是 `ssh root@115.190.174.247`。不要把服务器密码或私钥内容写入仓库或文档。
- 相关域名是 `spaceflows.cn`。当前已知 Caddy 映射：`www.spaceflows.cn -> 127.0.0.1:5173`，`dev-api.spaceflows.cn -> 127.0.0.1:8081`，`api.spaceflows.cn -> 127.0.0.1:8082`，`dev-admin.spaceflows.cn -> 127.0.0.1:9527`，`admin.spaceflows.cn -> 127.0.0.1:9728`。
- 已有服务包括 Caddy、Redis、PM2 管理的 `spaceflows-web`，以及 `/root/project/juju_find_*`、`/root/project/space-flows-web` 等项目。部署 Pawtale 时应使用独立目录、独立进程名、独立端口和可回滚配置。
- 修改 Caddy、PM2、systemd、防火墙、证书、DNS、全局 Node/Python 环境或已有项目之前，必须先说明影响范围并获得用户确认。
