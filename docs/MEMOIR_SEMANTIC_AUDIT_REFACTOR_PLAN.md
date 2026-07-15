# 回忆录语义审计与素材/章节数据重构计划

## 目的

本文档用于约束后续回忆录“素材卡片、章节卡片、LLM 内容审计、审计展示优化、章节素材关系结构化”的连续 PR。执行时以这里的边界为准，避免只做表面展示优化，或在后续 PR 中偏离最初的数据结构目标。

本文记录的素材卡、章节卡、审计快照和 SourceLink 重构已经完成，继续作为现有派生语义基础。后续“事实命题、跨访谈故事、章节契约、持久缺口、补访任务和正文块”不再追加到本历史计划，统一由 [回忆录叙事架构与成书质量演进方案](MEMOIR_NARRATIVE_ARCHITECTURE.md) 约束。两者职责边界是：本文对象仍是可重建卡片、快照和来源投影；新文档定义可被用户确认并长期演进的叙事领域数据。

本计划不是为了重构而重构。现有事实是：

- 原始语音、转写、音频、对话消息已经有独立事实层。
- 成书章节通过 `MemoirBookPlanChapter.source_refs_json` 引用素材。
- 新增素材、删除素材、待补写等状态部分塞在 `MemoirBookPlanChapter.metadata_json`。
- `MemoirAutomationEvent.summary_text` 是事件展示摘要，不是长期语义状态。
- 当前没有稳定的一等“素材摘要”或“章节摘要”。

因此重构目标不是合并所有表，而是把职责拆清：

```text
MemoirVoiceUtterance          # 原始素材事实
MemoirSourceCard              # 单条素材语义卡片，派生缓存
MemoirChapterSourceLink       # 章节和素材的结构化关系
MemoirBookChapterDraftVersion # 章节正文版本
MemoirChapterCard             # 单章语义状态，派生缓存
MemoirBookQualityAuditSnapshot# 项目/章节审计快照
MemoirAutomationEvent         # 用户可见事件和提醒
```

## 不变量

所有 PR 必须遵守：

- 原始素材事实层不能被摘要或审计结果替代。原始音频、ASR 原文、校正文、转写版本和音频时间段仍是最终证据源。
- 语义卡片和审计快照都是派生数据，必须能通过 `input_signature`、`source_text_hash` 或版本 ID 判断是否过期。
- LLM 审计失败不能阻塞 ASR、访谈、自动归章、章节补写和用户手动编辑。
- 审计和卡片不得凭空新增人物、地点、事件或情绪判断；所有关键事实必须能回溯到素材或章节正文。
- `AutomationEvent` 只做展示提醒，不承载长期语义状态。
- 不提前删除 `source_refs_json`。在 link 表完全接管读路径并稳定前，保持兼容。

## 数据职责判断

### 保留为事实源

- `MemoirVoiceSession`：一次访谈会话。
- `MemoirChatMessage`：对话流消息。
- `MemoirVoiceUtterance`：可追溯的语音素材片段。
- `MemoirTranscriptVersion`：转写版本。
- `AudioFile`：音频文件。
- `MemoirBookChapterDraftVersion`：章节正文版本。
- `MemoirBookOutputVersion`：全书版本快照。

### 逐步收敛职责

- `MemoirBookPlanChapter.source_refs_json`：当前是章节素材关系主载体，后续降级为兼容/快照字段。
- `MemoirBookPlanChapter.metadata_json.pending_source_utterance_ids`：后续由 link 表状态或 `merged_draft_version_id` 计算。
- `MemoirBookPlanChapter.metadata_json.removed_source_utterance_ids`：后续由 link 表 `status=removed` 和 `removed_at` 表达。
- `MemoirAutomationEvent.metadata_json.chapter_items`：后续只保留 snapshot 引用和展示字段，不保存大体量审计详情。

## 目标数据结构

### MemoirSourceCard

素材卡片是一条 `MemoirVoiceUtterance` 当前有效文本的结构化理解。

建议字段：

```text
id
project_id
utterance_id
transcript_version_id
source_text_hash
engine                 # rules_v1 / llm_source_card_v1
status                 # ready / stale / failed
card_json              # 结构化素材卡片
error_message
created_at
updated_at
```

`card_json` 建议结构：

