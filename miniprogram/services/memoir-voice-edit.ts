import { memoirAIRequest, memoirNarrativeProjectPath, memoirRequest } from "./memoir-api-client";

export interface VoiceEditOperation {
  action: "delete_chapter" | "delete_section" | "delete_material" | "rename_chapter" | "rename_section" | "regenerate_chapter_sections" | "consolidate_chapter_sections" | "split_chapter";
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
  return memoirAIRequest<VoiceEditPreview, { instruction: string; ai_model?: string; supports_chapter_split: boolean }>(
    memoirNarrativeProjectPath(projectId, "/voice-edit/preview"), { instruction, supports_chapter_split: true },
    { timeoutProfile: "generation", invalidatesSnapshots: false },
  );
}

export function applyVoiceEdit(projectId: string, signature: string, operations: VoiceEditOperation[]): Promise<{ message: string }> {
  return memoirRequest(memoirNarrativeProjectPath(projectId, "/voice-edit/apply"), {
    method: "POST", timeoutProfile: "mutation", data: { signature, operations },
  });
}
