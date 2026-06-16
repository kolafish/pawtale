import type {
  MemoirProjectListResponse,
  Project,
} from "../domain/memoir";
import { request } from "./request";

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

export async function deleteMemoirProject(projectId: string): Promise<void> {
  await request<unknown>(`/api/mobile/memoir/projects/${projectId}`, {
    method: "DELETE",
  });
}