```json
{
  "short_title": "外婆家的厨房",
  "summary": "讲述者回忆小时候在外婆家厨房帮忙，看见外婆做饭。",
  "people": ["外婆", "我"],
  "places": ["外婆家厨房"],
  "time_hints": ["小时候"],
  "event_tags": ["做饭", "家庭生活"],
  "emotion_tags": ["亲近", "怀念"],
  "key_facts": [
    {"text": "小时候常在外婆家厨房帮忙", "evidence_quote": "..."}
  ],
  "open_questions": ["当时厨房是什么样子？"]
}
```

### MemoirChapterSourceLink

章节素材关系表表达一条素材和一个章节之间的关系，以及它是否已经并入章节正文。

建议字段：

```text
id
project_id
plan_id
chapter_id
utterance_id
transcript_version_id
audio_file_id
start_ms
end_ms
quote
status                 # active / removed
assigned_by            # ai / user / system
assignment_confidence
assignment_reason
source_card_id
merged_draft_version_id
removed_at
created_at
updated_at
```

核心口径：

- 活跃素材：`status=active`。
- 待补写素材：`status=active AND merged_draft_version_id IS NULL`，或 `merged_draft_version_id` 早于当前最新章节正文版本。
- 已删除素材：`status=removed`。

### MemoirChapterCard

章节卡片是单章当前语义状态，不是成书导语。

建议字段：

```text
id
project_id
plan_id
chapter_id
draft_version_id
input_signature
engine                 # rules_v1 / llm_chapter_card_v1
status                 # ready / stale / failed
card_json
error_message
created_at
updated_at
```

`card_json` 建议结构：

```json
{
  "summary": "本章围绕外婆家的童年生活，已有厨房、院子、剥青豆等片段。",
  "covered": {
    "time": ["小时候", "夏天"],
    "places": ["外婆家", "院子"],
    "people": ["外婆", "我"],
    "events": ["摘菜", "剥青豆"]
  },
  "gaps": [
    {"type": "event_sequence", "severity": "medium", "message": "事件前后经过还不连贯"}
  ],
  "next_questions": ["那次剥青豆是怎么开始的？"],
  "source_card_ids": ["..."],
  "evidence_refs": [
    {"utterance_id": "...", "quote": "..."}
  ]
}
```

### MemoirBookQualityAuditSnapshot

审计快照保存一次规则审计或 LLM 语义审计的完整结果。

建议字段：

```text
id
project_id
engine                 # rules_v1 / llm_semantic_v1
input_signature
overall_status
summary_text
display_json
primary_action_json
chapter_audits_json
source_card_ids_json
chapter_card_ids_json
error_message
created_at
updated_at
```

事件层只引用 snapshot：

```json
{
  "source": "book_quality_audit",
  "snapshot_id": "...",
  "display": {
    "title": "《院子里的童年》有新内容待写入",
    "summary": "有 1 条新讲述还没有补进正文。",
    "detail": "建议先补写本章，之后再继续追问外婆、夏天和剥青豆的细节。"
  },
  "primary_action": {
    "type": "open_chapter_workspace",
    "chapter_id": "...",
    "cta_text": "整理本章"
  }
}
```

## PR 拆分

### PR 1：审计展示契约

目标：在不改底层模型的前提下，让当前审计事件变成用户可理解的成书整理建议。

范围：

- `book_quality_audit.py` 增加 `display`、`primary_action`、`primary_chapter` 输出。
- `automation_events.metadata_json` 写入展示字段。
- 小程序成书页优先展示 `display.title`、`display.summary`、`display.detail`、`primary_action.cta_text`。
- 保留旧字段 fallback。

验收：

- 不接 LLM。
- 当前审计结果不再只显示“章节审计完成”。
- 至少覆盖 `needs_plan`、`needs_sources`、`needs_updates`、`needs_drafts`、`collecting`、`healthy` 的展示文案。
- 后端和小程序测试覆盖新旧字段兼容。

禁止：

- 不新增长期数据表。
- 不改 source refs 读写路径。

### PR 2：新增语义卡片和审计快照模型

目标：落地派生数据表，但不接主流程。

范围：

