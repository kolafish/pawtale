# 回忆录叙事质量切换报告

更新日期：2026-07-13

## 当前结论

**正式切换状态：NOT READY**

工程门禁已经实现，但尚未取得项目所有者/主要编辑对至少两个脱敏真实章节的新旧盲评分。该状态不能由开发者自评或生成正文的同一 LLM 改成通过，也不能作为 PR12 删除旧链路的依据。

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
- 盲审准备工具会随机分配 A/B，将候选正文与答案/版本来源物理分离，并用 SHA-256 绑定正文、确定性指标和叙述者复核。
- 质量 evaluator 已拒绝空候选、正文换包、身份泄露、无版本来源、无人工评审来源和无审计来源的手填指标。

仍需完成：

- 从受控数据路径选择至少两个脱敏真实章节，且三类样本整体覆盖事件型、关系型、生涯型。
- 隐藏 A/B 的新旧链路身份，由项目所有者或主要编辑完成评分。
- 条件允许时增加一名不参与实现的独立评审者。
- 叙述者本人核对事实和个人声音；该意见优先于自动指标。
- 样本 Story 和 ChapterContract 必须由项目所有者按产品工作流确认后才能生成新链路正文；不得为制作盲评包而伪造确认人或绕过 `needs_work` 状态。
- 在本地受控路径建立 manifest，由准备脚本生成相互隔离的盲审包和答案文件；评审完成前不得查看答案文件。

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

## 运行方式

真实原始录音、未脱敏正文、A/B 对照答案和评审人信息只放在本地受控路径，不提交仓库。受控 manifest 中每个样本使用以下结构；`candidate_metrics` 必须来自绑定该新稿的确定性审计 artifact，不是人工估计：

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
        "candidate_metrics": {
          "fact_sentence_count": 12,
          "mapped_fact_sentence_count": 12,
          "unmapped_fact_ids": [],
          "unresolved_high_gaps": {"time": 0, "people": 0, "evidence": 0},
          "locked_block_changes": 0,
          "unrelated_block_changes": 0,
          "measurement_provenance": {
            "engine": "deterministic-quality-audit-v1",
            "artifact_id": "...",
            "measured_at": "2026-07-14T09:00:00+08:00"
          }
        }
      }
    }
  ]
}
```

先生成盲审材料：

```bash
cd backend
PYTHONPATH=. venv/bin/python scripts/prepare_memoir_narrative_blind_review.py \
  --manifest /controlled/memoir-quality/manifest.json \
  --packet /controlled/memoir-quality/blind-packet.json \
  --answer-key /controlled/memoir-quality/answer-key.json
```

准备脚本生成的 `reviews` 初始为空。主要编辑在不知道 A/B 身份的情况下，为同一条 review 同时填写两份稿件的七维评分，并记录匿名 `reviewer_id`、`reviewer_type=human`、`role`、`independent` 和带时区 `reviewed_at`。完成评审并锁定正文后再运行：

```bash
cd backend
PYTHONPATH=. venv/bin/python scripts/evaluate_memoir_narrative_quality.py \
  --packet /controlled/memoir-quality/blind-packet.json \
  --answer-key /controlled/memoir-quality/answer-key.json \
  --output /controlled/memoir-quality/report.md
```

命令退出码为 `0` 才表示满足切换门禁；`2` 表示报告已生成但仍有阻塞项。
