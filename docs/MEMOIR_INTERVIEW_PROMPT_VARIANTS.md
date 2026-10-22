# 回忆录访谈 Prompt 版本

当前实时语音访谈支持三套可切换的 Prompt。默认版本是 `v3_improved`，也就是本轮真机测试使用的新 baseline。

| 设置名称 | 后端值 | 用途 |
| --- | --- | --- |
| v3 改进版（默认） | `v3_improved` | 在现有产品访谈协议上加入 grounded v3 的事实核对、故事落点和拒绝边界规则。 |
| 全新 Prompt | `standalone` | 使用独立的回忆录访谈原则，强调倾听、顺着故事和自然节奏。当前版本额外明确自称“小余”、称呼讲述者使用“您”。 |
| v3_plus | `v3_plus` | 使用 v3_plus 的 system/user Prompt，包含完整的控制标记、事实边界和故事展开规则。 |

三套版本都继续使用现有的访谈上下文、资料提问、历史讲述、书稿概况和策略约束。成书补访继续使用原有成书补访 Prompt，避免把自由访谈规则带入成书流程。

开发版和发布版目前都显示这个设置。选择保存在本机，后续每个实时访谈回合会发送 `prompt_variant`；后端也会把实际版本写入助手消息的 `metadata_json.prompt_variant`，便于复盘。

## Prompt 文件

- `backend/app/services/memoir/prompt_variants/grounded_v3.md`
- `backend/app/services/memoir/prompt_variants/standalone_system.md`
- `backend/app/services/memoir/prompt_variants/standalone_user.md`
- `backend/app/services/memoir/prompt_variants/v3_plus_system.md`
- `backend/app/services/memoir/prompt_variants/v3_plus_user.md`

如果后续结束真机对比，只需要移除小程序设置入口、客户端 `prompt_variant` 字段和对应后端版本分支；保留消息 metadata 或迁移前先确定历史复盘需求。
