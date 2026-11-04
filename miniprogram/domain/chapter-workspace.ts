import type { MemoirChapterCompletionSummary } from "./chapter-completion";
import type { MemoirChapterDraftSection, MemoirChapterSectionSummary, MemoirBookManuscriptIllustration } from "./memoir-book";

export interface MemoirChapterWorkspace {
  family_notes?: import("./memoir-book").FeaturedFamilyNote[];
  illustrations?: MemoirBookManuscriptIllustration[];
  input_signature?: string;
  server_revision: number;
  project_id: string;
  chapter_id: string;
  chapter_title: string;
  completion_summary: MemoirChapterCompletionSummary;
  draft_needs_rewrite: boolean;
  can_rewrite_draft: boolean;
  pending_assignment_material_count: number;
  pending_completion_source_count: number;
  pending_new_source_count: number;
  sections: MemoirChapterSectionSummary[];
  draft_id?: string | null;
  draft_version_id?: string | null;
  draft_version?: number | null;
  draft_status?: string | null;
  draft_content: string;
  draft_sections: MemoirChapterDraftSection[];
}
