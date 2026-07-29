import { BACKEND_WS_BASE_URL } from "../config";
import { ensureBackendSession } from "./auth";
import { readSessionCookie } from "./request";

export type DictationState =
  | "idle"
  | "connecting"
  | "ready"
  | "starting"
  | "recording"
  | "stopping"
  | "error";

interface GatewayEvent {
  type?: string;
  text?: string;
  error?: string;
}

interface SocketTask {
  send(options: { data: string; fail?: (error: { errMsg?: string }) => void }): void;
  close(options?: { code?: number; reason?: string }): void;
  onOpen(callback: () => void): void;
  onMessage(callback: (message: { data: string | ArrayBuffer }) => void): void;
  onError(callback: (error: { errMsg?: string }) => void): void;
  onClose(callback: () => void): void;
}

interface RecorderManager {
  start(options: Record<string, unknown>): void;
  stop(): void;
  onStart(callback: () => void): void;
  onFrameRecorded(
    callback: (result: { frameBuffer: ArrayBuffer; isLastFrame: boolean }) => void,
  ): void;
  onStop(callback: () => void): void;
  onError(callback: (error: { errMsg?: string }) => void): void;
  offStart?(callback: () => void): void;
  offFrameRecorded?(
    callback: (result: { frameBuffer: ArrayBuffer; isLastFrame: boolean }) => void,
  ): void;
  offStop?(callback: () => void): void;
  offError?(callback: (error: { errMsg?: string }) => void): void;
}

export interface DictationCallbacks {
  onState(state: DictationState): void;
  onTranscript(text: string, final: boolean): void;
  onError(message: string): void;
}

export interface ArticleDictationOptions {
  projectId: string;
  documentId: string;
  callbacks: DictationCallbacks;
}

const CONNECT_TIMEOUT_MS = 10000;
const RECORDER_START_TIMEOUT_MS = 4000;
const FINISH_TIMEOUT_MS = 15000;
const RECORDING_DURATION_MS = 10 * 60 * 1000;

function gatewayErrorMessage(code = ""): string {
  const messages: Record<string, string> = {
    article_dictation_unavailable: "当前文章不可口述，请返回后重新打开",
    voice_gateway_auth_failed: "登录状态已失效，请返回后重试",
    voice_gateway_capacity_reached: "当前语音服务繁忙，请稍后再试",
    voice_gateway_user_session_exists: "已有语音输入正在收尾，请稍后再试",
    asr_finish_timeout: "语音识别收尾超时，请重试",
  };
  if (messages[code]) {
    return messages[code];
  }
  if (code.startsWith("asr_") || code.startsWith("provider_") || code.startsWith("volc")) {
    return "语音识别服务异常，请稍后重试";
  }
  return "语音连接异常，请稍后重试";
}

function connectionErrorMessage(message = ""): string {
  const normalized = message.toLowerCase();
  if (normalized.includes("timeout") || normalized.includes("超时")) {
    return "语音连接超时，请检查网络后重试";
  }
  if (normalized.includes("403") || normalized.includes("forbidden")) {
    return "语音连接被拒绝，请返回后重试";
  }
  return "语音连接失败，请检查网络后重试";
}

function recorderErrorMessage(message = ""): string {
  const normalized = message.toLowerCase();
  if (normalized.includes("auth") || normalized.includes("permission")) {
    return "没有麦克风权限，请在微信设置中允许使用麦克风";
  }
  if (normalized.includes("busy") || normalized.includes("is recording")) {
    return "麦克风正在使用中，请稍后重试";
  }
  return "麦克风启动失败，请稍后重试";
}

function gatewayUrl(projectId: string, documentId: string): string {
  const base = BACKEND_WS_BASE_URL.replace(/\/$/, "");
  return `${base}/api/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(documentId)}/dictation`;
}

export class ArticleDictationClient {
  private state: DictationState = "idle";
  private socket: SocketTask | null = null;
  private recorder: RecorderManager | null = null;
  private socketOpen = false;
  private turnStarted = false;
  private audioEndSent = false;
  private connectPromise: Promise<void> | null = null;
  private startPromise: Promise<void> | null = null;
  private startResolve: (() => void) | null = null;
  private startReject: ((error: Error) => void) | null = null;
  private startTimer: ReturnType<typeof setTimeout> | null = null;
  private eventWaiters = new Map<string, Array<{ resolve(): void; reject(error: Error): void }>>();
  private closePromise: Promise<void> | null = null;
  private closing = false;

  constructor(private readonly options: ArticleDictationOptions) {}

  async connect(): Promise<void> {
    if (this.state === "ready") {
      return;
    }
    if (this.connectPromise) {
      return this.connectPromise;
    }
    this.connectPromise = this.openSocket().finally(() => {
      this.connectPromise = null;
    });
    return this.connectPromise;
  }

