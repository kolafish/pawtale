import type {
  MemoirFollowupUnableToAnswerResolution,
  MemoirFollowupTask,
  MemoirInterviewType,
  MemoirSessionGoal,
} from "../../domain/voice-chat";
import {
  deferMemoirFollowupTask,
  getMemoirFollowupTask,
  markMemoirFollowupTaskUnableToAnswer,
  stopAskingMemoirFollowupTask,
  switchMemoirFollowupTask,
} from "../../services/memoir-voice-api";

const REFRESH_INTERVAL_MS = 1500;

export interface CompletionTaskState {
  voiceSessionId: string;
  projectId: string;
  interviewType: MemoirInterviewType;
  sessionGoal: MemoirSessionGoal;
  currentFollowupTaskId: string;
  completionTask: MemoirFollowupTask | null;
  completionTaskLoading: boolean;
  completionTaskAction: string;
  completionTaskError: string;
  completionTaskNotice: string;
}

type CompletionTaskPatch = Partial<CompletionTaskState>;

interface CompletionTaskRuntimeOptions {
  readState(): CompletionTaskState;
  applyPatch(patch: CompletionTaskPatch): void;
  isSuspended(): boolean;
  cancelAssistant(reason: string): void;
  requestAssistant(): void;
  refreshIntervalMs?: number;
}

export class VoiceChatCompletionTaskRuntime {
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;
  private refreshToken = 0;

  constructor(private readonly options: CompletionTaskRuntimeOptions) {}

