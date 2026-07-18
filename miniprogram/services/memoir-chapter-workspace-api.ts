import type { MemoirChapterWorkspace } from "../domain/chapter-workspace";
import {
  memoirNarrativeProjectPath,
  memoirRequest,
  withSelectedAIModel,
} from "./memoir-api-client";


function chapterWorkspacePath(projectId: string, chapterId: string): string {
  return memoirNarrativeProjectPath(
    projectId,
    `/chapters/${encodeURIComponent(chapterId)}/workspace`,
  );
}

export interface MemoirChapterSectionRegenerationResult {
  project_id: string;
  chapter_id: string;
  material_count: number;
  assigned_count: number;
  unassigned_count: number;
  section_count: number;
}

export function getMemoirChapterWorkspace(
  projectId: string,
  chapterId: string,
): Promise<MemoirChapterWorkspace> {
  return memoirRequest<MemoirChapterWorkspace>(chapterWorkspacePath(projectId, chapterId));
}

export function regenerateMemoirChapterSections(
  projectId: string,
  chapterId: string,
  expectedSections: Array<{ id: string; version: number }>,
): Promise<MemoirChapterSectionRegenerationResult> {
  return memoirRequest<MemoirChapterSectionRegenerationResult>(
    memoirNarrativeProjectPath(
      projectId,
      `/chapters/${encodeURIComponent(chapterId)}/sections/regenerate`,
    ),
    {
      method: "POST",
      data: withSelectedAIModel({
        expected_sections: expectedSections,
        ai_model: null,
      }),
      timeoutMs: 300000,
    },
  );
}
