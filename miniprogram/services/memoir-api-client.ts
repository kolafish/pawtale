import { ensureBackendSession } from "./auth";
import { readSelectedAIModel } from "./ai-model-settings";
import { request, type HttpMethod } from "./request";

interface MemoirRequestOptions {
  method?: HttpMethod;
  data?: unknown;
  header?: Record<string, string>;
  timeoutMs?: number;
}

type QueryValue = string | number | boolean | null | undefined;

export const MEMOIR_MUTATION_TIMEOUT_MS = 600000;

export function withSelectedAIModel<T extends { ai_model?: string | null }>(data: T): T {
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
  await ensureBackendSession();
  const method = options.method || "GET";
  return request<T>(path, {
    ...options,
    timeoutMs:
      options.timeoutMs ??
      (method === "GET" ? undefined : MEMOIR_MUTATION_TIMEOUT_MS),
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
