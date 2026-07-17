import type {
  MemoirCompletionUnableToAnswerResolution,
  MemoirCompletionItem,
  MemoirInterviewType,
  MemoirSessionGoal,
} from "../../domain/voice-chat";
import {
  deferMemoirCompletionItem,
  getMemoirCompletionItem,
  markMemoirCompletionItemUnableToAnswer,
  switchMemoirCompletionItem,
} from "../../services/memoir-voice-api";

const REFRESH_INTERVAL_MS = 1500;

export interface CompletionItemState {
  voiceSessionId: string;
  projectId: string;
  interviewType: MemoirInterviewType;
  sessionGoal: MemoirSessionGoal;
  currentCompletionItemId: string;
  completionItem: MemoirCompletionItem | null;
  completionItemLoading: boolean;
  completionItemAction: string;
  completionItemError: string;
  completionItemNotice: string;
}

type CompletionItemPatch = Partial<CompletionItemState>;

interface CompletionItemRuntimeOptions {
  readState(): CompletionItemState;
  applyPatch(patch: CompletionItemPatch): void;
  isSuspended(): boolean;
  cancelAssistant(reason: string): void;
  requestAssistant(): void;
  refreshIntervalMs?: number;
}

export class VoiceChatCompletionItemRuntime {
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;
  private refreshToken = 0;

  constructor(private readonly options: CompletionItemRuntimeOptions) {}

  async refresh(silent = false): Promise<MemoirCompletionItem | null> {
    const state = this.options.readState();
    if (state.sessionGoal !== "book_completion") {
      if (state.completionItem || state.completionItemLoading || state.completionItemError) {
        this.options.applyPatch({
          completionItem: null,
          completionItemLoading: false,
          completionItemError: "",
          completionItemNotice: "",
        });
      }
      return null;
    }
    const { projectId, currentCompletionItemId: itemId } = state;
    if (!projectId || !itemId) {
      this.stop();
      this.options.applyPatch({
        completionItem: null,
        completionItemLoading: false,
        completionItemError: "",
      });
      return null;
    }
    if (!silent) {
      this.options.applyPatch({ completionItemLoading: true, completionItemError: "" });
    }
    try {
      const item = await getMemoirCompletionItem(projectId, itemId);
      const latest = this.options.readState();
      if (latest.currentCompletionItemId !== itemId) {
        return null;
      }
      const wasAnswered = latest.completionItem?.status !== "resolved" && item.status === "resolved";
      this.options.applyPatch({
        completionItem: item,
        completionItemError: "",
        completionItemNotice: wasAnswered ? "这项已经补上了" : latest.completionItemNotice,
      });
      if (item.status !== "in_progress") {
        this.stop();
      } else if (
        latest.completionItemNotice === "正在核对这段是否补足问题"
        && !this.options.isSuspended()
      ) {
        this.scheduleRefresh();
      }
      return item;
    } catch (error) {
      if (this.options.readState().currentCompletionItemId === itemId) {
        this.options.applyPatch({
          completionItemError: error instanceof Error ? error.message : "补全问题加载失败",
        });
      }
      return null;
    } finally {
      if (!silent && this.options.readState().currentCompletionItemId === itemId) {
        this.options.applyPatch({ completionItemLoading: false });
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
    if (state.sessionGoal !== "book_completion" || state.completionItem?.status !== "in_progress") {
      return;
    }
    this.stop();
    this.options.applyPatch({
      completionItemNotice: "正在核对这段是否补足问题",
      completionItemError: "",
    });
    this.scheduleRefresh();
  }

  async switchItem(note = "用户选择换一个补全问题"): Promise<void> {
    const state = this.options.readState();
    const { projectId, voiceSessionId, currentCompletionItemId } = state;
    if (!projectId || !voiceSessionId || !currentCompletionItemId || state.completionItemAction) {
      return;
    }
    this.stop();
    this.options.cancelAssistant("completion_item_switch");
    this.options.applyPatch({
      completionItemAction: "switch",
      completionItemError: "",
      completionItemNotice: "正在换一个问题",
    });
    let shouldRequestAssistant = false;
    try {
      const item = await switchMemoirCompletionItem(projectId, voiceSessionId, {
        current_item_id: currentCompletionItemId,
        note,
      });
      this.options.applyPatch({
        currentCompletionItemId: item?.id || "",
        completionItem: item,
        completionItemNotice: item ? "已换到下一个问题" : "目前没有更多待补的问题",
      });
      shouldRequestAssistant = Boolean(item && state.interviewType === "ai_interview");
    } catch (error) {
      this.options.applyPatch({
        completionItemError: error instanceof Error ? error.message : "补全问题切换失败",
        completionItemNotice: "",
      });
    } finally {
      this.options.applyPatch({ completionItemAction: "" });
    }
    if (shouldRequestAssistant) {
      this.options.requestAssistant();
    }
  }

  async deferItem(): Promise<void> {
    const state = this.options.readState();
    const { projectId, currentCompletionItemId } = state;
    if (!projectId || !currentCompletionItemId || state.completionItemAction) {
      return;
    }
    this.stop();
    this.options.cancelAssistant("completion_item_deferred");
    this.options.applyPatch({
      completionItemAction: "defer",
      completionItemError: "",
      completionItemNotice: "正在保存您的选择",
    });
    try {
      const item = await deferMemoirCompletionItem(projectId, currentCompletionItemId, {
        note: "用户选择以后再补这个问题",
      });
      this.options.applyPatch({ completionItem: item, completionItemNotice: "已留到以后再补" });
    } catch (error) {
      this.options.applyPatch({
        completionItemError: error instanceof Error ? error.message : "补全问题暂存失败",
        completionItemNotice: "",
      });
    } finally {
      this.options.applyPatch({ completionItemAction: "" });
    }
  }

  async applyUnableToAnswer(
    resolution: MemoirCompletionUnableToAnswerResolution,
  ): Promise<void> {
    const state = this.options.readState();
    const { projectId, currentCompletionItemId } = state;
    if (!projectId || !currentCompletionItemId || state.completionItemAction) {
      return;
    }
    this.stop();
    this.options.cancelAssistant("completion_item_unable_to_answer");
    this.options.applyPatch({
      completionItemAction: "unable_to_answer",
      completionItemError: "",
      completionItemNotice: "正在保存您的选择",
    });
    try {
      const item = resolution === "unable_to_answer"
        ? await markMemoirCompletionItemUnableToAnswer(projectId, currentCompletionItemId, {
          note: "用户暂时想不起来这个具体问题",
        })
        : await deferMemoirCompletionItem(projectId, currentCompletionItemId, {
          note: "用户暂时想不起来，准备询问家人后再补",
        });
      this.options.applyPatch({
        completionItem: item,
        completionItemNotice: resolution === "unable_to_answer"
          ? "已记为暂时无法回答"
          : "已留到问过家人以后再补",
      });
    } catch (error) {
      this.options.applyPatch({
        completionItemError: error instanceof Error ? error.message : "想不起来的处理状态保存失败",
        completionItemNotice: "",
      });
    } finally {
      this.options.applyPatch({ completionItemAction: "" });
    }
  }

  private scheduleRefresh(): void {
    const state = this.options.readState();
    if (
      this.options.isSuspended()
      || state.sessionGoal !== "book_completion"
      || !state.currentCompletionItemId
      || state.completionItem?.status !== "in_progress"
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
      && this.options.readState().completionItem?.status === "in_progress"
      && !this.options.isSuspended()
    ) {
      this.scheduleRefresh();
    }
  }
}
