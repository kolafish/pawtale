# 回忆录叙事架构与成书质量演进方案

## 文档状态

本文档是 Pawtale 回忆录“事实整理、故事构建、章节结构、补访规划、正文生成和全书审校”的主设计文档。

- 状态：叙事数据基线已落地；2026-07 起按本文“完整度与成稿语义修订”继续演进。
- 适用范围：新版回忆录项目的叙事领域层、成书链路和成书驱动访谈。
- 不取代：实时录音、Streaming ASR、LLM/TTS 对话状态机等语音基础能力。
- 上游文档：`docs/MEMOIR_VOICE_CHAT_REDESIGN.md`。
- 下游规划：`docs/MEMOIR_STRUCTURED_INTERVIEW_AND_BOOK_COMPLETION_PLAN.md`，负责项目级结构化访谈框架与成书补全收敛；本文继续约束共同事实底座和成书质量不变量。
- 历史基础：`docs/MEMOIR_SEMANTIC_AUDIT_REFACTOR_PLAN.md` 已完成的素材卡、章节卡、SourceLink 和审计快照重构。
- 执行原则：如果实现发现本设计不成立，必须先在对应 PR 中修改本文档、说明证据和影响，再调整实现；不能通过临时字段、兼容分支或提示词补丁绕开设计问题。

原 6 阶段、12 PR 计划保留为历史实施记录，不再是当前切流依据。当前实现和 code review 以第十八章的 4 PR 修订计划及本文业务不变量为验收依据。

## 一、问题定义

当前系统已经能够完成录音、实时转写、素材整理、章节规划、逐章生成和全书导出，但最终章节仍容易呈现为“按素材依次拼接的整理稿”，与人工撰写回忆录的标准存在明显差距。

主要问题不是单次 LLM 能力不足，而是当前中间数据缺少稳定的叙事语义：

- 章节只有标题、顺序和素材引用，没有明确目标、读者收获、时间范围和核心问题。
- 单条素材可以提取人物、地点、时间和事实，但不同素材中的同一人物、同一事件和矛盾说法没有稳定身份。
- 章节事件聚类依赖当前章节和输入签名，是派生快照，不是能跨多次访谈持续补充的故事。
- 章节缺口和追问建议是每次审计重新生成的 JSON，没有稳定 ID、处理状态和用户决策。
- 正文版本是一个整章长字符串，无法表达段落作用、事实来源、局部锁定和局部重写。
- 当前章节生成强调覆盖每条来源，容易优化成“素材都写到了”，而不是“章节讲清楚了一件事”。

因此目标不是再建设一个更大的文章生成 Prompt，而是建立以下长期链路：

```text
原始录音
 -> 可追溯口述片段和转写版本
 -> 可核对事实命题
 -> 跨访谈故事单元
 -> 叙事槽位和持久缺口
 -> 章节契约和结构节点
 -> 可追溯正文块
 -> 全书一致性检查和正式书稿
```

## 二、产品目标与非目标

### 2.1 产品目标

完成本计划后，产品必须具备以下能力：

1. 同一件事可以在几天、几周或几个月内通过多次访谈逐步补充，不会被拆成多个孤立素材。
2. 人物、时间、地点、关系和事实具有稳定身份、不确定性、确认状态和证据来源。
3. 每章先明确写作目标和结构，再选择故事和生成正文，不再直接按素材列表生成整章。
4. 系统能够明确指出缺失的是哪一个故事的哪个部分，并允许用户选择补充、记不清、接受不完整或不收录。
5. 成书驱动访谈能够消费持久补访任务，新口述完成后自动回填目标故事并重新判断缺口。
6. 正文按结构节点和段落块保存，已确认内容可以锁定，新增素材只影响相关块。
7. 每个事实性正文块都能回溯到 Claim、转写版本、口述片段和原始音频。
8. 正式成书前能够检查时间顺序、人物关系、因果缺失、重复内容、前后矛盾和无来源事实。

### 2.2 非目标

本计划明确不做以下事情：

- 不重写 RecorderManager、Streaming ASR、语音网关、实时 TTS 或现有抢占状态机。
- 不删除多人语音转写、人工访谈、单条素材离线双模型 ASR 和方言增强；它们继续产生可追溯音频/转写输入。
- 不恢复旧版固定问题和整段录音上传链路。
- 不建设多 Agent 框架；每个 AI 步骤由明确输入、结构化输出和确定性校验组成。
- 不在第一阶段引入图数据库。
- 不在叙事模型落地的同时迁移 PostgreSQL。
- 不让 LLM 自动确认有争议的年份、人物关系或事实。
- 不把“完整度百分比”作为成书质量结论。
- 不为了保持旧技术路径长期存在而维护永久双写和永久 fallback。
- 不要求素材少的故事被 AI 扩写成完整故事；信息不足必须显式保留为缺口。

## 三、现有能力的保留与收敛

### 3.1 继续保护的事实源和历史产物

以下对象继续保留并作为不可绕过的证据基础：

```text
AudioFile                         # 原始音频和文件元数据
MemoirVoiceSession                # 一次语音会话
MemoirChatMessage                 # 对话消息流
MemoirVoiceUtterance              # 口述片段、音频和时间范围
MemoirTranscriptVersion           # ASR、方言校正、人工编辑和最终转写版本
MemoirImageAsset                  # 原始上传或派生图片资产，只有原始上传可以成为事实证据
```

原始音频、ASR 原文和历史转写版本不能被事实提取、故事整理或成书结果覆盖。转写编辑产生新版本后，旧版本仍用于历史追溯。

`MemoirBookChapterDraftVersion` 和 `MemoirBookOutputVersion` 是必须保护的历史写作产物，但不是原始事实源。它们只能引用事实证据，不能反向成为新 Claim 的唯一证据。

### 3.2 继续作为派生缓存

以下对象可以复用，但不能成为用户确认语义的唯一载体：

```text
MemoirSourceCard                  # 单条素材的结构化提取缓存
MemoirChapterCard                 # 当前章节输入下的语义摘要缓存
MemoirBookQualityAuditSnapshot    # 某次输入下的规则/语义审计快照
MemoirAutomationEvent             # 用户可见的后台处理事件
```

约束：

- 缓存必须可以通过输入签名、转写版本或 engine version 判断过期。
- 缓存失败不得破坏原始素材，但失败必须可观察，不能写入未经验证的业务真相。
- `MemoirAutomationEvent` 只表达处理进度和用户动作提醒，不承载长期缺口、确认状态或故事状态。

### 3.3 逐步降级或删除的旧职责

```text
MemoirChapterSourceLink           # 迁移期间保留为来源投影，不再作为目标叙事主关系
MemoirBookPlanChapter.source_refs_json
                                   # 只保留历史快照/迁移输入，不再驱动新成书主链路
MemoirBookChapterDraftVersion.content
                                   # 迁移后作为组装快照，不再是唯一可编辑正文真相
```

目标主关系必须收敛为：

```text
ChapterNode -> Story -> Claim -> ClaimEvidence -> TranscriptVersion / Utterance / AudioFile
```

## 四、架构原则和业务不变量

所有实现 PR 必须维持以下不变量。

### 4.1 证据不变量

- 事实命题至少包含一条有效 `ClaimEvidence` 才能进入故事和正文生成上下文。
- evidence quote 必须来自指定转写版本；不能只存在于 LLM 摘要中。
- 音频存在时，evidence 尽量保留 `audio_file_id`、`start_ms` 和 `end_ms`。
- AI 不能修改原始 evidence；人工修正通过新转写版本和新 Claim 版本表达。
- 来源版本变化时，旧 Claim 不删除，标记为 superseded 或 needs_review，并重新计算依赖状态。
- 已被 ClaimEvidence、历史 DraftBlockVersion 或正式书稿引用的 transcript version 必须被 pin，不能继续按普通保留窗口裁剪。
- 实时 ASR、离线双模型 ASR、方言校正和人工编辑只通过新增/选择有效 transcript version 改变叙事输入；后台增强不能覆盖人工编辑版本。
- ready/confirmed Claim 的 statement、实体关系和时间内容不可原地改写；事实修正创建新 Claim，并用 `superseded_by_claim_id` 保留因果链。verification/publication 状态可以受控更新，但必须写审计记录。

### 4.2 不确定性不变量

