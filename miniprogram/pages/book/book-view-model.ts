import type {
  MemoirBookChapterDraft,
  MemoirBookChapterSourceRef,
  MemoirBookManuscript,
  MemoirBookManuscriptSection,
  MemoirBookManuscriptTemplate,
  MemoirBookCoverStyle,
  MemoirBookOutput,
  MemoirBookOutputExportLayout,
  MemoirBookOutputVersion,
  MemoirBookPlan,
  MemoirBookPlanChapter,
  MemoirBookPlanChapterInput,
  MemoirBookPlanChapterUpdateInput,
  MemoirBookSourceCoverage,
  MemoirBookVoiceCommandParseResponse,
  MemoirBookVoiceCommandResponseKind,
  TTSAlignmentSegment,
  VoiceChatTranscriptLine,
} from "../../domain/voice-chat";
import {
  sourceRefKey,
} from "../../services/memoir-book-sources";
import { formatBeijingDateTimeShort } from "../../services/time";
import type { BookAutomationEventView } from "./book-automation-view-model";
import { createdAtTime, trimText } from "./book-view-model-utils";

export {
  uniqueSourceRefs,
} from "../../services/memoir-book-sources";

export type {
  BookAutomationEventDetailView,
  BookAutomationEventSuggestionView,
  BookAutomationEventView,
} from "./book-automation-view-model";
export {
  buildAutomationEventViews,
  mergeAutomationEvent,
  pendingBookGenerationEvents,
} from "./book-automation-view-model";
export { trimText } from "./book-view-model-utils";

export type BookStage = "empty" | "plan_draft" | "drafting";

export const NEW_CHAPTER_PICKER_VALUE = "__new_chapter__";
export const BOOK_EXPORT_LAYOUT_OPTIONS: BookExportLayoutOptionView[] = [
  {
    label: "阅读版",
    value: "standard",
    detailText: "A5 书页，含目录页码，适合微信阅读和分享",
  },
  {
    label: "打印版",
    value: "large_print",
    detailText: "字号更大，留白更宽，适合纸质打印",
  },
];

export interface ChapterPickerOption {
  label: string;
  value: string;
}

export interface BookUncoveredSourceView {
  utterance_id: string;
  recordNumber: number;
  previewText: string;
  created_at: string;
  createdAtText: string;
  sourceRef: MemoirBookChapterSourceRef;
}

export interface BookChapterSourceView extends MemoirBookChapterSourceRef {
  sourceIndex: number;
  recordNumber: number;
  labelText: string;
  createdAtText: string;
  charCount: number;
  previewText: string;
}

export interface BookChapterView extends MemoirBookPlanChapter {
  editingTitle: string;
  titleChanged: boolean;
  ttsTarget: string;
  sourceCount: number;
  sourceSummary: string;
  chapterSummaryMetaText: string;
  sourceViews: BookChapterSourceView[];
  sourceListExpanded: boolean;
  sourceToggleText: string;
  canMoveUp: boolean;
  canMoveDown: boolean;
  pendingSourceCount: number;
  removedSourceCount: number;
  sourceChangeNotice: string;
  draft: MemoirBookChapterDraft | null;
  draftContent: string;
  draftCharCount: number;
  draftChanged: boolean;
  draftStatusText: string;
  draftStatusHintText: string;
  draftVersionText: string;
  draftActionText: string;
  draftActionHintText: string;
  draftBusy: boolean;
  draftError: string;
}

export interface BookOutputTocView {
  id: string;
  chapterId: string;
  index: number;
  indexText: string;
  title: string;
  metaText: string;
}

export interface BookOutputChapterPreviewView {
  id: string;
  chapterId: string;
  index: number;
  indexText: string;
  title: string;
  content: string;
  metaText: string;
}

export interface BookOutputVersionOptionView {
  id: string;
  label: string;
  version: number;
  isLatest: boolean;
}

export interface BookManuscriptStructureItemView {
  id: string;
  title: string;
  metaText: string;
  statusText: string;
}

export interface BookManuscriptSectionView {
  id: string;
  title: string;
  kindText: string;
  contentPreview: string;
  enabled: boolean;
}

export interface BookManuscriptChapterView {
  id: string;
  chapterId: string;
  indexText: string;
  title: string;
  intro: string;
  quoteText: string;
  quoteSourceRefId: string;
  quoteSourceText: string;
  metaText: string;
  contentPreview: string;
}

export interface BookExportLayoutOptionView {
  label: string;
  value: MemoirBookOutputExportLayout;
  detailText: string;
}

export interface BookCoverStyleOptionView {
  value: MemoirBookCoverStyle;
  label: string;
  detailText: string;
  previewClass: string;
}

export const BOOK_COVER_STYLE_OPTIONS: BookCoverStyleOptionView[] = [
  {
    value: "classic_plain",
    label: "旧纸棕",
    detailText: "米白纸色、深褐字和细线，适合默认成书",
    previewClass: "classic-plain",
  },
  {
    value: "warm_gray",
    label: "暖灰书稿",
    detailText: "暖灰底、深灰字，更像整理后的手稿",
    previewClass: "warm-gray",
  },
  {
    value: "dark_album",
    label: "旧书封面",
    detailText: "深灰褐底、浅色字，适合更庄重的纪念册",
    previewClass: "dark-album",
  },
  {
    value: "moonlit_courtyard",
    label: "月下庭院",
    detailText: "水墨庭院底图、墨褐标题，适合家庭回忆录默认封面封底",
    previewClass: "moonlit-courtyard",
  },
  {
    value: "navy_gilt",
    label: "深蓝烫金",
    detailText: "深蓝布面底图、金色标题和边框，适合更庄重的纪念册",
    previewClass: "navy-gilt",
  },
];

export interface BookVoiceCommandUndoSnapshot {
  planTitle: string;
  chapters: MemoirBookPlanChapterUpdateInput[];
}

