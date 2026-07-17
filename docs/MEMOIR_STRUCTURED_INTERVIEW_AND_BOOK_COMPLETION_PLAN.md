# 回忆录主题访谈与成书补全设计

## 文档状态

本文是回忆录“主题讲述 + 成书补全”的当前开发基线。历史上的精细章节结构、逐块正文和多层任务设计已退出运行时；如需追溯，请查看 Git 历史，不在当前文档保留兼容路径。

- 事实与成书不变量见 `MEMOIR_NARRATIVE_ARCHITECTURE.md`。
- 实时语音状态机见 `MEMOIR_VOICE_CHAT_REDESIGN.md`。
- 当前原则：事实底座共享，访谈策略和章节补全各自负责，不维护重复的完整度真相。

## 一、产品边界

系统有两条相互协作但目标不同的链路。

### 1.1 主题讲述

主题讲述帮助用户继续说下去。它不要求用户按固定人生目录逐项完成，也不把“没有讲过”解释为“必须补齐”。

```text
当前对话 + Story / Claim + 话题地图 + 用户偏好
                    ↓
             LLM 选择受控动作
                    ↓
          自然回应、追问或提供新话题
```

允许的策略动作由系统提供候选集，例如：

- 继续倾听；
- 顺着当前故事追问；
- 询问人物关系；
- 询问原因或结果；
- 总结确认；
- 回到旧故事；
- 提供一个新话题；
- 暂停或结束。

LLM 可以选择动作和组织表达，但不能确认事实、改变隐私、决定内容入书或覆盖用户决定。

话题地图只记录：

- 这个方面是否出现过素材；
- 相关 Claim / Story；
- 用户是否不愿讨论或认为不适用。

它不是固定覆盖状态机，也不产生“全部讲完”的结论。

### 1.2 成书补全

成书补全只回答一个问题：当前章节下一步做什么，才能得到一份用户可以通读和确认的正文？

页面永远只展示一个下一步：

```text
尚未整理
  → 正在检查
  → 需要补讲或确认一件事
  → 正在生成整章正文
  → 通读、可选修改
  → 确认本章
```

不是所有问题都要采访：

- 缺少用户掌握的新事实：补讲；
- 事实冲突、隐私或收录决定：用户确认；
- 素材已经足够，仅需组织、开头、结尾、转场或去重：编辑处理；
- 次要事件简短但不影响理解：不产生任务。

## 二、共享数据与独立状态

两条链共享以下事实：

```text
AudioFile
  → VoiceUtterance
  → TranscriptVersion
  → ClaimEvidence
  → Claim
  → Story
```

它们也共享人物实体、章节素材关联和用户决定。

两条链不共享：

- 同一套完成百分比；
- 同一套持久任务；
- “话题没出现就必须采访”的规则；
- 由 LLM 直接写入的事实状态。

主题讲述的长期状态是话题地图和策略决策日志；成书补全的长期状态是 `MemoirCompletionItem` 和整章草稿版本。

## 三、当前最小数据模型

### 3.1 事实层

- `MemoirNarrativeEntity`：人物、地点、组织等稳定实体。
- `MemoirClaim`：可确认、可排除、可标私密的事实命题。
- `MemoirClaimEvidence`：Claim 到转写、音频片段、图片或人工证据的来源。
- `MemoirStory`：跨多次访谈持续补充的故事单元。
- `MemoirStoryClaim`：Story 的 Claim 顺序和叙事作用。
- `MemoirNarrativeDecision`：事实确认、排除、隐私及人工审阅决定。

### 3.2 主题讲述

- `MemoirProjectFramework`：项目使用的话题地图版本。
- `MemoirFrameworkItem`：候选话题及用户 resolution。
- `MemoirFrameworkItemClaim`：话题和已有 Claim 的被动匹配。
- `MemoirInterviewPolicyDecision`：当时状态、候选动作、选中动作、原因和生成问题。
- `MemoirInterviewPolicyEvaluation`：shadow 或人工评价结果。

语音层只消费经过整理的 InterviewBrief，不直接理解全部数据库表。

### 3.3 成书补全

- `MemoirChapterSourceLink`：Utterance 到章节的当前归属，是章节素材真相。
- `MemoirCompletionItem`：当前需要补讲或人工确认的一件事。
- `MemoirEditingJob`：章节检查或整章生成的一次异步执行。
- `MemoirBookChapterDraft`：某章当前草稿状态。
- `MemoirBookChapterDraftVersion`：完整整章正文的不可变版本。
- `MemoirBookOutputVersion`：由已确认章节版本组装出的整书版本。

