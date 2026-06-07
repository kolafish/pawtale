# 回忆录 Memory-first 架构规划

## 定位

回忆录产品的核心不应该是聊天机器人，也不应该是固定问卷，而是：

```text
Memory-first Conversational Interview System
记忆优先的聊天式人生采访系统
```

用户感知到的是一次自然访谈：老人用语音或文字讲述，系统像采访者一样接住上下文、追问细节、避免机械地显示“第几题”。后台真正沉淀的是可追溯的原始材料、结构化长期记忆、覆盖度地图、问题规划和持续写作产物。

这套架构吸收两个方向的经验：

- Lifehug 的价值在覆盖度、阶段式访谈、Spotlight 和版本化输出。
- StorySage 的价值在 Session Scribe、Planner、Writer、Coordinator 等角色分工。

Pawtale 不直接复用它们的实现。回忆录能力应沿用现有 FastAPI、SQLAlchemy、AI provider、认证和项目边界，在 `memoir` 模块内演进。

## 产品演进路线

### 当前形态

当前回忆录模块偏 `Question -> Answer -> Draft`：

- 模板问题驱动。
- 用户回答后生成单条整理稿。
- 问题顺序和记忆结构仍较弱。
- AI 整理稿和原始材料的证据关系不足。

这个形态适合 MVP，但不足以支撑长期回忆录。

### 下一形态

下一阶段转向 `Raw Material -> Structured Memory -> Question Planning -> Writing Output`：

- 原始材料不可丢，所有 AI 输出都能回溯到来源。
- 人物、事件、地点、主题、待确认问题成为核心资产。
- 系统按覆盖度和记忆缺口决定下一个问题。
- 每次访谈结束都会产生摘要、记忆卡片、待确认点和写作素材。

### 长期形态

长期目标是多轮、多阶段、多人协作的回忆录系统：

- 老人端负责低门槛讲述。
- 家人/编辑端负责确认事实、补材料、审阅写作产物。
- 后台持续维护长期记忆、人生时间线和主题覆盖。
- 写作层持续生成故事片段、人物小传、章节草稿、时间线和待确认清单。

## 七层架构

### 1. 输入层

第一期聚焦语音和文字：

- 语音回答：主要面向老人端。
- 文字回答：用于无法录音、转写修正和家人编辑。

长期可以扩展照片、子女补充材料等输入，但第一期不纳入实现范围。为了未来兼容，原始材料模型可以预留 `artifact_type`，但不做照片理解流程。

### 2. 短期会话状态

短期状态描述“这次聊天正在发生什么”，用于自然承接上下文：

```json
{
  "current_topic": "年轻时第一次工作",
  "current_life_stage": "青年",
  "recent_people": ["父亲", "厂长", "同事老张"],
  "recent_places": ["重庆", "成都"],
  "unresolved_references": ["那个人", "那个厂", "那年"],
  "emotional_tone": "怀念、略带遗憾",
  "last_question_asked": "当时为什么决定离开重庆？"
}
```

短期状态不是最终记忆。它服务当前访谈，帮助系统判断是否应该澄清代词、顺势深入、转场或收束。

### 3. 长期结构化记忆

长期结构化记忆是回忆录产品的核心资产，不应只依赖向量库。

#### Person Memory

- 人名
- 关系
- 别名和称呼
- 第一次出现在哪次聊天
- 与老人的关系变化
- 相关事件
- 可信度和确认状态

#### Event Memory

- 事件标题
- 人生阶段
- 时间或时间范围
- 地点
- 参与人物
- 起因、经过、结果
- 情绪
- 可信度
- 来源 session 和证据片段

#### Place Memory

- 地点名
- 老人的称呼
- 对应现代地名
- 地点类型
- 相关事件和人物

#### Theme Memory

- 反复出现的人生主题
- 例如：离乡、贫穷、责任、遗憾、骄傲、亲情
- 关联事件、人物和问题
- 强度和出现次数

#### Open Questions

- 还没问清楚的问题
- 需要下次确认的问题
- 家人可能需要补充的问题
- 敏感度、优先级和目标记忆

#### Evidence

每条结构化记忆必须能指回原始材料：

- 来自哪次 session。
- 来自哪条 answer。
- 来自 ASR 原文还是用户修正文。
- 对应 transcript span 或文本摘录。
- 由 AI、用户或系统创建。

没有 evidence，写作层就会变成不可审计的黑箱。

### 4. 原始材料库

原始材料库保存不可丢失的事实来源：

- 原始音频
- ASR 原文
- 方言和错词修正文
- 用户最终确认文本
- 每轮聊天记录
- 用户修改记录

回忆录产品必须允许回溯：某句话到底是不是老人说的？某个人名是不是听错了？某个事件是不是 AI 补出来的？

### 5. 覆盖度地图

覆盖度地图负责回答“这本人生故事哪里已经讲够，哪里还空着”。

第一批固定维度可以是：

```text
童年
上学
父母家庭
第一次工作
婚姻
孩子
重大转折
遗憾
骄傲
晚年生活
```

覆盖度不只是已回答问题数，还应综合：

- 相关 answer 数量
- 相关 event/person/theme 数量
- 关键字段完整度
- open question 数量
- 情绪强度
- 多次提及次数
- 矛盾或缺口

问题规划器依据覆盖度低、重要性高、情绪强、有缺口、适合当前气氛等信号决定下一问。

### 6. 问题规划器

问题规划器不应该机械取下一题。每次老人说完一段，系统先判断：

- 有没有新人物？
- 有没有新事件？
- 有没有时间线缺口？
- 有没有值得深入的情绪？
- 有没有老人不想继续的话题？
- 有没有适合转场的话题？

