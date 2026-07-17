# 回忆录事实与成书架构

## 文档状态

本文描述回忆录当前的事实底座和成书质量不变量。旧版 ChapterContract、ChapterNode、正文分块、StoryRevision 及其状态机已删除；历史方案只通过 Git 追溯。

本文不覆盖 VAD、Streaming ASR、TTS 和 WebSocket 状态机，相关内容见 `MEMOIR_VOICE_CHAT_REDESIGN.md`。主题讲述和成书补全的产品协作见 `MEMOIR_STRUCTURED_INTERVIEW_AND_BOOK_COMPLETION_PLAN.md`。

## 一、核心原则

### 1.1 原始证据不可被覆盖

原始音频、Utterance、ASR 原文和历史 TranscriptVersion 是基础资产。校正、事实提取和成书只能创建新的派生产物，不能反向改写原始证据。

### 1.2 事实与文章分层

```text
AudioFile
  → VoiceUtterance
  → TranscriptVersion
  → ClaimEvidence
  → Claim
  → Story
  → ChapterSourceLink
  → ChapterDraftVersion
  → BookOutputVersion
```

- Evidence 证明 Claim。
- Claim 表达可核对事实。
- Story 组织多个 Claim。
- SourceLink 决定当前素材属于哪章。
- ChapterDraftVersion 是事实的文章表达，不是新的事实来源。
- BookOutputVersion 是已确认章节版本的投影。

### 1.3 用户决定高于模型判断

事实确认、冲突裁定、私密标记、排除、章节收录和删除由用户或确定性领域逻辑负责。LLM 只能提出建议、选择受控策略和生成候选文本。

### 1.4 当前状态保持单一

每个业务概念只有一个当前真相：

- 章节素材：`MemoirChapterSourceLink`。
- 事实：`MemoirClaim`。
- 故事：`MemoirStory`。
- 当前补全工作：`MemoirCompletionItem`。
- 当前章节正文：`MemoirBookChapterDraft` 的最新 Version。
- 当前全书正文：`MemoirBookOutput` 的最新 Version。

不建立需要双写或互相同步的平行状态。

## 二、事实模型

### 2.1 Entity

`MemoirNarrativeEntity` 表示人物、地点、组织等稳定对象。实体合并保留决策日志；Claim 必须显式引用当前实体。

### 2.2 Claim

Claim 至少包含：

- statement 和类型；
- 时间、地点、主体、客体；
- certainty；
- verification status；
- publication status；
- conflict / supersede 关系；
- 创建人和最后修改人。

可写入正文的 Claim 必须满足统一 publication policy。冲突、被拒绝、被取代或无有效证据的事实不能进入生成 allowlist。

### 2.3 ClaimEvidence

Evidence 可以来自：

- TranscriptVersion 与 Utterance；
- 原始上传图片；
- 明确的人工证据。

转写证据必须保存原文摘录和内容哈希；音频证据保留时间范围。删除或修改被引用的来源时，系统必须阻止破坏证据链或显式使相关派生产物失效。

### 2.4 Story

Story 是跨多次访谈持续补充的当前故事对象，通过 `MemoirStoryClaim` 关联 Claim。

Story 本身保留一个递增 `version` 参与签名和并发判断，不再保存独立 StoryRevision 表。当前支持 active / archived；不再用 merged 状态保存第二个当前对象。

可能是同一故事但无法高置信复用时，系统记录人工审阅决定，不自动合并事实。

## 三、章节素材

`MemoirChapterSourceLink` 是 Utterance 到章节归属的规范表，禁止再以章节 JSON source refs 作为写入真相。

SourceLink 记录：

- 当前 active / removed 状态；
- 归章来源和置信度；
- 对应转写、音频和摘录；
- 最近被哪个 `draft_version_id` 吸收。

判断章节是否有新素材时，比较当前有效 SourceLink 与最新整章版本的吸收关系。SourceLink 变化会使章节草稿进入待重新整理状态，但不会静默覆盖用户确认的文本。

## 四、成书补全

### 4.1 CompletionItem

`MemoirCompletionItem` 同时表达“发现的问题”和“当前要执行的一件事”，避免 Gap、Task、Answer 多表同步。

Item 只用于需要持续状态的用户工作：

- interview：需要用户补讲；
- manual_review：需要确认事实、隐私或收录决定。

纯编辑问题不创建 Item，只作为本次整章生成的 guidance。

### 4.2 ChapterCompletionSummary

Summary 是由当前 Item、Job、SourceLink 和整章草稿推导出的读模型，不持久化第二份状态。

优先级：

1. 当前审计 Job；
2. 当前 CompletionItem；
3. 当前整章生成 Job；
4. 可通读的草稿；
5. 失败或尚未开始。

前端只消费 Summary 的一个 next action。

### 4.3 输入签名与原子发布

章节审计签名包含：