export interface BookData {
  loading: boolean;
  needsLogin: boolean;
  generatingPlan: boolean;
  confirmingPlan: boolean;
  updatingPlanStructure: boolean;
  error: string;
  projectId: string;
  projectTitle: string;
  projectNarratorName: string;
  plan: MemoirBookPlan | null;
  planTitle: string;
  stage: BookStage;
  stageText: string;
  chapters: BookChapterView[];
  chapterStructureEditing: boolean;
  editingChapterId: string;
  confirmedDraftCount: number;
  totalChapterCount: number;
  allChaptersConfirmed: boolean;
  canGenerateBook: boolean;
  progressText: string;
  sourceCoverage: MemoirBookSourceCoverage | null;
  sourceCoverageNotice: string;
  uncoveredSourceCount: number;
  uncoveredSourceDisplayText: string;
  uncoveredSources: BookUncoveredSourceView[];
  chapterPickerOptions: ChapterPickerOption[];
  assigningSourceId: string;
  titleBusyTarget: string;
  bookOutput: MemoirBookOutput | null;
  bookContent: string;
  bookCharCount: number;
  bookPreviewTitle: string;
  bookPreviewTocItems: BookOutputTocView[];
  bookPreviewChapters: BookOutputChapterPreviewView[];
  bookPreviewChaptersExpanded: boolean;
  bookOutputDetailsExpanded: boolean;
  bookManuscriptAvailable: boolean;
  bookManuscriptTitle: string;
  bookManuscriptSubtitle: string;
  bookManuscriptTemplateLabel: string;
  bookManuscriptSettingsText: string;
  bookManuscriptStructureItems: BookManuscriptStructureItemView[];
  bookManuscriptFrontMatterItems: BookManuscriptSectionView[];
  bookManuscriptChapterItems: BookManuscriptChapterView[];
  bookManuscriptBackMatterItems: BookManuscriptSectionView[];
  bookVersionText: string;
  bookVersionOptions: BookOutputVersionOptionView[];
  bookSelectedVersionId: string;
  bookSelectedVersionIndex: number;
  bookViewingHistoricalVersion: boolean;
  bookVersionNotice: string;
  bookStaleNotice: string;
  bookActionText: string;
  bookExportBusyTarget: string;
  bookExportLayout: MemoirBookOutputExportLayout;
  bookExportLayoutIndex: number;
  bookExportLayoutLabel: string;
  bookExportLayoutDetailText: string;
  bookExportLayoutOptions: BookExportLayoutOptionView[];
  bookExportShowSourceAppendix: boolean;
  bookTitleInput: string;
  bookSubtitleInput: string;
  bookNarratorNameInput: string;
  bookPreparedByInput: string;
  bookPrefaceInput: string;
  bookAfterwordInput: string;
  bookBackCoverQuoteInput: string;
  bookTitleChanged: boolean;
  bookMetadataChanged: boolean;
  bookTitleSaving: boolean;
  bookDesignSaveStatusText: string;
  bookTitleSuggesting: boolean;
  bookTitleSuggestions: string[];
  bookCoverStyle: MemoirBookCoverStyle;
  bookCoverStylePreviewClass: string;
  bookCoverStyleIndex: number;
  bookCoverStyleLabel: string;
  bookCoverStyleDetailText: string;
  bookCoverStyleOptions: BookCoverStyleOptionView[];
  bookCoverStyleChanged: boolean;
  bookEditorialCopyBusyTarget: string;
  bookChapterEditorialEditorVisible: boolean;
  bookChapterEditorialChapterId: string;
  bookChapterEditorialChapterTitle: string;
  bookChapterEditorialIntroInput: string;
  bookChapterEditorialPullQuoteInput: string;
  bookChapterEditorialSourceRefId: string;
  bookChapterEditorialSourceText: string;
  generatingBook: boolean;
  ttsBusyTarget: string;
  playingTarget: string;
  bookTTSActiveTarget: string;
  bookTTSActiveText: string;
  bookTTSSpokenText: string;
  bookTTSCurrentText: string;
  bookTTSRemainingText: string;
  bookTTSProgressPercent: number;
  bookTTSDurationSeconds: number;
  bookTTSAlignment: TTSAlignmentSegment[];
  bookVoiceAssistantExpanded: boolean;
  bookVoiceAssistantHint: string;
  bookVoiceAssistantStatusText: string;
  bookVoiceAssistantListening: boolean;
  bookVoiceAssistantBusy: boolean;
  bookVoiceAssistantConversationActive: boolean;
  bookVoiceAssistantReplyText: string;
  bookVoiceAssistantSpeakingLineId: string;
  bookVoiceLiveText: string;
  bookVoiceTranscriptLines: VoiceChatTranscriptLine[];
  bookVoiceAudioLevelPercent: number;
  bookVoiceCaptureStateText: string;
  bookVoiceCommandText: string;
  bookVoiceCommandIntentLabel: string;
  bookVoiceCommandResponseKind: MemoirBookVoiceCommandResponseKind | "";
  bookVoiceCommandPanelTitle: string;
  bookVoiceCommandStateText: string;
  bookVoiceCommandDetailText: string;
  bookVoiceCommandSummary: string;
  bookVoiceCommandRequiresConfirmation: boolean;
  bookVoiceCommandPreview: MemoirBookVoiceCommandParseResponse | null;
  bookVoiceCommandCanExecute: boolean;
  bookVoiceCommandActionText: string;
  bookVoiceCommandExecuting: boolean;
  bookVoiceCommandUndoText: string;
  bookVoiceCommandUndoSnapshot: BookVoiceCommandUndoSnapshot | null;
  automationEventItems: BookAutomationEventView[];
  automationEventBusyTarget: string;
  automationEventExpandedTargets: Record<string, boolean>;
  automationEventLoading: boolean;
  automationEventError: string;
  imageAssetLoading: boolean;
  imageAssetTotal: number;
  imageAssetReadyCount: number;
  imageAssetIncludedCount: number;
  imageAssetUnusedCount: number;
  imageAssetSummaryText: string;
}

export type BookHomeData = Omit<
  BookData,
  | "editingChapterId"
  | "bookPreviewTocItems"
  | "bookPreviewChapters"
  | "bookPreviewChaptersExpanded"
  | "bookOutputDetailsExpanded"
  | "bookManuscriptAvailable"
  | "bookManuscriptTitle"
  | "bookManuscriptSubtitle"
  | "bookManuscriptTemplateLabel"
  | "bookManuscriptSettingsText"
  | "bookManuscriptStructureItems"
  | "bookManuscriptFrontMatterItems"
  | "bookManuscriptChapterItems"
  | "bookManuscriptBackMatterItems"
  | "bookVersionText"
  | "bookVersionOptions"
  | "bookSelectedVersionId"
  | "bookSelectedVersionIndex"
  | "bookViewingHistoricalVersion"
  | "bookVersionNotice"
  | "bookExportBusyTarget"
  | "bookExportLayout"
  | "bookExportLayoutIndex"
  | "bookExportLayoutLabel"
  | "bookExportLayoutDetailText"
  | "bookExportLayoutOptions"
  | "bookExportShowSourceAppendix"
  | "bookTitleInput"
  | "bookSubtitleInput"
  | "bookNarratorNameInput"
  | "bookPreparedByInput"
  | "bookPrefaceInput"
  | "bookAfterwordInput"
  | "bookBackCoverQuoteInput"
  | "bookTitleChanged"
  | "bookMetadataChanged"
  | "bookTitleSaving"
  | "bookDesignSaveStatusText"
  | "bookTitleSuggesting"
  | "bookTitleSuggestions"
  | "bookCoverStyle"
  | "bookCoverStylePreviewClass"
  | "bookCoverStyleIndex"
  | "bookCoverStyleLabel"
  | "bookCoverStyleDetailText"
  | "bookCoverStyleOptions"
  | "bookCoverStyleChanged"
  | "bookEditorialCopyBusyTarget"
  | "bookChapterEditorialEditorVisible"
  | "bookChapterEditorialChapterId"
  | "bookChapterEditorialChapterTitle"
  | "bookChapterEditorialIntroInput"
  | "bookChapterEditorialPullQuoteInput"
  | "bookChapterEditorialSourceRefId"
  | "bookChapterEditorialSourceText"
