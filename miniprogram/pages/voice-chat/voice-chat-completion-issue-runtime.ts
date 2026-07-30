import type {
  MemoirCompletionIssue,
  MemoirCompletionIssueOutcome,
} from "../../domain/memoir-completion-issue";
import type { MemoirSessionGoal } from "../../domain/memoir-voice-session";
import {
  closeMemoirCompletionIssue,
  listMemoirCompletionIssues,
} from "../../services/memoir-completion-issue-api";

const REFRESH_INTERVAL_MS = 1200;

export interface CompletionIssueState {
  projectId: string;
  sessionGoal: MemoirSessionGoal;
  currentCompletionIssueId: string;
  completionIssue: MemoirCompletionIssue | null;
  completionIssueLoading: boolean;
  completionIssueAction: string;
  completionIssueError: string;
  completionIssueNotice: string;
}

type CompletionIssuePatch = Partial<CompletionIssueState>;

interface CompletionIssueRuntimeOptions {
  readState(): CompletionIssueState;
  applyPatch(patch: CompletionIssuePatch): void;
  isSuspended(): boolean;
  cancelAssistant(reason: string): void;
  refreshIntervalMs?: number;
}

function outcomeNotice(outcome?: MemoirCompletionIssueOutcome | null): string {
  return ({
    answered: "回答已经保存",
    resolved_by_material: "已有讲述已经回答了这个问题",
    confirmed: "这项已经确认",
    not_remembered: "已记下您暂时想不起来",
    deferred: "已留到以后再补",
    declined: "以后不会再追问这项",
    superseded: "这项已经不再需要处理",
  } as Record<MemoirCompletionIssueOutcome, string>)[outcome || "answered"];
}

export class VoiceChatCompletionIssueRuntime {
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;
  private refreshToken = 0;

  constructor(private readonly options: CompletionIssueRuntimeOptions) {}

  async refresh(silent = false): Promise<MemoirCompletionIssue | null> {
    const state = this.options.readState();
    if (state.sessionGoal !== "book_completion") {
      if (state.completionIssue || state.completionIssueLoading || state.completionIssueError) {
        this.options.applyPatch({
          completionIssue: null,
          completionIssueLoading: false,
          completionIssueError: "",
          completionIssueNotice: "",
        });
      }
      return null;
    }
    const { projectId, currentCompletionIssueId: issueId } = state;
    if (!projectId || !issueId) {
      this.stop();
      this.options.applyPatch({
        completionIssue: null,
        completionIssueLoading: false,
        completionIssueError: "",
      });
      return null;
    }
    if (!silent) {
      this.options.applyPatch({
        completionIssueLoading: true,
        completionIssueError: "",
      });
    }
    try {
      const response = await listMemoirCompletionIssues(projectId, {
        includeClosed: true,
      });
      const issue = response.issues.find((candidate) => candidate.id === issueId) || null;
      const latest = this.options.readState();
      if (latest.currentCompletionIssueId !== issueId) {
        return null;
      }
      if (!issue) {
        this.options.applyPatch({
          completionIssue: null,
          completionIssueError: "本次补充问题已经不存在",
        });
        this.stop();
        return null;
      }
      const justClosed = latest.completionIssue?.status !== "closed"
        && issue.status === "closed";
      this.options.applyPatch({
        completionIssue: issue,
        completionIssueError: "",
        completionIssueNotice: justClosed
          ? outcomeNotice(issue.outcome)
          : latest.completionIssueNotice,
      });
      if (issue.status === "closed") {
        this.stop();
      } else if (
        latest.completionIssueNotice === "正在保存这段回答"
        && !this.options.isSuspended()
      ) {
        this.scheduleRefresh();
      }
      return issue;
    } catch (error) {
      if (this.options.readState().currentCompletionIssueId === issueId) {
        this.options.applyPatch({
          completionIssueError: error instanceof Error
            ? error.message
            : "补充问题加载失败",
        });
      }
      return null;
    } finally {
      if (!silent && this.options.readState().currentCompletionIssueId === issueId) {
        this.options.applyPatch({ completionIssueLoading: false });
      }
    }
  }

  stop(): void {
    this.refreshToken += 1;
    if (this.refreshTimer !== null) {
      clearTimeout(this.refreshTimer);
      this.refreshTimer = null;
    }
  }

  startVerification(): void {
    const state = this.options.readState();
    if (
      state.sessionGoal !== "book_completion"
      || state.completionIssue?.status !== "in_progress"
    ) {
      return;
    }
    this.stop();
    this.options.applyPatch({
      completionIssueNotice: "正在保存这段回答",
      completionIssueError: "",
    });
    this.scheduleRefresh();
  }

  async close(
    outcome: "not_remembered" | "deferred" | "declined",
  ): Promise<void> {
    const state = this.options.readState();
    const { projectId, currentCompletionIssueId } = state;
    if (!projectId || !currentCompletionIssueId || state.completionIssueAction) {
      return;
    }
    this.stop();
    this.options.cancelAssistant(`completion_issue_${outcome}`);
    this.options.applyPatch({
      completionIssueAction: outcome,
      completionIssueError: "",
      completionIssueNotice: "正在保存您的选择",
    });
    const note = {
      not_remembered: "用户暂时想不起来这个问题",
      deferred: "用户选择以后再补这个问题",
      declined: "用户不愿继续讨论这个问题",
    }[outcome];
    try {
      const issue = await closeMemoirCompletionIssue(
        projectId,
        currentCompletionIssueId,
        { outcome, note },
      );
      this.options.applyPatch({
        completionIssue: issue,
        completionIssueNotice: outcomeNotice(issue.outcome),
      });
    } catch (error) {
      this.options.applyPatch({
        completionIssueError: error instanceof Error
          ? error.message
          : "补充状态保存失败",
        completionIssueNotice: "",
      });
    } finally {
      this.options.applyPatch({ completionIssueAction: "" });
    }
  }

  private scheduleRefresh(): void {
    const state = this.options.readState();
    if (
      this.options.isSuspended()
      || state.sessionGoal !== "book_completion"
      || !state.currentCompletionIssueId
      || state.completionIssue?.status !== "in_progress"
      || this.refreshTimer !== null
    ) {
      return;
    }
    const token = this.refreshToken;
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      void this.poll(token);
    }, this.options.refreshIntervalMs ?? REFRESH_INTERVAL_MS);
  }

  private async poll(token: number): Promise<void> {
    if (token !== this.refreshToken || this.options.isSuspended()) {
      return;
    }
    await this.refresh(true);
    if (
      token === this.refreshToken
      && this.options.readState().completionIssue?.status === "in_progress"
      && !this.options.isSuspended()
    ) {
      this.scheduleRefresh();
    }
  }
}
