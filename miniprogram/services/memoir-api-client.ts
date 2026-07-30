import {
  ensureBackendSession,
  requestWithBackendSession,
} from "./auth";
import { readSelectedAIModel } from "./ai-model-settings";
import {
  type HttpMethod,
  type RequestTimeoutProfile,
} from "./request";
import { markCurrentUserMemoirSnapshotsStale } from "../stores/memoir-read-cache";

interface MemoirRequestOptions {
  method?: HttpMethod;
  data?: unknown;
  header?: Record<string, string>;
  timeoutProfile?: RequestTimeoutProfile;
  invalidatesSnapshots?: boolean;
}

type QueryValue = string | number | boolean | null | undefined;

function withSelectedAIModel<T extends { ai_model?: string | null }>(data: T): T {
  const explicitModel = typeof data.ai_model === "string" ? data.ai_model.trim() : "";
  return {
    ...data,
    ai_model: explicitModel || readSelectedAIModel(),
  };
}

export async function memoirRequest<T>(
  path: string,
  options: MemoirRequestOptions = {},
): Promise<T> {
  const { invalidatesSnapshots = true, ...requestOptions } = options;
  const method = requestOptions.method || "GET";
  const response = await requestWithBackendSession<T>(path, {
    ...requestOptions,
    timeoutProfile: requestOptions.timeoutProfile ?? (method === "GET" ? "interactive" : "mutation"),
  });
  if (method !== "GET" && invalidatesSnapshots) {
    markCurrentUserMemoirSnapshotsStale();
  }
  return response;
}

export function memoirAIRequest<T, D extends { ai_model?: string | null }>(
  path: string,
  data: D,
  options: Omit<MemoirRequestOptions, "data"> = {},
): Promise<T> {
  return memoirRequest<T>(path, {
    ...options,
    method: options.method || "POST",
    timeoutProfile: options.timeoutProfile || "generation",
    data: withSelectedAIModel(data),
  });
}

export async function ensureMemoirBackendSession(): Promise<void> {
  await ensureBackendSession();
}

export function queryString(params: Record<string, QueryValue>): string {
  const query = Object.entries(params)
    .flatMap(([key, value]) => {
      if (value === undefined || value === null || value === "") {
        return [];
      }
      return `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`;
    });
  return query.length ? `?${query.join("&")}` : "";
}

export function memoirProjectPath(projectId: string, suffix = ""): string {
  return `/api/mobile/memoir/projects/${encodeURIComponent(projectId)}${suffix}`;
}

export function memoirNarrativeProjectPath(projectId: string, suffix = ""): string {
  return `/api/memoir/projects/${encodeURIComponent(projectId)}${suffix}`;
}

export function memoirVoiceSessionPath(voiceSessionId: string, suffix = ""): string {
  return `/api/mobile/memoir/voice-sessions/${encodeURIComponent(voiceSessionId)}${suffix}`;
}
