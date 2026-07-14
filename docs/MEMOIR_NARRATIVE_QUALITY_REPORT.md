# 回忆录叙事质量切换报告

更新日期：2026-07-14

## 当前结论

**正式切换状态：NOT READY**

工程门禁已经实现，但尚未取得项目所有者/主要编辑对至少两个脱敏真实章节的新旧盲评分。该状态不能由开发者自评或生成正文的同一 LLM 改成通过，也不能作为 PR12 删除旧链路的依据。

## PR 交付矩阵

| 设计 PR | 实际 PR | 当前状态 |
| --- | --- | --- |
| PR1 设计基线 | [#710](https://github.com/kolafish/pawtale/pull/710) | 已合并 |
| PR2 事实持久化 | [#711](https://github.com/kolafish/pawtale/pull/711) | 已合并；SQLite 外键边界修复 [#712](https://github.com/kolafish/pawtale/pull/712) 已合并 |
| PR3 Entity/Claim/Evidence ingestion | [#713](https://github.com/kolafish/pawtale/pull/713) | 已合并 |
| PR4 Story 生命周期和槽位 | [#714](https://github.com/kolafish/pawtale/pull/714) | 已合并 |
| PR5 ChapterContract/Node | [#715](https://github.com/kolafish/pawtale/pull/715) | 已合并 |
| PR6 完整性和章节工作台 | [#716](https://github.com/kolafish/pawtale/pull/716) | 已合并 |
| PR7 Followup Planner | [#717](https://github.com/kolafish/pawtale/pull/717) | 已合并 |
| PR8 小程序补访体验 | [#718](https://github.com/kolafish/pawtale/pull/718) | 已合并；当前真机 smoke 尚未执行 |
| PR9 DraftBlock/来源/锁定 | [#719](https://github.com/kolafish/pawtale/pull/719) | 已合并 |
| PR10 节点生成和局部修订 | [#720](https://github.com/kolafish/pawtale/pull/720) | 已合并 |
| PR11 全书一致性 | [#721](https://github.com/kolafish/pawtale/pull/721) | 已合并；人工质量门禁尚未通过 |
| PR12 回填和正式切换 | [#722](https://github.com/kolafish/pawtale/pull/722) | Draft；工程实现完成，禁止在本报告变为 READY 前合并 |

当前已完成：

- 事实正文块必须有直接 Claim 和有效 evidence。
- 全书明确时间逆序、冲突 Claim、跨章重复主 Story 使用确定性规则检查。
- disputed、rejected、private、superseded Claim 和 stale block 阻止正式成书。
- 语义审校只提交有 Chapter、Story、Claim 证据 ID 的 Gap，不直接改稿。
- locked block 变化数和局部重写无关 block 变化数进入质量门禁。
- 模型评分不能计入人工盲评。
- PR12 全量工具已在受控真实数据库副本上完成备份、schema 修复、dry-run、摄取任务执行、Story/Contract 回填、重复执行和回滚演练。
- 受控副本的自动迁移报告为 `migration_complete=true`、`cutover_data_ready=true`，无待处理或失败摄取任务，Claim evidence 缺失数为零。
- SQLite 恢复工具已验证会保留迁移后调查快照并清理 WAL/SHM/journal，恢复后 `integrity_check=ok`。
- 盲审准备工具会随机分配 A/B，将候选正文与答案/版本来源物理分离，并用 SHA-256 绑定正文、完整私有测量 artifact、确定性指标和叙述者复核。
- 质量 evaluator 已拒绝空候选、正文换包、身份泄露、无版本来源、无人工评审来源和无审计来源的手填指标；最终评估会重新校验完整 artifact、候选版本、正文逐句来源及汇总指标的一致性。
- PR12 代码集成提交 `d6f5b558` 已同步 `main` `c4663591`；后端全量测试 746 项通过，小程序全量测试 484 项和类型检查均通过。
- 当前 head 已在停止写入的受控真实数据库副本上完成三类 provider 工作流验证：生涯/事件章节事实句来源映射分别为 6/6、8/8，关系章节为 6/6；三章未解决的 time/people/evidence high gap、locked block 变化和 unrelated block 变化均为零，SQLite `integrity_check=ok` 且 `foreign_key_check` 无记录。
- 关系章节验证使用项目级候选 Story“与李阅的关系”，先通过正式 owner include 应用服务加入章节，再执行 Claim/slot/contract、块生成、带 feedback 的局部修订和章节组装。测试夹具只把其他未确认 Story 移出该受控章节，不再把人物指代不同的关系 Story 静默合并。
- 最新生产一致性快照先在 ECS 端停止 Pawtale writers、切换到独立 `DELETE` journal 并完成完整性、外键和 SHA-256 校验，再传回受控目录；本地 45,326,336 字节和 SHA-256 `5546d9018e68355fec92ed363f46f99451b73980e4c0ffc3a85c94e7a0e4981a` 与 ECS 完全一致。PR12 runtime schema 在该副本上迁移成功，87 张既有表中 84 张的行数和共同字段内容哈希未变化；`memoir_editing_jobs` 只清理了 17 条旧整章链路终态记录（16 条 `succeeded`、1 条 `skipped`），其余 684 条任务逐字段一致；另新增 1 个绑定历史章节版本的 legacy DraftBlock version，并只更新对应 DraftBlock 的 `current_version_id` 和 `updated_at`。迁移前后的 38 个项目、772 个音频、243 个 utterance、883 个 transcript version、59 个历史章节稿版本、12 个成书版本、779 个 Claim、794 条 Evidence 和 193 个 Story 均保留，迁移后 `integrity_check=ok`、`foreign_key_check=0`，且不存在指向 `*_old` 的外键。
- 当前线程 review 补充了旧任务切换前置条件：只要旧整章生成任务仍为 `pending/running`，schema 迁移必须失败并保留全部任务；只有 `succeeded/failed/skipped` 终态记录可以在删除旧枚举约束时清理。真实事件序列回归测试同时覆盖“活跃任务阻断、事务不删除、终态后允许清理”。
- 同步最新 `main` 时，当前线程 review 发现历史补全回答修复仍导入已删除的 `book_auto_revision`。该阻塞问题已修正为使结构化章节稿失效并要求显式重新组装，不再恢复旧整章重写任务；相关补全解释、迁移和素材归章回归测试均已通过。
- 章节候选素材默认只展示 8 条，支持按人物或事情搜索及显式展开全部；当前线程 review 发现并修复了“加入素材期间搜索会丢失忙碌状态并可能重复提交”的事件序列问题。
- 最新增量检查发现活跃回忆录项目从 20 个增加到 21 个，其中“写给他的话”有 12 条新 utterance 尚未进入叙事摄取，且 1 个新增历史章节版本尚未形成 legacy 快照。生产环境在停止 Pawtale writers、生成并校验完整 manifest 备份后，通过正式 `ingest`、`process-ingestion`、`stories` 阶段使 12 个任务全部成功，新增 116 个带 Evidence 的未核对 Claim，以及 13 个 AI `unreviewed` Story 和 9 个系统 `needs_verification` Story。前后数据库对比确认 4 个 Contract、既有 Story 的人工决策字段和既有 slot 的人工 resolution 均为零变化，没有自动确认 Story、Claim、slot 或 Contract。`structures` 正确跳过 4 个已存在 Contract，PR12 runtime migration 随后在受控副本上补齐 1 个 legacy version。最终全局检查的 21 个活跃项目均为 `migration_complete=true`、`cutover_data_ready=true`，无数据阻塞项目；`chapter_workflow_ready=false` 仍由真实用户确认和结构化正文块门禁保持。
- 当前 PR12 OpenAPI 的 98 个 memoir 路径全部已存在于 ECS 前置版本；生产只额外保留 `/book-editor/chapters/{chapter_id}/draft/generate` 和 `/book-editor/output/generate` 两个 PR12 将删除的旧生成入口。因此项目所有者可以在正式切流前完成 Story、Claim、slot、Contract 和 DraftBlock 决策，不需要临时部署 PR12 或兼容 API。

上述三章中的确认、缺失槽位处理和章节收录均标记为 disposable engineering validation，只验证真实事件序列、provider 输入输出边界和确定性测量工具，不代表项目所有者或叙述者作出了产品决定，也不计入人工切换门禁。

仍需完成：

- 按 `docs/MINIPROGRAM_REAL_DEVICE_SMOKE.md` 在当前 PR head 和真机上执行自由讲述、成书补访、人工补访及章节工作台 smoke，并记录 commit、设备、微信版本、执行人和失败证据；现有清单与单元测试不能替代该执行记录。
- 从受控数据路径选择至少两个脱敏真实章节，且三类样本整体覆盖事件型、关系型、生涯型。
- 隐藏 A/B 的新旧链路身份，由项目所有者或主要编辑完成评分。
- 条件允许时增加一名不参与实现的独立评审者。
- 每个完成样本都由叙述者本人核对事实和个人声音；缺少复核或任一项未通过都会阻止切换。
- 样本 Story 和 ChapterContract 必须由项目所有者按产品工作流确认后才能生成新链路正文；不得为制作盲评包而伪造确认人或绕过 `needs_work` 状态。
- 关系样本需要由项目所有者判断小学好友、其他匿名朋友与李阅是否为同一人物；在该判断完成前不得按相同 `story_type` 批量合并 Story。李阅 Story 当前缺少相识、共同经历和回望槽位，应由叙述者补访或明确接受不完整，工程验证中的临时 resolution 不能复用。
- 在本地受控路径先从数据库生成新稿测量 artifact，再建立只引用该 artifact 的 manifest，由准备脚本生成相互隔离的盲审包和答案文件；评审完成前不得查看答案文件。

## 人工量表

所有维度使用 1 到 5 分。2 分和 4 分表示相邻锚点之间的状态。

| 维度 | 1 分 | 3 分 | 5 分 |
| --- | --- | --- | --- |
| 主题集中度 | 主题散乱 | 主线可辨但有明显游离 | 始终围绕章节目标 |
| 章节结构完整性 | 素材堆叠 | 有开合但过程薄弱 | 开端、展开、转折和收束完整 |
| 时间顺序和因果逻辑 | 时间或因果难以成立 | 基本可跟随但有跳跃 | 时间与因果清楚严密 |
| 人物身份和关系 | 人物身份关系混乱 | 主要人物可辨但介绍不足 | 人物身份和关系清晰一致 |
| 原始事实忠实度 | 有明显扩写或事实偏移 | 主要事实可靠但个别表述过实 | 每个事实都忠实且可回溯 |
| 可读性和个人声音 | 机械或不像讲述者 | 通顺但个人声音不稳定 | 书面流畅且保留个人声音 |
| 缺口诚实度 | 用扩写掩盖缺口 | 部分缺口有提示 | 缺口和不确定性均被诚实呈现 |

## 切换标准

全部条件同时满足才允许在 PR12 切换主链路：

1. 新链路事实性句子 Claim 来源覆盖率为 100%，无未映射新增事实。
2. 时间、人物、证据相关未处理 high gap 为 0。
3. 新链路任何核心维度中位数不低于旧链路。
4. 结构、时间逻辑、人物清晰度中至少两个维度的中位数提升。
5. locked block 变化数为 0，局部修订导致的无关 block 变化数为 0。
6. 至少两个样本完成成对人工盲评，并包含项目所有者或主要编辑。
7. 每个完成样本均有绑定新稿 SHA-256 的叙述者复核，且事实和个人声音均通过。

## 运行方式

真实原始录音、未脱敏正文、A/B 对照答案和评审人信息只放在本地受控路径，不提交仓库。受控 manifest 中每个样本使用以下结构；新稿指标不允许直接写进 manifest，只能引用绑定该正文和版本的确定性测量 artifact：

```json
{
  "rubric_version": "1",
  "samples": [
    {
      "id": "opaque-sample-id",
      "categories": ["event", "relationship"],
      "old": {
        "content_path": "old-chapter.txt",
        "project_id": "...",
        "chapter_id": "...",
        "version_id": "..."
      },
      "new": {
        "content_path": "new-chapter.txt",
        "project_id": "...",
        "chapter_id": "...",
        "version_id": "...",
        "measurement_artifact_path": "new-chapter.measurement.json"
      }
    }
  ]
}
```

先在停止写入的受控数据库副本上生成新稿正文和 `0600` 测量 artifact。baseline 必须是局部修订前已经组装的结构化章节版本，candidate 必须是至少完成一次带 feedback 的单块修订后重新组装的版本：

```bash
cd backend
DATABASE_URL=sqlite+aiosqlite:////controlled/memoir.sqlite \
  PYTHONPATH=. venv/bin/python scripts/measure_memoir_narrative_candidate.py \
  --project-id ... \
  --chapter-id ... \
  --baseline-draft-version-id ... \
  --candidate-draft-version-id ... \
  --content-output /controlled/memoir-quality/new-chapter.txt \
  --artifact-output /controlled/memoir-quality/new-chapter.measurement.json
```

测量 artifact 会逐句列出 DraftBlock、Claim 和 Evidence 映射，统计时间/因果、人物和证据/完整性相关的未解决 high gap，并从前后两个结构化快照自动识别 intended、locked 和 unrelated block 变化。准备脚本把完整 artifact 放入私有答案文件，最终 evaluator 再次校验其完整性、候选身份、正文来源和汇总值。测量失败或指标不满足门禁时不能手改 artifact，必须回到章节工作台处理事实、缺口或局部修订。

再生成盲审材料：

```bash
cd backend
PYTHONPATH=. venv/bin/python scripts/prepare_memoir_narrative_blind_review.py \
  --manifest /controlled/memoir-quality/manifest.json \
  --packet /controlled/memoir-quality/blind-packet.json \
  --answer-key /controlled/memoir-quality/answer-key.json
```

准备脚本生成的 `reviews` 初始为空。主要编辑在不知道 A/B 身份的情况下，为同一条 review 同时填写两份稿件的七维评分，并记录匿名 `reviewer_id`、`reviewer_type=human`、`role`、`independent` 和带时区 `reviewed_at`。评审完成后，由叙述者对新稿事实和个人声音逐章确认，并在私有答案文件写入绑定新稿 SHA-256 的 `narrator_review`。完成这些复核并锁定正文后再运行：

```bash
cd backend
PYTHONPATH=. venv/bin/python scripts/evaluate_memoir_narrative_quality.py \
  --packet /controlled/memoir-quality/blind-packet.json \
  --answer-key /controlled/memoir-quality/answer-key.json \
  --output /controlled/memoir-quality/report.md
```

命令退出码为 `0` 才表示满足切换门禁；`2` 表示报告已生成但仍有阻塞项。
