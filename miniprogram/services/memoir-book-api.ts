import type {
  MemoirBookAudioRequest,
  MemoirBookAudioPlan,
  MemoirAudioPlanRequest,
  MemoirAudioSource,
  MemoirBookAssemblyResponse,
  MemoirBookChapterDraft,
  MemoirBookChapterDraftRevisionRequest,
  MemoirBookChapterTitleGenerateRequest,
  MemoirBookChapterTitleUpdateRequest,
  MemoirBookImagePlacementCreateRequest,
  MemoirBookImagePlacementUpdateRequest,
  MemoirBookOutput,
  MemoirBookShareCreated,
  MemoirBookShareStatus,
  MemoirReadingShareCardSelection,
  MemoirBookOutputEditorialCopyRegenerateRequest,
  MemoirBookEditorialCopyJobsResponse,
  MemoirBookOutputExportInfo,
  MemoirBookOutputExportFormat,
  MemoirBookOutputExportLayout,
  MemoirBookLayoutPreview,
  MemoirBookOutputUpdateRequest,
  MemoirBookOutputVersion,
  MemoirBookPlan,
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
import { downloadFile, downloadAuthenticatedFile, uploadAuthenticatedFile, type FileDownloadPhase } from "./authenticated-media";
import { ApiError, cleanBaseUrl, readBackendConfig, readSessionCookie, request } from "./request";

function memoirBookSharePath(projectId: string, chapterId: string, sectionId = "", preview = false): string {
  const path = memoirProjectPath(projectId, `/book-output/share${preview ? "/preview" : ""}`);
  const query = [chapterId ? `chapter_id=${encodeURIComponent(chapterId)}` : "", sectionId ? `section_id=${encodeURIComponent(sectionId)}` : ""].filter(Boolean).join("&");
  return query ? `${path}?${query}` : path;
}

export function getMemoirBookShareStatus(projectId: string, chapterId = "", sectionId = ""): Promise<MemoirBookShareStatus> {
  return memoirRequest<MemoirBookShareStatus>(memoirBookSharePath(projectId, chapterId, sectionId));
}

export function createMemoirBookShare(projectId: string, chapterId = "", sectionId = ""): Promise<MemoirBookShareCreated> {
  return memoirRequest<MemoirBookShareCreated>(memoirBookSharePath(projectId, chapterId, sectionId), {
    method: "POST",
    invalidatesSnapshots: false,
  });
}

export function updateMemoirBookShare(projectId: string, chapterId = "", sectionId = ""): Promise<MemoirBookShareCreated> {
  return memoirRequest<MemoirBookShareCreated>(memoirBookSharePath(projectId, chapterId, sectionId), {
    method: "PUT",
    invalidatesSnapshots: false,
  });
}

export function saveMemoirShareCardSelection(
  projectId: string, selection: MemoirReadingShareCardSelection & { token: string }, chapterId = "", sectionId = "",
): Promise<MemoirReadingShareCardSelection> {
  return memoirRequest<MemoirReadingShareCardSelection>(memoirBookSharePath(projectId, chapterId, sectionId), {
    method: "PATCH", data: selection, invalidatesSnapshots: false,
  });
}

export async function revokeMemoirBookShare(projectId: string, chapterId = "", sectionId = ""): Promise<void> {
  await memoirRequest<unknown>(memoirBookSharePath(projectId, chapterId, sectionId), {
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

export type MemoirSharedAudioPlan = MemoirBookAudioPlan & { content: string };

export function readMemoirSharedAudioPlan(
  token: string, versionId: string, chapterId?: string,
  playback?: { generate: true; start_index: number; source_text_hash: string; profile_key: string },
): Promise<MemoirSharedAudioPlan> {
  return request<MemoirSharedAudioPlan>("/api/mobile/memoir/book-shares/audio/plan", {
    method: "POST", data: { token, version_id: versionId, ...(chapterId ? { chapter_id: chapterId } : {}), generate: false, ...playback },
  });
}

export function downloadMemoirSharedAudio(token: string, versionId: string, audioId: string, chapterId?: string): Promise<string> {
  const query = `version_id=${encodeURIComponent(versionId)}${chapterId ? `&chapter_id=${encodeURIComponent(chapterId)}` : ""}`;
  return downloadFile(`${cleanBaseUrl(readBackendConfig().httpBaseUrl)}/api/mobile/memoir/book-shares/${encodeURIComponent(token)}/audio/${encodeURIComponent(audioId)}?${query}`,
    { failureMessage: "朗读暂时无法播放，请重新打开故事后重试", domainLabel: "分享朗读" });
}

export function readMemoirDemoBook(): Promise<MemoirBookOutput> {
  return request<MemoirBookOutput>("/api/mobile/memoir/demo-book");
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

export function uploadMemoirImageAsset(projectId: string, filePath: string): Promise<MemoirImageAsset> {
  const config = readBackendConfig();
  if (!config.httpBaseUrl) throw new ApiError(0, "未配置后端 HTTP 地址", {});
  return uploadAuthenticatedFile(`${cleanBaseUrl(config.httpBaseUrl)}${memoirProjectPath(projectId, "/image-assets")}`, {
    filePath, name: "file", failureMessage: "图片上传失败", domainLabel: "图片上传",
    parseResponse: (data) => {
      try { return JSON.parse(data) as MemoirImageAsset; }
      catch (error) { throw new ApiError(0, "图片上传结果解析失败", error); }
    },
    responseErrorMessage: (data) => {
      try { const response = JSON.parse(data) as { detail?: unknown }; return typeof response.detail === "string" ? response.detail : "图片上传失败"; }
      catch { return "图片上传失败"; }
    },
  });
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

export function updateMemoirBookImagePlacement(
  projectId: string,
  assetId: string,
  placementId: string,
  data: MemoirBookImagePlacementUpdateRequest,
): Promise<MemoirImageAsset> {
  return memoirRequest<MemoirImageAsset>(
    imageAssetPath(projectId, assetId, `/placements/${encodeURIComponent(placementId)}`),
    {
      method: "PATCH",
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

export function prepareMemoirAudio(
  projectId: string,
  source: MemoirAudioSource,
  data: MemoirAudioPlanRequest,
): Promise<MemoirBookAudioPlan> {
  let path: string;
  if (source.kind === "book") {
    path = memoirProjectPath(projectId, "/book-output/tts/plan");
  } else if (source.kind === "outline") {
    path = bookPlanPath(projectId, "/outline/tts/plan");
  } else {
    const suffix = source.kind === "section"
      ? `/draft/sections/${encodeURIComponent(source.sectionId)}/tts/plan`
      : "/draft/tts/plan";
    path = bookPlanChapterPath(projectId, source.chapterId, suffix);
  }
  return memoirRequest<MemoirBookAudioPlan>(path, {
    method: "POST", data: {
      ...((source.kind !== "outline" && source.versionId) ? { version_id: source.versionId } : {}),
      ...data,
    },
    timeoutProfile: "interactive", invalidatesSnapshots: false,
  });
}

export function memoirAudioPlanLoader(
  projectId: string, source: MemoirAudioSource, request: MemoirBookAudioRequest,
  prepare: typeof prepareMemoirAudio = prepareMemoirAudio,
) {
  return (index = 0, current?: MemoirBookAudioPlan, retryFailed = true): Promise<MemoirBookAudioPlan> =>
    prepare(projectId, source, {
      ...request,
      ...(current ? {
        version_id: current.version_id, source_text_hash: current.source_text_hash,
        voice: current.voice || undefined, speed_ratio: current.speed_ratio, profile_key: current.profile_key,
      } : {}),
      start_index: index, retry_failed: retryFailed,
    });
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

export function getMemoirBookEditorialCopyJobs(
  projectId: string,
): Promise<MemoirBookEditorialCopyJobsResponse> {
  return memoirRequest<MemoirBookEditorialCopyJobsResponse>(
    memoirProjectPath(projectId, "/book-output/editorial-copy/regenerate/jobs"),
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
  preview?: MemoirBookLayoutPreview,
): string {
  const config = readBackendConfig();
  if (!config.httpBaseUrl) {
    throw new ApiError(0, "未配置后端 HTTP 地址", {});
  }
  const params = [`format=${encodeURIComponent(format)}`];
  if (format === "pdf") {
    params.push(`layout=${encodeURIComponent(layout)}`);
    params.push(`show_source_appendix=${showSourceAppendix ? "true" : "false"}`);
    if (preview) {
      if (preview.template) params.push(`preview_template=${encodeURIComponent(preview.template)}`);
      if (preview.custom_layout) {
        params.push(`preview_font_family=${encodeURIComponent(preview.custom_layout.font_family)}`);
        params.push(`preview_font_scale=${encodeURIComponent(preview.custom_layout.font_scale)}`);
        params.push(`preview_line_spacing=${encodeURIComponent(preview.custom_layout.line_spacing)}`);
        params.push(`preview_page_margin=${encodeURIComponent(preview.custom_layout.page_margin)}`);
      }
    }
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
  preview?: MemoirBookLayoutPreview,
  onPhase?: (phase: FileDownloadPhase) => void,
): Promise<string> {
  const sessionCookie = readSessionCookie();
  onPhase?.("checking");
  const infoUrl = memoirBookOutputExportUrl(projectId, format, versionId, layout, showSourceAppendix, preview);
  const infoPath = infoUrl.slice(cleanBaseUrl(readBackendConfig().httpBaseUrl).length).replace("/export?", "/export/info?");
  const info = await memoirRequest<MemoirBookOutputExportInfo>(infoPath);
  const url = `${memoirBookOutputExportUrl(projectId, format, info.version_id, layout, showSourceAppendix, preview)}&cache_key=${encodeURIComponent(info.cache_key)}`;
  return downloadAuthenticatedFile(url, {
    timeoutProfile: "export",
    failureMessage: "文件下载失败",
    domainLabel: "文件下载",
    cache: { key: info.cache_key, filename: info.filename, ready: info.ready, sessionCookie, onPhase },
  });
}


export function previewMemoirBookShare(projectId: string, chapterId = "", sectionId = ""): Promise<MemoirBookOutput> {
  return memoirRequest(memoirBookSharePath(projectId, chapterId, sectionId, true));
}
