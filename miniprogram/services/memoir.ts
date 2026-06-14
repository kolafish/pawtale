import type {
  MemoirCoverageItem,
  MemoirDashboardResponse,
  MemoirEventMemory,
  MemoirMemoryStatus,
  MemoirMemoryType,
  MemoirOpenQuestion,
  MemoirOpenQuestionStatus,
  MemoirOutput,
  MemoirPersonMemory,
  MemoirPlaceMemory,
  MemoirQuestion,
  MemoirQuestionCandidate,
  MemoirProjectListResponse,
  MemoirSessionProcessResponse,
  MemoirSessionSummary,
  MemoirStoryList,
  MemoirThemeMemory,
  MemoirTtsAsset,
  MemoirTtsAssetResponse,
  MemoirTtsJob,
  MemoirTtsSourceType,
  Project,
} from "../domain/memoir";
import { getMemoirIdentity } from "./auth";
import { request } from "./request";

export function getMemoirProject(): Promise<Project> {
  return request<Project>("/api/mobile/memoir/project");
}

export function listMemoirProjects(): Promise<MemoirProjectListResponse> {
  return request<MemoirProjectListResponse>("/api/mobile/memoir/projects");
}

export function createMemoirProject(data: { title?: string; description?: string; elder_name?: string } = {}): Promise<Project> {
  return request<Project>("/api/mobile/memoir/projects", {
    method: "POST",
    data,
  });
}

export function updateMemoirProject(projectId: string, data: { title?: string; description?: string; elder_name?: string }): Promise<Project> {
  return request<Project>(`/api/mobile/memoir/projects/${projectId}`, {
    method: "PATCH",
    data,
  });
}

export function activateMemoirProject(projectId: string): Promise<MemoirProjectListResponse> {
  return request<MemoirProjectListResponse>(`/api/mobile/memoir/projects/${projectId}/activate`, {
    method: "POST",
    data: {},
  });
}

export function getMemoirDashboard(): Promise<MemoirDashboardResponse> {
  return request<MemoirDashboardResponse>("/api/mobile/memoir/dashboard");
}

export function processSessionMemory(sessionId: string): Promise<MemoirSessionProcessResponse> {
  return request<MemoirSessionProcessResponse>(`/api/mobile/memoir/sessions/${sessionId}/process`, {
    method: "POST",
    data: {},
  });
}

export function getSessionSummary(sessionId: string): Promise<MemoirSessionSummary | null> {
  return request<MemoirSessionSummary | null>(`/api/mobile/memoir/sessions/${sessionId}/summary`);
}

export async function getMemoirStories(): Promise<MemoirStoryList> {
  await getMemoirIdentity();
  return request<MemoirStoryList>("/api/mobile/memoir/stories");
}

export function listPeopleMemories(): Promise<MemoirPersonMemory[]> {
  return request<MemoirPersonMemory[]>("/api/mobile/memoir/memories/people");
}

export function listEventMemories(): Promise<MemoirEventMemory[]> {
  return request<MemoirEventMemory[]>("/api/mobile/memoir/memories/events");
}

export function listPlaceMemories(): Promise<MemoirPlaceMemory[]> {
  return request<MemoirPlaceMemory[]>("/api/mobile/memoir/memories/places");
}

export function listThemeMemories(): Promise<MemoirThemeMemory[]> {
  return request<MemoirThemeMemory[]>("/api/mobile/memoir/memories/themes");
}

export function updateMemoryStatus(
  memoryType: Exclude<MemoirMemoryType, "open_question">,
  memoryId: string,
  status: MemoirMemoryStatus,
): Promise<unknown> {
  return request(`/api/mobile/memoir/memories/${memoryType}/${memoryId}`, {
    method: "PATCH",
    data: { status },
  });
}

export function listOpenQuestions(): Promise<MemoirOpenQuestion[]> {
  return request<MemoirOpenQuestion[]>("/api/mobile/memoir/open-questions");
}

export function updateOpenQuestionStatus(questionId: string, status: MemoirOpenQuestionStatus): Promise<MemoirOpenQuestion> {
  return request<MemoirOpenQuestion>(`/api/mobile/memoir/open-questions/${questionId}`, {
    method: "PATCH",
    data: { status },
  });
}

export function getCoverageMap(): Promise<MemoirCoverageItem[]> {
  return request<MemoirCoverageItem[]>("/api/mobile/memoir/coverage");
}

export function recalculateCoverage(): Promise<MemoirCoverageItem[]> {
  return request<MemoirCoverageItem[]>("/api/mobile/memoir/coverage/recalculate", {
    method: "POST",
    data: {},
  });
}

export function listQuestionCandidates(): Promise<MemoirQuestionCandidate[]> {
  return request<MemoirQuestionCandidate[]>("/api/mobile/memoir/question-candidates");
}

export function generateQuestionCandidates(): Promise<MemoirQuestionCandidate[]> {
  return request<MemoirQuestionCandidate[]>("/api/mobile/memoir/question-candidates/generate", {
    method: "POST",
    data: {},
  });
}

export function selectQuestionCandidate(candidateId: string): Promise<MemoirQuestion> {
  return request<MemoirQuestion>(`/api/mobile/memoir/question-candidates/${candidateId}/select`, {
    method: "POST",
    data: {},
  });
}

export function listMemoirOutputs(): Promise<MemoirOutput[]> {
  return request<MemoirOutput[]>("/api/mobile/memoir/outputs");
}

export interface MemoirTtsAssetInput {
  source_type: MemoirTtsSourceType;
  source_id: string;
  voice?: string;
  model?: string;
  format?: "mp3";
  synthesis_speed?: number;
}

export function createMemoirTtsAsset(data: MemoirTtsAssetInput): Promise<MemoirTtsAssetResponse> {
  return request<MemoirTtsAssetResponse>("/api/mobile/memoir/tts-assets", {
    method: "POST",
    data,
  });
}

export function getMemoirTtsAsset(ttsAssetId: string): Promise<MemoirTtsAsset> {
  return request<MemoirTtsAsset>(`/api/mobile/memoir/tts-assets/${ttsAssetId}`);
}

export function getMemoirTtsJob(ttsJobId: string): Promise<MemoirTtsJob> {
  return request<MemoirTtsJob>(`/api/mobile/memoir/tts-jobs/${ttsJobId}`);
}

export function processMemoirTtsJob(ttsJobId: string): Promise<MemoirTtsJob> {
  return request<MemoirTtsJob>(`/api/mobile/memoir/tts-jobs/${ttsJobId}/process`, {
    method: "POST",
    data: {},
  });
}
