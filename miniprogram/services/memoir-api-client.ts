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
  return request<T>(path, options);
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

export function memoirVoiceSessionPath(voiceSessionId: string, suffix = ""): string {
  return `/api/mobile/memoir/voice-sessions/${encodeURIComponent(voiceSessionId)}${suffix}`;
}