- “确定程度”“人工核实状态”“是否允许收录”是三个独立维度，不能合并成一个 status。
- “1972 年左右”“记不清具体年份”和“家人说法不同”必须是正式结构化数据。
- 未确认的时间和关系可以进入草稿，但必须在审校和 UI 中明确显示，不能被改写成确定事实。
- 用户选择“记不清”或“可以接受不完整”后，系统不得永久重复追问同一问题。

### 4.3 故事不变量

- Story 是项目级长期对象，不依附单次 voice session 或某一章。
- 同一 Story 可以关联多次访谈、多个 Claim 和多段音频。
- 一条素材可以为多个 Story 提供不同 Claim，但不能因为章节不同复制出多个相同 Story。
- AI 合并 Story 只能生成建议；低置信度合并必须进入 review，不能静默覆盖。
- StorySlot 和 `completion_status` 用于描述还可以补充什么、安排后续访谈，不是正文生成或正式成书的硬门槛。
- Story 只要处于 active、允许收录、没有 disputed/rejected，并且至少有一条可追溯的有效 Claim，就可以支持草稿生成；不要求用户逐条确认 Story。
- `completion_status` 是由当前 StorySlot 计算并持久化的查询投影；重算不得覆盖槽位上的人工 resolution，也不得把“缺少固定槽位”解释为“已有内容不可成书”。

### 4.4 章节不变量

- Chapter 在生成正文前必须有当前章节方向和至少一个 ChapterNode；当前方向可以是系统生成的 draft，不要求用户先理解或确认内部 ChapterContract。
- 章节结构节点只能通过 Story 引用事实，不能重新建立独立事实副本。
- 一个 Story 可以服务多个章节，但每个节点必须说明该 Story 在本章的叙事作用。
- 章节标题变化不能隐式修改目标、时间范围和必须覆盖内容。
- 正式章节不得存在未解决的 high correctness gap；缺少细节、固定槽位和一般丰富度建议不阻塞已有正文成书。

### 4.5 正文不变量

- 每个事实性 DraftBlock 必须关联 Story 和 Claim。
- 仅承担转场、节奏或总结作用的块可以没有新增 Claim，但必须标记 block role，且不能引入新事实。
- 锁定块不能被后台任务、整章重写或全书润色修改。
- 新素材只使依赖相关 Story/Claim 的块 stale，不得默认使整章全部失效。
- 组装后的整章字符串是输出快照，不是唯一编辑真相。

### 4.6 多项目与隐私不变量

- 所有 Entity、Claim、Story、Gap、Task、ChapterNode 和 DraftBlock 查询必须以 `project_id` 隔离。
- 私密或排除收录的 Claim 不得进入正文生成、补访提示或分享内容。
- `eligible` 表示可进入成书；`excluded` 表示保留在项目中但不进入成书和补访；`private` 还要求限制普通家人视图、检索、分享和导出。
- Claim 级限制只排除该 Claim，不能因为一个私密细节自动隐藏整个 Story；Story 级限制向该 Story 的补访、摘要和正文上下文传播。
- DraftBlock 只能引用 eligible Claim；生成上下文必须先做 publication 过滤，再构建摘要和 Prompt。
- provider key 继续只存在后端环境变量中。

### 4.7 审计不变量

- 所有可被用户确认、修改、合并、排除或锁定的领域对象都必须记录 `created_by/updated_by` 和可用时的 `actor_user_id`。
- AI、system 和 user 行为使用受控 actor type；不能只从自由文本 reason 推断操作来源。
- 用户状态变更写入 append-only `MemoirNarrativeDecision` 或聚合专用 version/revision，当前表保存最新查询状态，二者职责分开。
- 现有 `AuditEvent` 是客户端产品使用分析，`MemoirAutomationEvent` 是后台进度展示，二者都不能替代领域决策记录。
- 后台重算不能把用户状态更新伪装成 system 或 AI 创建的新结论。

## 五、目标领域模型

### 5.1 Entity：稳定人物、地点和机构

`MemoirNarrativeEntity` 用于解决“老李”“李主任”“李某某”是否是同一人，以及地点/机构名称变体问题。

第一版目标字段：

```text
id
project_id
entity_type             # person / place / organization
canonical_name
aliases_json            # 仅保存名称变体，不保存核心状态
description
verification_status     # unreviewed / confirmed / needs_verification / rejected
merged_into_entity_id   # 实体合并后指向保留实体
created_by              # ai / user / system
created_at
updated_at
```

约束：

- 叙述者本人也应在每个项目中有稳定 person entity。
- 人物关系不在 Entity 上用自由文本保存，而是由带 evidence 的 Claim 表达。
- 实体合并不能删除历史 ID；旧 ID 通过 `merged_into_entity_id` 可追溯。
- aliases 只用于检索和候选匹配，不能作为确认同一人的充分证据。

### 5.2 Claim：可核对事实命题

`MemoirClaim` 是可以确认、驳回、产生冲突和被正文引用的最小事实单元。

第一版目标字段：

```text
id
project_id
claim_type              # event / relationship / attribute / chronology / reflection
statement               # 面向用户的事实描述
subject_entity_id
predicate_code          # 受控词表；无法结构化时允许 narrative_fact
object_entity_id
object_text
place_entity_id
time_text               # 原始时间表达，例如“七二年前后”
time_start
time_end
time_precision          # exact / day / month / year / period / relative / unknown
time_anchor_claim_id    # “两年后”等相对时间所依赖的 Claim
certainty               # exact / approximate / uncertain
verification_status     # unreviewed / confirmed / needs_verification / disputed / rejected
publication_status      # eligible / private / excluded
conflict_group_id
superseded_by_claim_id
created_by
created_by_user_id
created_at
updated_at
```

字段原则：

- 核心查询、状态和关系使用明确列，不塞进任意 JSON。
- 不同 `claim_type` 的低频扩展字段可以进入带 `schema_version` 的 JSON，但必须由 Pydantic schema 校验。
- 时间归一化保留原始表达；`time_start/time_end` 表示按 precision 归一化后的包含区间，归一化失败不能丢弃 `time_text`。
- 相对时间只有在 anchor Claim 可确定时才生成绝对区间；否则保留 `time_anchor_claim_id` 和 uncertain 状态，不猜测具体年份。
- 人物关系通过 `claim_type=relationship`、subject、predicate 和 object entity 表达，关系成立时间由 time fields 表达。

### 5.3 ClaimEvidence：事实证据

`MemoirClaimEvidence` 表达 Claim 是由哪段原始材料支持或反驳的。

第一版目标字段：

```text
id
project_id
claim_id
evidence_role           # supports / contradicts
evidence_source_type    # transcript / image / manual
utterance_id
transcript_version_id
audio_file_id
image_asset_id
speaker_entity_id
speaker_role            # narrator / interviewer / other
start_ms
end_ms
quote
evidence_note
source_text_hash
created_by
created_by_user_id
created_at
```

约束：

- transcript evidence 的 `quote` 必须能在指定 transcript version 中找到。
- image evidence 必须引用原始上传的 image asset，不能引用 AI 生成图、章节插图或分享视频背景；图片可使用 `evidence_note` 描述证件字段或可见内容。
- manual evidence 必须记录创建人和说明，只用于用户/家人明确录入且暂时没有音频或图片的来源。
- 数据库约束必须保证每条 evidence 只使用一种来源形态，并满足该形态的必填字段。
- 多人/人工访谈必须保留原说话人；采访者的问题默认不提取为叙述者事实，采访者提供的新事实只能作为单独来源并进入 needs_verification。
- 一个 Claim 可以有多条支持证据，也可以有相互冲突的证据。
- `source_text_hash` 用于检测转写版本内容变化，不替代 version ID。
- 用户人工输入的事实也要记录 evidence 类型和创建人；没有音频时仍需保存明确来源。

### 5.4 Story：跨访谈故事单元

`MemoirStory` 是叙事层核心对象。

第一版目标字段：

```text
id
project_id
title
story_type              # event / relationship / life_stage / theme
summary
time_start
time_end
time_precision
primary_place_entity_id
lifecycle_status        # active / merged / archived
completion_status       # incomplete / sufficient / accepted_incomplete
verification_status     # unreviewed / confirmed / needs_verification / disputed / rejected
publication_status      # eligible / private / excluded
slot_schema_version
merged_into_story_id
version
created_by
created_by_user_id
confirmed_by_user_id
confirmed_at
superseded_at
created_at
updated_at
```

Story 与 Claim 通过 `MemoirStoryClaim` 关联：