问题类型包括：

- 澄清型：确认代词、时间、地点、人物。
- 深入型：追问关键事件的原因、经过、结果。
- 场景型：追问声音、气味、空间、动作和细节。
- 人物型：追问关系、后来联系、对方影响。
- 情绪型：追问当时感受和现在怎么看。
- 连接型：追问这件事如何影响后续人生。
- 收束型：确认标题、摘要和是否继续这个话题。

候选问题结构：

```json
{
  "question": "你刚才说第一次去重庆工作，那天是谁送你去的？",
  "type": "scene_detail",
  "target_memory_type": "event",
  "target_memory_id": "event:first_job_chongqing",
  "reason": "关键事件缺少人物和场景细节",
  "sensitivity": "low",
  "stage": "depth",
  "priority": 0.82
}
```

前台只拿最高优先级、最自然的一个问题。候选列表和排序理由保存在后台，供调试和家人/编辑端查看。

### 7. 写作层

写作层不等所有聊天结束才开始。每次 session 结束后，后台都可以生成中间产物：

- 本次聊天摘要
- 新增人物
- 新增事件
- 新增地点
- 新增主题
- 新增待确认点
- 可写入章节
- 下次建议问题
- 故事片段
- 人物小传
- 时间线
- 金句摘录

长期再生成章节草稿、专题文章、家书、完整回忆录等版本化输出。

## 多 Agent 后端角色

第一期可以先用 service 编排，不必引入复杂 agent runtime。但长期后端应保留以下角色边界。

### Intake Normalizer

负责输入归一化：

- 接收语音、文字和后续扩展材料。
- 维护原始材料和 transcript version。
- 对 ASR 文本做轻量修正，但不覆盖原文。

### Session State Tracker

负责短期会话状态：

- 更新当前 topic、life stage、recent people、recent places。
- 识别 unresolved references。
- 判断当前情绪和是否适合继续深入。

### Memory Extractor

负责从本次回答提取结构化记忆：

- 人物、事件、地点、主题。
- 关键字段完整度。
- evidence span 和可信度。
- 待确认问题。

### Memory Resolver

负责合并和消歧：

- 同一个人不同称呼的合并。
- 同一事件多次讲述的合并。
- 地点别名和现代地名映射。
- 冲突事实标记，不强行覆盖。

### Coverage Planner

负责覆盖度和重要性评分：

- 更新人生覆盖度地图。
- 识别 red/yellow/green 状态。
- 计算哪些 topic 值得下一次优先追问。

### Question Planner

负责生成和排序候选问题：

- 规则层发现缺口。
- LLM 层把缺口改写成自然问题。
- 排序层综合覆盖度、敏感度、最近上下文和情绪。

### Writing Planner

负责判断哪些素材已经可写：

- 选择可写入片段的事件和主题。
- 维护章节候选、人物小传候选和时间线候选。

### Draft Composer

负责输出写作产物：

- 生成故事片段、摘要、人物小传和章节草稿。
- 生成时必须引用 memory evidence。
- 输出保存为可版本化、可回滚、可审阅的对象。

### Review Coordinator

负责家人/编辑端确认：

- 待确认事实。
- 敏感内容提醒。
- 被用户拒绝或修改的 AI 记忆。
- 写作产物的版本差异。

## 后端数据流

```text
Answer submitted
 -> Source artifact and transcript versions saved
 -> Session state updated
 -> Memory extraction job created
 -> Person/Event/Place/Theme/OpenQuestion drafted
 -> Evidence links attached
 -> Memory resolver merges or marks conflicts
 -> Coverage map recalculated
 -> Question candidates generated
 -> Session summary and writing fragments generated
 -> Family/editor review surfaces updated
```

第一期可以同步或 FastAPI background task 处理。随着转写和提取耗时增加，再升级为队列。

## 与现有模型的关系

现有模型仍保留：

```text
MemoirSession
 -> MemoirQuestion
 -> MemoirAnswer
 -> MemoirDraft
 -> AudioFile
```

它们负责采集流程。新增记忆层负责长期资产：

```text
MemoirSession
 -> SourceArtifact / TranscriptVersion
 -> MemoryPerson / MemoryEvent / MemoryPlace / MemoryTheme
 -> MemoryEvidence
 -> OpenQuestion
 -> CoverageMap
 -> QuestionCandidate
 -> MemoirOutput / MemoirOutputVersion
```

不要把所有业务塞进 `MemoirAnswer` 或 `Document`。回答、记忆、写作产物是不同生命周期的对象。

## 产品界面原则

### 老人端

- 像聊天，不像问卷。
- 主要暴露一个问题、一个回答入口和必要反馈。
- 不显示复杂覆盖度、证据链和 agent 状态。

### 家人/编辑端

- 展示本次访谈摘要。
- 展示人物、事件、地点、主题记忆卡片。
- 展示待确认清单。
- 展示覆盖度地图。
- 展示推荐下次聊什么。
- 展示故事片段和后续章节草稿。

### 系统后台

- 每次 AI 提取、合并、写作都要留状态。
- 所有结构化记忆都能回到原始材料。
- 不确定事实标为待确认，而不是写死。

## 非目标

长期架构不意味着第一期要做完所有能力。以下能力暂不进入第一期：

- 完整多 agent runtime。
- 照片理解和照片叙事。
- 子女多人协作权限系统。
- 完整成书生成。
- 向量库/RAG 大改。
- 多实例任务队列和分布式锁。

第一期先把 memory foundation 做稳。
