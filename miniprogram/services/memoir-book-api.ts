import type {
  MemoirBookAudioRequest,
  MemoirBookAudioResponse,
  MemoirBookAssemblyResponse,
  MemoirBookChapterDraft,
  MemoirBookChapterDraftRevisionRequest,
  MemoirBookChapterTitleGenerateRequest,
  MemoirBookChapterTitleUpdateRequest,
  MemoirBookImagePlacementCreateRequest,
  MemoirBookOutput,
  MemoirBookShareCreated,
  MemoirBookShareStatus,
  MemoirBookOutputEditorialCopyRegenerateRequest,
  MemoirBookOutputExportFormat,
  MemoirBookOutputExportLayout,
  MemoirBookOutputUpdateRequest,
  MemoirBookOutputVersion,
  MemoirBookPlan,
  MemoirBookPlanConfirmRequest,
  MemoirBookPlanGenerateRequest,
  MemoirBookPlanStructureCommandRequest,
  MemoirBookTitleSuggestionRequest,
  MemoirBookTitleSuggestionResponse,
  MemoirImageAsset,
  MemoirImageAssetListResponse,
  MemoirImageAssetUpdateRequest,
} from "../domain/memoir-book";
import type { MemoirAutomationEvent } from "../domain/memoir-voice-session";
import {
  memoirNarrativeProjectPath,
  memoirProjectPath,
  memoirAIRequest,
  memoirRequest,
} from "./memoir-api-client";
import { downloadAuthenticatedFile } from "./authenticated-media";
import { ApiError, cleanBaseUrl, readBackendConfig, request } from "./request";

export function getMemoirBookShareStatus(projectId: string): Promise<MemoirBookShareStatus> {
  return memoirRequest<MemoirBookShareStatus>(memoirProjectPath(projectId, "/book-output/share"));
}

export function createMemoirBookShare(projectId: string): Promise<MemoirBookShareCreated> {
  return memoirRequest<MemoirBookShareCreated>(memoirProjectPath(projectId, "/book-output/share"), {
    method: "POST",
    invalidatesSnapshots: false,
  });
}

export async function revokeMemoirBookShare(projectId: string): Promise<void> {
  await memoirRequest<unknown>(memoirProjectPath(projectId, "/book-output/share"), {
    method: "DELETE",
    invalidatesSnapshots: false,
  });
}

export function readMemoirBookShare(token: string): Promise<MemoirBookOutput> {
  return request<MemoirBookOutput>("/api/mobile/memoir/book-shares/read", {
    method: "POST",
    data: { token },
  });
}

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
  return memoirAIRequest<MemoirBookPlan, MemoirBookPlanGenerateRequest>(
    bookPlanPath(projectId, "/generate"),
    data,
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
  return memoirAIRequest<MemoirBookPlan, MemoirBookChapterTitleGenerateRequest>(
    bookPlanChapterPath(projectId, chapterId, "/title/generate"),
    data,
  );
}

export function getMemoirBookChapterDraft(
  projectId: string,
  chapterId: string,
): Promise<MemoirBookChapterDraft> {
  return memoirRequest<MemoirBookChapterDraft>(bookPlanChapterPath(projectId, chapterId, "/draft"));
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

export function reviseMemoirBookChapterDraft(
  projectId: string,
  chapterId: string,
  data: MemoirBookChapterDraftRevisionRequest,
): Promise<MemoirBookChapterDraft> {
  return memoirRequest<MemoirBookChapterDraft>(
    bookPlanChapterPath(projectId, chapterId, "/draft/revise"),
    {
      method: "POST",
      data,
    },
  );
}

export function assembleMemoirBookOutput(
  projectId: string,
): Promise<MemoirBookAssemblyResponse> {
  return memoirRequest<MemoirBookAssemblyResponse>(
    memoirNarrativeProjectPath(projectId, "/book-output/assemble"),
    {
      method: "POST",
      data: {},
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
      timeoutProfile: "generation",
      invalidatesSnapshots: false,
    },
  );
}

export function createMemoirBookChapterDraftSectionTTS(
  projectId: string,
  chapterId: string,
  sectionId: string,
  data: MemoirBookAudioRequest = {},
): Promise<MemoirBookAudioResponse> {
  return memoirRequest<MemoirBookAudioResponse>(
    bookPlanChapterPath(
      projectId,
      chapterId,
      `/draft/sections/${encodeURIComponent(sectionId)}/tts`,
    ),
    {
      method: "POST",
      data,
      timeoutProfile: "generation",
      invalidatesSnapshots: false,
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
      timeoutProfile: "generation",
      invalidatesSnapshots: false,
    },
  );
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
      timeoutProfile: "generation",
      invalidatesSnapshots: false,
    },
  );
}

export function getMemoirBookOutput(projectId: string): Promise<MemoirBookOutput> {
  return memoirRequest<MemoirBookOutput>(memoirProjectPath(projectId, "/book-output"));
}

export function getMemoirBookOutputVersion(
  projectId: string,
  versionId: string,
): Promise<MemoirBookOutputVersion> {
  return memoirRequest<MemoirBookOutputVersion>(
    memoirProjectPath(
      projectId,
      `/book-output/versions/${encodeURIComponent(versionId)}`,
    ),
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
  return memoirAIRequest<MemoirAutomationEvent, MemoirBookOutputEditorialCopyRegenerateRequest>(
    memoirProjectPath(projectId, "/book-output/editorial-copy/regenerate/jobs"),
    data,
    { timeoutProfile: "mutation" },
  );
}

export function suggestMemoirBookOutputTitles(
  projectId: string,
  data: MemoirBookTitleSuggestionRequest = {},
): Promise<MemoirBookTitleSuggestionResponse> {
  return memoirAIRequest<MemoirBookTitleSuggestionResponse, MemoirBookTitleSuggestionRequest>(
    memoirProjectPath(projectId, "/book-output/title-suggestions"),
    data,
    { invalidatesSnapshots: false },
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
    timeoutProfile: "export",
    failureMessage: "文件下载失败",
    domainLabel: "文件下载",
  });
}