```text
story_id
claim_id
narrative_role          # background / trigger / action / turning_point / outcome / reflection / context
sort_order
is_core
```

Story 的版本历史通过 `MemoirStoryRevision` 保存；人工确认、合并、拆分和重要 AI 重算都递增 Story version，并记录变更前后的结构化快照、创建人和原因。历史 revision 只用于审计和回滚，不作为当前查询主表。

Story 槽位使用 `MemoirStorySlot` 和 `MemoirStorySlotClaim` 表达：

```text
MemoirStorySlot
  id
  project_id
  story_id
  schema_version
  slot_key
  status                  # missing / partial / sufficient / accepted_incomplete / not_applicable
  assessment_summary
  user_resolution_note
  resolution_actor_type
  resolved_by_user_id
  resolved_at
  updated_at

MemoirStorySlotClaim
  story_slot_id
  claim_id
  evidence_role           # supports / contradicts
```

槽位模型必须满足：

- `slot_key` 必须来自对应 `story_type + schema_version` 的 Pydantic schema。
- 每个槽位使用受控 key 和受控状态，核心状态不保存在 JSON。
- 每个槽位通过关系表保存支撑 Claim，不能只保存一段 AI 总结。
- AI assessment 和 user resolution 使用不同字段，AI 重算不能覆盖人工决策。
- schema 升级提供显式 migration，不在读取时静默猜测旧结构。

统一槽位状态：

```text
missing
partial
sufficient
accepted_incomplete
not_applicable
```

事件型 Story 槽位：

```text
background -> trigger -> people -> difficulty -> actions -> turning_point -> outcome -> reflection
```

关系型 Story 槽位：

```text
meeting -> person_portrait -> shared_experience -> relationship_change -> representative_scene -> reflection
```

生涯阶段型 Story 槽位：

```text
entry_reason -> responsibilities -> representative_events -> ability_and_character -> transition -> reflection
```

主题型 Story 槽位：

```text
theme_definition -> supporting_cases -> contrast_or_failure -> continuity -> reflection
```

第一版 schema 的重点槽位是明确、版本化的访谈规划信号：

- event：`background`、`trigger`、`people`、`actions`、`outcome`。
- relationship：`person_portrait`、`shared_experience`、`representative_scene`。
- life_stage：`entry_reason`、`responsibilities`、`representative_events`、`transition`。
- theme：`theme_definition`、`supporting_cases`、`continuity`。

未列入重点集合的槽位为 optional。Story 的 `completion_status` 仍可根据重点槽位计算，供“还可以补什么”和任务排序使用；任何槽位缺失都不直接阻止已有事实进入草稿或正式书稿。已有充分证据的槽位不能直接降级为 `accepted_incomplete` 或 `not_applicable`，需要先处理其 Claim/Evidence 冲突或确认状态。

### 5.5 ChapterContract：章节契约

`MemoirChapterContract` 与现有 `MemoirBookPlanChapter` 按版本一对多关联，表达章节为什么存在；`chapter_id + version` 唯一。它在产品上称为“章节方向/章节说明”，Contract 只是内部模型名。

第一版目标字段：

```text
id
project_id
chapter_id
version
objective
reader_takeaway
time_scope_text
time_start
time_end
core_questions_json
constraints_json         # 只保存经 schema 校验的收录/排除约束
status                   # draft / confirmed / superseded
created_by
created_at
updated_at
```

契约必须回答：

- 读完这一章，读者应该明白什么？
- 章节覆盖哪个时间范围或主题范围？
- 哪些核心问题必须被回答？
- 哪些内容必须覆盖，哪些内容不应放在本章？

标题不是契约。修改标题默认只修改展示名称；修改目标必须显式创建新 contract version，并重新评估节点、缺口和正文块。

已确认的 contract version 及其 ChapterNode 集合不可原地改写。结构编辑创建新的 contract version 并从上一版本显式复制或调整节点，历史 DraftBlockVersion 继续引用原 contract/node，保证旧书稿可解释。

用户明确调整并确认章节方向时，确认和替代必须记录执行用户与时间。系统生成的 draft 可以直接驱动缺口分析和草稿生成，但不得伪造确认人；数据库层继续保护已确认 contract、Node 和 NodeStory，除 `confirmed -> superseded` 生命周期迁移外不允许原地更新或删除。

`core_questions_json` 中每个问题必须有稳定 question key；问题是否已回答由 Node/Story/Claim 和 Gap 计算，不能在 JSON 中维护第二套完成状态。

### 5.6 ChapterNode：章节结构节点

`MemoirChapterNode` 表达章节的叙事结构，而不是正文段落样式。

第一版目标字段：

```text
id
project_id
chapter_id
contract_id
node_type               # opening / background / story / transition / reflection / closing
title
purpose
sort_order
required
transition_from_node_id # node_type=transition 时使用
transition_to_node_id   # node_type=transition 时使用
status                  # planned / ready / needs_work / excluded
created_by
created_at
updated_at
```

节点和 Story 通过 `MemoirChapterNodeStory` 关联：

```text
node_id
story_id
story_role              # primary / supporting / contrast / transition_context
sort_order
required
```

约束：

- 一个核心 Story 通常由一个 primary 节点承载，避免同章重复。
- 过渡节点必须说明连接哪两个节点，不允许借过渡引入未经引用的新事实。
- 开场和结尾必须服务 contract objective，不使用通用抒情模板填充。
- 拆章、并章和拖拽顺序只修改结构关系，不复制 Story 或 Claim。
- 拆章和并章只允许操作 draft contract；移动 Node 时保留 NodeStory 主键关系，并同步调整两个 draft contract 的 Story 收录约束。目标 contract 已排除的 Story 或会造成同章重复 primary 的移动必须拒绝。
- 旧章节迁移只生成 draft 建议；候选 Story 必须能通过 ChapterSourceLink、ClaimEvidence 和 StoryClaim 回溯到章节素材，确认由独立用户 API 完成。

### 5.7 NarrativeGap：持久内容缺口

`MemoirNarrativeGap` 是审校发现的稳定问题，不等于某次 LLM 返回的一段建议。

第一版目标字段：

```text
id
project_id
scope_type              # story / chapter / book
primary_story_id        # scope_type=story 时必填
primary_chapter_id      # scope_type=chapter 时必填；book scope 两者均为空
gap_type                # missing_slot / chronology / relationship / causality / contradiction / repetition / unclear_reference / unsupported_fact
severity                # high / medium / low
target_slot
title
description
fingerprint             # 相同范围和问题的幂等键
status                  # open / resolved / accepted_incomplete / not_applicable / dismissed
detected_by             # rules / ai / user
resolution_note
resolved_by
resolved_at
created_at
updated_at
```

Gap 通过显式关系表记录涉及的 Claim、Story 和 Chapter，支持一个全书矛盾同时指向多个章节：

```text
MemoirNarrativeGapClaim
gap_id
claim_id
claim_role              # supporting / conflicting / unsupported_candidate

MemoirNarrativeGapStory
gap_id
story_id
scope_role              # primary / involved

MemoirNarrativeGapChapter
gap_id
chapter_id
scope_role              # primary / involved
```

规则：

- 新审计命中同一 fingerprint 时更新证据和最后检测时间，不创建无限重复记录。
- fingerprint 由 scope、gap type、target slot 和涉及的稳定对象 ID 组成，不能使用易变化的 AI 文案计算。
- 审计不再通过“下一次快照没有返回”自动关闭旧 gap。
- `accepted_incomplete` 和 `not_applicable` 是正式完成状态。
- high gap 只有在 resolved、accepted_incomplete、not_applicable，或经人工确认是误报并 dismissed 后才不阻塞正式成书；dismissed 必须记录理由和操作人。

### 5.8 FollowupTask：补访任务

`MemoirFollowupTask` 是针对 Gap 的一次可执行访谈动作。不是所有 Gap 都需要补访，例如重复段落可以通过编辑解决。

第一版目标字段：

```text
id
project_id
gap_id
chapter_id
story_id
target_slot
question
purpose
known_context_summary
priority
status                  # pending / in_progress / answered / deferred / cannot_recall / excluded / cancelled
assigned_session_id
defer_until
defer_reason
created_by
started_at
completed_at
created_at
updated_at
```

状态转换：

```text
pending -> in_progress
in_progress -> answered | deferred | unable_to_answer | closed_without_answer | cancelled
deferred -> pending      # 用户主动恢复，或明确冷却期结束后允许
```