  async refresh(silent = false): Promise<MemoirFollowupTask | null> {
    const state = this.options.readState();
    if (state.sessionGoal !== "book_completion") {
      if (state.completionTask || state.completionTaskLoading || state.completionTaskError) {
        this.options.applyPatch({
          completionTask: null,
          completionTaskLoading: false,
          completionTaskError: "",
          completionTaskNotice: "",
        });
      }
      return null;
    }
    const { projectId, currentFollowupTaskId: taskId } = state;
    if (!projectId || !taskId) {
      this.stop();
      this.options.applyPatch({
        completionTask: null,
        completionTaskLoading: false,
        completionTaskError: "",
      });
      return null;
    }
    if (!silent) {
      this.options.applyPatch({ completionTaskLoading: true, completionTaskError: "" });
    }
    try {
      const task = await getMemoirFollowupTask(projectId, taskId);
      const latest = this.options.readState();
      if (latest.currentFollowupTaskId !== taskId) {
        return null;
      }
      const wasAnswered = latest.completionTask?.status !== "answered" && task.status === "answered";
      this.options.applyPatch({
        completionTask: task,
        completionTaskError: "",
        completionTaskNotice: wasAnswered ? "这项已经补上了" : latest.completionTaskNotice,
      });
      if (task.status !== "in_progress") {
        this.stop();
      } else if (
        latest.completionTaskNotice === "正在核对这段是否补足问题"
        && !this.options.isSuspended()
      ) {
        this.scheduleRefresh();
      }
      return task;
    } catch (error) {
      if (this.options.readState().currentFollowupTaskId === taskId) {
        this.options.applyPatch({
          completionTaskError: error instanceof Error ? error.message : "补访问题加载失败",
        });
      }
      return null;
    } finally {
      if (!silent && this.options.readState().currentFollowupTaskId === taskId) {
        this.options.applyPatch({ completionTaskLoading: false });
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
    if (state.sessionGoal !== "book_completion" || state.completionTask?.status !== "in_progress") {
      return;
    }
    this.stop();
    this.options.applyPatch({
      completionTaskNotice: "正在核对这段是否补足问题",
      completionTaskError: "",
    });
    this.scheduleRefresh();
  }

  async switchTask(note = "用户选择换一个补访问题"): Promise<void> {
    const state = this.options.readState();
    const { projectId, voiceSessionId, currentFollowupTaskId } = state;
    if (!projectId || !voiceSessionId || !currentFollowupTaskId || state.completionTaskAction) {
      return;
    }
    this.stop();
    this.options.cancelAssistant("followup_task_switch");
    this.options.applyPatch({
      completionTaskAction: "switch",
      completionTaskError: "",
      completionTaskNotice: "正在换一个问题",
    });
    let shouldRequestAssistant = false;
    try {
      const task = await switchMemoirFollowupTask(projectId, voiceSessionId, {
        current_task_id: currentFollowupTaskId,
        idempotency_key: this.idempotencyKey("switch"),
        note,
      });
      this.options.applyPatch({
        currentFollowupTaskId: task?.id || "",
        completionTask: task,
        completionTaskNotice: task ? "已换到下一个问题" : "目前没有更多待补的问题",
      });
      shouldRequestAssistant = Boolean(task && state.interviewType === "ai_interview");
    } catch (error) {
      this.options.applyPatch({
        completionTaskError: error instanceof Error ? error.message : "补访问题切换失败",
        completionTaskNotice: "",
      });
    } finally {
      this.options.applyPatch({ completionTaskAction: "" });
    }
    if (shouldRequestAssistant) {
      this.options.requestAssistant();
    }
  }

  async deferTask(): Promise<void> {
    const state = this.options.readState();
    const { projectId, currentFollowupTaskId } = state;
    if (!projectId || !currentFollowupTaskId || state.completionTaskAction) {
      return;
    }
    this.stop();
    this.options.cancelAssistant("followup_task_deferred");
    this.options.applyPatch({
      completionTaskAction: "defer",
      completionTaskError: "",
      completionTaskNotice: "正在保存您的选择",
    });
    try {
      const task = await deferMemoirFollowupTask(projectId, currentFollowupTaskId, {
        idempotency_key: this.idempotencyKey("defer"),
        note: "用户选择以后再补这个问题",
      });
      this.options.applyPatch({ completionTask: task, completionTaskNotice: "已留到以后再补" });
    } catch (error) {
      this.options.applyPatch({
        completionTaskError: error instanceof Error ? error.message : "补访问题暂存失败",
        completionTaskNotice: "",
      });
    } finally {
      this.options.applyPatch({ completionTaskAction: "" });
    }
  }

  async applyUnableToAnswer(
    resolution: MemoirFollowupUnableToAnswerResolution,
  ): Promise<void> {
    const state = this.options.readState();
    const { projectId, currentFollowupTaskId } = state;
    if (!projectId || !currentFollowupTaskId || state.completionTaskAction) {
      return;
    }
    this.stop();
    this.options.cancelAssistant("followup_task_unable_to_answer");
    this.options.applyPatch({
      completionTaskAction: "unable_to_answer",
      completionTaskError: "",
      completionTaskNotice: "正在保存您的选择",
    });
    try {
      const task = resolution === "unable_to_answer"
        ? await markMemoirFollowupTaskUnableToAnswer(projectId, currentFollowupTaskId, {
          idempotency_key: this.idempotencyKey("unable-to-answer"),
          note: "用户暂时想不起来这个具体问题",
        })
        : await deferMemoirFollowupTask(projectId, currentFollowupTaskId, {
          idempotency_key: this.idempotencyKey("ask-family-later"),
          note: "用户暂时想不起来，准备询问家人后再补",
        });
      this.options.applyPatch({
        completionTask: task,
        completionTaskNotice: resolution === "unable_to_answer"
          ? "已记为暂时无法回答"
          : "已留到问过家人以后再补",
      });
    } catch (error) {
      this.options.applyPatch({
        completionTaskError: error instanceof Error ? error.message : "想不起来的处理状态保存失败",
        completionTaskNotice: "",
      });
    } finally {
      this.options.applyPatch({ completionTaskAction: "" });
    }
  }

  async stopAsking(): Promise<void> {
    const state = this.options.readState();
    const { projectId, currentFollowupTaskId } = state;
    if (!projectId || !currentFollowupTaskId || state.completionTaskAction) {
      return;
    }
    this.stop();
    this.options.cancelAssistant("followup_task_closed_without_answer");
    this.options.applyPatch({
      completionTaskAction: "stop_asking",
      completionTaskError: "",
      completionTaskNotice: "正在保存您的选择",
    });
    try {
      const task = await stopAskingMemoirFollowupTask(projectId, currentFollowupTaskId, {
        idempotency_key: this.idempotencyKey("stop-asking"),
        note: "用户明确选择不再询问这个具体问题",
      });
      this.options.applyPatch({
        completionTask: task,
        completionTaskNotice: "以后不再问这个问题，已有内容不受影响",
      });
    } catch (error) {
      this.options.applyPatch({
        completionTaskError: error instanceof Error ? error.message : "停止询问状态保存失败",
        completionTaskNotice: "",
      });
    } finally {
      this.options.applyPatch({ completionTaskAction: "" });
    }
  }

  private idempotencyKey(action: string): string {
    const state = this.options.readState();
    return `miniprogram:${state.voiceSessionId}:${state.currentFollowupTaskId}:${action}:${Date.now()}`;
  }

  private scheduleRefresh(): void {
    const state = this.options.readState();
    if (
      this.options.isSuspended()
      || state.sessionGoal !== "book_completion"
      || !state.currentFollowupTaskId
      || state.completionTask?.status !== "in_progress"
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
      && this.options.readState().completionTask?.status === "in_progress"
      && !this.options.isSuspended()
    ) {
      this.scheduleRefresh();
    }
  }
}
