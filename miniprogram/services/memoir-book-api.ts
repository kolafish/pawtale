import type {
  MemoirBookAudioRequest,
  MemoirBookAudioResponse,
  MemoirBookChapterDraft,
  MemoirBookChapterDraftGenerateRequest,
  MemoirBookChapterDraftUpdateRequest,
  MemoirBookChapterTitleGenerateRequest,
  MemoirBookChapterTitleUpdateRequest,
  MemoirBookOutputChapterEditorialCopyUpdateRequest,
  MemoirBookOutput,
  MemoirBookOutputEditorialCopyRegenerateRequest,
  MemoirBookOutputExportFormat,
  MemoirBookOutputExportLayout,
  MemoirBookOutputGenerateRequest,
  MemoirBookOutputUpdateRequest,
  MemoirBookPlan,
  MemoirBookPlanConfirmRequest,
  MemoirBookPlanGenerateRequest,
  MemoirBookPlanStructureCommandRequest,
  MemoirBookPlanUpdateRequest,
  MemoirBookSourceAssignmentRequest,
  MemoirBookSourceCoverage,
  MemoirBookSourceMoveRequest,
  MemoirBookTitleSuggestionRequest,
  MemoirBookTitleSuggestionResponse,
  MemoirBookVoiceCommandExecuteRequest,
  MemoirBookVoiceCommandExecuteResponse,
  MemoirBookVoiceCommandParseRequest,
  MemoirBookVoiceCommandParseResponse,
  MemoirAutomationEvent,
} from "../domain/voice-chat";
import {
  ensureMemoirBackendSession,
  memoirProjectPath,
  memoirRequest,
  withSelectedAIModel,
} from "./memoir-api-client";
import { ApiError, cleanBaseUrl, readBackendConfig, readSessionCookie } from "./request";

const BOOK_OUTPUT_GENERATE_TIMEOUT_MS = 60000;
const BOOK_TTS_TIMEOUT_MS = 180000;

function bookPlanPath(projectId: string, suffix = ""): string {
  return memoirProjectPath(projectId, `/book-plan${suffix}`);
}

function bookPlanChapterPath(projectId: string, chapterId: string, suffix = ""): string {
  return bookPlanPath(projectId, `/chapters/${encodeURIComponent(chapterId)}${suffix}`);
}

export function getMemoirBookPlan(projectId: string): Promise<MemoirBookPlan> {
  return memoirRequest<MemoirBookPlan>(bookPlanPath(projectId));
}

function downloadDomain(url: string): string {
  const parts = url.split("/");
  if (parts.length >= 3 && parts[0].startsWith("http")) {
    return parts.slice(0, 3).join("/");
  }
  return url;
}

function downloadErrorMessage(errMsg: string | undefined, url: string): string {
  const message = String(errMsg || "");
  if (message.toLowerCase().includes("url not in domain list")) {
    return `文件下载域名未配置，请在微信公众平台把 ${downloadDomain(url)} 添加到 downloadFile 合法域名。`;
  }
  return message || "文件下载失败";
}

export function generateMemoirBookPlan(
  projectId: string,
  data: MemoirBookPlanGenerateRequest = {},
): Promise<MemoirBookPlan> {
  return memoirRequest<MemoirBookPlan>(
    bookPlanPath(projectId, "/generate"),
    {
      method: "POST",
      data: withSelectedAIModel(data),
    },
  );
}

export function confirmMemoirBookPlan(
  projectId: string,
  data: MemoirBookPlanConfirmRequest,
): Promise<MemoirBookPlan> {
  return memoirRequest<MemoirBookPlan>(
    bookPlanPath(projectId),
    {
      method: "PUT",
      data,
    },
  );
}

export function updateMemoirBookPlan(
  projectId: string,
  data: MemoirBookPlanUpdateRequest,
): Promise<MemoirBookPlan> {
  return memoirRequest<MemoirBookPlan>(
    bookPlanPath(projectId),
    {
      method: "PATCH",
      data,
    },
  );
}

