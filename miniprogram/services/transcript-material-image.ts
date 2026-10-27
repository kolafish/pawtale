import type {
  MemoirMaterialImageGenerationJob,
} from "../domain/memoir-transcript";
import { ApiError } from "./request";

export interface TranscriptMaterialImageTaskState {
  materialImageCreating: boolean;
  materialImagePolling: boolean;
  materialImageError: string;
  materialImageStatusText: string;
  materialImageStatusClass: string;
  materialImageJob: MemoirMaterialImageGenerationJob | null;
}

export interface TranscriptMaterialImageCreateInput {
  utteranceId: string;
  startChar: number;
  endChar: number;
  styleId: string;
  backgroundNote?: string;
}

type CreateMaterialImageJob = (
  utteranceId: string,
  startChar: number,
  endChar: number,
  styleId: string,
  backgroundNote?: string,
) => Promise<MemoirMaterialImageGenerationJob>;
type GetMaterialImageJob = (jobId: string) => Promise<MemoirMaterialImageGenerationJob>;
type MaterialImageStateListener = (patch: Partial<TranscriptMaterialImageTaskState>) => void;
type PollTimer = ReturnType<typeof setTimeout>;

export interface TranscriptMaterialImageControllerOptions {
  createJob: CreateMaterialImageJob;
  getJob: GetMaterialImageJob;
  pollIntervalMs?: number;
  maxPollFailures?: number;
  schedule?: (callback: () => void, delayMs: number) => PollTimer;
  cancelSchedule?: (timer: PollTimer) => void;
}

const DEFAULT_POLL_INTERVAL_MS = 1500;
const DEFAULT_MAX_POLL_FAILURES = 3;

export function initialTranscriptMaterialImageTaskState(): TranscriptMaterialImageTaskState {
  return {
    materialImageCreating: false,
    materialImagePolling: false,
    materialImageError: "",
    materialImageStatusText: "",
    materialImageStatusClass: "",
    materialImageJob: null,
  };
}

function materialImageJobStatusText(job: MemoirMaterialImageGenerationJob): string {
  if (job.status === "succeeded" && job.asset_status === "ready") {
    return "图片已生成，已放入图片素材";
  }
  if (job.status === "failed" || job.asset_status === "failed") {
    return job.error_message || "图片生成失败";
  }
  if (job.status === "running" || job.asset_status === "running") {
    return job.stage === "generating" ? "正在根据口述生成图片" : "正在准备图片";
  }
  if (job.status === "skipped") {
    return "图片任务已停止，请重试";
  }
  return "图片任务已创建，等待生成";
}

function isTransientPollError(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.statusCode === 0 || error.statusCode >= 500;
  }
  return true;
}

export class TranscriptMaterialImageController {
  private state = initialTranscriptMaterialImageTaskState();
  private listener: MaterialImageStateListener | null = null;
  private pollTimer: PollTimer | null = null;
  private token = 0;
  private pollFailureCount = 0;

  constructor(private readonly options: TranscriptMaterialImageControllerOptions) {}

  setStateListener(listener: MaterialImageStateListener): void {
    this.listener = listener;
  }

  reset(): void {
    this.invalidateOperations();
    this.replaceState(initialTranscriptMaterialImageTaskState());
  }

  clearResult(): void {
    this.invalidateOperations();
    this.patchState(initialTranscriptMaterialImageTaskState());
  }

  cancel(): void {
    this.invalidateOperations();
    this.patchState({
      materialImageCreating: false,
      materialImagePolling: false,
    });
  }

