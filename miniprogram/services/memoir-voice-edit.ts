import { memoirAIRequest, memoirNarrativeProjectPath, memoirRequest } from "./memoir-api-client";

export interface VoiceEditOperation {
  action:
    | "delete_chapter" | "delete_section" | "delete_material" | "rename_chapter" | "rename_section"
    | "regenerate_chapter_sections" | "consolidate_chapter_sections" | "split_chapter" | "merge_sections"
    | "merge_chapters" | "move_chapter" | "set_chapter_book_inclusion" | "correct_term" | "update_book_title" | "update_narrator_profile";
  target_ref: string;
  target_chapter_ref?: string | null;
  new_title: string | null;
  utterance_ids?: string[];
  section_ids?: string[];
  // Other fields the server proposes are echoed back unchanged; it signs every field.
  [field: string]: unknown;
}

export interface VoiceEditItem extends VoiceEditOperation {
  label: string;
  detail: string;
}

export interface VoiceEditNavigation {
  page: "section_revision" | "chapter" | "book_info" | "project_profile" | "transcripts";
  label: string;
  chapter_id?: string | null;
  section_id?: string | null;
  section_title?: string | null;
  feedback?: string | null;
}

export interface VoiceEditPreview {
  signature: string;
  operations: VoiceEditItem[];
  questions: string[];
  reply: string;
  navigation?: VoiceEditNavigation | null;
}

export interface VoiceEditResult {
  message: string;
  undoable?: boolean;
}

interface VoiceEditPreviewRequest {
  instruction: string;
  ai_model?: string;
  supports_chapter_split: boolean;
  supports_section_merge: boolean;
  supports_composed_chapter_edits: boolean;
  supports_multiple_chapter_splits: boolean;
  supports_extended_edits: boolean;
}

export function previewVoiceEdit(projectId: string, instruction: string): Promise<VoiceEditPreview> {
  return memoirAIRequest<VoiceEditPreview, VoiceEditPreviewRequest>(
    memoirNarrativeProjectPath(projectId, "/voice-edit/preview"),
    {
      instruction,
      supports_chapter_split: true,
      supports_section_merge: true,
      supports_composed_chapter_edits: true,
      supports_multiple_chapter_splits: true,
      supports_extended_edits: true,
    },
    { timeoutProfile: "generation", invalidatesSnapshots: false },
  );
}

export function applyVoiceEdit(projectId: string, signature: string, operations: VoiceEditOperation[]): Promise<VoiceEditResult> {
  return memoirRequest(memoirNarrativeProjectPath(projectId, "/voice-edit/apply"), {
    method: "POST", timeoutProfile: "mutation", data: { signature, operations },
  });
}

export function undoVoiceEdit(projectId: string): Promise<VoiceEditResult> {
  return memoirRequest(memoirNarrativeProjectPath(projectId, "/voice-edit/undo"), {
    method: "POST", timeoutProfile: "mutation",
  });
}