任务与已知事实、禁止重复事实和结果口述通过关系表表达，而不是保存在 JSON ID 列表：

```text
MemoirFollowupTaskClaim
  task_id
  claim_id
  context_role            # known / do_not_repeat / expected_evidence

MemoirFollowupTaskAnswer
  task_id
  utterance_id
  result_role             # target_answer / related_context

MemoirUtteranceInterviewAssignment
  utterance_id             # UNIQUE，固定创建时的访谈目标
  followup_task_id         # nullable
  policy_decision_id       # nullable
```

完成条件：

- 用户结束录音不等于任务 answered。
- 异步处理只读取 `MemoirUtteranceInterviewAssignment`，不能回读 Session 当前任务推断这句话原本在回答什么。
- 新 utterance 完成 Claim/Story ingestion 后，目标槽位获得新有效证据，任务才能自动变为 answered。
- 用户选择无法回忆时任务进入 `unable_to_answer`，对应 Gap 应转为 accepted_incomplete 或保持待家人核实，由用户选择决定。
- 用户选择“不再继续补问”时任务进入 `closed_without_answer`；是否排除素材或设为 private 仍由独立的内容决定处理。

### 5.9 DraftBlock：可追溯正文块

正文使用稳定块和版本表达：

```text
MemoirDraftBlock
  id
  project_id
  chapter_id
  node_id
  block_role             # factual / transition / reflection / opening / closing
  sort_order
  editorial_status       # draft / confirmed / excluded
  freshness_status       # current / stale
  locked
  current_version_id
  created_at
  updated_at

MemoirDraftBlockVersion
  id
  block_id
  version
  content
  created_by
  model
  prompt_snapshot
  feedback
  created_at

MemoirDraftBlockClaim
  block_version_id
  claim_id
  usage_role             # core / context / detail / quoted

MemoirDraftBlockStory
  block_version_id
  story_id
  story_revision_id      # 固定生成时使用的 Story version
  usage_role             # primary / supporting / contrast
```

约束：

- 新版本不覆盖旧版本。
- 用户手动编辑、语音修改和 AI 局部重写都创建 block version。
- `locked=true` 后任何自动生成任务必须跳过该 block。
- factual block version 必须直接记录 Story 和 Claim 引用，不能只通过可能变化的 ChapterNode 间接推导来源。
- DraftBlockStory 必须引用生成时的 StoryRevision；后续 Story summary、槽位或 Claim 关系变化不能改变历史块的生成依据。
- Story/Claim 更新只把实际依赖的 block 标记 `freshness_status=stale`，不得丢失原 editorial confirmation。
- ChapterDraftVersion 可以保存组装后的 content 和 block version ID 快照，用于现有导出和历史回滚。

### 5.10 NarrativeDecision：用户和系统决策审计

`MemoirNarrativeDecision` 只保存不可变的领域决策历史，不作为当前状态主表。

第一版目标字段：

```text
id
project_id
target_type             # entity / claim / story / story_slot / gap / followup_task / chapter_contract / draft_block
target_id
action                  # confirm / reject / merge / resolve / accept_incomplete / exclude / mark_private / lock / unlock / needs_review
actor_type              # user / ai / system
actor_user_id
before_state_json       # 审计快照，不承载当前业务真相
after_state_json
reason
idempotency_key
created_at
```

约束：

- decision 只允许 insert，不允许 update/delete。
- 用户动作必须记录 `actor_user_id`；AI/system 动作必须记录对应 actor type。
- `target_type + target_id` 是审计定位，不替代目标表的 project ownership 和外键校验。
- idempotency key 防止客户端重试重复记录同一决策。
- 内容版本仍由 Claim supersede、StoryRevision、ChapterContract version 和 DraftBlockVersion 表达，不能把完整领域模型长期塞进 decision JSON。

## 六、完整性与逻辑审校

### 6.1 两层判断

审校必须分成确定性规则和语义判断两层。

确定性规则负责：

- Claim 是否有有效 evidence。
- 必填节点是否存在 Story。
- required Story 的核心槽位是否 missing。
- 明确时间范围是否重叠或逆序。
- disputed/rejected/private Claim 是否进入正文。
- DraftBlock 是否引用已 superseded Claim。
- locked block 是否被生成任务改动。
- 同一 Story 是否在同章多个 primary 节点重复出现。

LLM 语义审校负责：

- 因果是否缺失。
- 人物首次出现是否缺少必要说明。
- 指代是否不清。
- 章节开头和结尾是否回应 contract objective。
- 不同 Story 之间是否缺少合理过渡。
- 内容是否像履历或素材清单，而不是有重点的叙事。
- 是否存在可能的重复、矛盾或无来源扩写候选。

LLM 输出必须先经过 schema 和 evidence 校验，再 upsert NarrativeGap；不能直接修改 Claim、Story 或正文。

### 6.2 不使用单一完整度百分比

用户界面按以下三组展示：

```text
必须处理
建议补充
可以接受的不完整
```

内部可以为任务排序计算权重，但不得把权重包装成精确的“章节完整度 72%”。

三组含义必须稳定：

- “必须处理”只包含会造成事实错误、来源失效、隐私泄漏或正文逻辑无法成立的问题。
- “建议补充”包含缺少细节、重点槽位不充分、可增强的场景/感受和重复表达优化。
- “可以接受的不完整”表示用户已明确决定不再追问，但不能被系统改写为“事实已确认”。
- 历史数据中即使把 `missing_slot` 标成 high，读取正式成书门槛时也按建议补充处理，避免旧派生状态继续错误阻塞。

### 6.3 正式成书门槛

草稿预览随时可以生成；标记为“正式书稿”必须满足：

- 每章有当前章节方向和至少一个可成稿的故事节点；章节方向可以是 draft。
- 正文使用的 Story 处于 active、允许收录、没有 disputed/rejected，并有可追溯 Claim/Evidence。
- 没有未处理的 high correctness gap，包括事实冲突、无来源事实、关键时间/人物关系/因果或指代无法成立。
- 没有 disputed/rejected/private Claim 进入正文。
- 每个 factual DraftBlock 都有有效 Claim 引用。
- 全书时间线和人物关系审校没有未处理 high gap。

缺少细节、固定 StorySlot、可选开场/反思/收束不会阻塞正式书稿。用户可以明确接受不完整；当系统无法判断一个解释是正文事实、编辑说明还是事实修正时，必须询问用户，不能静默猜测或把所有回答都拼入正文。

## 七、访谈产品与编排

### 7.1 用户入口

老人端 AI 访谈提供两个主要入口：

```text
随便讲一段          # open_narration 目标 + free_narration 弱引导
帮我补全这本书      # book_completion 目标 + guided_interview 强引导
```

人工访谈继续作为独立 `interview_type=human_interview`。采访者可以从章节工作台选择某个 FollowupTask 后开始人工访谈。

目标会话维度：

```text
interview_type            # ai_interview / human_interview
guidance_mode             # free_narration / guided_interview
session_goal              # open_narration / book_completion
current_followup_task_id  # optional
```

`guidance_mode` 继续表达 AI 的交互强度，`session_goal` 表达本次访谈的业务目标，两者不能复用同一个字段。`guided_interview` 的主题菜单在没有可用 FollowupTask 或用户主动换方向时作为冷启动策略复用，但不承载章节完整度状态。主题菜单是内部引导策略，不是第三套录音或 ASR 链路。

### 7.2 自由讲述

- AI 顺着叙述者当前话题做弱引导。
- 新 utterance 仍进入 Claim/Story ingestion。
- 如果新内容补足已有 Gap，后台自动解决对应 Gap 和 FollowupTask。
- AI 不因为发现成书缺口而突然打断当前重要叙述。

### 7.3 补全成书

进入时先展示章节级全局补全视图，再由 Followup Planner 选择一个 pending task。用户必须先看见每章当前已经讲清、必须核对和建议补充的主题，不能只能跟随 AI 连续回答局部小问题。

任务排序考虑：

- high gap 优先于 medium/low。
- required Story 优先于 supporting Story。
- 当前章节优先级和用户选择。
- 避免连续多轮追问同一 Story。
- 避免询问 `do_not_repeat` 中已经充分回答的问题。
- `cannot_recall`、`excluded`、private publication 和 accepted_incomplete 的内容不得再次自动选择。

每次只向老人呈现一个具体目标，例如：

