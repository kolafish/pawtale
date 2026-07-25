import type { MemoirChapterWorkspace } from "../domain/chapter-workspace";
import {
  memoirNarrativeProjectPath,
  memoirAIRequest,
  memoirRequest,
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
  return memoirAIRequest<
    MemoirChapterSectionRegenerationResult,
    {
      expected_sections: Array<{ id: string; version: number }>;
      ai_model: null;
    }
  >(
    memoirNarrativeProjectPath(
      projectId,
      `/chapters/${encodeURIComponent(chapterId)}/sections/regenerate`,
    ),
    {
      expected_sections: expectedSections,
      ai_model: null,
    },
  );
}

export function reviseMemoirChapterDraftSectionWithAI(
  projectId: string,
  chapterId: string,
  sectionId: string,
  expectedVersionId: string,
  instructionText: string,
): Promise<MemoirChapterWorkspace> {
  return memoirAIRequest<
    MemoirChapterWorkspace,
    {
      expected_version_id: string;
      instruction_text: string;
      ai_model: null;
    }
  >(
    memoirNarrativeProjectPath(
      projectId,
      `/chapters/${encodeURIComponent(chapterId)}/draft-sections/${encodeURIComponent(sectionId)}/ai-revise`,
    ),
    {
      expected_version_id: expectedVersionId,
      instruction_text: instructionText.trim(),
      ai_model: null,
    },
  );
}

export function moveMemoirChapterMaterial(
  projectId: string,
  utteranceId: string,
  targetSectionId: string,
): Promise<unknown> {
  return memoirRequest<unknown>(
    memoirNarrativeProjectPath(
      projectId,
      `/section-materials/${encodeURIComponent(utteranceId)}/move`,
    ),
    {
      method: "POST",
      data: {
        target_section_id: targetSectionId,
      },
    },
  );
}
