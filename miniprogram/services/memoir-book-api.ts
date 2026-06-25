import type {
  MemoirBookAudioRequest,
  MemoirBookAudioResponse,
  MemoirBookChapterDraft,
  MemoirBookChapterDraftGenerateRequest,
  MemoirBookChapterDraftUpdateRequest,
  MemoirBookChapterTitleGenerateRequest,
  MemoirBookChapterTitleUpdateRequest,
  MemoirBookOutput,
  MemoirBookOutputGenerateRequest,
  MemoirBookPlan,
  MemoirBookPlanConfirmRequest,
  MemoirBookPlanGenerateRequest,
  MemoirBookPlanStructureCommandRequest,
  MemoirBookPlanUpdateRequest,
  MemoirBookSourceAssignmentRequest,
  MemoirBookSourceCoverage,
  MemoirBookSourceMoveRequest,
  MemoirBookVoiceAssistantTTSRequest,
  MemoirBookVoiceCommandParseRequest,
  MemoirBookVoiceCommandParseResponse,
} from "../domain/voice-chat";
import {
  memoirProjectPath,
  memoirRequest,
  withSelectedAIModel,
} from "./memoir-api-client";
import { cleanBaseUrl, readBackendConfig } from "./request";

function bookPlanPath(projectId: string, suffix = ""): string {
  return memoirProjectPath(projectId, `/book-plan${suffix}`);
}

function bookPlanChapterPath(projectId: string, chapterId: string, suffix = ""): string {
  return bookPlanPath(projectId, `/chapters/${encodeURIComponent(chapterId)}${suffix}`);
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

export function bookVoiceCommandGatewayUrl(projectId: string): string {
  const config = readBackendConfig();
  if (!config.wsBaseUrl) {
    throw new Error("未配置后端 WebSocket 地址");
  }
  return `${cleanBaseUrl(config.wsBaseUrl)}${memoirProjectPath(projectId, "/book-voice-command/gateway")}`;
}

export function createMemoirBookVoiceAssistantTTS(
  projectId: string,
  data: MemoirBookVoiceAssistantTTSRequest,
): Promise<MemoirBookAudioResponse> {
  return memoirRequest<MemoirBookAudioResponse>(
    memoirProjectPath(projectId, "/book-voice-assistant/tts"),
    {
      method: "POST",
      data,
    },
  );
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
      data,
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
    },
  );
}
