import { memoirAIRequest, memoirNarrativeProjectPath, memoirRequest } from "./memoir-api-client";

export interface VoiceEditOperation {
  action: "delete_chapter" | "delete_section" | "delete_material" | "rename_chapter" | "rename_section";
  target_ref: string;
  new_title: string | null;
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
  return memoirAIRequest<VoiceEditPreview, { instruction: string; ai_model?: string }>(
    memoirNarrativeProjectPath(projectId, "/voice-edit/preview"), { instruction },
    { timeoutProfile: "generation", invalidatesSnapshots: false },
  );
}

export function applyVoiceEdit(projectId: string, signature: string, operations: VoiceEditOperation[]): Promise<{ message: string }> {
  return memoirRequest(memoirNarrativeProjectPath(projectId, "/voice-edit/apply"), {
    method: "POST", timeoutProfile: "mutation", data: { signature, operations },
  });
}