export function applyMemoirBookPlanStructureCommand(
  projectId: string,
  data: MemoirBookPlanStructureCommandRequest,
): Promise<MemoirBookPlan> {
  return memoirRequest<MemoirBookPlan>(
    bookPlanPath(projectId, "/structure-commands"),
    {
      method: "POST",
      data,
    },
  );
}

export function getMemoirBookSourceCoverage(projectId: string): Promise<MemoirBookSourceCoverage> {
  return memoirRequest<MemoirBookSourceCoverage>(memoirProjectPath(projectId, "/book-source-coverage"));
}

export function assignMemoirBookSources(
  projectId: string,
  data: MemoirBookSourceAssignmentRequest,
): Promise<MemoirBookPlan> {
  return memoirRequest<MemoirBookPlan>(
    memoirProjectPath(projectId, "/book-source-assignments"),
    {
      method: "POST",
      data: withSelectedAIModel(data),
    },
  );
}

export function moveMemoirBookSource(
  projectId: string,
  data: MemoirBookSourceMoveRequest,
): Promise<MemoirBookPlan> {
  return memoirRequest<MemoirBookPlan>(
    memoirProjectPath(projectId, "/book-source-moves"),
    {
      method: "POST",
      data: withSelectedAIModel(data),
    },
  );
}

export function updateMemoirBookChapterTitle(
  projectId: string,
  chapterId: string,
  data: MemoirBookChapterTitleUpdateRequest,
): Promise<MemoirBookPlan> {
  return memoirRequest<MemoirBookPlan>(
    bookPlanChapterPath(projectId, chapterId, "/title"),
    {
      method: "PATCH",
      data,
    },
  );
}

export function generateMemoirBookChapterTitle(
  projectId: string,
  chapterId: string,
  data: MemoirBookChapterTitleGenerateRequest = {},
): Promise<MemoirBookPlan> {
  return memoirRequest<MemoirBookPlan>(
    bookPlanChapterPath(projectId, chapterId, "/title/generate"),
    {
      method: "POST",
      data: withSelectedAIModel(data),
    },
  );
}

export function getMemoirBookChapterDraft(
  projectId: string,
  chapterId: string,
): Promise<MemoirBookChapterDraft> {
  return memoirRequest<MemoirBookChapterDraft>(bookPlanChapterPath(projectId, chapterId, "/draft"));
}

export function generateMemoirBookChapterDraft(
  projectId: string,
  chapterId: string,
  data: MemoirBookChapterDraftGenerateRequest = {},
): Promise<MemoirBookChapterDraft> {
  return memoirRequest<MemoirBookChapterDraft>(
    bookPlanChapterPath(projectId, chapterId, "/draft"),
    {
      method: "POST",
      data: withSelectedAIModel(data),
    },
  );
}

export function enqueueMemoirBookChapterDraftGeneration(
  projectId: string,
  chapterId: string,
  data: MemoirBookChapterDraftGenerateRequest = {},
): Promise<MemoirAutomationEvent> {
  return memoirRequest<MemoirAutomationEvent>(
    bookPlanChapterPath(projectId, chapterId, "/draft/jobs"),
    {
      method: "POST",
      data: withSelectedAIModel(data),
    },
  );
}

export function updateMemoirBookChapterDraft(
  projectId: string,
  chapterId: string,
  data: MemoirBookChapterDraftUpdateRequest,
): Promise<MemoirBookChapterDraft> {
  return memoirRequest<MemoirBookChapterDraft>(
    bookPlanChapterPath(projectId, chapterId, "/draft"),
    {
      method: "PATCH",
      data,
    },
  );
}

export function confirmMemoirBookChapterDraft(
  projectId: string,
  chapterId: string,
): Promise<MemoirBookChapterDraft> {
  return memoirRequest<MemoirBookChapterDraft>(
    bookPlanChapterPath(projectId, chapterId, "/draft/confirm"),
    {
      method: "POST",
    },
  );
}

