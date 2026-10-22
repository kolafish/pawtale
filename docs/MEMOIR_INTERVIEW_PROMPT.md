# 回忆录访谈 Prompt

实时语音访谈统一使用 `v4` Prompt，不再提供开发版、发布版或客户端可切换的 Prompt 选项。

v4 继续使用现有的访谈上下文、话题地图、检索到的历史讲述候选、资料提问、书稿概况、项目进度和策略约束。成书补访继续使用原有成书补访 Prompt，避免把自由访谈规则带入成书流程。

普通 AI 访谈统一由 `interview_type=ai_interview` 和 `session_goal` 决定行为，数据库里的 `guidance_mode` 仅用于读取历史数据和兼容旧客户端，不再决定 Prompt 选择。

Prompt 文件：

- `backend/app/services/memoir/prompt_variants/v4_system.md`
- `backend/app/services/memoir/prompt_variants/v4_user.md`

助手消息仍会在 `metadata_json.prompt_variant` 中记录 `v4`，便于历史数据复盘；客户端不再发送或保存 Prompt 选择。