- 章节 ID 和标题；
- Story version；
- StoryClaim 关系；
- Claim 状态和证据计数；
- 当前草稿版本、内容哈希和素材变化状态。

分析前、发布前各校验一次。签名变化时旧 Job 跳过并排队新 Job，不能以旧结果关闭新问题或生成正文。

## 五、整章正文

### 5.1 数据模型

```text
MemoirBookChapterDraft
  status
  metadata_json

MemoirBookChapterDraftVersion
  draft_id
  chapter_id
  version
  content
  created_by
  model
  prompt_snapshot
  feedback
```

一个 Version 保存完整章节正文。不存在正文块、块锁定、块状态、节点拼装或逐块确认。

### 5.2 生成输入

整章生成只能使用：

- 章节标题；
- 章节 SourceLink 可追溯到的 Story；
- 通过 publication policy 的 Claim；
- Claim 的支持证据；
- 当前草稿；
- 编辑 guidance。

当前草稿只提供组织和表达参考，不是事实来源。

### 5.3 生成输出校验

模型返回：

- 完整 content；
- 实际使用的 Claim ID；
- 想写但缺乏依据的候选。

系统必须拒绝：

- allowlist 外的 Claim；
- 把无依据候选写入正文；
- 把不确定内容改写成确定事实；
- 编辑说明、内部 ID 或第三人称“叙述者”文本。

### 5.4 人工修改

人工修改提交完整 content 和 `expected_version_id`：

1. 校验最新版本未变化；
2. 创建新的不可变 Version；
3. 将草稿状态改回 draft；
4. 将同一批当前 SourceLink 标记为已被新版本吸收；
5. 使正式全书输出失效。

人工改写不能创建或修改 Claim。

### 5.5 确认

确认条件：

- 章节规划已确认；
- 草稿存在且内容非空；
- 当前 SourceLink 没有未吸收的新增或删除；
- 当前版本仍是最新版本。

确认是整章单一动作。

## 六、全书输出

组装全书只读取 `include_in_book = true` 的章节，并要求每章最新草稿已确认。

每章快照至少保存：

- chapter ID、标题、顺序；
- draft ID 和 draft version ID；
- 正文；
- SourceLink 摘要。

相同章节快照重复组装必须幂等复用版本。章节正文、章节顺序、章节收录或来源发生变化时，全书输出和正式化状态必须失效。

正式化前的全书检查可以发现：

- 明确时间逆序；
- 同一 Story 跨章重复；
- 未裁定事实冲突；
- 人物关系未确认；
- 跨章语义重复、指代或转场问题。

检查不能修改正文或事实。

## 七、主题访谈策略

主题访谈使用事实层作为上下文，但不直接改变事实。

`InterviewBrief` 向语音层提供：

- 当前 Story 状态；
- conversation momentum；
- 用户偏好和安全约束；
- 相关已有素材；
- 未解决问题；
- 允许的候选动作。

`MemoirInterviewPolicyDecision` 保存状态快照、候选动作、选中动作、原因、策略版本、生成问题和结果。低风险动作可以逐步自动执行；高风险决定始终留在领域服务。

## 八、API 边界

主要运行接口：

```text
GET  /memoir/projects/{project_id}/book-completion/overview
POST /memoir/projects/{project_id}/book-completion/chapters/{chapter_id}/prepare
GET  /memoir/projects/{project_id}/chapters/{chapter_id}/workspace

GET  /mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter_id}/draft
POST /mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter_id}/draft/revise
POST /mobile/memoir/projects/{project_id}/book-plan/chapters/{chapter_id}/draft/confirm

POST /memoir/projects/{project_id}/book-output/assemble
```

旧章节契约、章节节点、正文分块、分块组装和 Story merge API 不提供兼容层。

## 九、数据库与删除原则

当前运行模型只创建当前需要的表。破坏性迁移直接删除退出运行时的表、列、索引和枚举值，不维护双写、数据回填壳或永久 fallback。

删除顺序必须遵守外键：

1. 旧关联和历史明细；
2. 旧正文与结构对象；
3. 旧任务和列；
4. 重建受枚举约束影响的表；
5. 启动时执行严格 schema assert。

迁移前必须确认没有运行中的旧 Job；迁移后 OpenAPI、ORM metadata 和物理 schema 都不得出现旧对象。

## 十、测试不变量

测试关注用户可观察结果和真实数据边界：

- Evidence → Claim → Story → Chapter 的追溯；
- 输入变化时旧 Job 不发布；
- 有足够素材时生成一个完整章节版本；
- 缺素材时只出现一个当前 Item；
- 人工修改后可直接确认；
- 未确认章节不能组装全书；
- 相同输入组装幂等；
- project 隔离、隐私和人工决定不被 LLM 覆盖；
- 旧模型、API、表和客户端调用完全消失。

不保留只验证已删除实现细节的测试。