不再维护：

- 另一套 Gap 表；
- FollowupTask / Answer 表；
- Story 槽位完成度；
- 章节契约和章节节点；
- 正文分块、分块锁定和分块拼装；
- StoryRevision 和 Story 合并状态。

## 四、成书补全端到端链路

### 4.1 准备章节

用户点击“开始整理”后：

1. 读取当前章节的有效 SourceLink。
2. 通过证据找到相关 Claim 和 Story。
3. 运行少量确定性规则。
4. 可选运行章节语义审计。
5. 把可持久问题收敛为最多一个当前 `MemoirCompletionItem`。
6. 没有待处理 Item 时，排队生成完整章节正文。

审计输入签名包含章节、Story、Claim、证据计数和当前草稿版本。运行过程中输入变化时，本次结果作废并重新排队，不能发布过期判断。

### 4.2 需要补讲

`MemoirCompletionItem` 记录问题、解决通道和状态。用户开始回答时，VoiceSession 绑定当前 Item；新 Utterance 仍按普通素材进入转写、Claim、Story 和 SourceLink 链路。

回答完成后：

1. 关联 Item 与本次素材；
2. 重新评估章节；
3. Item 已被事实解决则关闭；
4. 没有其他 Item 时生成整章正文。

回答不是“特殊记录”。是否入章由 SourceLink、Claim 可发表性和 Story 关系决定。

### 4.3 生成整章

生成器只接收：

- 章节标题；
- 相关 Story；
- 可发表 Claim；
- Claim 的原始证据摘录；
- 当前整章正文；
- 仅控制组织方式的编辑建议。

LLM 输出完整正文和实际使用的 Claim ID。系统校验：

- Claim ID 必须在本次允许集合内；
- 无依据候选不能出现在正文；
- 正文使用第一人称；
- 不把不确定事实改成确定事实。

生成成功后创建一个 `MemoirBookChapterDraftVersion`，并把当前章节 SourceLink 标记为已被该版本吸收。

### 4.4 通读、修改与确认

章节工作台只加载一次 workspace 请求，直接得到：

- 当前下一步；
- 完整章节正文；
- 草稿版本 ID；
- 草稿状态。

用户可以整章修改。保存时提交 `expected_version_id` 做乐观并发检查，并创建新的完整版本。修改后的版本继承同一批已吸收素材关系，不需要再次拼装。

确认只执行一次整章确认，不存在逐段确认或解除保护。

### 4.5 组装全书

全书组装只接受：

- 已确认的章节规划；
- `include_in_book = true` 的章节；
- 每章最新且已确认的完整草稿版本。

输出快照直接记录 `chapter_id + draft_version_id`。相同输入重复组装复用当前输出版本；任一章节版本变化都会使全书输出失效。

## 五、用户可见状态

`ChapterCompletionSummary` 是唯一章节状态读模型：

- `not_started`：可以开始整理；
- `preparing`：审计或整章生成正在运行；
- `needs_input`：当前只显示一个补讲或确认项；
- `draft_ready`：正文可通读、修改和确认；
- `confirmed`：本章已收入书稿；
- `failed`：本次后台处理失败，可重试，原始素材未丢失。

这些状态由事实、Item、Job 和草稿推导，不单独持久化，不允许前端自行拼装第二套状态机。

## 六、LLM 与确定性逻辑边界

LLM 可以：

- 识别高层章节问题；
- 判断继续听还是切换话题；
- 生成自然问题；
- 根据已有事实整理正文；
- 提供编辑性组织建议。

LLM 不可以：

- 确认 Claim 真实；
- 修改隐私和收录决定；
- 删除素材；
- 解决事实冲突；
- 覆盖人工决定；
- 用旧草稿内容反向创造事实。

所有 LLM 输出必须经过显式 schema 和 ID allowlist 校验。

## 七、开发与验收顺序

当前收敛按以下顺序交付：

1. 删除重复完成度状态和旧任务链。
2. 统一为一个 `MemoirCompletionItem`。
3. 用整章草稿替代章节契约、节点和正文分块。
4. 执行不向前兼容的数据库破坏性迁移。
5. 跑端到端测试后统一部署。

验收至少覆盖：

- 有素材时自动生成一份整章正文；
- 缺素材时只出现一个补讲问题；
- 人工修改后可直接确认，不要求再次组装；
- 已确认章节可稳定组装全书；
- 旧 API、旧表、旧枚举和旧测试不再存在；
- 小程序只走 workspace → 整章修改/确认链路。