- 新增 `MemoirSourceCard`、`MemoirChapterCard`、`MemoirBookQualityAuditSnapshot` 模型和迁移。
- 新增基础 schema 和 repository/helper。
- 增加幂等 upsert 或 create/reuse 工具函数。

验收：

- migration 测试通过。
- 表约束覆盖 project/chapter/utterance 基本索引。
- 不改变现有 API 行为。

禁止：

- 不在本 PR 调 LLM。
- 不让成书、归章、补写依赖新表。

### PR 3：素材卡片生成服务

目标：给单条素材生成结构化语义卡片。

范围：

- 新增 `source_cards.py`。
- 新增 `generate_or_reuse_source_card`。
- 增加后台 job 或服务入口，用于新素材和转写编辑后生成/刷新卡片。
- 增加 fake AI 测试，校验 JSON schema、证据 quote 和过期判断。

验收：

- 卡片带 `source_text_hash`。
- 原文未变时复用卡片。
- 原文变化时生成新卡片或标记旧卡片 stale。
- AI 失败只记录 failed/stale，不影响原流程。

禁止：

- 不要求所有素材都有卡片才能归章。
- 不改章节规划/补写主流程。

### PR 4：章节卡片生成服务

目标：基于 source cards、章节标题和最新章节正文生成章节语义状态。

范围：

- 新增 `chapter_cards.py`。
- 新增 `generate_or_reuse_chapter_card`。
- `input_signature` 覆盖章节标题、素材卡片签名、活跃素材列表、最新 draft version。
- 输出章节摘要、覆盖维度、缺口、下一轮追问、证据 refs。

验收：

- 没有 source card 时可以 fallback 到 source quote/raw text。
- 签名不变时复用章节卡片。
- 草稿版本、章节标题或素材关系变化会使卡片过期。
- 测试覆盖高/中/低缺口和 fallback。

禁止：

- 不让章节卡片直接修改章节正文。
- 不把章节导语 `intro` 当章节卡片 summary。

### PR 5：审计快照和 LLM 语义审计

目标：接入真正内容审计，但基于卡片和必要证据，不全量无脑塞原文。

范围：

- `book_quality_audit.py` 拆规则审计和语义审计。
- 规则审计先写 `rules_v1` snapshot。
- 对 high/medium 章节确保 chapter card，然后生成 `llm_semantic_v1` snapshot。
- `AutomationEvent` 只保存 snapshot 引用和展示字段。

验收：

- LLM 审计输出 `chapter_audits`、`gaps`、`next_questions`、`evidence_refs`。
- LLM 失败时降级到规则审计 snapshot。
- 重复输入通过 `input_signature` 复用 snapshot。
- 后端测试覆盖成功、复用、失败降级。

禁止：

- 不让审计直接改正文。
- 不要求一次审完整本书所有章节。

### PR 6：上层调用切换到语义审计结果

目标：让小程序和语音追问使用审计快照/章节卡片。

范围：

- 小程序成书页展示 `audit_snapshot.display` 和 `primary_action`。
- `voice_followup_ai.py` 优先使用 `chapter_card.next_questions` 或 `audit_snapshot.next_questions`。
- 成书页展示“建议补问/建议补写/待整理素材”。

验收：

- 小程序旧事件仍可展示。
- 语音追问不会机械重复审计文案。
- 有审计结果时，AI 追问能自然使用缺口提示。
- 小程序测试覆盖新卡片字段和 fallback。

禁止：

- 不把 LLM 审计建议当硬性脚本。
- 不在小程序端保存 provider key 或做 LLM 审计。

### PR 7：章节素材关系表双写

目标：引入 `MemoirChapterSourceLink`，但仍保持旧 `source_refs_json` 兼容。

范围：

- 新增 link 表和 backfill 服务。
- 自动归章、手动移动、删除素材、确认归属时双写 `source_refs_json` 和 link 表。
- 记录 `assigned_by`、`confidence`、`assignment_reason`、`status`。

验收：

- 从现有 `source_refs_json` 回填 links 幂等。
- 自动归章、手动移动、删除素材测试覆盖双写一致性。
- 不改变现有 API response。

禁止：

- 不在本 PR 切主读路径。
- 不删除 `source_refs_json`。

### PR 8：服务层读路径切到 SourceLink 抽象

目标：统一章节素材读取口径，逐步让 link 表成为主数据源。