  private async openSocket(): Promise<void> {
    await ensureBackendSession();
    this.setState("connecting");
    const cookie = readSessionCookie();
    await new Promise<void>((resolve, reject) => {
      let settled = false;
      const timer = setTimeout(() => {
        settle(() => reject(new Error("语音连接超时，请检查网络后重试")));
        this.socket?.close({ code: 1000, reason: "dictation connect timeout" });
      }, CONNECT_TIMEOUT_MS);
      const settle = (callback: () => void) => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timer);
        callback();
      };
      const socket = wx.connectSocket({
        url: gatewayUrl(this.options.projectId, this.options.documentId),
        header: cookie ? { Cookie: cookie } : {},
        fail(error: { errMsg?: string }) {
          settle(() => reject(new Error(connectionErrorMessage(error.errMsg))));
        },
      }) as SocketTask;
      this.socket = socket;
      socket.onOpen(() => {
        this.socketOpen = true;
      });
      socket.onMessage((message) => {
        const event = this.parseEvent(message.data);
        if (event.type === "session.ready") {
          this.setState("ready");
          settle(resolve);
        }
        if (event.type === "session.error") {
          settle(() => reject(new Error(gatewayErrorMessage(event.error))));
        }
        this.handleEvent(event);
      });
      socket.onError((error) => {
        if (this.closing) {
          return;
        }
        const message = connectionErrorMessage(error.errMsg);
        settle(() => reject(new Error(message)));
        this.fail(message);
      });
      socket.onClose(() => {
        this.socketOpen = false;
        if (!this.closing && this.state !== "idle") {
          settle(() => reject(new Error("语音连接已关闭")));
          this.fail("语音连接已关闭，请重新开始口述");
        }
      });
    }).catch((error) => {
      this.setState("error");
      throw error;
    });
  }

  start(): Promise<void> {
    if (this.state !== "ready") {
      return Promise.reject(new Error("语音连接尚未就绪"));
    }
    this.setState("starting");
    this.turnStarted = false;
    this.audioEndSent = false;
    this.bindRecorder();
    this.startPromise = new Promise<void>((resolve, reject) => {
      this.startResolve = resolve;
      this.startReject = reject;
    }).finally(() => {
      this.startPromise = null;
      this.startResolve = null;
      this.startReject = null;
    });
    try {
      this.startTimer = setTimeout(() => {
        this.fail("麦克风启动超时，请重试");
      }, RECORDER_START_TIMEOUT_MS);
      this.recorder?.start({
        duration: RECORDING_DURATION_MS,
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: 96000,
        format: "PCM",
        frameSize: 4,
      });
    } catch (error) {
      this.clearStartTimer();
      this.fail(recorderErrorMessage(error instanceof Error ? error.message : ""));
    }
    return this.startPromise;
  }

  async stop(): Promise<void> {
    if (this.state === "starting") {
      this.clearStartTimer();
      this.startReject?.(new Error("已停止口述"));
      this.recorder?.stop();
      this.setState("ready");
      return;
    }
    if (this.state !== "recording" && this.state !== "stopping") {
      return;
    }
    const finished = this.waitForEvent("audio.ended", FINISH_TIMEOUT_MS);
    if (this.state === "recording") {
      this.setState("stopping");
      this.recorder?.stop();
    }
    await finished;
  }

  close(): Promise<void> {
    if (this.closePromise) {
      return this.closePromise;
    }
    this.closing = true;
    this.closePromise = (async () => {
      try {
        await this.stop();
      } catch {
        // The page is leaving; close the transport even if ASR finalization timed out.
      }
      this.unbindRecorder();
      if (this.socketOpen) {
        const stopped = this.waitForEvent("session.stopped", 3000);
        this.send({ type: "session.stop" });
        await stopped.catch(() => undefined);
      }
      this.socket?.close({ code: 1000, reason: "dictation page closed" });
      this.socket = null;
      this.socketOpen = false;
      this.setState("idle");
    })();
    return this.closePromise;
  }

  private bindRecorder(): void {
    if (this.recorder) {
      return;
    }
    this.recorder = wx.getRecorderManager() as RecorderManager;
    this.recorder.onStart(this.handleRecorderStart);
    this.recorder.onFrameRecorded(this.handleRecorderFrame);
    this.recorder.onStop(this.handleRecorderStop);
    this.recorder.onError(this.handleRecorderError);
  }

  private readonly handleRecorderStart = () => {
    this.clearStartTimer();
    if (this.state !== "starting") {
      this.recorder?.stop();
      return;
    }
    this.turnStarted = true;
    this.send({ type: "session.start" });
    this.setState("recording");
    this.startResolve?.();
  };

  private readonly handleRecorderFrame = (
    frame: { frameBuffer: ArrayBuffer; isLastFrame: boolean },
  ) => {
    if (this.state !== "recording" || !this.turnStarted) {
      return;
    }
    if (frame.frameBuffer.byteLength) {
      this.send({
        type: "audio.frame",
        audio: wx.arrayBufferToBase64(frame.frameBuffer),
      });
    }
    if (frame.isLastFrame) {
      this.finishAudio();
    }
  };

  private readonly handleRecorderStop = () => this.finishAudio();

  private readonly handleRecorderError = (error: { errMsg?: string }) => {
    this.clearStartTimer();
    this.fail(recorderErrorMessage(error.errMsg));
  };

  private unbindRecorder(): void {
    this.recorder?.offStart?.(this.handleRecorderStart);
    this.recorder?.offFrameRecorded?.(this.handleRecorderFrame);
    this.recorder?.offStop?.(this.handleRecorderStop);
    this.recorder?.offError?.(this.handleRecorderError);
    this.recorder = null;
  }

  private finishAudio(): void {
    if (!this.turnStarted || this.audioEndSent) {
      return;
    }
    this.audioEndSent = true;
    this.turnStarted = false;
    this.setState("stopping");
    this.send({ type: "audio.end" });
  }

  private handleEvent(event: GatewayEvent): void {
    if (event.type === "asr.partial") {
      this.options.callbacks.onTranscript(event.text || "", false);
      return;
    }
    if (event.type === "asr.final") {
      this.options.callbacks.onTranscript(event.text || "", true);
      return;
    }
    if (event.type === "audio.ended") {
      this.setState("ready");
      this.resolveWaiters("audio.ended");
      return;
    }
    if (event.type === "session.stopped") {
      this.resolveWaiters("session.stopped");
      return;
    }
    if (event.type === "session.error") {
      this.fail(gatewayErrorMessage(event.error));
    }
  }

  private parseEvent(data: string | ArrayBuffer): GatewayEvent {
    if (typeof data !== "string") {
      return { type: "session.error", error: "invalid_gateway_event" };
    }
    try {
      const event = JSON.parse(data) as GatewayEvent;
      return event && typeof event === "object" ? event : {};
    } catch {
      return { type: "session.error", error: "invalid_gateway_event" };
    }
  }

  private send(payload: Record<string, unknown>): void {
    if (!this.socketOpen || !this.socket) {
      return;
    }
    this.socket.send({
      data: JSON.stringify(payload),
      fail: () => this.fail("语音数据发送失败，请重试"),
    });
  }

  private waitForEvent(type: string, timeoutMs: number): Promise<void> {
    return new Promise((resolve, reject) => {
      const waiter = {
        resolve: () => {
          clearTimeout(timer);
          resolve();
        },
        reject: (error: Error) => {
          clearTimeout(timer);
          reject(error);
        },
      };
      const timer = setTimeout(() => {
        this.removeWaiter(type, waiter);
        reject(new Error(type === "audio.ended" ? "语音识别收尾超时" : "语音连接关闭超时"));
      }, timeoutMs);
      this.eventWaiters.set(type, [...(this.eventWaiters.get(type) || []), waiter]);
    });
  }

  private resolveWaiters(type: string): void {
    const waiters = this.eventWaiters.get(type) || [];
    this.eventWaiters.delete(type);
    waiters.forEach((waiter) => waiter.resolve());
  }

  private removeWaiter(
    type: string,
    waiter: { resolve(): void; reject(error: Error): void },
  ): void {
    this.eventWaiters.set(
      type,
      (this.eventWaiters.get(type) || []).filter((item) => item !== waiter),
    );
  }

  private rejectWaiters(message: string): void {
    const error = new Error(message);
    this.eventWaiters.forEach((waiters) => {
      waiters.forEach((waiter) => waiter.reject(error));
    });
    this.eventWaiters.clear();
  }

  private fail(message: string): void {
    if (this.state === "error" || this.state === "idle") {
      return;
    }
    this.clearStartTimer();
    const capturing =
      this.state === "starting" || this.state === "recording" || this.state === "stopping";
    if (this.state === "starting") {
      this.startReject?.(new Error(message));
    }
    this.turnStarted = false;
    if (capturing) {
      this.recorder?.stop();
    }
    this.setState("error");
    this.rejectWaiters(message);
    this.options.callbacks.onError(message);
  }

  private clearStartTimer(): void {
    if (this.startTimer) {
      clearTimeout(this.startTimer);
      this.startTimer = null;
    }
  }

  private setState(state: DictationState): void {
    this.state = state;
    this.options.callbacks.onState(state);
  }
}
