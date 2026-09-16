# AGENTS.md

## Default Delivery Workflow

除非用户明确要求只做本地修改、不开 PR 或不 merge，提交类任务默认按以下流程交付：

1. 使用独立 `codex/*` 分支并保留任务所需的现有基线，严格按任务范围实现；修复 issue 时在 PR 中关联 issue。
2. push 到 `origin` 并创建指向 `main` 的 PR。
3. 在当前线程完成 code review，不另开线程或独立 agent；逐条处理 review 结论并更新 PR。
4. 按 Testing Strategy 运行与改动风险匹配的最小验证。
5. 仅在 review 已处理、所选验证通过且无阻塞问题时 merge。

如测试受环境阻塞，必须说明原因、影响和替代验证，等待用户确认后再决定是否 merge。最终汇报包含 PR 链接、实际执行的验证及结果、merge commit SHA；涉及部署或小程序更新时，还要包含目标、验证结果和对应 SHA。

## Implementation Principles

- 严格按用户确认的范围实现；不擅自增加功能、流程或页面，也不顺带移动、重排或改变既有入口和布局。
- 新增页面、组件、service、workflow、状态机或工具前，先搜索现有实现和调用链；语义相同的能力优先扩展或组合现有路径，不建立平行实现。
- 把净新增代码视为维护成本。替换实现时在同一改动中删除旧路径、兼容壳、死分支、重复配置和低价值测试；显著增加代码量时应说明现有能力为何不能复用。
- 抽象只有在能减少真实重复、隔离实际变化或提升可测试性时才引入，不为未来假设增加框架和样板代码。
- 小说、公众号和回忆录保持清晰产品边界；认证、用户、AI provider、存储、导出和 transport 等基础能力放在 shared 层，不为减少行数把不同业务语义塞进万能模型。
- 新增编辑或语音入口必须复用现有共享能力和完整状态机；缺失能力优先在共享层补齐，不复制不完整链路。
- 容错只处理真实可预期且有明确恢复路径的异常；跨模块和 API 边界使用显式类型与 schema，不用任意 JSON 承载核心语义。
- 每个 PR 聚焦一个目标，保持 diff 可 review，并删除本次改动取代的代码。

## Investigation And Root Cause

- 调查 bug、线上异常、真机问题或不稳定行为时，先建立可验证的因果链，不用重试、超时、吞错或宽泛兼容代替根因修复。
- 对 ASR/TTS/LLM、WebSocket、录音、播放、权限和后台切换等状态机问题，优先还原真实事件序列、provider payload、客户端状态迁移和原生日志。
- 尚未找到根因时必须明确说明已排除项、剩余假设和下一步证据；兜底只能保护数据、暴露失败或临时止血，不能包装成根因结论。

## Testing Strategy

- 默认从能证明本次改动的最小测试开始：单个回归用例、受影响测试文件或相关模块检查；不默认运行完整 backend、miniprogram 或 E2E 套件。
- 只有改动涉及共享基础设施、schema/迁移、依赖或配置、跨模块契约、发布边界，或用户明确要求时，才扩大到更广测试范围。
- 已有覆盖充分的纯重构不强制新增测试；真实 bug 只补能复现根因条件的最小回归样本，优先使用脱敏后的真实 payload、事件序列或数据形态。
- 测试断言用户可观察行为和稳定边界，不锁 WXML/WXSS 样式、文案、源码结构或未来字段。路由、隐私声明、配置或模板绑定本身是失败边界时才做轻量静态断言。
- UI、语音、权限、后台切换和播放体验仅在相关改动时做聚焦 smoke；不要为了自动化建设宽泛、缓慢的 E2E。删除或改写重复、脆弱、不能证明业务结果的测试。
- PR CI 已对同一提交运行完整套件时，不在本地机械重复；仅在代码、依赖或环境变化、CI 缺失或失败时补跑。

## Architecture Sources And Invariants

- 回忆录实时语音以 `docs/MEMOIR_VOICE_CHAT_ARCHITECTURE.md` 为准；事实、素材、访谈、成书、缓存、请求和模型上下文以 `docs/MEMOIR_INTERVIEW_AND_BOOK_ARCHITECTURE.md` 为准；物理数据结构和编辑入口见 `docs/MEMOIR_DATA_MODEL_AND_EDITING_GUIDE.md`。
- 后端数据库是事实源，本地缓存按用户和项目隔离；持久化结构变化时明确提升缓存 schema、迁移或失效，不宽泛合并未知旧数据。
- 原始音频和用户讲述必须可追溯；ASR 原文、校正文、结构化记忆和最终文章保留到原始音频、transcript version 和时间段的关联。
- provider key、微信 AppSecret 和 GitHub App private key 只存在于后端运行环境，禁止进入客户端、tracked 配置或文档示例。

## Post-Merge Deployment And Safety

- 合并后的 `backend/` 代码、依赖、配置模板、迁移或部署脚本变更，默认部署最新 `main` 到 Pawtale 后端；只操作 Pawtale 自己的目录、进程和端口。
- 后端启动只断言 runtime schema，不自动迁移。数据库结构或约束变化时，先备份，再在 `backend/` 显式运行 `PYTHONPATH=. alembic upgrade head`，然后重启并核对数据行数、约束和外键完整性。
- 部署后至少验证健康检查、OpenAPI/关键 API，以及本次改动相关的最小 smoke。
- 合并后的 `miniprogram/` 变更默认把本地小程序更新到 merged commit，并运行相关的最小类型检查和测试；除非用户明确要求，不自动调用微信开发者工具执行预览、上传、体验版、审核或发布。
- 删除、覆盖或恢复数据库及其备份前必须先确认，并先备份当前状态。
- ECS 是共享环境。修改其他项目、已有进程、Caddy、PM2、systemd、防火墙、证书、DNS 或全局运行环境前，必须说明影响并取得用户明确确认。
