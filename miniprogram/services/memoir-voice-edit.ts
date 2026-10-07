import { memoirAIRequest, memoirNarrativeProjectPath, memoirRequest } from "./memoir-api-client";

export interface VoiceEditOperation {
  action: "delete_chapter" | "delete_section" | "delete_material" | "rename_chapter" | "rename_section" | "regenerate_chapter_sections" | "consolidate_chapter_sections" | "split_chapter" | "merge_sections";
  target_ref: string;
  new_title: string | null;
  utterance_ids?: string[];
  section_ids?: string[];
}

export interface VoiceEditItem extends VoiceEditOperation {
  label: string;
  detail: string;
}

export interface VoiceEditPreview {
  signature: string;
  operations: VoiceEditItem[];
  questions: string[];
  reply: string;
}

export function previewVoiceEdit(projectId: string, instruction: string): Promise<VoiceEditPreview> {
  return memoirAIRequest<VoiceEditPreview, { instruction: string; ai_model?: string; supports_chapter_split: boolean; supports_section_merge: boolean; supports_composed_chapter_edits: boolean }>(
    memoirNarrativeProjectPath(projectId, "/voice-edit/preview"), { instruction, supports_chapter_split: true, supports_section_merge: true, supports_composed_chapter_edits: true },
    { timeoutProfile: "generation", invalidatesSnapshots: false },
  );
}

export function applyVoiceEdit(projectId: string, signature: string, operations: VoiceEditOperation[]): Promise<{ message: string }> {
  return memoirRequest(memoirNarrativeProjectPath(projectId, "/voice-edit/apply"), {
    method: "POST", timeoutProfile: "mutation", data: { signature, operations },
  });
}
