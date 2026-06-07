import type {
  MemoirAnswer,
  MemoirAudioFile,
  MemoirAudioTranscription,
  MemoirCoverageItem,
  MemoirDashboardResponse,
  MemoirDraft,
  MemoirEventMemory,
  MemoirInterviewState,
  MemoirMemoryStatus,
  MemoirMemoryType,
  MemoirOpenQuestion,
  MemoirOpenQuestionStatus,
  MemoirOutput,
  MemoirPersonMemory,
  MemoirPlaceMemory,
  MemoirQuestion,
  MemoirQuestionCandidate,
  MemoirSessionProcessResponse,
  MemoirSessionSummary,
  MemoirStoryList,
  MemoirStoryDetail,
  MemoirThemeMemory,
  Project,
} from "../domain/memoir";
import { request, uploadFile } from "./request";

export function getMemoirProject(): Promise<Project> {
  return request<Project>("/api/mobile/memoir/project");
}

export function getMemoirDashboard(): Promise<MemoirDashboardResponse> {
  return request<MemoirDashboardResponse>("/api/mobile/memoir/dashboard");
}

export function getCurrentSession(): Promise<MemoirInterviewState> {
  return request<MemoirInterviewState>("/api/mobile/memoir/sessions/current");
}

export function getNextQuestion(): Promise<MemoirQuestion | null> {
  return request<MemoirQuestion | null>("/api/mobile/memoir/questions/next");
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

export function getMemoirStories(): Promise<MemoirStoryList> {
  return request<MemoirStoryList>("/api/mobile/memoir/stories");
}

export function getMemoirStoryDetail(answerId: string): Promise<MemoirStoryDetail> {
  return request<MemoirStoryDetail>(`/api/mobile/memoir/stories/${answerId}`);
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

export function skipQuestion(questionId: string): Promise<MemoirInterviewState> {
  return request<MemoirInterviewState>(`/api/mobile/memoir/questions/${questionId}/skip`, {
    method: "POST",
  });
}

export function createTextAnswer(
  questionId: string,
  text: string,
  audioFileId?: string,
  durationSeconds?: number,
): Promise<MemoirAnswer> {
  const data: Record<string, string | number> = { question_id: questionId, text };
  if (audioFileId) {
    data.audio_file_id = audioFileId;
  }
  if (durationSeconds !== undefined) {
    data.duration_seconds = durationSeconds;
  }

  return request<MemoirAnswer>("/api/mobile/memoir/answers", {
    method: "POST",
    data,
  });
}

export function uploadMemoirAudioFile(filePath: string, durationSeconds?: number, sizeBytes?: number): Promise<MemoirAudioFile> {
  const formData: Record<string, string> = {};
  if (durationSeconds !== undefined) {
    formData.duration_seconds = String(durationSeconds);
  }
  if (sizeBytes !== undefined) {
    formData.size_bytes = String(sizeBytes);
  }
  return uploadFile<MemoirAudioFile>("/api/mobile/memoir/audio-files", {
    filePath,
    name: "file",
    formData,
  });
}

export function transcribeMemoirAudioFile(audioFileId: string): Promise<MemoirAudioTranscription> {
  return request<MemoirAudioTranscription>(`/api/mobile/memoir/audio-files/${audioFileId}/transcribe`, {
    method: "POST",
  });
}

export function updateTextAnswer(answerId: string, text: string): Promise<MemoirAnswer> {
  return request<MemoirAnswer>(`/api/mobile/memoir/answers/${answerId}`, {
    method: "PATCH",
    data: { text },
  });
}

export function generateDraft(answerId: string): Promise<MemoirDraft> {
  return request<MemoirDraft>(`/api/mobile/memoir/answers/${answerId}/draft`, {
    method: "POST",
    data: {},
  });
}

export function updateDraft(draftId: string, contentEdited: string): Promise<MemoirDraft> {
  return request<MemoirDraft>(`/api/mobile/memoir/drafts/${draftId}`, {
    method: "PATCH",
    data: { content_edited: contentEdited },
  });
}

export function acceptDraft(draftId: string): Promise<MemoirDraft> {
  return request<MemoirDraft>(`/api/mobile/memoir/drafts/${draftId}/accept`, {
    method: "POST",
  });
}
