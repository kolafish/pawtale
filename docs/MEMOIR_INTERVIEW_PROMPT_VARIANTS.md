# 回忆录访谈 Prompt 版本

当前实时语音访谈支持三套可切换的 Prompt。默认版本是 `v3_improved`，也就是本轮真机测试使用的新 baseline。

| 设置名称 | 后端值 | 用途 |
| --- | --- | --- |
| v3 改进版（默认） | `v3_improved` | 在现有产品访谈协议上加入 grounded v3 的事实核对、故事落点和拒绝边界规则。 |
| v4 | `v4` | 使用新的回忆录访谈 system/user Prompt，强调倾听、顺着故事自然继续，并保留事实和业务边界。 |
| v3_plus | `v3_plus` | 使用 v3_plus 的 system/user Prompt，包含完整的控制标记、事实边界和故事展开规则。 |

三套版本都继续使用现有的访谈上下文、话题地图、检索到的历史讲述候选、资料提问、书稿概况、项目进度和策略约束。成书补访继续使用原有成书补访 Prompt，避免把自由访谈规则带入成书流程。

普通 AI 访谈运行时不再按“自由讲述 / 引导访谈”分支，统一由 `interview_type=ai_interview` 和 `session_goal` 决定行为。数据库里的 `guidance_mode` 仍保留，用于读取历史数据和兼容旧客户端；它不再决定话题地图、检索、策略或 Prompt 选择。

开发版和发布版目前都显示这个设置。选择保存在本机，后续每个实时访谈回合会发送 `prompt_variant`；后端也会把实际版本写入助手消息的 `metadata_json.prompt_variant`，便于复盘。

## Prompt 文件

- `backend/app/services/memoir/prompt_variants/grounded_v3.md`
- `backend/app/services/memoir/prompt_variants/v4_system.md`
- `backend/app/services/memoir/prompt_variants/v4_user.md`
- `backend/app/services/memoir/prompt_variants/v3_plus_system.md`
- `backend/app/services/memoir/prompt_variants/v3_plus_user.md`

如果后续结束真机对比，只需要移除小程序设置入口、客户端 `prompt_variant` 字段和对应后端版本分支；保留消息 metadata 或迁移前先确定历史复盘需求。
