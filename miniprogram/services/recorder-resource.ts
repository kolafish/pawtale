export interface RecorderFrameResult {
  frameBuffer: ArrayBuffer;
  isLastFrame: boolean;
}

export interface RecorderResourceHandlers {
  onStart?: () => void;
  onFrame?: (result: RecorderFrameResult) => void;
  onError?: (message: string) => void;
  onStop?: () => void;
}

type RecorderManagerFactory = () => WechatMiniprogram.RecorderManager;

const STOP_SETTLE_TIMEOUT_MS = 1500;

export class WechatRecorderResource {
  private manager: WechatMiniprogram.RecorderManager | null = null;
  private eventsBound = false;
  private ownerId = "";
  private handlers: RecorderResourceHandlers | null = null;
  private stopPromise: Promise<void> | null = null;
  private stopWaiters: Array<() => void> = [];

  constructor(
    private readonly managerFactory: RecorderManagerFactory = () => wx.getRecorderManager(),
  ) {}

  currentOwner(): string {
    return this.ownerId;
  }

  isOwnedBy(ownerId: string): boolean {
    return Boolean(ownerId) && this.ownerId === ownerId;
  }

  isBusy(): boolean {
    return Boolean(this.ownerId);
  }

  start(
    ownerId: string,
    options: WechatMiniprogram.RecorderManagerStartOption,
    handlers: RecorderResourceHandlers,
  ): boolean {
    if (!ownerId || this.ownerId || this.stopPromise) {
      return false;
    }
    this.ensureManager();
    this.ownerId = ownerId;
    this.handlers = handlers;
    try {
      this.manager?.start(options);
      return true;
    } catch (error) {
      this.releaseOwner();
      throw error;
    }
  }

  stop(ownerId: string): void {
    void this.stopAndWait(ownerId);
  }

  stopAndWait(ownerId: string): Promise<void> {
    if (!this.isOwnedBy(ownerId)) {
      return Promise.resolve();
    }
    if (this.stopPromise) {
      return this.stopPromise;
    }
    this.stopPromise = (async () => {
      const stopped = this.waitForStop();
      try {
        this.manager?.stop();
      } catch {
        this.handleStop();
      }
      await stopped;
    })().finally(() => {
      this.stopPromise = null;
    });
    return this.stopPromise;
  }

  forceRelease(ownerId: string): void {
    if (this.isOwnedBy(ownerId)) {
      this.releaseOwner();
    }
  }

  private ensureManager(): void {
    if (!this.manager) {
      this.manager = this.managerFactory();
    }
    if (this.eventsBound) {
      return;
    }
    this.manager.onStart?.(() => {
      this.handlers?.onStart?.();
    });
    this.manager.onFrameRecorded?.((result) => {
      this.handlers?.onFrame?.(result);
    });
    this.manager.onError?.((error) => {
      this.handleError(error.errMsg || "录音失败");
    });
    this.manager.onStop?.(() => {
      this.handleStop();
    });
    this.eventsBound = true;
  }

  private handleError(message: string): void {
    const onError = this.handlers?.onError;
    this.releaseOwner();
    onError?.(message);
  }

  private handleStop(): void {
    const onStop = this.handlers?.onStop;
    this.releaseOwner();
    onStop?.();
  }

  private waitForStop(): Promise<void> {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.handleStop();
      }, STOP_SETTLE_TIMEOUT_MS);
      this.stopWaiters.push(() => {
        clearTimeout(timer);
        resolve();
      });
    });
  }

  private releaseOwner(): void {
    this.ownerId = "";
    this.handlers = null;
    const waiters = this.stopWaiters;
    this.stopWaiters = [];
    waiters.forEach((resolve) => resolve());
  }
}

export const sharedRecorderResource = new WechatRecorderResource();