export function createMemoirBookChapterDraftTTS(
  projectId: string,
  chapterId: string,
  data: MemoirBookAudioRequest = {},
): Promise<MemoirBookAudioResponse> {
  return memoirRequest<MemoirBookAudioResponse>(
    bookPlanChapterPath(projectId, chapterId, "/draft/tts"),
    {
      method: "POST",
      data,
      timeoutMs: BOOK_TTS_TIMEOUT_MS,
    },
  );
}

export function createMemoirBookPlanOutlineTTS(
  projectId: string,
  data: MemoirBookAudioRequest = {},
): Promise<MemoirBookAudioResponse> {
  return memoirRequest<MemoirBookAudioResponse>(
    bookPlanPath(projectId, "/outline/tts"),
    {
      method: "POST",
      data,
      timeoutMs: BOOK_TTS_TIMEOUT_MS,
    },
  );
}

export function parseMemoirBookVoiceCommand(
  projectId: string,
  data: MemoirBookVoiceCommandParseRequest,
): Promise<MemoirBookVoiceCommandParseResponse> {
  return memoirRequest<MemoirBookVoiceCommandParseResponse>(
    memoirProjectPath(projectId, "/book-voice-command/parse"),
    {
      method: "POST",
      data: withSelectedAIModel(data),
    },
  );
}

export function executeMemoirBookVoiceCommand(
  projectId: string,
  data: MemoirBookVoiceCommandExecuteRequest,
): Promise<MemoirBookVoiceCommandExecuteResponse> {
  return memoirRequest<MemoirBookVoiceCommandExecuteResponse>(
    memoirProjectPath(projectId, "/book-voice-command/execute"),
    {
      method: "POST",
      data,
    },
  );
}

export function bookVoiceCommandGatewayUrl(projectId: string): string {
  const config = readBackendConfig();
  if (!config.wsBaseUrl) {
    throw new Error("未配置后端 WebSocket 地址");
  }
  return `${cleanBaseUrl(config.wsBaseUrl)}${memoirProjectPath(projectId, "/book-voice-command/gateway")}`;
}

export function bookVoiceAssistantTurnStreamUrl(projectId: string): string {
  const config = readBackendConfig();
  if (!config.wsBaseUrl) {
    throw new Error("未配置后端 WebSocket 地址");
  }
  return `${cleanBaseUrl(config.wsBaseUrl)}${memoirProjectPath(projectId, "/book-voice-assistant/turn-stream")}`;
}

export function getMemoirBookOutput(projectId: string): Promise<MemoirBookOutput> {
  return memoirRequest<MemoirBookOutput>(memoirProjectPath(projectId, "/book-output"));
}

export function generateMemoirBookOutput(
  projectId: string,
  data: MemoirBookOutputGenerateRequest = {},
): Promise<MemoirBookOutput> {
  return memoirRequest<MemoirBookOutput>(
    memoirProjectPath(projectId, "/book-output/generate"),
    {
      method: "POST",
      data: withSelectedAIModel(data),
      timeoutMs: BOOK_OUTPUT_GENERATE_TIMEOUT_MS,
    },
  );
}

export function enqueueMemoirBookOutputGeneration(
  projectId: string,
  data: MemoirBookOutputGenerateRequest = {},
): Promise<MemoirAutomationEvent> {
  return memoirRequest<MemoirAutomationEvent>(
    memoirProjectPath(projectId, "/book-output/generate/jobs"),
    {
      method: "POST",
      data: withSelectedAIModel(data),
    },
  );
}

export function updateMemoirBookOutput(
  projectId: string,
  data: MemoirBookOutputUpdateRequest,
): Promise<MemoirBookOutput> {
  return memoirRequest<MemoirBookOutput>(
    memoirProjectPath(projectId, "/book-output"),
    {
      method: "PATCH",
      data,
    },
  );
}

