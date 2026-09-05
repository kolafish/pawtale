import { ApiError, readSessionCookie, request } from "./request";

type AuditEventType = "page_view" | "page_leave" | "feature_start" | "feature_end" | "action" | "heartbeat";
type AuditExitReason = "app_background" | "page_unload" | "logout" | "timeout" | "navigate" | "unknown";

interface AuditEventPayload {
  client_event_id: string;
  event_type: AuditEventType;
  occurred_at: string;
  event_date: string;
  page_path: string;
  feature_code: string;
  project_id?: string;
  voice_session_id?: string;
  duration_ms?: number;
  exit_reason?: string;
  metadata_json?: Record<string, unknown>;
}

interface AuditEventsResponse {
  accepted: number;
  duplicate: number;
  session_id: string;
}

interface ActivePage {
  pagePath: string;
  featureCode: string;
  shownAtMs: number;
  lastTickMs: number;
}

type PageDefinition = Record<string, unknown> & {
  onShow?: (this: PageInstance, ...args: unknown[]) => unknown;
  onHide?: (this: PageInstance, ...args: unknown[]) => unknown;
  onUnload?: (this: PageInstance, ...args: unknown[]) => unknown;
};

interface PageInstance {
  route?: string;
  __route__?: string;
}

type PageFactory = (definition: PageDefinition) => void;

const PAGE_FEATURES: Record<string, string> = {
  "/pages/home/index": "home.view",
  "/pages/login/index": "account.login",
  "/pages/voice-authorization/index": "voice_authorization.view",
  "/pages/voice-chat/index": "voice_chat.session",
  "/pages/transcripts/index": "transcript.view",
  "/pages/transcript-detail/index": "transcript.view",
  "/pages/transcript-media/index": "transcript.view",
  "/pages/book/index": "book.view",
  "/pages/book-completion/index": "book.view",
  "/pages/revision-workbench/index": "book.view",
  "/pages/image-assets/index": "book.view",
  "/pages/chapter-workspace/index": "book.view",
  "/pages/book-export/index": "book.view",
  "/pages/book-reader/index": "book.view",
  "/pages/voice-progress/index": "voice_progress.view",
  "/pages/projects/index": "project.manage",
  "/pages/project-edit/index": "project.manage",
  "/pages/voice-settings/index": "voice_settings.view",
  "/pages/me/index": "account.profile",
};

const HEARTBEAT_INTERVAL_MS = 30_000;
const MAX_QUEUE_EVENTS = 200;
const FLUSH_BATCH_SIZE = 50;

let clientSessionId = newClientSessionId();
let eventSequence = 0;
let activePage: ActivePage | null = null;
let queue: AuditEventPayload[] = [];
let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
let flushPromise: Promise<void> | null = null;
let installed = false;
let originalPageFactory: PageFactory | null = null;
let nowFn: () => Date = () => new Date();