```text
今天补一件事：桂林缺粮
还想知道：当时采取的第一步措施是什么？
```

老人可选择：

```text
开始讲
换一个
想不起来
以后再说
不想收录
```

### 7.4 跑题处理

补全访谈中用户可以自由跑题：

- 录音、转写和新 Story 正常保存。
- 不强行把跑题内容挂到当前 Story。
- 只有目标槽位获得有效新 Claim 时才完成当前 task。
- 当前 task 未完成时可以继续、跳过或稍后再问，不能丢失状态。

## 八、后台任务和幂等

### 8.1 复用现有任务队列

继续使用 `MemoirEditingJob` 的 lease、dedupe、retry 和 worker，不建设第二套队列。目标任务类型如下：

```text
ingest_narrative_state
recompute_story_state
recompute_chapter_structure_audit
generate_followup_tasks
generate_draft_blocks
audit_book_consistency
```

不建议为 Entity、Claim、Story、Slot 分别排一个细粒度任务，否则一条 utterance 会产生过多队列编排和中间失败状态。`ingest_narrative_state` 应在一个明确应用服务中完成同一输入版本的提取和关联，再按发生变化的 Story/Chapter 触发下游任务。

### 8.2 触发链路

```text
ASR final、离线/方言增强产生新的有效转写，或人工编辑改变有效转写版本
 -> 生成/刷新 SourceCard
 -> ingest_narrative_state
      -> 提取并校验 Entity / Claim / ClaimEvidence
      -> 关联或创建 Story 候选
      -> 重新计算受影响 Story slots
 -> recompute_chapter_structure_audit
      -> upsert NarrativeGap
 -> generate_followup_tasks
      -> 为可通过访谈解决的 gap 创建/更新 task
 -> 将依赖受影响 Claim 的未锁定 DraftBlock 标记 stale
```

### 8.3 幂等键

至少使用以下维度组成 dedupe/input signature：

```text
project_id
utterance_id 或 scope_id
effective_transcript_version_id
engine_version
schema_version
```

同一输入重试不得重复创建 Entity、Claim、Story、Gap 或 Task。人工已经确认的状态不得被重试任务降级。

历史版本已经生成 Claim/Evidence、但没有生成 Story 时，不得通过重新执行整条 ingestion 进行修复。LLM 对同一事实的拆分和措辞可能变化，直接重跑会把同一 transcript version 扩增为重复 Claim。历史修复必须只读取现有 Claim/Evidence，在同一 transcript version 内按共享的非叙述者人物或地点锚点生成待确认 Story 候选，并使用稳定 fingerprint 幂等落库；低置信度拆分、合并和槽位归属继续由用户 review。

### 8.4 失败策略

- AI 不可用时任务失败或延期，原始素材仍可查看和编辑。
- 不把“每条素材单独建立一个 Story”作为静默 fallback 写入业务真相。
- 低置信度实体合并、Story 合并和冲突判断进入 review queue。
- 后台失败通过 AutomationEvent 和任务状态暴露，但 AutomationEvent 不保存长期语义状态。
- 恢复任务必须从持久输入版本重放，不能依赖进程内临时上下文。

## 九、章节生成与局部重写

### 9.1 生成顺序

新成书流程必须按以下顺序执行：

```text
读取当前章节方向（draft 或 confirmed）
 -> 生成/调整 ChapterNode
 -> 为节点选择 Story 并说明叙事作用
 -> 运行事实和结构建议审校
 -> 对有有效 Story/Claim/Evidence 的节点逐块生成 DraftBlock
 -> 校验 DraftBlockClaim 和无来源事实
 -> 生成必要过渡块
 -> 组装章节快照
 -> 运行章节整体审校
```

禁止直接把本章全部 transcript 拼进一次 Prompt 后生成整章。

### 9.2 Prompt 输入边界

每个 factual block 的 LLM 输入只包含：

- 当前 ChapterContract 的必要部分。
- 当前 ChapterNode 的 purpose。
- 节点关联 Story 的已选 revision 和 Claim。
- ClaimEvidence 的必要引用。
- 前后相邻块的短上下文。
- 用户明确写作反馈。
- 补全回答的用途：直接讲述、编辑解释或事实修正。编辑解释只帮助理解原文，不能直接写进正文；事实修正必须先更新 Claim 语义再参与生成。

不默认输入全章所有录音和旧稿。LLM 输出正文和使用的 claim IDs，服务端必须验证 claim IDs 属于允许集合。

### 9.3 局部更新

```text
Claim 更新
 -> 找到 DraftBlockClaim
 -> 标记相关未锁定块 stale
 -> 生成局部修订建议
 -> 用户确认后创建新 BlockVersion
 -> 重新组装 ChapterDraftVersion
```

Story 顺序变化只影响节点顺序和必要过渡块；不能无故重写已确认 factual block。

### 9.4 全书润色边界

全书润色只能：

- 建议章节顺序。
- 建议过渡和重复删除。
- 统一称谓、标点和有限书面表达。
- 标记人物、时间和事实冲突。

全书润色不能：

- 绕过 DraftBlock 锁定。
- 新增 Claim。
- 把 uncertain Claim 改成确定表达。
- 直接覆盖确认版本。

## 十、章节工作台

成书首页承担项目级概览，章节工作台承担单章全局视图。普通用户不需要先理解 Story、Claim、slot、Contract 等内部模型；这些术语只允许出现在开发/审计接口或明确的高级编辑视图。

章节工作台至少包含：

```text
本章概览
  已经讲清的主题
  必须核对的问题
  建议补充的方向
  已接受的不完整

正文与来源
  当前章节正文
  每段可查看原始音频、转写和修改记录
  有歧义时向用户展示具体选择，不展示内部状态名

下一步
  选择一个主题开始补充
  换一个、想不起来、以后再说、不想收录
```

老人端复用现有 AI 访谈的实时 ASR/TTS/录音链路，只显示当前访谈所需的最少信息；复杂证据审计可以提供给家人或编辑，但仍使用产品语言而不是数据模型名。

## 十一、API 边界

目标 API 按领域职责拆分，不能继续全部追加到现有大 `book.py` router。

目标资源边界：

```text
/memoir/projects/{project_id}/entities
/memoir/projects/{project_id}/claims
/memoir/projects/{project_id}/stories
/memoir/projects/{project_id}/stories/{story_id}/slots
/memoir/projects/{project_id}/chapters/{chapter_id}/contract
/memoir/projects/{project_id}/chapters/{chapter_id}/nodes
/memoir/projects/{project_id}/gaps
/memoir/projects/{project_id}/followup-tasks
/memoir/projects/{project_id}/chapters/{chapter_id}/draft-blocks
/memoir/projects/{project_id}/book-consistency-audits
```

API 要求：

- 请求和响应使用显式 schema，不把核心操作封装成任意 command JSON。
- 所有更新带 project ownership 校验。
- merge、resolve、lock、accept incomplete 等动作使用明确 endpoint 和状态转换。
- AI 建议和用户确认分开；AI endpoint 不能伪装成已确认更新。
- 列表接口支持增量加载，避免一次返回全项目所有 Claim 和 evidence。

## 十二、代码组织

当前 `backend/app/services/memoir/` 已经包含大量平铺服务文件，后续实现不得继续无边界扩张。目标组织：

```text
backend/app/models/memoir_narrative/
  entities.py
  claims.py
  decisions.py
  stories.py
  chapters.py
  gaps.py
  followups.py
  drafts.py
backend/app/domain/memoir/narrative_types.py
backend/app/schemas/memoir_narrative/
backend/app/services/memoir/narrative/
  extraction.py
  ingestion.py
  ingestion_jobs.py
  backfill.py
  queries.py
  claims.py
  decisions.py
  entities.py
  stories.py
  story_slots.py
  chapter_contracts.py
  chapter_nodes.py
  gaps.py
  followup_tasks.py
  draft_blocks.py
  consistency_audit.py
backend/app/routers/memoir/narrative/
```

依赖方向：

```text
router -> application service -> repository/model
                         -> typed AI client adapter
```

约束：

- provider-specific payload 不进入领域模型。
- narrative 模型按真实聚合边界拆文件并由 `app.models` 统一注册，不继续扩展现有大 `memoir.py`，也不建立一个新的万能模型文件。
- LLM JSON 先解析为 Pydantic 输出，再进入应用服务校验。
- repository 只负责持久化，不包含 Prompt 或产品策略。
- voice 服务通过 FollowupTask application service 获取任务，不直接查询 Story JSON。
- book 生成服务通过 ChapterNode/DraftBlock application service 工作，不直接拼接原始模型字段。
- 只有能够减少真实重复或隔离变化的共享 helper 才允许下沉。

