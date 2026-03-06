# AGENTS.md

## Default Workflow For Fixing Issues (Required)

当用户要求“修某个 issue”时，默认必须按以下流程执行，不可跳步：

1. 实现 issue 修复并提交到独立分支，创建 PR（关联 issue）。
2. 在**新线程**发起代码评审（code review）。
3. 回到原线程，根据 review 意见逐条修改并推送更新。
4. 重新运行相关测试（至少覆盖本次改动影响范围）。
5. 仅在以下条件全部满足后执行 merge：
   - review 意见已处理完毕（或有明确结论）；
   - 测试通过；
   - 无阻塞性问题。

## Delivery Requirements

- 最终汇报必须包含：
  - PR 链接；
  - 本次执行过的测试命令与结果；
  - 最终 merge 结果（含 commit SHA）。
- 如测试因环境问题无法正常执行，必须明确说明原因、影响和替代验证方式，再等待用户确认是否继续 merge。