范围：

- 新增 `book_source_links.py` repository/service。
- 暴露统一接口返回当前 `MemoirBookChapterSourceRef` 形状。
- 优先读 link 表，缺失 fallback 到 `source_refs_json`。
- 切换覆盖率、章节草稿生成、自动补写 pending 计算、进度快照、规则审计读取路径。

验收：

- link 表和旧 JSON 混合数据能正确工作。
- pending/removed/source count 口径和旧行为一致或有明确迁移说明。
- 相关后端测试全部更新到新抽象。

禁止：

- 不一次性删除旧 JSON 写入。
- 不让业务层直接散落查询 link 表。

### PR 9：历史数据回填和检查工具

目标：给已有项目安全补齐 link/card/snapshot 相关数据。

范围：

- `backend/scripts/backfill_memoir_chapter_source_links.py`
- `backend/scripts/backfill_memoir_source_cards.py`
- `backend/scripts/backfill_memoir_chapter_cards.py`
- `backend/scripts/inspect_memoir_semantic_state.py`

验收：

- 支持 dry-run。
- 支持按 project_id 限定。
- 输出变更统计和失败原因。
- 不默认自动大规模执行。

禁止：

- 不在启动时自动跑大规模 LLM backfill。
- 不覆盖用户编辑的转写或正文。

### PR 10：旧路径收敛和清理

目标：在新路径稳定后清理旧职责，减少双轨维护成本。

范围：

- 停止依赖 `pending_source_utterance_ids` 和 `removed_source_utterance_ids` 作为主状态。
- `source_refs_json` 降级为兼容字段或 API 派生字段。
- 清理旧字符串拼接审计展示逻辑。
- 文档更新实际数据职责。

验收：

- 所有读路径已通过 SourceLink 抽象。
- 旧项目 fallback 仍可工作，或迁移脚本已明确执行。
- 测试覆盖无 `source_refs_json`、仅 link 表数据的核心流程。

禁止：

- 不删除 `BookOutputVersion.chapter_snapshot_json` 中的 source refs。那是历史书稿快照，不是冗余主数据。
- 不删除事件历史。

## 执行检查清单

每个 PR 开始前必须检查：

- 本 PR 是否只做本节范围内的事情。
- 是否有明确 fallback，不破坏现有成书流程。
- 是否新增或更新了测试覆盖。
- 是否更新了本文档中对应 PR 状态。

每个 PR 完成时必须在 PR 描述中列出：

- 本 PR 对应的计划编号。
- 完成项。
- 未做项及原因。
- 测试命令和结果。
- 是否影响后端部署或小程序验证。

## 当前状态

- PR 1：已完成。审计展示契约已落地，后端输出并写入 `display`、`primary_action`、`primary_chapter`，小程序成书页已优先展示这些字段并保留旧事件 fallback。
- PR 2：已完成。新增 `MemoirSourceCard`、`MemoirChapterCard`、`MemoirBookQualityAuditSnapshot` 派生表、基础 schema、幂等 upsert helper 和迁移/索引约束测试；暂不接入 LLM 或现有成书主流程。
- PR 3：已完成。新增素材卡片生成服务、`generate_or_reuse_source_card`、后台 job 入口、source text hash 复用/过期逻辑和 fake AI 测试；暂不要求归章或补写依赖素材卡片。
- PR 4：已完成。新增章节卡片生成服务、`generate_or_reuse_chapter_card`、章节输入签名、source card 优先和原文 fallback、过期/失败记录以及 high/medium/low 缺口测试；暂不让章节卡片修改正文或接管主流程。
- PR 5：已完成。规则审计写入 `rules_v1` snapshot；显式开启语义审计时会确保 high/medium 章节的 chapter card 并生成/复用 `llm_semantic_v1` snapshot；LLM 失败降级到规则 snapshot，事件只保存 snapshot 引用和展示字段。
- PR 6：已完成。小程序成书页优先消费 `audit_snapshot.display` / `primary_action`，并展示“建议补问 / 建议补写 / 待整理素材”；语音追问 prompt 优先使用章节卡片 `next_questions`，再降级到审计快照或旧事件字段，且明确建议只作为可改写方向。
- PR 7：已完成。新增 `MemoirChapterSourceLink`、迁移表清单和 `chapter_source_links.py` 幂等 backfill/sync 服务；确认/更新章节规划、自动归章、手动分配、素材移动、结构命令、转写编辑和删除素材会继续写旧 `source_refs_json`，同时双写或同步 link 表并记录 `assigned_by`、confidence、reason 和 active/removed 状态；读路径仍保持旧 JSON。
- PR 8：已完成。新增 `book_source_links.py` 统一读取抽象；计划响应、素材覆盖率、章节草稿生成、章节卡片、进度快照/规则审计、自动归章路由、转写收录展示、语音修正上下文和全书稿快照生成均优先读取 active link，并在缺少 link 记录时按章节/素材回退旧 `source_refs_json`；`pending_source_utterance_ids` / `removed_source_utterance_ids` 仍作为当前补写状态 metadata 保留到 PR 10 收敛。
- PR 9：已完成。新增章节素材 link 回填、素材卡片回填、章节卡片回填和语义状态检查脚本；所有回填脚本支持 `--project-id`、默认 dry-run、输出 JSON 统计和失败原因，素材/章节卡片脚本必须显式 `--execute` 才会调用 LLM provider，不会在启动时自动执行大规模回填。
- PR 10：已完成。收敛 `pending_source_utterance_ids` / `removed_source_utterance_ids` 主读口径到 `MemoirChapterSourceLink.merged_draft_version_id`、`status` 和 `removed_at`；`source_refs_json` 保留为写兼容和旧项目 fallback，不删除历史书稿快照或事件历史。

