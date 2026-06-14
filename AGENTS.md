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
- 如测试因环境问题无法正常执行，必须明确说明原因、影响和替代验证方式，再等待用户确认是否继续 merge。

## Default Git Delivery Preference

除非用户明确说明“只本地提交”“不要开 PR”“不要 merge”或有测试/环境阻塞，本仓库的提交类任务默认按以下交付：

1. 从当前改动创建独立 `codex/*` 分支。
2. push 到 `origin`。
3. 创建指向远端 `main` 的 PR。
4. 在测试/验证结论明确后 merge 到 `main`。
5. 最终汇报包含 PR 链接、测试或验证结果、merge commit SHA。

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

## Memoir Voice Chat Redesign Context

- 回忆录小程序新版主线文档是 `docs/MEMOIR_VOICE_CHAT_REDESIGN.md`，后续回忆录小程序改造默认以它为准。
- 旧版固定问题、整段录音上传、离线 ASR、整理稿确认链路只通过 commit `bd3599ad3dddf382b242c47071c856d382543cac` 追溯，不在文档里维护旧代码路径清单。
- 旧文档 `docs/MEMOIR_MINIPROGRAM_PRODUCT.md`、`docs/MEMOIR_MEMORY_FIRST_V1_PLAN.md`、`docs/MEMOIR_MEMORY_ARCHITECTURE.md` 仅作为 legacy 背景或长期原则参考。
- 新版产品形态是 `VAD + Streaming ASR + LLM Dynamic Follow-up + Streaming TTS`，以 voice session、chat message、realtime transcript、audio evidence 为核心，不再继续强化固定问答模型。
- 新版创建/开始访谈时要区分 `free_narration` 弱引导和 `guided_interview` 强引导：弱引导支持叙述者按时间线自主讲述，强引导由 AI 按人物、事件、地点、时间段、主题等模块推进。
- 用户原始语音是回忆录基础数据资产。ASR 原文、校正文、结构化记忆和最终文章都必须能回溯到原始音频、transcript version 和音频时间段。
- 小程序端不能保存或暴露 ASR/TTS/LLM provider key。开发验证阶段 provider key 统一放在 `backend/.env`，不要写入 tracked config、文档示例或小程序代码。
