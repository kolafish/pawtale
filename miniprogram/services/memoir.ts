import type {
  MemoirProjectListResponse,
  Project,
} from "../domain/memoir";
import type { MemoirGuidanceMode } from "../domain/voice-chat";
import { request } from "./request";

export type MemoirNarratorGender = "male" | "female" | "unspecified";

export interface MemoirProjectInput {
  title?: string;
  description?: string;
  elder_name?: string;
  narrator_gender?: MemoirNarratorGender;
  narrator_origin?: string;
  narrator_age?: number | null;
  default_guidance_mode?: MemoirGuidanceMode;
}

export function listMemoirProjects(): Promise<MemoirProjectListResponse> {
  return request<MemoirProjectListResponse>("/api/mobile/memoir/projects");
}

export function createMemoirProject(data: MemoirProjectInput = {}): Promise<Project> {
  return request<Project>("/api/mobile/memoir/projects", {
    method: "POST",
    data,
  });
}

export function updateMemoirProject(projectId: string, data: MemoirProjectInput): Promise<Project> {
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

export async function deleteMemoirProject(projectId: string): Promise<void> {
  await request<unknown>(`/api/mobile/memoir/projects/${projectId}`, {
    method: "DELETE",
  });
}