## 十三、数据库与存储策略

### 13.1 当前阶段继续使用关系数据库

当前 SQLite 足以完成模型验证和单实例产品迭代。新增模型使用 SQLAlchemy 标准关系、外键、唯一约束和索引，保持未来迁移 PostgreSQL 的可行性。

现有运行库曾在关闭外键检查时重建语音表，部分历史外键目标被 SQLite 改写为已经删除的 `*_old` 表，同时旧派生缓存中存在源素材删除后留下的孤儿引用。在这些历史问题清理完成前，不得仅通过连接级 `PRAGMA foreign_keys=ON` 全局开启外键执行，否则会把本阶段的 shadow 表迁移变成现有语音和素材链路的行为变更。过渡期要求：

- 新叙事表仍声明标准外键和项目级复合外键，保持 schema 语义和未来数据库迁移能力。
- narrative repository 在写入前显式校验项目、Entity、Claim、transcript、utterance、audio 和 image 的归属；不能依赖当前 SQLite 连接级外键开关保证隔离。
- PR12 删除旧主链路时修复所有指向 `*_old` 的外键定义，清理可重建派生缓存的孤儿引用，并对必须保留的原始音频、转写和书稿引用逐条核对。
- 只有迁移报告确认 `PRAGMA foreign_key_check` 为零，且现有删除、回填和 worker smoke 通过后，才能统一开启 SQLite 外键执行。

PostgreSQL 迁移单独立项，触发条件包括：

- 多实例 worker 和写并发成为真实瓶颈。
- JSON 字段需要大规模索引和复杂检索。
- 全文/向量检索需要数据库级整合。
- SQLite 锁竞争已经有监控证据。

不能把数据库迁移和叙事模型切换放在同一 PR 系列中同时进行。

### 13.2 JSON 使用原则

允许 JSON 的场景：

- Story slot 的低频、类型特有补充属性；slot key、status 和 Claim 关系仍使用关系表。
- ChapterContract 的低频结构化约束。
- 任务的非 ID、非状态类 Prompt 提示；known/do-not-repeat Claim 和结果 utterance 关系仍使用关系表。
- 历史输出快照和派生 AI 结果。
- append-only NarrativeDecision 的 before/after 审计快照。

禁止 JSON 承载：

- 核心 status、project ownership、外键关系和排序。
- Claim 与 evidence 的关系。
- Story 与 Claim、ChapterNode 与 Story、DraftBlock 与 Claim 的关系。
- 用户确认、锁定和隐私状态。
- StorySlot、FollowupTask 与 Claim/Utterance 的核心关系。

### 13.3 检索

第一阶段使用结构化字段、名称别名和现有摘要完成候选检索。向量检索仅用于提供 Entity/Story 候选，不能单独决定合并。

## 十四、迁移与切换策略

不保留永久兼容链路，但必须保护已有用户数据和历史书稿。

### 14.1 一次性回填来源

```text
MemoirSourceCard.key_facts
 -> Claim / ClaimEvidence 候选

MemoirSourceCard.people / places / time_hints
 -> Entity 和时间候选

MemoirChapterSourceLink
 -> Story/Chapter 迁移检查和来源覆盖校验

MemoirBookChapterDraftVersion
 -> 仅保留 DraftBlock 组装后的历史快照
```

### 14.2 切换阶段

```text
1. 新表和 repository 落地，不改变行为。
2. 对新转写 shadow 生成 Claim/Story，不参与现有成书。
3. 对脱敏真实项目运行 backfill dry-run 和一致性检查。
4. 章节工作台读取新模型，旧成书链继续可用。
5. 补全访谈开始使用 FollowupTask。
6. DraftBlock 新链路生成新章节，进行新旧质量对照。
7. 新链路达到门槛后切换正式成书。
8. 删除旧 source -> whole chapter prompt、旧事件聚类主读和永久 fallback。
```

临时双读/双写必须在 PR 描述中声明删除 PR，最迟在第十八章的最终切流 PR 清理。

### 14.3 数据保护和回滚

- 所有 backfill 脚本默认 dry-run，必须支持 `--project-id`。
- 执行前生成数据库和对象存储引用清单。
- 回填只新增叙事数据，不覆盖 utterance、transcript、audio 和历史 book output。
- 切换前比较用户数、项目数、utterance 数、有效 transcript version、Claim evidence 覆盖和章节来源覆盖。
- 回滚切换只切读路径；已经生成的新叙事数据保留用于排查，不反向污染旧事实层。
- 最终切流 PR 删除旧代码前必须确认所有活跃项目已完成迁移检查。

### 14.4 最终切流前的安全迁移

章节工作台的真实数据准备必须先于最终正式切流，并保持 14.2 的旧成书链可用。该阶段只做三件事：修复 SQLite 历史表重建遗留的外键目标、创建可验证且可恢复的完整备份、按阶段新增 Claim/Story/章节方向建议和 legacy DraftBlock 快照。不得在该阶段删除旧生成任务、停止旧成书事件或改变正式成书读路径。

正式环境执行时必须先停止 API 和后台 worker。备份、迁移和所有写入阶段必须指向同一个文件型 SQLite 数据库；每个写入阶段必须使用同一份由备份工具生成的 `manifest.json`。manifest 必须绑定源数据库绝对路径、独立备份库 SHA-256、`PRAGMA integrity_check`、核心表行数、对象引用清单和目录校验和，不能用手工复制的 SQLite 主文件代替。

```bash
cd backend

# 1. 停止所有数据库写进程后创建可恢复备份。
PYTHONPATH=. venv/bin/python scripts/backup_runtime_data.py \
  --label pre-narrative-backfill

# 2. 使用迁移专用连接修复历史外键并校验完整 runtime schema。
PYTHONPATH=. venv/bin/python scripts/migrate_runtime_schema.py --skip-backup

# 3. 每个阶段先 dry-run，再带同一份 manifest 执行。
PYTHONPATH=. venv/bin/python scripts/prepare_memoir_narrative_data.py inventory
PYTHONPATH=. venv/bin/python scripts/prepare_memoir_narrative_data.py ingest
PYTHONPATH=. venv/bin/python scripts/prepare_memoir_narrative_data.py ingest \
  --execute --application-writers-stopped \
  --backup-manifest /controlled/backup/manifest.json
PYTHONPATH=. venv/bin/python scripts/prepare_memoir_narrative_data.py process-ingestion \
  --execute --application-writers-stopped \
  --backup-manifest /controlled/backup/manifest.json
PYTHONPATH=. venv/bin/python scripts/prepare_memoir_narrative_data.py stories
PYTHONPATH=. venv/bin/python scripts/prepare_memoir_narrative_data.py stories \
  --execute --application-writers-stopped \
  --backup-manifest /controlled/backup/manifest.json
PYTHONPATH=. venv/bin/python scripts/prepare_memoir_narrative_data.py structures
PYTHONPATH=. venv/bin/python scripts/prepare_memoir_narrative_data.py structures \
  --execute --application-writers-stopped \
  --backup-manifest /controlled/backup/manifest.json
```

`cutover_data_ready` 只表示历史叙事数据已回填、Claim 均有 evidence、同一 transcript version 中共享人物或地点锚点的叙事 Claim 簇均已进入 Story 候选、章节方向建议已物化且没有失败的摄取任务。它不表示 Story 已被用户确认。`chapter_workflow_ready` 单独表示章节已有当前方向和结构化 DraftBlock；迁移脚本不得伪造确认人或产品决策。两者都不能替代来源校验、一致性审校和真实章节质量验收。

恢复操作默认只做预检。只有确认所有写进程仍处于停止状态时才允许执行；工具会先保存当前数据库调查快照，再清理目标库侧车并原子替换为经过 SHA-256 和完整性校验的备份。

```bash
PYTHONPATH=. venv/bin/python scripts/restore_runtime_data.py \
  --backup-manifest /controlled/backup/manifest.json

PYTHONPATH=. venv/bin/python scripts/restore_runtime_data.py \
  --backup-manifest /controlled/backup/manifest.json \
  --execute --database-writers-stopped
```

## 十五、质量评估和测试策略

### 15.1 质量基线

建立三类脱敏评估样本：