function newClientSessionId(): string {
  return `mp-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function now(): Date {
  return nowFn();
}

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function localDate(value: Date): string {
  return `${value.getFullYear()}-${pad2(value.getMonth() + 1)}-${pad2(value.getDate())}`;
}

function normalizePagePath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) {
    return "";
  }
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

export function auditFeatureForPage(pagePath: string): string {
  return PAGE_FEATURES[normalizePagePath(pagePath)] || "page.view";
}

function pagePathFromContext(context: PageInstance): string {
  return normalizePagePath(context.route || context.__route__ || "");
}

function nextEventId(eventTime: Date): string {
  eventSequence += 1;
  return `${clientSessionId}-${eventTime.getTime().toString(36)}-${eventSequence}`;
}

function eventPayload(
  eventType: AuditEventType,
  pagePath: string,
  featureCode: string,
  options: {
    eventTime?: Date;
    durationMs?: number;
    exitReason?: AuditExitReason | string;
    metadata?: Record<string, unknown>;
    projectId?: string;
    voiceSessionId?: string;
  } = {},
): AuditEventPayload {
  const eventTime = options.eventTime || now();
  return {
    client_event_id: nextEventId(eventTime),
    event_type: eventType,
    occurred_at: eventTime.toISOString(),
    event_date: localDate(eventTime),
    page_path: normalizePagePath(pagePath),
    feature_code: featureCode,
    ...(options.projectId ? { project_id: options.projectId } : {}),
    ...(options.voiceSessionId ? { voice_session_id: options.voiceSessionId } : {}),
    duration_ms: Math.max(0, Math.round(options.durationMs || 0)),
    ...(options.exitReason ? { exit_reason: options.exitReason } : {}),
    ...(options.metadata ? { metadata_json: options.metadata } : {}),
  };
}

function enqueue(event: AuditEventPayload): void {
  queue.push(event);
  if (queue.length > MAX_QUEUE_EVENTS) {
    queue = queue.slice(queue.length - MAX_QUEUE_EVENTS);
  }
  if (queue.length >= FLUSH_BATCH_SIZE) {
    void flushAuditEvents();
  }
}

function stopHeartbeat(): void {
  if (heartbeatTimer !== null) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

function startHeartbeat(): void {
  stopHeartbeat();
  heartbeatTimer = setInterval(() => {
    trackAuditHeartbeat();
  }, HEARTBEAT_INTERVAL_MS);
}

function discardOrRequeue(sentEvents: AuditEventPayload[], error: unknown): void {
  if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
    return;
  }
  queue = [...sentEvents, ...queue].slice(-MAX_QUEUE_EVENTS);
}

function hasSessionCookie(): boolean {
  try {
    return Boolean(readSessionCookie());
  } catch {
    return false;
  }
}

export function flushAuditEvents(): Promise<void> {
  if (flushPromise) {
    return flushPromise;
  }
  if (queue.length === 0 || !hasSessionCookie()) {
    if (!hasSessionCookie()) {
      queue = [];
    }
    return Promise.resolve();
  }

  const events = queue.slice(0, FLUSH_BATCH_SIZE);
  queue = queue.slice(events.length);
  flushPromise = request<AuditEventsResponse>("/api/audit/events", {
    method: "POST",
    data: {
      client_session_id: clientSessionId,
      platform: "miniprogram",
      events,
    },
    timeoutProfile: "telemetry",
  })
    .then(() => undefined)
    .catch((error) => {
      discardOrRequeue(events, error);
    })
    .finally(() => {
      flushPromise = null;
      if (queue.length >= FLUSH_BATCH_SIZE) {
        void flushAuditEvents();
      }
    });
  return flushPromise;
}

export function trackAuditPageShow(pagePath: string): void {
  const normalizedPath = normalizePagePath(pagePath);
  if (!normalizedPath) {
    return;
  }
  if (activePage && activePage.pagePath !== normalizedPath) {
    void trackAuditPageLeave("navigate");
  }
  const eventTime = now();
  activePage = {
    pagePath: normalizedPath,
    featureCode: auditFeatureForPage(normalizedPath),
    shownAtMs: eventTime.getTime(),
    lastTickMs: eventTime.getTime(),
  };
  enqueue(eventPayload("page_view", activePage.pagePath, activePage.featureCode, { eventTime }));
  startHeartbeat();
}

export function trackAuditHeartbeat(): void {
  if (!activePage) {
    return;
  }
  const eventTime = now();
  const currentMs = eventTime.getTime();
  const durationMs = Math.max(0, currentMs - activePage.lastTickMs);
  activePage.lastTickMs = currentMs;
  enqueue(
    eventPayload("heartbeat", activePage.pagePath, activePage.featureCode, {
      eventTime,
      durationMs,
    }),
  );
  void flushAuditEvents();
}

export function trackAuditPageLeave(reason: AuditExitReason | string = "page_unload"): Promise<void> {
  if (!activePage) {
    stopHeartbeat();
    return flushAuditEvents();
  }
  const leavingPage = activePage;
  activePage = null;
  stopHeartbeat();

  const eventTime = now();
  enqueue(
    eventPayload("page_leave", leavingPage.pagePath, leavingPage.featureCode, {
      eventTime,
      durationMs: eventTime.getTime() - leavingPage.lastTickMs,
      exitReason: reason,
    }),
  );
  return flushAuditEvents();
}

export function trackAuditAction(
  featureCode: string,
  metadata: Record<string, unknown> = {},
  context: { projectId?: string; voiceSessionId?: string } = {},
): void {
  const pagePath = activePage?.pagePath || "";
  enqueue(
    eventPayload("action", pagePath, featureCode, {
      metadata,
      projectId: context.projectId,
      voiceSessionId: context.voiceSessionId,
    }),
  );
  void flushAuditEvents();
}

export function installAuditMemoryWarningTracker(): void {
  wx.onMemoryWarning?.(({ level }) => {
    trackAuditAction("runtime.memory_warning", {
      level: typeof level === "number" && Number.isFinite(level) ? level : null,
    });
  });
}

export function installAuditPageTracker(): void {
  if (installed) {
    return;
  }
  const globalWithPage = globalThis as Record<string, unknown>;
  if (typeof globalWithPage.Page !== "function") {
    return;
  }

  originalPageFactory = globalWithPage.Page as PageFactory;
  globalWithPage.Page = (definition: PageDefinition) => {
    const originalOnShow = definition.onShow;
    const originalOnHide = definition.onHide;
    const originalOnUnload = definition.onUnload;
    originalPageFactory?.({
      ...definition,
      onShow(this: PageInstance, ...args: unknown[]) {
        trackAuditPageShow(pagePathFromContext(this));
        return originalOnShow?.apply(this, args);
      },
      onHide(this: PageInstance, ...args: unknown[]) {
        const result = originalOnHide?.apply(this, args);
        void trackAuditPageLeave("app_background");
        return result;
      },
      onUnload(this: PageInstance, ...args: unknown[]) {
        const result = originalOnUnload?.apply(this, args);
        void trackAuditPageLeave("page_unload");
        return result;
      },
    });
  };
  installed = true;
}

export function __resetAuditTrackerForTest(): void {
  const globalWithPage = globalThis as Record<string, unknown>;
  if (installed && originalPageFactory) {
    globalWithPage.Page = originalPageFactory;
  }
  installed = false;
  originalPageFactory = null;
  stopHeartbeat();
  activePage = null;
  queue = [];
  flushPromise = null;
  clientSessionId = newClientSessionId();
  eventSequence = 0;
  nowFn = () => new Date();
}

export function __setAuditNowForTest(nextNow: () => Date): void {
  nowFn = nextNow;
}
