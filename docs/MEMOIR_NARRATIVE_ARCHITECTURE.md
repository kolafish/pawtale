# 回忆录素材与成书架构

## 文档状态

本文描述回忆录以 Material 和 Section 为核心的事实来源、派生语义和成书质量不变量。历史实现只通过 Git 追溯，不在当前架构中保留兼容模型。

本文不覆盖 VAD、Streaming ASR、TTS 和 WebSocket 状态机，相关内容见 `MEMOIR_VOICE_CHAT_REDESIGN.md`。主题访谈与成书补全协作见 `MEMOIR_STRUCTURED_INTERVIEW_AND_BOOK_COMPLETION_PLAN.md`。

## 一、核心原则

### 1.1 原始讲述是事实来源

原始音频、VoiceUtterance、ASR 原文和历史 TranscriptVersion 是基础资产。校正、摘要、审阅和正文只能创建派生产物，不能反向改写这些来源。

```text
AudioFile
  → VoiceUtterance
  → TranscriptVersion
  → SectionMaterial
  → Section
  → ChapterDraftVersion
  → BookOutputVersion
```

### 1.2 一条讲述保持完整

`VoiceUtterance` 直接作为 Material。一次连续讲述只拥有一个当前 Section 归属，不把同一次回答拆成多个片段穿插进正文。需要调整结构时移动 Material 或重组 Section。

### 1.3 摘要不是事实

SectionDigest、ChapterDigest 和 BookReview 是可失效、可重新生成的编辑派生数据。它们帮助组织和发现问题，但不能作为新事实来源。

### 1.4 用户决定高于模型判断

收录、隐藏、隐私、删除、冲突确认和拒绝继续讨论由用户或确定性领域逻辑负责。LLM 只能提出建议和候选文本。

### 1.5 每个概念只有一个当前真相

- 原始讲述：VoiceUtterance 和 TranscriptVersion；
- 素材归属：SectionMaterial；
- 小章节结构：Section；
- 当前补全工作：CompletionIssue；
- 当前章节正文：BookChapterDraft 的最新 Version；
- 当前全书正文：BookOutput 的最新 Version。

不建立需要双写或互相同步的平行状态。

## 二、Material

Material 由 VoiceUtterance 表示，并可追溯到：

- VoiceSession；
- ChatMessage；
- AudioFile 和时间范围；
- ASR 原文；
- 用户校正文；
- 当前使用的 TranscriptVersion。

Material 的原始文本不因归章、生成正文或用户隐藏而被删除。正文使用哪个版本，必须由 SectionMaterial 显式记录。

## 三、Section 与素材归属

### 3.1 Section

Section 是能够相对独立理解的小故事、人物片段、人生阶段或观点。它属于一个 Chapter，保存：

- title；
- section_type；
- sort_order；
- active / archived；
- include_in_book；
- version；
- 非核心扩展 metadata。

### 3.2 SectionMaterial

SectionMaterial 是 Material 当前归属的规范表，保存：

- Section 和 VoiceUtterance；
- 被采用的 TranscriptVersion；
- Section 内顺序；
- assigned_by、置信度和原因；
- include_in_book；
- 创建和更新时间。

数据库保证同一项目的一条 VoiceUtterance 只有一个 SectionMaterial。移动素材更新该归属，不创建第二份并行关系。

### 3.3 结构操作

创建、移动、排序、合并、拆分、隐藏和归档 Section 时：

- 校验 project 和 Chapter 边界；
- 在一个事务内完成顺序更新；
- 增加受影响 Section 的 version；
- 使相关 Digest、Review 和未发布 Job 失效；
- 不静默覆盖用户确认的章节正文。

## 四、输入签名与派生摘要

每个派生结果都保存输入签名。

SectionDigest 签名至少包含：

- Section ID、类型、标题、顺序和 version；
- 有序 SectionMaterial；
- 每条 Material 采用的 TranscriptVersion；
- Material 文本哈希和 include_in_book；
- 引擎与 prompt 版本。

ChapterDigest 签名至少包含：

- Chapter 标题、顺序和 include_in_book；
- 有序 Section 及其当前 Digest；
- 当前章节正文版本与内容哈希；
- 引擎与 prompt 版本。

BookReview 签名至少包含：

- 章节顺序和 include_in_book；
- 每章当前 Digest；
- 当前已确认正文版本；
- 引擎与 prompt 版本。