## 后台编辑优化拆分

前 10 个 PR 已完成底层数据结构和主读路径收敛。下一阶段不继续大改模型，而是让 AI 后台编辑链路主动使用这些结构。

### 后台编辑优化 PR 1：后台自动语义审计默认化

目标：自动归章、自动补写等后台链路触发质量审计时，默认进入 LLM 语义审计路径，并在失败时降级到规则审计。

范围：

- `schedule_project_quality_audit` 写入 `semantic_audit` 和可选 `ai_model` payload。
- `run_book_quality_audit_job` 从 job payload 构造 `MemoirBookQualityAuditRunRequest`。
- job result 写入实际 `snapshot_engine`，方便确认本次后台审计使用规则还是语义 snapshot。
- 测试覆盖默认语义审计和显式关闭语义审计。

状态：本 PR 完成。

### 后台编辑优化 PR 2：素材卡片事件驱动刷新

目标：让新素材、转写校正和素材文本变化后自动生成或刷新 `MemoirSourceCard`，并把可用卡片绑定到章节素材关系。

范围：

- ASR ready、转写校正、手动文本编辑后排 `GENERATE_SOURCE_CARD`。
- source card ready 后回填 active `MemoirChapterSourceLink.source_card_id`。
- 不要求 source card 成功才能归章、补写或用户编辑。

状态：本 PR 完成。

### 后台编辑优化 PR 3：卡片驱动自动归章

目标：自动归章时优先使用 source card 和 chapter card 的结构化语义，提高路由稳定性。

范围：

- route source 前生成或复用当前素材 source card。
- 候选章节摘要优先使用 chapter card，缺失时 fallback 到旧 digest。
- 保持现有 confidence threshold 和 hold-for-review 逻辑。

状态：本 PR 完成。

### 后台编辑优化 PR 4：卡片驱动章节补写和删除素材改稿

目标：章节自动改稿使用 source card/chapter card 的关键事实和缺口，并补上 deletion-only revision。

范围：

- 章节 draft prompt 增加 source card 的关键事实、时间、地点、人物和 open questions。
- revision 根据 SourceLink change state 区分新增素材、素材文本变化、删除素材。
- 删除素材后可自动生成移除过期内容的修订稿，但不直接覆盖用户确认稿。

状态：本 PR 完成。

### 后台编辑优化 PR 5：后台语义状态检查和追问建议

目标：给后台和小程序提供可解释的语义状态入口。

范围：

- 增加项目级 semantic state inspector，展示 source/chapter/audit snapshot 的 ready/stale/failed 状态。
- 从 latest chapter cards 和 audit snapshot 汇总建议追问。
- 只产出建议和事件，不直接改正文。

状态：本 PR 完成。
