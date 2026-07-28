type TimerHandle = ReturnType<typeof setTimeout>;

export interface PollingTimerScheduler {
  schedule(callback: () => void, delayMs: number): TimerHandle;
  cancel(timer: TimerHandle): void;
}

const DEFAULT_SCHEDULER: PollingTimerScheduler = {
  schedule(callback, delayMs) {
    const timer = setTimeout(callback, delayMs);
    (timer as unknown as { unref?: () => void }).unref?.();
    return timer;
  },
  cancel(timer) {
    clearTimeout(timer);
  },
};

export class PollingTimer {
  private timer: TimerHandle | null = null;

  constructor(
    private readonly delayMs: number,
    private readonly scheduler: PollingTimerScheduler = DEFAULT_SCHEDULER,
  ) {}

  get pending(): boolean {
    return this.timer !== null;
  }

  schedule(callback: () => void): boolean {
    if (this.timer !== null) {
      return false;
    }
    const timer = this.scheduler.schedule(() => {
      if (this.timer !== timer) {
        return;
      }
      this.timer = null;
      callback();
    }, this.delayMs);
    this.timer = timer;
    return true;
  }

  cancel(): void {
    if (this.timer === null) {
      return;
    }
    this.scheduler.cancel(this.timer);
    this.timer = null;
  }
}
