# 回忆录 Memory-first Interview v1 迭代计划

## 版本目标

本期目标是把回忆录模块从“固定问题 + 回答整理”推进到第一版“原始材料可追溯 + 结构化记忆 + 覆盖度驱动追问”。

完成后，产品体验应满足：

- 老人端仍然像聊天一样回答，不需要理解复杂题库。
- 后台能保存原始材料、转写版本和用户确认文本。
- 每次 session 结束后能提取人物、事件、地点、主题和待确认问题。
- 系统能基于覆盖度和记忆缺口推荐下一问。
- 家人/编辑端能看到摘要、记忆卡片、待确认点和覆盖度。

本期不做照片输入、完整多 agent runtime、多人协作权限、完整成书和复杂向量检索。

## 产品形态

### 老人端

老人端只保留低摩擦访谈：

- 一个自然语言问题。
- 语音回答或文字回答。
- 转写结果可编辑。
- AI 整理结果可确认。
- 继续下一问、跳过、重录。

问题文案应像聊天，不直接强调“第 1 题、第 2 题”。内部可以有 question id，但前台默认不展示。

### 家人/编辑端

本期新增或预留一个回忆录管理视图：

- 最近一次访谈摘要。
- 新增人物、事件、地点、主题。
- 待确认清单。
- 覆盖度地图。
- 推荐下次问题。
- 本次生成的故事片段草稿。

家人/编辑端不是正文编辑器的替代，而是帮助确认事实、补足缺口和选择下一次访谈方向。

## 数据结构规划

### 现有模型保留

```text
memoir_sessions
memoir_questions
memoir_answers
memoir_drafts
audio_files
```

这些表继续承载采集流程。

### 新增原始材料和转写版本

#### memoir_source_artifacts

保存回答背后的原始材料。第一期使用 `audio` 和 `text`，预留其他类型。

```text
id
project_id
session_id
answer_id
artifact_type        # audio / text
storage_path
mime_type
size_bytes
metadata_json
created_at
```

#### memoir_transcript_versions

保存同一回答的多版本文本。

```text
id
answer_id
source_artifact_id
version_type         # asr_raw / dialect_corrected / user_edited / final
content
provider
confidence
created_by           # asr / ai / user / system
created_at
```

`memoir_answers.transcript_raw` 和 `transcript_edited` 可继续保留，作为当前读路径兼容字段；新能力以 transcript version 为准。

### 新增长期记忆

#### memoir_person_memories

```text
id
project_id
canonical_name
aliases_json
relationship
first_seen_session_id
description
confidence
status               # draft / confirmed / rejected
created_by           # ai / user / system
created_at
updated_at
```

#### memoir_event_memories

```text
id
project_id
title
life_stage
time_text
start_date
end_date
place_text
cause
process
result
emotion
importance
confidence
status               # draft / confirmed / rejected
created_by
created_at
updated_at
```

第一期可以先用 `time_text` 和 `place_text` 承载不确定信息，不强行结构化日期和地点。

#### memoir_place_memories

```text
id
project_id
name
elder_alias
modern_name
place_type
description
confidence
status
created_by
created_at
updated_at
```

#### memoir_theme_memories

```text
id
project_id
name
description
strength
mention_count
confidence
status
created_by
created_at
updated_at
```

#### memoir_memory_links

连接人物、事件、地点、主题。

```text
id
project_id
source_type          # person / event / place / theme
source_id
target_type          # person / event / place / theme
target_id
relation_type        # participated_in / happened_at / expresses_theme / related_to
confidence
created_at
```

#### memoir_memory_evidence

结构化记忆的证据链。

```text
id
project_id
memory_type          # person / event / place / theme / open_question
memory_id
session_id
question_id
answer_id
transcript_version_id
quote
start_offset
end_offset
confidence
created_at
```

`quote` 第一阶段足够重要，即使 offset 暂时为空，也要能快速回看来源。

#### memoir_open_questions

待确认或待追问的问题。

```text
id
project_id
session_id
target_memory_type
target_memory_id
question
question_type        # clarification / depth / scene / person / emotion / connection / closure
reason
sensitivity          # low / medium / high
priority
status               # open / asked / answered / dismissed
created_by
created_at
updated_at
```

### 覆盖度和问题候选

#### memoir_coverage_topics

固定或半固定的人生覆盖维度。

```text
id
project_id
key                  # childhood / school / first_job ...
name
life_stage
description
sort_order
created_at
updated_at
```

第一期默认维度：

- 童年
- 上学
- 父母家庭
- 第一次工作
- 婚姻
- 孩子
- 重大转折
- 遗憾
- 骄傲
- 晚年生活

#### memoir_coverage_snapshots