export function regenerateMemoirBookOutputEditorialCopy(
  projectId: string,
  data: MemoirBookOutputEditorialCopyRegenerateRequest = {},
): Promise<MemoirBookOutput> {
  return memoirRequest<MemoirBookOutput>(
    memoirProjectPath(projectId, "/book-output/editorial-copy/regenerate"),
    {
      method: "POST",
      data: withSelectedAIModel(data),
      timeoutMs: BOOK_OUTPUT_GENERATE_TIMEOUT_MS,
    },
  );
}

export function enqueueMemoirBookOutputEditorialCopyGeneration(
  projectId: string,
  data: MemoirBookOutputEditorialCopyRegenerateRequest = {},
): Promise<MemoirAutomationEvent> {
  return memoirRequest<MemoirAutomationEvent>(
    memoirProjectPath(projectId, "/book-output/editorial-copy/regenerate/jobs"),
    {
      method: "POST",
      data: withSelectedAIModel(data),
    },
  );
}

export function updateMemoirBookOutputChapterEditorialCopy(
  projectId: string,
  chapterId: string,
  data: MemoirBookOutputChapterEditorialCopyUpdateRequest,
): Promise<MemoirBookOutput> {
  return memoirRequest<MemoirBookOutput>(
    memoirProjectPath(projectId, `/book-output/chapters/${encodeURIComponent(chapterId)}/editorial-copy`),
    {
      method: "PATCH",
      data,
    },
  );
}

export function suggestMemoirBookOutputTitles(
  projectId: string,
  data: MemoirBookTitleSuggestionRequest = {},
): Promise<MemoirBookTitleSuggestionResponse> {
  return memoirRequest<MemoirBookTitleSuggestionResponse>(
    memoirProjectPath(projectId, "/book-output/title-suggestions"),
    {
      method: "POST",
      data: withSelectedAIModel(data),
    },
  );
}

export function memoirBookOutputExportUrl(
  projectId: string,
  format: MemoirBookOutputExportFormat = "pdf",
  versionId = "",
  layout: MemoirBookOutputExportLayout = "standard",
  showSourceAppendix = true,
): string {
  const config = readBackendConfig();
  if (!config.httpBaseUrl) {
    throw new ApiError(0, "未配置后端 HTTP 地址", {});
  }
  const params = [`format=${encodeURIComponent(format)}`];
  if (format === "pdf") {
    params.push(`layout=${encodeURIComponent(layout)}`);
    params.push(`show_source_appendix=${showSourceAppendix ? "true" : "false"}`);
  }
  if (versionId) {
    params.push(`version_id=${encodeURIComponent(versionId)}`);
  }
  return `${cleanBaseUrl(config.httpBaseUrl)}${memoirProjectPath(projectId, `/book-output/export?${params.join("&")}`)}`;
}

export async function downloadMemoirBookOutputExport(
  projectId: string,
  format: MemoirBookOutputExportFormat = "pdf",
  versionId = "",
  layout: MemoirBookOutputExportLayout = "standard",
  showSourceAppendix = true,
): Promise<string> {
  await ensureMemoirBackendSession();
  const cookie = readSessionCookie();
  const url = memoirBookOutputExportUrl(projectId, format, versionId, layout, showSourceAppendix);
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url,
      header: {
        ...(cookie ? { Cookie: cookie } : {}),
      },
      success: (result) => {
        if (result.statusCode >= 200 && result.statusCode < 300) {
          resolve(result.tempFilePath);
          return;
        }
        reject(new ApiError(result.statusCode, "文件下载失败", result));
      },
      fail: (error) => {
        reject(new ApiError(0, downloadErrorMessage(error.errMsg, url), error));
      },
    });
  });
}

export function createMemoirBookOutputTTS(
  projectId: string,
  data: MemoirBookAudioRequest = {},
): Promise<MemoirBookAudioResponse> {
  return memoirRequest<MemoirBookAudioResponse>(
    memoirProjectPath(projectId, "/book-output/tts"),
    {
      method: "POST",
      data,
      timeoutMs: BOOK_TTS_TIMEOUT_MS,
    },
  );
}
