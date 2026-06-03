import type {
  MemoirAnswer,
  MemoirAudioFile,
  MemoirDraft,
  MemoirInterviewState,
  MemoirQuestion,
  Project,
} from "../domain/memoir";
import { request, uploadFile } from "./request";

export function getMemoirProject(): Promise<Project> {
  return request<Project>("/api/mobile/memoir/project");
}

export function getCurrentSession(): Promise<MemoirInterviewState> {
  return request<MemoirInterviewState>("/api/mobile/memoir/sessions/current");
}

export function getNextQuestion(): Promise<MemoirQuestion | null> {
  return request<MemoirQuestion | null>("/api/mobile/memoir/questions/next");
}

export function skipQuestion(questionId: string): Promise<MemoirInterviewState> {
  return request<MemoirInterviewState>(`/api/mobile/memoir/questions/${questionId}/skip`, {
    method: "POST",
  });
}

export function createTextAnswer(questionId: string, text: string): Promise<MemoirAnswer> {
  return request<MemoirAnswer>("/api/mobile/memoir/answers", {
    method: "POST",
    data: { question_id: questionId, text },
  });
}

export function uploadMemoirAudioFile(filePath: string, durationSeconds?: number): Promise<MemoirAudioFile> {
  const formData: Record<string, string> = {};
  if (durationSeconds !== undefined) {
    formData.duration_seconds = String(durationSeconds);
  }
  return uploadFile<MemoirAudioFile>("/api/mobile/memoir/audio-files", {
    filePath,
    name: "file",
    formData,
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