  async create(
    input: TranscriptMaterialImageCreateInput,
  ): Promise<MemoirMaterialImageGenerationJob | null> {
    this.invalidateOperations();
    const requestToken = this.token;
    this.patchState({
      materialImageCreating: true,
      materialImagePolling: false,
      materialImageError: "",
      materialImageStatusText: "正在创建图片任务",
      materialImageStatusClass: "running",
      materialImageJob: null,
    });
    try {
      const job = await this.options.createJob(
        input.utteranceId,
        input.startChar,
        input.endChar,
        input.styleId,
        input.backgroundNote,
      );
      if (!this.isCurrent(requestToken)) {
        return null;
      }
      this.applyJob(job, requestToken);
      return job;
    } catch (error) {
      if (!this.isCurrent(requestToken)) {
        return null;
      }
      this.patchState({
        materialImageError: error instanceof Error ? error.message : "图片任务创建失败",
        materialImageStatusText: "图片任务创建失败",
        materialImageStatusClass: "failed",
      });
      return null;
    } finally {
      if (this.isCurrent(requestToken)) {
        this.patchState({ materialImageCreating: false });
      }
    }
  }

  async refresh(jobId: string, requestToken = this.token): Promise<void> {
    if (!this.isCurrent(requestToken)) {
      return;
    }
    this.patchState({
      materialImagePolling: true,
      ...(!this.state.materialImageStatusText ? {
        materialImageStatusText: "正在生成图片",
        materialImageStatusClass: "running",
      } : {}),
    });
    try {
      const job = await this.options.getJob(jobId);
      if (!this.isCurrent(requestToken)) {
        return;
      }
      this.pollFailureCount = 0;
      this.applyJob(job, requestToken);
    } catch (error) {
      if (!this.isCurrent(requestToken)) {
        return;
      }
      this.pollFailureCount += 1;
      if (isTransientPollError(error) && this.pollFailureCount < this.maxPollFailures()) {
        this.patchState({
          materialImagePolling: true,
          materialImageError: "",
          materialImageStatusText: "图片状态刷新中断，正在重试",
          materialImageStatusClass: "running",
        });
        this.schedulePoll(jobId, requestToken);
        return;
      }
      this.clearPollTimer();
      this.pollFailureCount = 0;
      this.patchState({
        materialImagePolling: false,
        materialImageError: error instanceof Error ? error.message : "图片状态刷新失败",
        materialImageStatusText: "图片状态刷新失败",
        materialImageStatusClass: "failed",
      });
    }
  }

  dispose(): void {
    this.invalidateOperations();
    this.state = initialTranscriptMaterialImageTaskState();
    this.listener = null;
  }

  private applyJob(job: MemoirMaterialImageGenerationJob, requestToken: number): void {
    const running = job.status === "pending" || job.status === "running";
    const failed = job.status === "failed" || job.asset_status === "failed";
    this.patchState({
      materialImageJob: job,
      materialImagePolling: running,
      materialImageStatusText: materialImageJobStatusText(job),
      materialImageStatusClass: failed ? "failed" : (running ? "running" : "succeeded"),
      materialImageError: failed ? (job.error_message || "图片生成失败") : "",
    });
    if (running) {
      this.schedulePoll(job.job_id, requestToken);
      return;
    }
    this.clearPollTimer();
  }

  private invalidateOperations(): void {
    this.clearPollTimer();
    this.pollFailureCount = 0;
    this.token += 1;
  }

  private isCurrent(requestToken: number): boolean {
    return requestToken === this.token;
  }

  private schedulePoll(jobId: string, requestToken: number): void {
    this.clearPollTimer();
    const schedule = this.options.schedule || ((callback, delayMs) => setTimeout(callback, delayMs));
    this.pollTimer = schedule(() => {
      void this.refresh(jobId, requestToken);
    }, this.options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS);
  }

  private clearPollTimer(): void {
    if (this.pollTimer === null) {
      return;
    }
    const cancelSchedule = this.options.cancelSchedule || clearTimeout;
    cancelSchedule(this.pollTimer);
    this.pollTimer = null;
  }

  private maxPollFailures(): number {
    return this.options.maxPollFailures ?? DEFAULT_MAX_POLL_FAILURES;
  }

  private patchState(patch: Partial<TranscriptMaterialImageTaskState>): void {
    this.state = { ...this.state, ...patch };
    this.listener?.(patch);
  }

  private replaceState(state: TranscriptMaterialImageTaskState): void {
    this.state = state;
    this.listener?.(state);
  }
}
