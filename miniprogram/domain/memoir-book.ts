import type { MemoirShareLikeSummary } from "./memoir-share-likes";
import type { TTSAlignmentSegment } from "./memoir-audio";

export const DEFAULT_BOOK_DESIGN = "warm_journal" as const;

export type MemoirCreatedBy = "user" | "ai" | "asr" | "system";
export type MemoirBookPlanStatus = "draft" | "confirmed";
export type MemoirBookChapterDraftStatus = "draft" | "confirmed";
export type MemoirOutputStatus = "draft" | "accepted" | "archived";
export type MemoirImageAssetStatus = "pending" | "running" | "ready" | "failed" | "deleted";
export type MemoirImageAssetSourceType =
  | "share_video_background"
  | "chapter_illustration"
  | "utterance_illustration"
  | "cover_candidate"
  | "manual_upload";
export type MemoirImageInlinePosition = "auto" | "before" | "middle" | "after";

export type MemoirBookImagePlacementType =
  | "chapter_opening"
  | "chapter_inline"
  | "appendix"
  | "cover"
  | "back_cover";

export interface MemoirBookImagePlacement {
  inline_position?: MemoirImageInlinePosition;
  id: string;
  project_id: string;
  image_asset_id: string;
  placement_type: MemoirBookImagePlacementType;
  chapter_id?: string | null;
  chapter_title: string;
  caption_override: string;
  display_size: string;
  sort_order: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface MemoirImageAsset {
  id: string;
  project_id: string;
  status: MemoirImageAssetStatus;
  source_type: MemoirImageAssetSourceType;
  source_signature: string;
  storage_path: string;
  thumbnail_storage_path: string;
  file_url: string;
  thumbnail_url: string;
  mime_type: string;
  size_bytes?: number | null;
  width?: number | null;
  height?: number | null;
  title: string;
  caption: string;
  alt_text: string;
  style_id: string;
  prompt_hash: string;
  prompt_version: string;
  provider: string;
  model: string;
  source_utterance_id?: string | null;
  source_chapter_id?: string | null;
  source_chapter_title: string;
  share_video_job_id?: string | null;
  source_quote: string;
  metadata_json: Record<string, unknown>;
  include_in_book: boolean;
  placements: MemoirBookImagePlacement[];
  created_at: string;
  updated_at: string;
}

export interface MemoirImageAssetListResponse {
  items: MemoirImageAsset[];
  total: number;
  ready_count: number;
  included_count: number;
  unused_count: number;
}

export interface MemoirImageAssetUpdateRequest {
  title?: string | null;
  caption?: string | null;
  alt_text?: string | null;
}

export type MemoirImageDisplaySize = "full_width" | "half_width" | "inline";
// How an uploaded cover photo sits on the front cover.
export type MemoirCoverImageLayout = "cover_frame" | "cover_full_bleed";

export interface MemoirBookImagePlacementCreateRequest {
  inline_position?: MemoirImageInlinePosition;
  placement_type?: MemoirBookImagePlacementType;
  chapter_id?: string | null;
  caption_override?: string | null;
  display_size?: MemoirImageDisplaySize | MemoirCoverImageLayout;
}

export interface MemoirBookImagePlacementUpdateRequest {
  inline_position?: MemoirImageInlinePosition;
  caption_override?: string | null;
  display_size?: MemoirImageDisplaySize | MemoirCoverImageLayout;
  enabled?: boolean;
}

export interface MemoirBookChapterSourceRef {
  voice_session_id: string;
  message_id: string;
  utterance_id: string;
  transcript_version_id?: string | null;
  audio_file_id?: string | null;
  start_ms?: number | null;
  end_ms?: number | null;
  created_at?: string | null;
  quote: string;
}

export type MemoirSectionType =
  | "event"
  | "life_fragment"
  | "person_portrait"
  | "life_stage_summary"
  | "viewpoint_reflection";

export type MemoirSectionStatus = "active" | "archived";

export interface MemoirChapterSectionMaterialSummary {
  utterance_id: string;
  sort_order: number;
  text: string;
  include_in_book: boolean;
}

export interface MemoirChapterSectionSummary {
  id: string;
  title: string;
  section_type: MemoirSectionType;
  sort_order: number;
  status: MemoirSectionStatus;
  include_in_book: boolean;
  requires_user_assignment: boolean;
  version: number;
  material_count: number;
  included_material_count: number;
  materials: MemoirChapterSectionMaterialSummary[];
}


export interface MemoirBookPlanChapter {
  id: string;
  plan_id: string;
  project_id: string;
  title: string;
  sort_order: number;
  include_in_book: boolean;
  source_refs: MemoirBookChapterSourceRef[];
  sections: MemoirChapterSectionSummary[];
  metadata_json: Record<string, unknown>;
  pending_source_count: number;
  pending_completion_source_count: number;
  pending_new_source_count: number;
  removed_source_count: number;
  created_at: string;
  updated_at: string;
}

export interface MemoirBookPlan {
  id: string;
  project_id: string;
  title: string;
  status: MemoirBookPlanStatus;
  created_by: MemoirCreatedBy;
  model?: string | null;
  metadata_json: Record<string, unknown>;
  chapters: MemoirBookPlanChapter[];
  created_at: string;
  updated_at: string;
}


export interface MemoirBookChapterTitleUpdateRequest {
  title: string;
}

export interface MemoirBookChapterTitleGenerateRequest {
  ai_model?: string;
}


export type MemoirBookPlanStructureCommandType =
  | "delete_chapter"
  | "merge_chapters"
  | "move_chapter"
  | "rename_chapter"
  | "set_chapter_book_inclusion"
  | "split_chapter";

export interface MemoirBookPlanStructureCommandRequest {
  type: MemoirBookPlanStructureCommandType;
  chapter_id?: string;
  target_chapter_id?: string;
  direction?: "up" | "down";
  target_position?: "first" | "last";
  target_index?: number;
  relative_chapter_id?: string;
  relative_position?: "before" | "after";
  title?: string;
  include_in_book?: boolean;
  utterance_ids?: string[];
}

export interface MemoirBookChapterDraftVersion {
  id: string;
  draft_id: string;
  project_id: string;
  chapter_id: string;
  version: number;
  content: string;
  sections: MemoirChapterDraftSection[];
  created_by: MemoirCreatedBy;
  model?: string | null;
  prompt_snapshot: string;
  feedback?: string | null;
  created_at: string;
}

export interface MemoirChapterDraftSection {
  section_id: string;
  title: string;
  section_type: MemoirSectionType;
  section_version: number;
  sort_order: number;
  content: string;
  used_material_ids: string[];
}

export interface MemoirBookChapterDraft {
  id: string;
  project_id: string;
  chapter_id: string;
  status: MemoirBookChapterDraftStatus;
  metadata_json: Record<string, unknown>;
  pending_source_count: number;
  removed_source_count: number;
  latest_version?: MemoirBookChapterDraftVersion | null;
  versions: MemoirBookChapterDraftVersion[];
  created_at: string;
  updated_at: string;
}

export interface MemoirBookChapterDraftRevisionRequest {
  expected_version_id: string;
  expected_input_signature?: string;
  sections: Array<{
    section_id: string;
    content: string;
  }>;
}

export interface MemoirBookAssemblyResponse {
  project_id: string;
  output_id: string;
  output_version_id: string;
  version: number;
  content: string;
  chapter_snapshot_json: Array<Record<string, unknown>>;
  created_at: string;
}

export interface MemoirBookAudioRequest {
  voice?: string;
  speed_ratio?: number;
  force_regenerate?: boolean;
}

export interface MemoirAudioPlanRequest extends MemoirBookAudioRequest {
  version_id?: string;
  source_text_hash?: string;
  start_index?: number;
  profile_key?: string;
  retry_failed?: boolean;
}

export type MemoirAudioSource =
  | { kind: "book"; versionId: string }
  | { kind: "outline" }
  | { kind: "chapter"; chapterId: string; versionId?: string }
  | { kind: "section"; chapterId: string; sectionId: string; versionId?: string };

export interface MemoirBookAudioSegment {
  index: number;
  text_start: number;
  text_end: number;
  status: "idle" | "pending" | "running" | "succeeded" | "failed";
  job_id?: string | null;
  audio_file_id?: string | null;
  duration_seconds?: number | null;
  alignment: TTSAlignmentSegment[];
  error_message: string;
}

export interface MemoirBookAudioPlan {
  version_id: string;
  source_text_hash: string;
  profile_key: string;
  voice: string | null;
  speed_ratio: number;
  segments: MemoirBookAudioSegment[];
  ready_count: number;
  poll_after_ms: number;
}

export type MemoirBookCoverStyle =
  | "classic_plain"
  | "warm_gray"
  | "dark_album"
  | "moonlit_courtyard"
  | "navy_gilt"
  | "heritage_classic"
  | "warm_journal"
  | "modern_minimal";

export interface MemoirBookOutputUpdateRequest {
  title?: string | null;
  subtitle?: string | null;
  narrator_name?: string | null;
  prepared_by?: string | null;
  preface?: string | null;
  afterword?: string | null;
  back_cover_quote?: string | null;
  cover_style?: MemoirBookCoverStyle | null;
  manuscript_template?: MemoirBookManuscriptTemplate;
  custom_layout?: MemoirBookCustomLayout;
}

export type MemoirBookEditorialCopyTarget =
  | "preface"
  | "afterword"
  | "back_cover_quote"
  | "chapter_intros"
  | "pull_quotes";

export interface MemoirBookOutputEditorialCopyRegenerateRequest {
  ai_model?: string | null;
  targets?: MemoirBookEditorialCopyTarget[];
  chapter_ids?: string[];
  feedback?: string | null;
}

export type MemoirBookEditorialCopyJobTarget = "preface" | "afterword" | "back_cover_quote";
export type MemoirBookEditorialCopyJobStatus = "pending" | "running" | "succeeded" | "failed" | "skipped";

export interface MemoirBookEditorialCopyJob {
  target: MemoirBookEditorialCopyJobTarget;
  job_id: string;
  status: MemoirBookEditorialCopyJobStatus;
  error_message: string;
  updated_at: string;
}

export interface MemoirBookEditorialCopyJobsResponse {
  items: MemoirBookEditorialCopyJob[];
}

export interface MemoirBookTitleSuggestionRequest {
  ai_model?: string | null;
  count?: number;
}

export interface MemoirBookTitleSuggestionResponse {
  suggestions: string[];
  provider?: string | null;
  model?: string | null;
}

export interface MemoirBookOutputExportInfo {
  version_id: string;
  cache_key: string;
  filename: string;
  ready: boolean;
}

export type MemoirBookOutputExportFormat = "pdf" | "txt";
export type MemoirBookOutputExportLayout = "standard" | "large_print";

export interface MemoirBookOutputSectionTableOfContentsItem {
  section_id: string;
  index: number;
  title: string;
  char_count: number;
  source_count: number;
}

export interface MemoirBookOutputSection extends MemoirBookOutputSectionTableOfContentsItem {
  section_type: string;
  content: string;
  source_ref_ids: string[];
}

export interface MemoirBookOutputTableOfContentsItem {
  chapter_id: string;
  index: number;
  title: string;
  char_count: number;
  source_count: number;
  sections: MemoirBookOutputSectionTableOfContentsItem[];
}

export type MemoirBookManuscriptTemplate =
  | "classic_family"
  | "photo_memory"
  | "oral_archive"
  | "heritage_classic"
  | "warm_journal"
  | "modern_minimal"
  | "custom";

export interface MemoirBookCustomLayout {
  font_family: "auto" | "serif" | "sans";
  font_scale: "standard" | "large";
  line_spacing: "compact" | "standard" | "relaxed";
  page_margin: "narrow" | "standard" | "wide";
}

export interface MemoirBookLayoutPreview {
  template?: MemoirBookManuscriptTemplate;
  custom_layout?: MemoirBookCustomLayout;
}

export interface MemoirBookManuscriptProfile {
  template: MemoirBookManuscriptTemplate;
  title: string;
  subtitle: string;
  narrator_name: string;
  prepared_by: string;
  interview_date_range: string;
  privacy_note: string;
  ai_disclosure_note: string;
}

export interface MemoirBookManuscriptSection {
  kind: string;
  title: string;
  content: string;
  enabled: boolean;
}

export interface MemoirBookManuscriptQuote {
  text: string;
  source_ref_ids: string[];
  source?: "original_quote" | "editorial_generated" | "manual";
}

export interface MemoirBookManuscriptPersonNote {
  name: string;
  note: string;
}

export interface MemoirBookManuscriptUnconfirmedFact {
  text: string;
  source_ref_ids: string[];
}

export interface MemoirBookManuscriptSourceRef {
  utterance_id: string;
  transcript_version_id?: string | null;
  voice_session_id?: string | null;
  quote: string;
}

export interface MemoirBookManuscriptIllustration {
  inline_position?: Exclude<MemoirImageInlinePosition, "auto">;
  image_url?: string;
  image_asset_id: string;
  storage_path: string;
  mime_type: string;
  caption: string;
  alt_text: string;
  placement: MemoirBookImagePlacementType;
  display_size: string;
  source_quote: string;
  source_utterance_id?: string | null;
  section_id?: string | null;
  disclosure: string;
}

export interface MemoirBookManuscriptChapter {
  chapter_id: string;
  index: number;
  title: string;
  subtitle: string;
  intro: string;
  time_hint: string;
  place_hint: string;
  pull_quotes: MemoirBookManuscriptQuote[];
  people_notes: MemoirBookManuscriptPersonNote[];
  uncertain_facts: MemoirBookManuscriptUnconfirmedFact[];
  content: string;
  sections: MemoirBookOutputSection[];
  source_refs: MemoirBookManuscriptSourceRef[];
  illustrations: MemoirBookManuscriptIllustration[];
}

export interface MemoirBookManuscriptRenderSettings {
  page_size: "a5";
  layout: "reading" | "print";
  font_scale: "standard" | "large";
  custom_layout?: MemoirBookCustomLayout;
  show_headers: boolean;
  show_source_appendix: boolean;
  photo_mode: "none" | "chapter" | "appendix";
  cover_style: MemoirBookCoverStyle;
  cover_image?: MemoirBookManuscriptIllustration | null;
  back_cover_quote: string;
  editorial_copy_model?: string | null;
}

export interface MemoirBookManuscriptBackMatter {
  afterword: MemoirBookManuscriptSection;
  interview_records: MemoirBookManuscriptSection;
  version_note: MemoirBookManuscriptSection;
  unconfirmed_facts: MemoirBookManuscriptSection;
  source_appendix: MemoirBookManuscriptSection;
}

export interface MemoirBookManuscript {
  schema_version: number;
  profile: MemoirBookManuscriptProfile;
  front_matter: MemoirBookManuscriptSection[];
  table_of_contents: MemoirBookOutputTableOfContentsItem[];
  chapters: MemoirBookManuscriptChapter[];
  image_appendix: MemoirBookManuscriptIllustration[];
  back_matter: MemoirBookManuscriptBackMatter;
  render_settings: MemoirBookManuscriptRenderSettings;
}

export interface MemoirBookOutputVersionSummary {
  id: string;
  version: number;
  created_by: MemoirCreatedBy;
  created_at: string;
}

export interface MemoirBookOutputVersion extends MemoirBookOutputVersionSummary {
  content: string;
  manuscript?: MemoirBookManuscript | null;
}

export interface MemoirBookOutput {
  share_scope?: "book" | "chapter" | "story" | null;
  id: string;
  project_id: string;
  plan_id: string;
  title: string;
  status: MemoirOutputStatus;
  is_stale: boolean;
  stale_chapter_titles: string[];
  latest_version?: MemoirBookOutputVersion | null;
  versions: MemoirBookOutputVersionSummary[];
  created_at: string;
  updated_at: string;
}

export interface MemoirReadingShareCardSelection {
  version_id: string;
  excerpt_start: number;
  excerpt_length: number;
}

export interface MemoirBookShareStatus {
  card_selection?: MemoirReadingShareCardSelection | null;
  active: boolean;
  expires_at: string | null;
  token: string | null;
  stale: boolean;
  current: boolean;
  // Missing until the backend that records likes is deployed.
  likes?: MemoirShareLikeSummary;
}

export interface MemoirBookShareCreated {
  token: string;
  expires_at: string | null;
}
