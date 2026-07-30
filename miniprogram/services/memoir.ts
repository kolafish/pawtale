import type {
  MemoirTranscriptPolishLevel,
  MemoirProjectListResponse,
  Project,
} from "../domain/memoir";
import type { MemoirGuidanceMode } from "../domain/memoir-voice-session";
import {
  memoirProjectPath,
  memoirRequest,
} from "./memoir-api-client";

export type MemoirNarratorGender = "male" | "female" | "unspecified";

export interface MemoirProjectInput {
  title?: string;
  description?: string;
  elder_name?: string;
  narrator_gender?: MemoirNarratorGender;
  narrator_origin?: string;
  narrator_age?: number | null;
  default_guidance_mode?: MemoirGuidanceMode;
  transcript_polish_level?: MemoirTranscriptPolishLevel;
}

export function listMemoirProjects(): Promise<MemoirProjectListResponse> {
  return memoirRequest<MemoirProjectListResponse>("/api/mobile/memoir/projects");
}

export function createMemoirProject(data: MemoirProjectInput = {}): Promise<Project> {
  return memoirRequest<Project>("/api/mobile/memoir/projects", {
    method: "POST",
    data,
  });
}

export function updateMemoirProject(projectId: string, data: MemoirProjectInput): Promise<Project> {
  return memoirRequest<Project>(memoirProjectPath(projectId), {
    method: "PATCH",
    data,
  });
}

export function activateMemoirProject(projectId: string): Promise<MemoirProjectListResponse> {
  return memoirRequest<MemoirProjectListResponse>(memoirProjectPath(projectId, "/activate"), {
    method: "POST",
    data: {},
  });
}

export async function deleteMemoirProject(projectId: string): Promise<void> {
  await memoirRequest<unknown>(memoirProjectPath(projectId), {
    method: "DELETE",
  });
}
