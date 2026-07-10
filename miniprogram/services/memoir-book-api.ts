import type {
  MemoirBookAudioRequest,
  MemoirBookAudioResponse,
  MemoirBookChapterDraft,
  MemoirBookChapterDraftGenerateRequest,
  MemoirBookChapterDraftUpdateRequest,
  MemoirBookChapterTitleGenerateRequest,
  MemoirBookChapterTitleUpdateRequest,
  MemoirBookImagePlacementCreateRequest,
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
  MemoirAutomationEvent,
  MemoirImageAsset,
  MemoirImageAssetListResponse,
  MemoirImageAssetUpdateRequest,
} from "../domain/voice-chat";
import {
  memoirProjectPath,
  memoirRequest,
  withSelectedAIModel,
} from "./memoir-api-client";
import { downloadAuthenticatedFile } from "./authenticated-media";
import { ApiError, cleanBaseUrl, readBackendConfig } from "./request";

const BOOK_OUTPUT_EXPORT_DOWNLOAD_TIMEOUT_MS = 120000;
const BOOK_TTS_TIMEOUT_MS = 180000;

function bookPlanPath(projectId: string, suffix = ""): string {
  return memoirProjectPath(projectId, `/book-plan${suffix}`);
}

function bookPlanChapterPath(projectId: string, chapterId: string, suffix = ""): string {
  return bookPlanPath(projectId, `/chapters/${encodeURIComponent(chapterId)}${suffix}`);
}

function imageAssetPath(projectId: string, assetId = "", suffix = ""): string {
  const assetPath = assetId ? `/${encodeURIComponent(assetId)}` : "";
  return memoirProjectPath(projectId, `/image-assets${assetPath}${suffix}`);
}

export function getMemoirBookPlan(projectId: string): Promise<MemoirBookPlan> {
  return memoirRequest<MemoirBookPlan>(bookPlanPath(projectId));
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

export function listMemoirImageAssets(projectId: string): Promise<MemoirImageAssetListResponse> {
  return memoirRequest<MemoirImageAssetListResponse>(imageAssetPath(projectId));
}

export function updateMemoirImageAsset(
  projectId: string,
  assetId: string,
  data: MemoirImageAssetUpdateRequest,
): Promise<MemoirImageAsset> {
  return memoirRequest<MemoirImageAsset>(
    imageAssetPath(projectId, assetId),
    {
      method: "PATCH",
      data,
    },
  );
}

export async function deleteMemoirImageAsset(projectId: string, assetId: string): Promise<void> {
  await memoirRequest<unknown>(
    imageAssetPath(projectId, assetId),
    {
      method: "DELETE",
    },
  );
}

export function createMemoirBookImagePlacement(
  projectId: string,
  assetId: string,
  data: MemoirBookImagePlacementCreateRequest,
): Promise<MemoirImageAsset> {
  return memoirRequest<MemoirImageAsset>(
    imageAssetPath(projectId, assetId, "/placements"),
    {
      method: "POST",
      data,
    },
  );
}

export function deleteMemoirBookImagePlacement(
  projectId: string,
  assetId: string,
  placementId: string,
): Promise<MemoirImageAsset> {
  return memoirRequest<MemoirImageAsset>(
    imageAssetPath(projectId, assetId, `/placements/${encodeURIComponent(placementId)}`),
    {
      method: "DELETE",
    },
  );
}

export function memoirImageAssetFileUrl(projectId: string, assetId: string, thumbnail = false): string {
  const config = readBackendConfig();
  const suffix = thumbnail ? "?variant=thumbnail" : "";
  return `${cleanBaseUrl(config.httpBaseUrl)}${imageAssetPath(projectId, assetId, `/file${suffix}`)}`;
}

export function downloadMemoirImageAssetFile(
  projectId: string,
  assetId: string,
  thumbnail = false,
): Promise<string> {
  const url = memoirImageAssetFileUrl(projectId, assetId, thumbnail);
  return downloadAuthenticatedFile(url, {
    failureMessage: "图片下载失败",
    domainLabel: "图片下载",
  });
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

export function downloadMemoirBookOutputExport(
  projectId: string,
  format: MemoirBookOutputExportFormat = "pdf",
  versionId = "",
  layout: MemoirBookOutputExportLayout = "standard",
  showSourceAppendix = true,
): Promise<string> {
  const url = memoirBookOutputExportUrl(projectId, format, versionId, layout, showSourceAppendix);
  return downloadAuthenticatedFile(url, {
    timeoutMs: BOOK_OUTPUT_EXPORT_DOWNLOAD_TIMEOUT_MS,
    failureMessage: "文件下载失败",
    domainLabel: "文件下载",
  });
}