```text
事件型：背景和结果已有，但行动过程缺失
关系型：人物多次出现，称谓和关系需要合并确认
生涯型：职位和时间较多，容易写成履历
```

至少选择 2 个章节做新旧盲评，覆盖：

- 主题集中度。
- 章节结构完整性。
- 时间顺序和因果逻辑。
- 人物身份和关系清晰度。
- 原始事实忠实度。
- 书面可读性和个人声音保留。
- 缺口是否被诚实暴露而非 AI 扩写。

真实原始录音和未脱敏文本不提交仓库。仓库测试只保存最小脱敏片段和结构化事件序列；完整评估样本通过本地受控路径运行。

每个维度使用预先写明锚点的 1 到 5 分人工量表，不能让参与生成的同一 LLM 充当唯一裁判。正式切换至少满足：

- 脱敏样本中的事实性句子来源覆盖为 100%，没有无法映射 Claim 的新增事实。
- 时间、人物和证据相关 high gap 为 0，或已有明确人工 resolution。
- 新链路在任何核心维度的中位数不低于旧链路，并在章节结构、时间逻辑、人物清晰度三个关键维度中至少两个有明确提升。
- 锁定块变更数为 0，局部补写不相关块变更数为 0。
- 项目所有者/主要编辑完成评审；条件允许时增加一名不参与实现的独立评审者。叙述者本人对事实和个人声音的意见优先于自动评分。

### 15.2 必须测试的业务边界

- ClaimEvidence quote 与 transcript version 的一致性。
- 转写版本变化后 Claim supersede 和依赖重算。
- Entity alias 合并和跨项目隔离。
- NarrativeDecision 幂等、append-only 和 actor 归属。
- 多次访谈归入同一 Story，低置信度进入 review。
- 每种 Story schema 的槽位计算和 schema migration。
- Gap fingerprint 幂等、accepted incomplete 和 not applicable。
- 全书 Gap 可以显式关联多个 Story、Chapter 和 Claim。
- FollowupTask 只有在新证据补足槽位后才 answered。
- `cannot_recall`、excluded/private publication 和跑题事件序列。
- ChapterContract/Node/Story 关系和拆并章。
- Claim 更新只使相关 DraftBlock stale。
- locked DraftBlock 不被局部或全书生成修改。
- high gap 正式成书门槛。
- backfill dry-run、重复执行、部分失败和项目隔离。

### 15.3 不增加的低价值测试

- 不用 WXML/WXSS 正则锁定文案、颜色和间距。
- 不测试函数名、私有 helper 调用次数或模型类是否存在。
- 不用理想化的 LLM JSON 代替真实失败形态和 evidence 校验。
- 不为临时兼容字段保留长期测试。
- 不以快照测试覆盖复杂业务状态机。

### 15.4 PR 质量门槛

每个 PR 必须在描述中包含：

```text
计划编号和对应章节
本 PR 架构目标
完成项
明确未做项
数据模型/API/状态机变化
迁移和回滚方式
临时双轨及删除 PR
测试命令和结果
人工验证结果
风险和后续依赖
```

review 必须对照本文档检查范围和不变量。测试通过但违反领域边界的 PR 不得合并。

## 十六、历史实施记录：6 个阶段、12 个 PR

本章记录叙事基础模型最初如何分阶段落地，相关 PR 已不再作为当前实现顺序或切流门槛。尤其是下文 PR12 的早期方案，不得直接覆盖第十八章对完整度、回答用途和工作台产品边界的修订。

### 阶段 1：设计基线与持久化基础

#### PR 1：叙事架构设计基线

范围：

- 新增本文档。
- 在语音聊天主线和历史语义审计计划中增加职责边界和链接。
- 明确质量基线、业务不变量、迁移方案和 12 PR 范围。

验收：

- 文档可以独立回答目标模型、状态机、任务链路、切换顺序和删除策略。
- 不修改生产代码和运行行为。

禁止：

- 不提前实现模型或 UI。
- 不复制多份相同设计。

#### PR 2：事实持久化基础

范围：

- 新增 narrative model/type/schema 包和迁移注册。
- 只落地下一 PR 会立即使用的 Entity、Claim、ClaimEvidence 基础表。
- 增加外键、source type check constraint、唯一约束、项目索引和对应 repository 基础能力。
- 暂不接入生产读写链路。

验收：

- SQLite migration、重复迁移和 schema 约束测试通过。
- 核心状态和关系不使用任意 JSON。
- 模型文件不继续追加到现有大 `memoir.py`。

禁止：

- 不在 migration PR 调 LLM。
- 不提前创建 Chapter、Gap、FollowupTask 或 DraftBlock 等尚未进入本阶段行为的表。
- 不增加无使用计划的通用框架。

### 阶段 2：事实和故事记忆

#### PR 3：Entity、Claim 与 Evidence ingestion

范围：

- typed AI extraction、evidence quote 校验和幂等写入。
- 新增 NarrativeDecision 模型/迁移，并用于 Entity/Claim 人工确认和合并决策。
- 转写变化后的 supersede/needs_review 处理。
- 人物、地点和机构候选匹配；低置信度进入 review。
- 接入现有 EditingJob 和最小 backfill dry-run。

验收：

- 没有 evidence 的事实不能进入 ready Claim。
- 人物关系、时间原文、时间精度和确认状态可表达。
- AI 失败不产生伪事实。

禁止：

- 不把 SourceCard JSON 直接复制成已确认 Claim。
- 不让向量相似度直接合并 Entity。

#### PR 4：Story 生命周期和槽位引擎

范围：

- 新增 Story、StoryRevision、StoryClaim、StorySlot 和 StorySlotClaim 模型及迁移。
- Story 创建、候选关联、合并 review 和 StoryClaim。
- 四类版本化 slot schema 和确定性状态计算。
- 从 SourceCard/EventCluster 生成 Story 候选的 backfill。
- Story 查询和人工确认 API。

验收：

- 不同会话可以补充同一 Story。
- EventCluster 只作为候选，不直接成为 confirmed Story。
- accepted incomplete/not applicable 不会被后续 AI 覆盖。

禁止：

- 不创建 chapter-scoped Story。
- 不以素材数直接判定 sufficient。

### 阶段 3：章节结构和可执行缺口

#### PR 5：ChapterContract 和 ChapterNode

范围：

- 新增 ChapterContract、ChapterNode 和 ChapterNodeStory 模型及迁移。
- 章节契约版本、结构节点和 NodeStory 关系。
- 现有章节计划到 contract/node 的迁移建议和确认 API。
- 拆章、并章、排序和 Story 复用的领域操作。

验收：

- 生成正文前能够验证 contract 和 required nodes。
- 移动结构不会复制 Story/Claim。
- 标题修改和目标修改是两个不同操作。

禁止：

- 不允许 Node 直接持有 source refs 作为主关系。
- 不在本 PR 切换正文生成。

#### PR 6：完整性引擎和章节工作台

范围：

- 新增 NarrativeGap、GapClaim、GapStory、GapChapter 模型、迁移和 resolution 状态机。
- 确定性规则、LLM 语义检查和 NarrativeGap upsert。
- Gap resolve/accept/not-applicable API。
- 新增独立章节工作台，展示结构、Story、证据和三类检查结果。

验收：

- 相同问题重复审计不会生成重复 Gap。
- 用户决策跨审计保留。
- UI 不显示伪精确完整度百分比。

禁止：

- 不让审计直接改事实或正文。
- 不把完整工作台继续堆进现有成书首页。

### 阶段 4：成书驱动访谈

#### PR 7：Followup Planner 和会话编排

范围：

- 新增 FollowupTask、FollowupTaskClaim、FollowupTaskAnswer 和 UtteranceInterviewAssignment 模型及迁移。
- Gap 到 FollowupTask 的生成、排序和状态机。
- `session_goal`、`current_followup_task_id` 和任务上下文读取。
- 新 utterance ingestion 后的 task completion 判断。
- 跑题、unable_to_answer、closed_without_answer、private publication 和 accepted incomplete 处理。

验收：

- 结束录音不会错误完成任务。
- 已回答和不再追问的内容不会重复选择。
- 复用现有 ASR/TTS/LLM streaming 链路。

禁止：

- 不建设第二套 voice gateway。
- 不把一次 LLM 回答当作任务完成证据。

#### PR 8：小程序双入口和补访体验

范围：