任务运行前和发布前各校验一次签名。输入变化时旧结果不得发布。

## 五、CompletionIssue

CompletionIssue 同时表达“发现的问题”和“当前要处理的一件事”，不再维护问题、任务和回答之间的多套状态。

只有需要用户持续处理的问题才持久化：

- `interview`：需要用户补讲；
- `manual_review`：需要确认收录、隐私、冲突或结构选择。

纯编辑问题留在 Digest guidance 中，由生成器处理。

一个审阅轮次最多保留一到三个高价值候选，用户界面只显示一个当前问题。Issue 通过 fingerprint、相关 Material 和处理结果避免重复询问。

## 六、章节正文

### 6.1 Material allowlist

生成器只能接收当前 Chapter 下满足以下条件的 Material：

- 通过有效 SectionMaterial 归入本章；
- 所属 Section 为 active 且 include_in_book；
- SectionMaterial include_in_book；
- TranscriptVersion 仍然存在；
- 未被用户排除。

输入包含 Material ID、原文摘录、当前 Section 和顺序。LLM 返回：

- 完整章节 content；
- `used_material_ids`；
- 缺乏来源但想表达的候选；
- 可选编辑说明。

服务端必须拒绝：

- allowlist 外的 Material ID；
- 把无来源候选写入正文；
- 把模糊记忆写成确定事实；
- 内部 ID、编辑指令或第三人称“叙述者”文本。

当前正文只提供组织和表达参考，不是事实来源。

### 6.2 完整章节版本

一个 BookChapterDraftVersion 保存完整章节正文，不创建正文分块状态机。

生成或人工修改时：

1. 校验 expected version 和输入签名；
2. 创建不可变的新 Version；
3. 记录实际使用的 Material 快照；
4. 使全书输出失效；
5. 等待用户整章确认。

人工修改可以改变表达，但不会改写原始 Material。

### 6.3 确认

确认条件：

- 章节规划已确认；
- 草稿存在且内容非空；
- 当前 Section 结构和 Material 与生成快照一致；
- 当前版本仍是最新版本；
- 没有阻塞性的用户确认问题。

确认是整章单一动作。

## 七、全书输出

全书只读取 `include_in_book = true` 的 Chapter 和每章最新已确认正文。

输出快照至少记录：

- Chapter ID、标题、顺序；
- Draft 和 DraftVersion ID；
- 正文；
- Section 与 used Material 摘要。

章节正文、顺序、收录状态或素材发生变化时，全书输出失效。相同输入重复组装必须幂等复用版本。

## 八、主题访谈

InterviewBrief 向语音层提供经过整理的上下文：

- 最近对话和 Material；
- 当前 Section 摘要；
- 相关话题；
- 当前 CompletionIssue；
- conversation momentum；
- 用户偏好和安全约束；
- 允许的候选动作。

语音层不直接理解底层全部表。策略日志保存当时输入快照、候选动作、选中动作、原因、问题和用户结果，但不能改变 Material 或用户决定。

## 九、数据库与删除原则

最终运行模型只创建当前需要的表。退出运行时的表、列、索引、枚举、路由、任务、脚本、配置和测试必须物理删除，不保留：

- 双写；
- 空实现兼容壳；
- 永久 fallback；
- 只为旧测试存在的配置；
- 已无消费者的序列化字段；
- 注释掉但仍留在仓库的实现。

破坏性迁移前确认没有运行中的旧 Job；迁移后 OpenAPI、ORM metadata、物理 schema、任务注册和小程序类型中都不得出现退出运行时的对象。

## 十、测试不变量

测试关注真实业务边界：

- Audio / TranscriptVersion → Material → Section → Chapter 的追溯；
- 一条 Material 不会同时属于多个 Section；
- 跨项目归属被拒绝；
- 输入变化时旧 Digest 或 Job 不发布；
- 有足够素材时生成一份完整章节正文；
- 正文返回的 used Material 全部属于 allowlist；
- 缺素材时只展示一个当前 Issue；
- 用户跳过、拒答或排除后不会重复追问；
- 人工修改后可以整章确认；
- 未确认章节不能组装全书；
- 相同输入组装幂等；
- 已退出模型、API、表和客户端调用完全消失。

不保留只验证已删除实现细节的测试。