>;

export function positiveFiniteSeconds(value: number | null | undefined): number {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

export function latestDraftContent(draft: MemoirBookChapterDraft | null): string {
  return draft?.latest_version?.content || "";
}

export function draftStatusText(draft: MemoirBookChapterDraft | null): string {
  if (!draft?.latest_version) {
    return "未生成";
  }
  return draft.status === "confirmed" ? "已确认" : "待确认";
}

function draftStatusHintText(draft: MemoirBookChapterDraft | null): string {
  if (!draft?.latest_version) {
    return "先生成正文后再确认";
  }
  if (draft.status === "confirmed") {
    return "修改后需要重新确认";
  }
  return "确认前可继续修改";
}

export function sourceChangeNotice(pendingCount: number, removedCount: number): string {
  if (removedCount) {
    return `${removedCount} 条原始讲述已删除，章节稿需要更新`;
  }
  if (pendingCount) {
    return `${pendingCount} 条新增讲述待补写`;
  }
  return "";
}

export function draftActionText(draft: MemoirBookChapterDraft | null, pendingCount: number, removedCount = 0): string {
  if (removedCount) {
    return "整理本章";
  }
  if (pendingCount) {
    return "补写新增";
  }
  return draft?.latest_version ? "生成新版本" : "生成本章";
}

function draftActionHintText(draft: MemoirBookChapterDraft | null, pendingCount: number, removedCount = 0): string {
  if (removedCount) {
    return "会保留剩余讲述并更新正文";
  }
  if (pendingCount) {
    return "只补入新增讲述";
  }
  return draft?.latest_version ? "不覆盖当前版本" : "";
}

export function draftVersionText(draft: MemoirBookChapterDraft | null): string {
  const version = draft?.latest_version?.version;
  return version ? `历史版本 · 已修改 ${version} 次` : "暂无正文";
}

export function responseCount(value: number | null | undefined): number {
  const count = Number(value);
  return Number.isFinite(count) && count > 0 ? Math.floor(count) : 0;
}

export function latestBookContent(output: MemoirBookOutput | null): string {
  return output?.latest_version?.content || "";
}

export function bookOutputVersions(output: MemoirBookOutput | null): MemoirBookOutputVersion[] {
  if (!output) {
    return [];
  }
  const versions = [...(output.versions || [])];
  const latest = output.latest_version || null;
  if (latest && !versions.some((version) => version.id === latest.id)) {
    versions.push(latest);
  }
  return versions
    .filter((version) => version.id)
    .sort((left, right) => right.version - left.version);
}

export function selectedBookOutputVersion(
  output: MemoirBookOutput | null,
  selectedVersionId = "",
): MemoirBookOutputVersion | null {
  const versions = bookOutputVersions(output);
  if (!versions.length) {
    return null;
  }
  return versions.find((version) => version.id === selectedVersionId) || versions[0];
}

export function selectedBookContent(output: MemoirBookOutput | null, selectedVersionId = ""): string {
  return selectedBookOutputVersion(output, selectedVersionId)?.content || "";
}

function bookOutputLatestVersionId(output: MemoirBookOutput | null): string {
  return output?.latest_version?.id || bookOutputVersions(output)[0]?.id || "";
}

export function bookVersionOptions(
  output: MemoirBookOutput | null,
  selectedVersionId = "",
): {
  options: BookOutputVersionOptionView[];
  selectedId: string;
  selectedIndex: number;
  viewingHistorical: boolean;
} {
  const versions = bookOutputVersions(output);
  const latestId = bookOutputLatestVersionId(output);
  const selected = selectedBookOutputVersion(output, selectedVersionId);
  const selectedId = selected?.id || "";
  const options = versions.map((version) => {
    const createdAtText = version.created_at ? formatBeijingDateTimeShort(version.created_at) : "";
    const isLatest = version.id === latestId;
    const label = `${isLatest ? "最新版 · " : ""}已修改 ${version.version} 次`;
    return {
      id: version.id,
      label: createdAtText ? `${label} · ${createdAtText}` : label,
      version: version.version,
      isLatest,
    };
  });
  const selectedIndex = Math.max(0, options.findIndex((option) => option.id === selectedId));
  return {
    options,
    selectedId,
    selectedIndex,
    viewingHistorical: Boolean(selectedId && latestId && selectedId !== latestId),
  };
}

function bookSourceCountText(count: number): string {
  return count > 0 ? `${count} 条原始讲述` : "暂无原始讲述计数";
}

function bookOralSourceCountText(count: number): string {
  return count > 0 ? `${count} 条口述` : "暂无口述";
}

export function bookOutputPreviewTitle(
  output: MemoirBookOutput | null,
  selectedVersionId = "",
): string {
  const version = selectedBookOutputVersion(output, selectedVersionId);
  return trimText(version?.structured_content?.title || output?.title || "");
}

export function bookOutputTocItems(output: MemoirBookOutput | null, selectedVersionId = ""): BookOutputTocView[] {
  const toc = selectedBookOutputVersion(output, selectedVersionId)?.structured_content?.table_of_contents || [];
  return toc
    .filter((item) => item.chapter_id && item.title)
    .map((item, index) => {
      const chapterIndex = responseCount(item.index) || index + 1;
      const charCount = responseCount(item.char_count);
      const sourceCount = responseCount(item.source_count);
      return {
        id: `${item.chapter_id}:${chapterIndex}`,
        chapterId: item.chapter_id,
        index: chapterIndex,
        indexText: `${chapterIndex}`,
        title: trimText(item.title),
        metaText: [`${charCount} 字`, bookSourceCountText(sourceCount)].join(" · "),
      };
    });
}

export function bookOutputChapterPreviews(
  output: MemoirBookOutput | null,
  selectedVersionId = "",
): BookOutputChapterPreviewView[] {
  const chapters = selectedBookOutputVersion(output, selectedVersionId)?.structured_content?.chapters || [];
  return chapters
    .filter((chapter) => chapter.chapter_id && chapter.title && trimText(chapter.content))
    .map((chapter, index) => {
      const chapterIndex = responseCount(chapter.index) || index + 1;
      const charCount = responseCount(chapter.char_count) || trimText(chapter.content).length;
      const sourceCount = responseCount(chapter.source_count);
      const versionText = responseCount(chapter.draft_version) ? `已修改 ${chapter.draft_version} 次` : "";
      return {
        id: `${chapter.chapter_id}:${chapterIndex}`,
        chapterId: chapter.chapter_id,
        index: chapterIndex,
        indexText: `第 ${chapterIndex} 章`,
        title: trimText(chapter.title),
        content: trimText(chapter.content),
        metaText: [`${charCount} 字`, bookSourceCountText(sourceCount), versionText].filter(Boolean).join(" · "),
      };
    });
}

function selectedBookManuscript(output: MemoirBookOutput | null, selectedVersionId = ""): MemoirBookManuscript | null {
  return selectedBookOutputVersion(output, selectedVersionId)?.manuscript || null;
}

function manuscriptFrontSectionContent(manuscript: MemoirBookManuscript | null, kind: string): string {
  return trimText((manuscript?.front_matter || []).find((section) => section.kind === kind)?.content || "");
}

function manuscriptBackSectionContent(manuscript: MemoirBookManuscript | null, kind: string): string {
  const section = manuscript?.back_matter?.[kind as keyof MemoirBookManuscript["back_matter"]];
  return section && typeof section === "object" ? trimText(section.content || "") : "";
}

function manuscriptTemplateLabel(template: MemoirBookManuscriptTemplate | undefined): string {
  if (template === "photo_memory") {
    return "家庭纪念版";
  }
  if (template === "oral_archive") {
    return "口述档案版";
  }
  return "素雅书籍版";
}

function manuscriptSectionKindText(kind: string): string {
  const labels: Record<string, string> = {
    cover: "封面",
    title_page: "扉页",
    generation_note: "说明",
    dedication: "献辞",
    preface: "前言",
    afterword: "后记",
    interview_records: "原始讲述",
    version_note: "整理说明",
    unconfirmed_facts: "待补充",
    source_appendix: "讲述来源",
  };
  return labels[kind] || "书稿";
}

function manuscriptSectionTitle(section: Pick<MemoirBookManuscriptSection, "kind" | "title">): string {
  const titles: Record<string, string> = {
    interview_records: "原始讲述",
    version_note: "整理说明",
    unconfirmed_facts: "待补充的信息",
    source_appendix: "原始讲述索引",
  };
  return titles[section.kind] || trimText(section.title) || manuscriptSectionKindText(section.kind);
}

function previewText(text: string, maxLength = 72): string {
  const value = trimText(text).replace(/\s+/g, " ");
  return value.length > maxLength ? `${value.slice(0, maxLength)}...` : value;
}

function sectionView(section: MemoirBookManuscriptSection, index: number, prefix: string): BookManuscriptSectionView {
  return {
    id: `${prefix}:${section.kind}:${index}`,
    title: manuscriptSectionTitle(section),
    kindText: manuscriptSectionKindText(section.kind),
    contentPreview: previewText(section.content),
    enabled: section.enabled !== false,
  };
}

function backMatterSections(manuscript: MemoirBookManuscript): MemoirBookManuscriptSection[] {
  return [
    manuscript.back_matter.afterword,
    manuscript.back_matter.interview_records,
    manuscript.back_matter.version_note,
    manuscript.back_matter.unconfirmed_facts,
    manuscript.back_matter.source_appendix,
  ].filter(Boolean);
}

function manuscriptSettingsText(manuscript: MemoirBookManuscript | null): string {
  if (!manuscript) {
    return "按已确认章节生成基础书稿";
  }
  const settings = manuscript.render_settings;
  const pageSize = settings.page_size === "a5" ? "A5" : "默认纸张";
  const layout = settings.layout === "print" ? "打印排版" : "阅读排版";
  const header = settings.show_headers ? "含页眉" : "无页眉";
  const appendix = settings.show_source_appendix ? "附上原始讲述" : "隐藏原始讲述";
  return [pageSize, layout, header, appendix].join(" · ");
}

function fallbackManuscriptStructureItems(
  output: MemoirBookOutput | null,
  selectedVersionId = "",
): BookManuscriptStructureItemView[] {
  const tocItems = bookOutputTocItems(output, selectedVersionId);
  const chapters = bookOutputChapterPreviews(output, selectedVersionId);
  if (!tocItems.length && !chapters.length) {
    return [];
  }
  return [
    { id: "fallback:cover", title: "封面", metaText: "基础信息", statusText: "已生成" },
    { id: "fallback:toc", title: "目录", metaText: `${tocItems.length} 章`, statusText: "已生成" },
    { id: "fallback:chapters", title: "正文", metaText: `${chapters.length} 章`, statusText: "已生成" },
  ];
}

function manuscriptStructureItems(
  manuscript: MemoirBookManuscript | null,
  output: MemoirBookOutput | null,
  selectedVersionId = "",
): BookManuscriptStructureItemView[] {
  if (!manuscript) {
    return fallbackManuscriptStructureItems(output, selectedVersionId);
  }
  const frontMatter = (manuscript.front_matter || []).map((section, index) => ({
    id: `front:${section.kind}:${index}`,
    title: manuscriptSectionTitle(section),
    metaText: manuscriptSectionKindText(section.kind),
    statusText: section.enabled === false ? "已隐藏" : "已生成",
  }));
  const backMatter = backMatterSections(manuscript).map((section, index) => ({
    id: `back:${section.kind}:${index}`,
    title: manuscriptSectionTitle(section),
    metaText: manuscriptSectionKindText(section.kind),
    statusText: section.enabled === false ? "已隐藏" : "已生成",
  }));
  return [
    ...frontMatter,
    {
      id: "toc",
      title: "目录",
      metaText: `${manuscript.table_of_contents.length} 章`,
      statusText: "已生成",
    },
    {
      id: "chapters",
      title: "正文",
      metaText: `${manuscript.chapters.length} 章`,
      statusText: "已生成",
    },
    ...backMatter,
  ];
}

function manuscriptChapterViews(
  manuscript: MemoirBookManuscript | null,
  output: MemoirBookOutput | null,
  selectedVersionId = "",
): BookManuscriptChapterView[] {
  if (!manuscript) {
    return bookOutputChapterPreviews(output, selectedVersionId).map((chapter) => ({
      id: `fallback:${chapter.id}`,
      chapterId: chapter.chapterId,
      indexText: chapter.indexText,
      title: chapter.title,
      intro: "",
      quoteText: "",
      quoteSourceRefId: "",
      quoteSourceText: "",
      metaText: chapter.metaText,
      contentPreview: previewText(chapter.content, 96),
    }));
  }
  return (manuscript.chapters || []).map((chapter, index) => {
    const chapterIndex = responseCount(chapter.index) || index + 1;
    const quote = (chapter.pull_quotes || []).find((item) => trimText(item.text));
    const quoteText = trimText(quote?.text || "");
    const quoteSourceRefId = trimText(quote?.source_ref_ids?.[0] || "");
    const quoteSourceText = quoteSourceRefId
      ? "来自原始口述"
      : (quote?.source === "manual" ? "手动编辑" : (quoteText ? "整理生成" : ""));
    const sourceCount = chapter.source_refs?.length || 0;
    const hintText = [trimText(chapter.time_hint), trimText(chapter.place_hint)].filter(Boolean).join(" · ");
    return {
      id: `${chapter.chapter_id}:${chapterIndex}`,
      chapterId: chapter.chapter_id,
      indexText: `第 ${chapterIndex} 章`,
      title: trimText(chapter.title) || "未命名章节",
      intro: trimText(chapter.intro),
      quoteText,
      quoteSourceRefId,
      quoteSourceText,
      metaText: [hintText, `${trimText(chapter.content).length} 字`, bookSourceCountText(sourceCount)]
        .filter(Boolean)
        .join(" · "),
      contentPreview: previewText(chapter.content, 96),
    };
  });
}

export function bookManuscriptData(
  output: MemoirBookOutput | null,
  selectedVersionId = "",
): Pick<
  BookData,
  | "bookManuscriptAvailable"
  | "bookManuscriptTitle"
  | "bookManuscriptSubtitle"
  | "bookManuscriptTemplateLabel"
  | "bookManuscriptSettingsText"
  | "bookManuscriptStructureItems"
  | "bookManuscriptFrontMatterItems"
  | "bookManuscriptChapterItems"
  | "bookManuscriptBackMatterItems"
> {
  const manuscript = selectedBookManuscript(output, selectedVersionId);
  const fallbackTitle = bookOutputPreviewTitle(output, selectedVersionId);
  const structureItems = manuscriptStructureItems(manuscript, output, selectedVersionId);
  const chapterItems = manuscriptChapterViews(manuscript, output, selectedVersionId);
  return {
    bookManuscriptAvailable: Boolean(manuscript || structureItems.length || chapterItems.length),
    bookManuscriptTitle: trimText(manuscript?.profile.title || fallbackTitle),
    bookManuscriptSubtitle: trimText(manuscript?.profile.subtitle || "根据已确认章节整理"),
    bookManuscriptTemplateLabel: manuscript ? manuscriptTemplateLabel(manuscript.profile.template) : "基础成书版",
    bookManuscriptSettingsText: manuscriptSettingsText(manuscript),
    bookManuscriptStructureItems: structureItems,
    bookManuscriptFrontMatterItems: manuscript
      ? (manuscript.front_matter || []).map((section, index) => sectionView(section, index, "front"))
      : [],
    bookManuscriptChapterItems: chapterItems,
    bookManuscriptBackMatterItems: manuscript
      ? backMatterSections(manuscript).map((section, index) => sectionView(section, index, "back"))
      : [],
  };
}

function selectedBookCoverStyle(output: MemoirBookOutput | null, selectedVersionId = ""): MemoirBookCoverStyle {
  const manuscript = selectedBookManuscript(output, selectedVersionId);
  const style = manuscript?.render_settings?.cover_style;
  const option = BOOK_COVER_STYLE_OPTIONS.find((candidate) => candidate.value === style);
  return option ? option.value : "classic_plain";
}

export function bookCoverStyleData(
  output: MemoirBookOutput | null,
  selectedVersionId = "",
): Pick<
  BookData,
  | "bookCoverStyle"
  | "bookCoverStylePreviewClass"
  | "bookCoverStyleIndex"
  | "bookCoverStyleLabel"
  | "bookCoverStyleDetailText"
  | "bookCoverStyleOptions"
  | "bookCoverStyleChanged"
> {
  const style = selectedBookCoverStyle(output, selectedVersionId);
  const index = Math.max(0, BOOK_COVER_STYLE_OPTIONS.findIndex((option) => option.value === style));
  const option = BOOK_COVER_STYLE_OPTIONS[index] || BOOK_COVER_STYLE_OPTIONS[0];
  return {
    bookCoverStyle: option.value,
    bookCoverStylePreviewClass: option.previewClass,
    bookCoverStyleIndex: index,
    bookCoverStyleLabel: option.label,
    bookCoverStyleDetailText: option.detailText,
    bookCoverStyleOptions: BOOK_COVER_STYLE_OPTIONS,
    bookCoverStyleChanged: false,
  };
}

function bookMetadataInputData(
  output: MemoirBookOutput | null,
  selectedVersionId = "",
): Pick<
  BookData,
  | "bookSubtitleInput"
  | "bookNarratorNameInput"
  | "bookPreparedByInput"
  | "bookPrefaceInput"
  | "bookAfterwordInput"
  | "bookBackCoverQuoteInput"
  | "bookMetadataChanged"
> {
  const manuscript = selectedBookManuscript(output, selectedVersionId);
  return {
    bookSubtitleInput: trimText(manuscript?.profile.subtitle || ""),
    bookNarratorNameInput: trimText(manuscript?.profile.narrator_name || ""),
    bookPreparedByInput: trimText(manuscript?.profile.prepared_by || "家人整理"),
    bookPrefaceInput: manuscriptFrontSectionContent(manuscript, "preface"),
    bookAfterwordInput: manuscriptBackSectionContent(manuscript, "afterword"),
    bookBackCoverQuoteInput: trimText(manuscript?.render_settings?.back_cover_quote || ""),
    bookMetadataChanged: false,
  };
}

export function bookOutputVersionText(output: MemoirBookOutput | null): string {
  const version = output?.latest_version?.version;
  return version ? `已修改 ${version} 次` : "尚未生成全书";
}

function selectedBookOutputVersionText(output: MemoirBookOutput | null, selectedVersionId = ""): string {
  const selected = selectedBookOutputVersion(output, selectedVersionId);
  return selected ? `已修改 ${selected.version} 次` : "尚未生成全书";
}

export function bookOutputStaleNotice(output: MemoirBookOutput | null): string {
  if (!output?.is_stale) {
    return "";
  }
  const titles = (output.stale_chapter_titles || []).filter(Boolean).slice(0, 2);
  if (!titles.length) {
    return "章节内容已更新，请重新整理一版。";
  }
  const suffix = (output.stale_chapter_titles || []).length > 2 ? "等章节" : "";
  return `章节「${titles.join("、")}」${suffix}已更新，请重新整理一版。`;
}

function bookActionText(output: MemoirBookOutput | null): string {
  if (output?.is_stale) {
    return "更新书稿";
  }
  return output?.latest_version ? "重新整理一版" : "生成全书";
}

function bookVersionNotice(output: MemoirBookOutput | null, viewingHistorical: boolean): string {
  if (!output?.latest_version) {
    return "";
  }
  if (viewingHistorical) {
    return "正在查看历史版本。朗读前请切回最新版。";
  }
  return "";
}

export function bookOutputData(output: MemoirBookOutput | null, selectedVersionId = ""): Pick<
  BookData,
  | "bookOutput"
  | "bookContent"
  | "bookCharCount"
  | "bookPreviewTitle"
  | "bookPreviewTocItems"
  | "bookPreviewChapters"
  | "bookManuscriptAvailable"
  | "bookManuscriptTitle"
  | "bookManuscriptSubtitle"
  | "bookManuscriptTemplateLabel"
  | "bookManuscriptSettingsText"
  | "bookManuscriptStructureItems"
  | "bookManuscriptFrontMatterItems"
  | "bookManuscriptChapterItems"
  | "bookManuscriptBackMatterItems"
  | "bookVersionText"
  | "bookVersionOptions"
  | "bookSelectedVersionId"
  | "bookSelectedVersionIndex"
  | "bookViewingHistoricalVersion"
  | "bookVersionNotice"
  | "bookStaleNotice"
  | "bookActionText"
  | "bookTitleInput"
  | "bookSubtitleInput"
  | "bookNarratorNameInput"
  | "bookPreparedByInput"
  | "bookPrefaceInput"
  | "bookAfterwordInput"
  | "bookBackCoverQuoteInput"
  | "bookTitleChanged"
  | "bookMetadataChanged"
  | "bookDesignSaveStatusText"
  | "bookTitleSuggestions"
  | "bookCoverStyle"
  | "bookCoverStylePreviewClass"
  | "bookCoverStyleIndex"
  | "bookCoverStyleLabel"
  | "bookCoverStyleDetailText"
  | "bookCoverStyleOptions"
  | "bookCoverStyleChanged"
> {
  const versionState = bookVersionOptions(output, selectedVersionId);
  const content = selectedBookContent(output, versionState.selectedId);
  const manuscriptData = bookManuscriptData(output, versionState.selectedId);
  return {
    bookOutput: output,
    bookContent: content,
    bookCharCount: trimText(content).length,
    bookPreviewTitle: bookOutputPreviewTitle(output, versionState.selectedId),
    bookPreviewTocItems: bookOutputTocItems(output, versionState.selectedId),
    bookPreviewChapters: bookOutputChapterPreviews(output, versionState.selectedId),
    ...manuscriptData,
    bookVersionText: selectedBookOutputVersionText(output, versionState.selectedId),
    bookVersionOptions: versionState.options,
    bookSelectedVersionId: versionState.selectedId,
    bookSelectedVersionIndex: versionState.selectedIndex,
    bookViewingHistoricalVersion: versionState.viewingHistorical,
    bookVersionNotice: bookVersionNotice(output, versionState.viewingHistorical),
    bookStaleNotice: bookOutputStaleNotice(output),
    bookActionText: bookActionText(output),
    bookTitleInput: manuscriptData.bookManuscriptTitle || bookOutputPreviewTitle(output, versionState.selectedId),
    ...bookMetadataInputData(output, versionState.selectedId),
    bookTitleChanged: false,
    bookDesignSaveStatusText: output?.latest_version
      ? (versionState.viewingHistorical ? "切回最新版后自动保存" : "已保存")
      : "生成时应用",
    bookTitleSuggestions: [],
    ...bookCoverStyleData(output, versionState.selectedId),
  };
}

export function bookHomeOutputData(output: MemoirBookOutput | null): Pick<
  BookHomeData,
  | "bookOutput"
  | "bookContent"
  | "bookCharCount"
  | "bookPreviewTitle"
  | "bookStaleNotice"
  | "bookActionText"
> {
  const content = latestBookContent(output);
  return {
    bookOutput: output,
    bookContent: content,
    bookCharCount: trimText(content).length,
    bookPreviewTitle: bookOutputPreviewTitle(output),
    bookStaleNotice: bookOutputStaleNotice(output),
    bookActionText: bookActionText(output),
  };
}

export function bookSourceCoverageNotice(
  plan: MemoirBookPlan | null,
  coverage: MemoirBookSourceCoverage | null,
): string {
  const count = coverage?.uncovered_source_count || 0;
  if (!count) {
    return "";
  }
  if (!plan) {
    return `有 ${count} 条讲述记录可用于生成章节`;
  }
  return `有 ${count} 条新讲述尚未收录到书中`;
}

export function sourceCoverageData(
  plan: MemoirBookPlan | null,
  coverage: MemoirBookSourceCoverage | null,
): Pick<
  BookData,
  "sourceCoverage" | "sourceCoverageNotice" | "uncoveredSourceCount" | "uncoveredSourceDisplayText" | "uncoveredSources"
> {
  const uncoveredSources = buildUncoveredSourceViews(coverage);
  const uncoveredSourceCount = coverage?.uncovered_source_count || 0;
  return {
    sourceCoverage: coverage,
    sourceCoverageNotice: bookSourceCoverageNotice(plan, coverage),
    uncoveredSourceCount,
    uncoveredSourceDisplayText: uncoveredSourceCount > uncoveredSources.length
      ? `最近 ${uncoveredSources.length}/${uncoveredSourceCount} 条`
      : `${uncoveredSourceCount} 条待收录`,
    uncoveredSources,
  };
}

export function buildUncoveredSourceViews(
  coverage: MemoirBookSourceCoverage | null,
  limit = 12,
): BookUncoveredSourceView[] {
  return [...(coverage?.uncovered_sources || [])]
    .sort((left, right) => createdAtTime(right.created_at) - createdAtTime(left.created_at))
    .slice(0, limit)
    .map((source) => {
      const text = trimText(source.text);
      return {
        utterance_id: source.utterance_id,
        recordNumber: source.source_index,
        previewText: text.length > 96 ? `${text.slice(0, 96)}...` : text,
        created_at: source.created_at,
        createdAtText: formatBeijingDateTimeShort(source.created_at) || "讲述记录",
        sourceRef: {
          voice_session_id: source.voice_session_id,
          message_id: source.message_id,
          utterance_id: source.utterance_id,
          transcript_version_id: source.transcript_version_id,
          audio_file_id: source.audio_file_id,
          start_ms: source.start_ms,
          end_ms: source.end_ms,
          created_at: source.created_at,
          quote: text.slice(0, 180),
        },
      };
    });
}

export function buildChapterPickerOptions(chapters: BookChapterView[]): ChapterPickerOption[] {
  return chapters.map(chapterOption);
}

export function buildSourceActionPickerOptions(
  chapters: BookChapterView[],
  includeNewChapter = false,
): ChapterPickerOption[] {
  const options = buildChapterPickerOptions(chapters);
  if (includeNewChapter) {
    options.push({
      label: "作为新章",
      value: NEW_CHAPTER_PICKER_VALUE,
    });
  }
  return options;
}

function chapterOption(chapter: Pick<BookChapterView, "id" | "title" | "editingTitle">, index: number): ChapterPickerOption {
  const title = trimText(chapter.editingTitle || chapter.title) || "未命名章节";
  return {
    label: `第 ${index + 1} 章 ${title}`,
    value: chapter.id,
  };
}

function sourcePreviewText(ref: MemoirBookChapterSourceRef): string {
  const text = trimText(ref.quote || "");
  if (!text) {
    return "未摘录讲述内容";
  }
  return text.length > 72 ? `${text.slice(0, 72)}...` : text;
}

function sourceCreatedAtText(ref: MemoirBookChapterSourceRef): string {
  return ref.created_at ? formatBeijingDateTimeShort(ref.created_at) : "时间未知";
}

function sourceRefSortKey(ref: MemoirBookChapterSourceRef): string {
  return sourceRefKey(ref);
}

function planSourceRecordNumbers(chapters: Pick<MemoirBookPlanChapter, "source_refs">[]): Record<string, number> {
  const seen = new Set<string>();
  const refs: MemoirBookChapterSourceRef[] = [];
  chapters.forEach((chapter) => {
    chapter.source_refs.forEach((ref) => {
      const key = sourceRefKey(ref);
      if (seen.has(key)) {
        return;
      }
      seen.add(key);
      refs.push(ref);
    });
  });
  return refs
    .sort((left, right) => (
      createdAtTime(left.created_at || "") - createdAtTime(right.created_at || "")
      || sourceRefSortKey(left).localeCompare(sourceRefSortKey(right))
    ))
    .reduce<Record<string, number>>((memo, ref, index) => {
      memo[sourceRefKey(ref)] = index + 1;
      return memo;
    }, {});
}

function coverageSourceRecordNumbers(coverage: MemoirBookSourceCoverage | null): Record<string, number> {
  return [...(coverage?.sources || [])].reduce<Record<string, number>>((memo, source) => {
    memo[source.utterance_id] = source.source_index;
    return memo;
  }, {});
}

function chapterSourceView(
  ref: MemoirBookChapterSourceRef,
  index: number,
  recordNumbers: Record<string, number>,
  fallbackRecordNumbers: Record<string, number>,
): BookChapterSourceView {
  const previewText = sourcePreviewText(ref);
  const createdAtText = sourceCreatedAtText(ref);
  const rawCharCount = trimText(ref.quote || "").length;
  const charCount = rawCharCount || previewText.length;
  const key = sourceRefKey(ref);
  const recordNumber = recordNumbers[key] || fallbackRecordNumbers[key] || index + 1;
  return {
    ...ref,
    sourceIndex: index + 1,
    recordNumber,
    labelText: `语音记录 ${recordNumber} · 素材 ${index + 1} · ${createdAtText} · ${charCount} 字`,
    createdAtText,
    charCount,
    previewText,
  };
}

function decorateChapterStructureOptions(
  chapters: BookChapterView[],
  coverage: MemoirBookSourceCoverage | null = null,
): BookChapterView[] {
  const recordNumbers = coverageSourceRecordNumbers(coverage);
  const fallbackRecordNumbers = planSourceRecordNumbers(chapters);
  return chapters.map((chapter, index) => {
    return {
      ...chapter,
      chapterSummaryMetaText: chapterSummaryMetaText(chapter),
      sourceViews: chapter.source_refs.map((ref, sourceIndex) => (
        chapterSourceView(ref, sourceIndex, recordNumbers, fallbackRecordNumbers)
      )),
      sourceToggleText: chapter.sourceListExpanded ? "收起素材" : `查看 ${chapter.source_refs.length} 条`,
      canMoveUp: index > 0,
      canMoveDown: index < chapters.length - 1,
    };
  });
}

function chapterStateData(chapters: BookChapterView[]): Pick<
  BookData,
  "confirmedDraftCount" | "totalChapterCount" | "allChaptersConfirmed" | "canGenerateBook" | "progressText"
> {
  const confirmedDraftCount = chapters.filter((chapter) => chapter.draft?.status === "confirmed").length;
  const totalChapterCount = chapters.length;
  const allChaptersConfirmed = totalChapterCount > 0 && confirmedDraftCount === totalChapterCount;
  return {
    confirmedDraftCount,
    totalChapterCount,
    allChaptersConfirmed,
    canGenerateBook: allChaptersConfirmed,
    progressText: bookProgressText(chapters),
  };
}

export function chapterCollectionData(
  chapters: BookChapterView[],
  stage: BookStage,
  coverage: MemoirBookSourceCoverage | null = null,
): Pick<
  BookData,
  | "chapters"
  | "confirmedDraftCount"
  | "totalChapterCount"
  | "allChaptersConfirmed"
  | "canGenerateBook"
  | "progressText"
  | "chapterPickerOptions"
> {
  const decorated = decorateChapterStructureOptions(chapters, coverage);
  return {
    chapters: decorated,
    ...chapterStateData(decorated),
    chapterPickerOptions: stage === "drafting"
      ? buildSourceActionPickerOptions(decorated, true)
      : [],
  };
}

export function bookChapterSourceSummary(chapter: Pick<MemoirBookPlanChapter, "source_refs">): string {
  return `本章素材：${chapter.source_refs.length} 条原始讲述`;
}

function chapterSummaryMetaText(
  chapter: Pick<BookChapterView, "sourceCount" | "draftCharCount" | "draft" | "draftStatusText">,
): string {
  const sourceText = bookOralSourceCountText(chapter.sourceCount);
  if (!chapter.draft?.latest_version) {
    return `${sourceText} · 尚未生成正文`;
  }
  return `${sourceText} · ${chapter.draftCharCount} 字 · ${chapter.draftStatusText}`;
}

export function bookOutlineNarrationText(
  planTitle: string,
  chapters: Pick<BookChapterView, "title" | "sourceCount">[],
): string {
  const title = trimText(planTitle) || "回忆录章节规划";
  const lines = [`《${title}》现在有 ${chapters.length} 章。`];
  chapters.forEach((chapter, index) => {
    const chapterTitle = trimText(chapter.title) || "未命名章节";
    lines.push(`第 ${index + 1} 章，${chapterTitle}。本章收录 ${chapter.sourceCount} 条语音素材。`);
  });
  lines.push("你可以继续调整章节顺序，或者说出想修改的地方。");
  return lines.join("\n");
}

export function buildChapterViews(
  plan: MemoirBookPlan,
  draftsByChapterId: Record<string, MemoirBookChapterDraft | null> = {},
  coverage: MemoirBookSourceCoverage | null = null,
): BookChapterView[] {
  const chapters = [...plan.chapters]
    .sort((left, right) => left.sort_order - right.sort_order)
    .map((chapter) => {
      const draft = draftsByChapterId[chapter.id] || null;
      const content = latestDraftContent(draft);
      const assignedPendingCount = responseCount(chapter.pending_source_count);
      const assignedRemovedCount = responseCount(chapter.removed_source_count);
      const draftCharCount = trimText(content).length;
      const statusText = draftStatusText(draft);
      const viewBase = {
        sourceCount: chapter.source_refs.length,
        draftCharCount,
        draft,
        draftStatusText: statusText,
      };
      return {
        ...chapter,
        editingTitle: chapter.title,
        titleChanged: false,
        ttsTarget: `chapter:${chapter.id}`,
        sourceCount: chapter.source_refs.length,
        sourceSummary: bookChapterSourceSummary(chapter),
        chapterSummaryMetaText: chapterSummaryMetaText(viewBase),
        sourceViews: [],
        sourceListExpanded: false,
        sourceToggleText: "",
        canMoveUp: false,
        canMoveDown: false,
        pendingSourceCount: assignedPendingCount,
        removedSourceCount: assignedRemovedCount,
        sourceChangeNotice: sourceChangeNotice(assignedPendingCount, assignedRemovedCount),
        draft,
        draftContent: content,
        draftCharCount,
        draftChanged: false,
        draftStatusText: statusText,
        draftStatusHintText: draftStatusHintText(draft),
        draftVersionText: draftVersionText(draft),
        draftActionText: draftActionText(draft, assignedPendingCount, assignedRemovedCount),
        draftActionHintText: draftActionHintText(draft, assignedPendingCount, assignedRemovedCount),
        draftBusy: false,
        draftError: "",
      };
    });
  return decorateChapterStructureOptions(chapters, coverage);
}

export function bookProgressText(chapters: BookChapterView[]): string {
  if (!chapters.length) {
    return "还没有章节";
  }
  const confirmed = chapters.filter((chapter) => chapter.draft?.status === "confirmed").length;
  const generated = chapters.filter((chapter) => Boolean(chapter.draft?.latest_version)).length;
  return `${confirmed}/${chapters.length} 章已确认，${generated} 章已有正文`;
}

export function planStage(plan: MemoirBookPlan | null): BookStage {
  if (!plan) {
    return "empty";
  }
  return plan.status === "confirmed" ? "drafting" : "plan_draft";
}

export function stageText(stage: BookStage): string {
  if (stage === "empty") {
    return "等待生成章节";
  }
  if (stage === "plan_draft") {
    return "确认章节结构";
  }
  return "逐章成稿";
}

export function guestBookData(): Partial<BookHomeData> {
  return {
    needsLogin: true,
    projectId: "",
    projectTitle: "我的回忆录",
    projectNarratorName: "",
    plan: null,
    planTitle: "",
    stage: "empty",
    stageText: stageText("empty"),
    chapters: [],
    chapterStructureEditing: false,
    confirmedDraftCount: 0,
    totalChapterCount: 0,
    allChaptersConfirmed: false,
    canGenerateBook: false,
    progressText: "登录后可以开始整理",
    sourceCoverage: null,
    sourceCoverageNotice: "",
    uncoveredSourceCount: 0,
    uncoveredSourceDisplayText: "",
    uncoveredSources: [],
    chapterPickerOptions: [],
    assigningSourceId: "",
    titleBusyTarget: "",
    bookOutput: null,
    bookContent: "",
    bookCharCount: 0,
    bookPreviewTitle: "",
    bookStaleNotice: "",
    bookActionText: "生成全书",
    ttsBusyTarget: "",
    playingTarget: "",
    bookTTSActiveTarget: "",
    bookTTSActiveText: "",
    bookTTSSpokenText: "",
    bookTTSCurrentText: "",
    bookTTSRemainingText: "",
    bookTTSProgressPercent: 0,
    bookTTSDurationSeconds: 0,
    bookTTSAlignment: [],
    bookVoiceAssistantExpanded: false,
    bookVoiceAssistantHint: "生成章节后，可以听目录或说修改",
    bookVoiceAssistantStatusText: "等待章节",
    bookVoiceAssistantListening: false,
    bookVoiceAssistantBusy: false,
    bookVoiceAssistantConversationActive: false,
    bookVoiceAssistantReplyText: "",
    bookVoiceAssistantSpeakingLineId: "",
    bookVoiceLiveText: "",
    bookVoiceTranscriptLines: [],
    bookVoiceAudioLevelPercent: 0,
    bookVoiceCaptureStateText: "麦克风未开始",
    bookVoiceCommandText: "",
    bookVoiceCommandIntentLabel: "",
    bookVoiceCommandResponseKind: "",
    bookVoiceCommandPanelTitle: "",
    bookVoiceCommandStateText: "",
    bookVoiceCommandDetailText: "",
    bookVoiceCommandSummary: "",
    bookVoiceCommandRequiresConfirmation: false,
    bookVoiceCommandPreview: null,
    bookVoiceCommandCanExecute: false,
    bookVoiceCommandActionText: "确认执行",
    bookVoiceCommandExecuting: false,
    bookVoiceCommandUndoText: "",
    bookVoiceCommandUndoSnapshot: null,
    automationEventItems: [],
    automationEventBusyTarget: "",
    automationEventExpandedTargets: {},
    automationEventLoading: false,
    automationEventError: "",
    imageAssetLoading: false,
    imageAssetTotal: 0,
    imageAssetReadyCount: 0,
    imageAssetIncludedCount: 0,
    imageAssetUnusedCount: 0,
    imageAssetSummaryText: "还没有图片素材",
  };
}

export function toPlanInput(chapters: BookChapterView[]): MemoirBookPlanChapterInput[] {
  return chapters.map((chapter) => ({
    title: trimText(chapter.editingTitle),
    source_refs: chapter.source_refs,
  }));
}

export function toPlanUpdateInput(chapters: BookChapterView[]): MemoirBookPlanChapterUpdateInput[] {
  return chapters.map((chapter) => ({
    id: chapter.id,
    title: trimText(chapter.editingTitle || chapter.title),
    source_refs: chapter.source_refs,
  }));
}