```text
id
project_id
topic_id
answered_count
memory_count
open_question_count
coverage_score       # 0-100
status               # red / yellow / green
signals_json
calculated_at
```

第一期可每次 session 结束后重算，不需要复杂增量。

#### memoir_question_candidates

```text
id
project_id
session_id
question
question_type
target_memory_type
target_memory_id
coverage_topic_id
reason
sensitivity
stage                # skeleton / depth / connection / polish
priority
status               # candidate / selected / asked / dismissed
created_by
created_at
```

`memoir_questions` 仍是实际要问的问题表。被选中的 candidate 可以转成 `memoir_questions.source = ai_generated`。

### 写作产物

#### memoir_session_summaries

```text
id
project_id
session_id
summary
new_people_json
new_events_json
new_places_json
new_themes_json
open_questions_json
next_question_suggestions_json
created_at
updated_at
```

#### memoir_outputs

```text
id
project_id
output_type          # session_summary / story_fragment / timeline / confirmation_todo
title
source_session_id
status               # draft / accepted / archived
created_at
updated_at
```

#### memoir_output_versions

```text
id
output_id
version
content
model
prompt_snapshot
feedback
created_at
```

第一期只要求生成 `session_summary` 和 `story_fragment`，章节草稿放后续。

## 后端服务规划

建议新增或扩展以下模块：

```text
backend/app/services/memoir/
├── source_artifacts.py
├── transcript_versions.py
├── memory_extraction.py
├── memory_repository.py
├── memory_resolution.py
├── coverage.py
├── question_planner.py
├── session_summary.py
└── outputs.py
```

### source_artifacts.py

- 创建原始材料记录。
- 关联 `AudioFile`、`MemoirAnswer` 和 session。
- 保存后续输入类型的扩展字段，但第一期只处理语音和文字。

### transcript_versions.py

- 写入 ASR 原文、AI 修正文、用户编辑版和最终版。
- 提供某条 answer 的最佳文本。
- 兼容现有 `transcript_raw` / `transcript_edited`。

### memory_extraction.py

- 汇总本次 session 的问答。
- 调用 AI 提取 person/event/place/theme/open_question。
- 返回结构化 JSON。
- 写入 draft memory 和 evidence。

### memory_repository.py

- 提供结构化记忆 CRUD。
- 统一处理状态流转：draft、confirmed、rejected。
- 提供 family/editor 端查询。

### memory_resolution.py

- 第一阶段做保守合并：
  - 完全同名人物合并候选。
  - 同标题事件不自动覆盖，只标记 related。
  - 不确定事实生成 open question。

### coverage.py

- 初始化默认覆盖维度。
- 根据 answer、memory、open question 计算覆盖度。
- 生成 red/yellow/green 状态。

### question_planner.py

- 从 open questions、coverage gaps 和 session state 生成候选问题。
- 按优先级、敏感度、最近上下文排序。
- 选择最高优先级候选转成下一条 `MemoirQuestion`。

### session_summary.py

- 生成本次访谈摘要。
- 列出新增记忆和待确认点。
- 给 family/editor 端提供可读摘要。

### outputs.py

- 保存故事片段和后续输出版本。
- 保留 prompt snapshot 和 model。

## API 规划

现有小程序 API 保留。新增 family/editor 查询接口：

```text
GET  /api/mobile/memoir/sessions/{session_id}/summary
POST /api/mobile/memoir/sessions/{session_id}/process

GET  /api/mobile/memoir/memories/people
GET  /api/mobile/memoir/memories/events
GET  /api/mobile/memoir/memories/places
GET  /api/mobile/memoir/memories/themes
PATCH /api/mobile/memoir/memories/{memory_type}/{memory_id}

GET  /api/mobile/memoir/open-questions
PATCH /api/mobile/memoir/open-questions/{question_id}

GET  /api/mobile/memoir/coverage
GET  /api/mobile/memoir/question-candidates
POST /api/mobile/memoir/question-candidates/{candidate_id}/select

GET  /api/mobile/memoir/outputs
GET  /api/mobile/memoir/outputs/{output_id}
```

第一期可以先实现后端服务和测试，再逐步接 UI。

## AI Prompt 和结构化输出

第一期 AI 调用只做 session 结束后的批处理，不做复杂实时 agent。

### Memory extraction 输出

```json
{
  "people": [],
  "events": [],
  "places": [],
  "themes": [],
  "open_questions": [],
  "session_summary": "",
  "story_fragment": ""
}
```

要求：

- 不确定的事实写 `confidence`，不要硬补。
- 每条 memory 附 `evidence_quote`。
- 代词不清、时间不清、地点不清时生成 open question。
- 敏感问题标记 `sensitivity`。