- “随便讲一段”和“帮我补全这本书”入口。
- 当前补访目标、换一个、想不起来、以后再说和不想收录交互。
- 人工访谈选择 FollowupTask。
- view model/state tests 和真机 smoke。

验收：

- 老人端一次只看到一个明确问题。
- 所有操作有可见状态，不因后台任务禁用无关按钮。
- 不上传或发布微信版本，由用户手动完成。

禁止：

- 不在小程序计算故事完整性。
- 不用 WXML 正则测试替代状态测试和真机 smoke。

### 阶段 5：结构化成稿和局部重写

#### PR 9：DraftBlock、来源和锁定

范围：

- 新增 DraftBlock、DraftBlockVersion、DraftBlockClaim 和 DraftBlockStory 模型及迁移。
- DraftBlock/Version/Claim 持久化和状态机。
- 旧章节正文迁移为历史组装快照或 legacy block。
- 手动编辑、确认、锁定、解锁和 stale 传播。

验收：

- 每个 factual block 有 Story/Claim 来源。
- Claim 变化只标记实际依赖块。
- locked block 不可被后台任务修改。

禁止：

- 不把整章正文保存成一个新的单 block 继续冒充结构化实现。
- 不丢弃旧章节版本。

#### PR 10：节点生成、局部修订和章节组装

范围：

- 按 contract/node/story/claim 生成正文块。
- claim allowlist 校验、过渡块生成和局部重写。
- 组装 ChapterDraftVersion 和 BookOutputVersion。
- 成书 UI 接入块级编辑、来源查看和锁定。

验收：

- Prompt 不再全量输入本章 transcript。
- 新素材不会无故重写无关或锁定块。
- 章节组装结果保持完整顺序和历史快照。

禁止：

- 不保留旧 source -> whole chapter prompt 作为默认 fallback。
- 不允许全书润色绕过块版本。

### 阶段 6：全书一致性和正式切换

#### PR 11：全书人物、时间和逻辑审校

范围：

- 项目级时间线、人物关系、跨章重复和矛盾检查。
- 正式书稿 readiness gate。
- 脱敏真实章节新旧盲评和质量报告。

验收：

- 明确时间冲突使用确定性规则优先检测。
- LLM 只产生 Gap 建议，不直接改稿。
- 正式书稿满足本文 6.3 的门槛。

禁止：

- 不使用单一 LLM 自评分作为上线结论。
- 不因润色覆盖不确定性和用户确认。

#### PR 12：回填、切换和旧链路删除

范围：

- 全量 backfill dry-run、执行、检查和回滚工具。
- 新读写链路正式切换。
- 删除旧 source -> whole chapter 主生成、旧 EventCluster 主读、临时双轨和无用 fallback。
- 更新文档为实际最终职责。

验收：

- 所有活跃项目通过迁移完整性检查。
- 不再存在指向 `*_old` 的外键，`PRAGMA foreign_key_check` 为零，并已统一开启 SQLite 外键执行。
- 新链路部署和 smoke 通过。
- 代码库中不存在未声明的永久双写和死分支。
- 历史音频、转写和书稿版本完整保留。

禁止：

- 不以“后续再清理”为理由保留旧主链路。
- 不在没有备份和检查报告时执行破坏性迁移。

## 十七、阶段验收效果

```text
阶段 1：团队拥有统一设计和持久化边界。
阶段 2：系统开始真正认识跨访谈的人、事实和故事。
阶段 3：能够明确回答每章缺什么、为什么缺、是否接受不完整。
阶段 4：AI 访谈能够围绕成书目标补齐具体故事，而不是泛泛按主题提问。
阶段 5：章节从结构节点和故事生成，可局部修订并保护已确认内容。
阶段 6：全书人物、时间和逻辑一致，旧拼接链路被彻底移除。
```

最终产品不再把章节视为“一篇已经生成的文章”，而是把它视为一个有目标、有结构、由长期 Story 组成、可以逐步补齐和确认的容器。Story 也不再等于一次录音，而是能在长期访谈中持续积累证据、修正事实并服务多个章节的核心数据资产。

## 十八、2026-07 修订实施计划：4 个 PR

旧 Draft PR #722 不直接合入。它保留为历史参考；当前实现从最新 `main` 按以下四个独立 PR 演进。

### PR 1：重定义完整度与成书门槛

- 区分事实/来源/隐私/逻辑硬问题与内容丰富度建议。
- 章节方向 draft 可以参与缺口分析和草稿生成，不伪造用户确认。
- Story 未逐条人工确认、重点槽位缺失不再自动阻止成书。
- 历史 high `missing_slot` 在 readiness 读取时降为建议，避免迁移前派生状态继续阻塞。
- 高优先级事实冲突、无来源事实、失效正文块和隐私内容继续阻塞。

### PR 2：补全回答用途与结构化成稿

- 把补全回答明确分为直接讲述、编辑解释和事实修正。
- 直接讲述进入 Story/Claim/Evidence；编辑解释作为非引用生成上下文；事实修正先更新语义状态。
- 结构化 DraftBlock 生成接入上述上下文，禁止把“我只是解释这句话”等编辑说明写入正文。
- 编辑上下文必须携带 task、utterance 和 transcript version 来源，但不得包含可被绕过 Claim 直接写入正文的完整回答或直接讲述片段。
- 编辑上下文参与生成任务签名；排队后上下文发生变化时拒绝使用旧任务结果，不静默套用过期解释。
- 新解释使受影响的结构化正文块进入 stale；用户再次生成时在原块上追加版本，locked 块保持 stale 且不被自动改写。
- 系统无法可靠判断回答用途时询问用户，不增加静默 fallback。

### PR 3：简化章节工作台

- 提供单章全局视图：已讲清、必须核对、建议补充、已接受不完整。
- 普通页面隐藏 Story/Claim/slot/Contract 等内部模型名。
- 补全入口复用现有 AI 访谈的实时 ASR/TTS/录音链路，章节工作台只选择目标和展示进度。
- 用户可以按主题选择下一步，不被迫沿单个细节无限追问。

### PR 4：验收、切流和旧链路删除

- 用真实章节验证补全回答归章、编辑解释不入正文、事实修正生效和全书一致性。
- 新结构化链路达到来源、逻辑和产品验收条件后切换正式读写路径。
- 删除旧整章 prompt、旧页面、旧任务链路、永久双轨和无用测试。
- 保留原始音频、转写版本、多人转写、离线双模型 ASR 和方言增强能力。

#### PR 4 实际切换结果

- `MemoirBookChapterDraft` 和 `MemoirBookChapterDraftVersion` 继续作为已组装的章节正文投影，用于确认、版本追溯、TTS、导出和全书组装；它不再是“把素材整章丢给 LLM”的创作主模型。
- 章节组装只接受当前 contract 下非 legacy、`confirmed` 且 `current` 的 DraftBlock，并要求所有 ready node 都有正文块。组装完成后还必须单独确认章节投影。
- 全书组装只读取已确认、且 block snapshot 仍与当前结构一致的章节投影。任一章未确认、正文块 stale 或章节结构已变更时，不得生成新的全书版本。
- 素材归章、转写修改、章节合并和补全回答修正只会更新来源/叙事状态并将受影响的 DraftBlock 标为 stale，不再排队整章重写。
- 小程序的章节卡片统一进入章节工作台；用户逐段核对后执行“确认本章正文”。全书整理顺序固定为“组装已确认段落 -> 保留书名和封面等成书设置 -> 生成新 BookOutputVersion”。
- 已删除旧章节页、整章生成/修订 API、整书生成任务、`book_auto_revision.py`、整章 prompt/input/event-cluster 实现和只验证这些旧行为的测试。成书前言、后记、引语等编辑性文案仍可使用独立的后台任务，但不得修改章节事实正文。
- 运行时迁移会在切换前阻止存在 `pending` 或 `running` 的旧生成/修订任务，删除已终止的旧任务，重建 job type 约束，并将遗留的等待中旧自动化事件标记为失败。迁移不伪造 contract、Story 或 DraftBlock 的用户确认。
- 上线前库存检查显示 21 个活跃项目；旧编辑任务均已处于终止态，不存在需要继续执行的旧 writer 任务。大部分历史项目没有完整的已确认结构化正文，切换后应进入章节工作台完成核对，不得在迁移中自动确认。

每个 PR 必须独立 review、运行影响范围内的高价值测试并给出迁移/删除结论。第 4 个 PR 合并前不得提前删除仍承载线上语义的旧实现。