### Question planning 输出

```json
{
  "candidates": [
    {
      "question": "你刚才说第一次去重庆工作，那天是谁送你去的？",
      "type": "scene_detail",
      "target_memory_type": "event",
      "target_memory_id": "...",
      "reason": "关键事件缺少人物和场景细节",
      "sensitivity": "low",
      "stage": "depth",
      "priority": 0.82
    }
  ]
}
```

## PR 计划

### PR 1: Memoir raw material and memory schema

目标：建立原始材料、转写版本、结构化记忆和证据链的数据库地基。

范围：

- 新增 SQLAlchemy models。
- 新增 Pydantic schemas。
- 初始化默认 coverage topics 的领域常量。
- 增加基础 repository/service。
- 补充模型和 service 单元测试。
- 更新技术文档。

不做：

- 不调用 AI。
- 不接前端 UI。
- 不生成下一问。

验收：

- 可以为某个 memoir project 创建 person/event/place/theme/open question。
- 每条 memory 能关联 evidence。
- 测试覆盖创建、查询、状态流转和权限校验。

### PR 2: Memoir memory extraction pipeline

目标：在 session 结束后从问答中提取结构化记忆和摘要。

范围：

- 新增 `memory_extraction.py`。
- 使用现有 `AITextClient` 调用模型。
- 支持 debug/mock client 测试。
- 写入 memories、evidence、open questions、session summary、story fragment。
- 提供 `POST /sessions/{id}/process`。

不做：

- 不做复杂合并。
- 不做实时边聊边抽取。

验收：

- 给定一组问答，能生成 draft memories 和 evidence。
- AI 失败时返回可理解错误，不破坏原 answer。
- 测试覆盖正常、空回答、无效 JSON、AI 服务异常。

### PR 3: Memoir coverage map and question planner

目标：让下一问从固定队列升级为覆盖度和记忆缺口驱动。

范围：

- 初始化默认 coverage topics。
- 计算 coverage snapshots。
- 从 open questions 和 memory 缺字段生成候选问题。
- 选中 candidate 并转为 `MemoirQuestion`。
- `get_next_question` 优先使用 planner，回退模板题。

不做：

- 不做完整四阶段 pass 管理。
- 不做复杂敏感内容策略。

验收：

- 覆盖度低的 topic 优先生成候选。
- 缺地点/人物/时间的 event 能生成澄清问题。
- 高敏感问题不会连续优先出现。
- 现有固定问题测试仍通过。

### PR 4: Memoir review surfaces and session summary UI

目标：家人/编辑端能看到本次访谈沉淀。

范围：

- API 返回 session summary、memory cards、open questions、coverage。
- 小程序或 Web 回忆录面板展示摘要和待确认点。
- memory 状态支持确认、拒绝和编辑。
- 展示推荐下一问。

不做：

- 不做完整章节编辑器。
- 不做多人权限。

验收：

- 访谈结束后能看到新增人物/事件/地点/主题。
- 可以确认或拒绝一条 memory。
- 可以看到覆盖度地图和推荐下一问。

## 测试计划

后端测试优先：

```bash
cd backend
venv/bin/python -m pytest tests/test_memoir_models.py -q
venv/bin/python -m pytest tests/test_memoir_services.py -q
venv/bin/python -m pytest tests/test_memoir_mobile_api.py -q
venv/bin/python -m pytest tests/test_memoir_drafting.py -q
```

新增测试文件建议：

```text
tests/test_memoir_memory_models.py
tests/test_memoir_memory_extraction.py
tests/test_memoir_coverage.py
tests/test_memoir_question_planner.py
tests/test_memoir_outputs.py
```

文档和 schema 变更不要求跑前端构建；接 UI 的 PR 再跑小程序 typecheck 和前端 build。

## 成功标准

第一期完成后应满足：

- 固定问卷不再是系统中心，结构化记忆开始成为系统中心。
- 任意 AI 生成摘要或故事片段都能回到原始回答。
- 系统可以解释“为什么建议问这个问题”。
- 家人/编辑端能看到待确认事实，而不是只能看到整理后的正文。
- 后续 StorySage 式写作 agent 和 Lifehug 式覆盖度轮转都有稳定数据基础。

## 风险和控制

- AI 抽取幻觉：所有 memory 默认 `draft`，必须附 evidence quote。
- 数据模型过大：第一期只实现必要字段，复杂时间线和地点标准化后置。
- 问题规划不自然：规则层只产缺口，最终问题文案由 LLM 润色。
- 影响现有小程序流程：`get_next_question` 保留模板题回退。
- 后台处理耗时：第一期可以手动触发或 background task，队列后置。
