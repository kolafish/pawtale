import type {
  MemoirBookChapterDraft,
  MemoirBookChapterSourceRef,
  MemoirBookManuscript,
  MemoirBookManuscriptSection,
  MemoirBookManuscriptTemplate,
  MemoirBookCoverStyle,
  MemoirBookOutput,
  MemoirBookOutputVersion,
  MemoirBookOutputVersionSummary,
  MemoirBookPlan,
  MemoirBookPlanChapter,
  MemoirBookPlanChapterInput,
} from "../../domain/memoir-book";
import type { TTSAlignmentSegment } from "../../domain/memoir-audio";
import type { MemoirDraftProgress } from "../../domain/memoir-snapshots";
import {
  sourceRefKey,
} from "../../services/memoir-book-sources";
import { formatBeijingDateTimeShort } from "../../services/time";
import type { BookAutomationEventView } from "./book-automation-view-model";
import { createdAtTime, trimText } from "./book-view-model-utils";

export type {
  BookAutomationEventDetailView,
  BookAutomationEventSuggestionView,
  BookAutomationEventView,
} from "./book-automation-view-model";
export {
  buildAutomationEventViews,
} from "./book-automation-view-model";
export { trimText } from "./book-view-model-utils";

export type BookStage = "empty" | "plan_draft" | "drafting";

export interface ChapterPickerOption {
  label: string;
  value: string;
}

export interface BookChapterSourceView extends Pick<MemoirBookChapterSourceRef, "utterance_id"> {
  sourceIndex: number;
  recordNumber: number;
  labelText: string;
  createdAtText: string;
  charCount: number;
  previewText: string;
  splitSelected: boolean;
}

export interface BookChapterView extends Pick<
  MemoirBookPlanChapter,
  "id" | "title" | "sort_order" | "include_in_book"
> {
  editingTitle: string;
  titleChanged: boolean;
  ttsTarget: string;
  sourceCount: number;
  sectionCount: number;
  sourceSummary: string;
  chapterSummaryMetaText: string;
  sourceViews: BookChapterSourceView[];
  sourceListExpanded: boolean;
  sourceToggleText: string;
  mergeTargetOptions: ChapterPickerOption[];
  splitSelectedCount: number;
  splitActionText: string;
  canSplitSelectedSources: boolean;
  canMoveUp: boolean;
  canMoveDown: boolean;
  pendingSourceCount: number;
  pendingCompletionSourceCount: number;
  pendingNewSourceCount: number;
  removedSourceCount: number;
  draftAvailable: boolean;
  draftCharCount: number;
  draftChanged: boolean;
  draftVersionText: string;
  draftActionText: string;
  chapterGuidanceText: string;
  bookInclusionNotice: string;
  chapterNeedsAction: boolean;
  draftBusy: boolean;
  draftError: string;
}

export type BookChapterSources = Record<string, MemoirBookChapterSourceRef[]>;

export interface BookOutputTocView {
  id: string;
  chapterId: string;
  index: number;
  indexText: string;
  title: string;
  metaText: string;
  sections: BookOutputSectionView[];
}

export interface BookOutputSectionView {
  id: string;
  index: number;
  indexText: string;
  title: string;
  content: string;
  paragraphs: string[];
  excerpt: string;
  metaText: string;
}

export interface BookOutputChapterPreviewView {
  id: string;
  chapterId: string;
  index: number;
  indexText: string;
  title: string;
  content: string;
  excerpt: string;
  metaText: string;
  sections: BookOutputSectionView[];
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

interface BookCoverStyleOptionView {
  value: MemoirBookCoverStyle;
  previewClass: string;
  previewImage?: string;
}

const BOOK_COVER_STYLE_OPTIONS: BookCoverStyleOptionView[] = [
  {
    value: "classic_plain",
    previewClass: "classic-plain",
  },
  {
    value: "warm_gray",
    previewClass: "warm-gray",
  },
  {
    value: "dark_album",
    previewClass: "dark-album",
  },
  {
    value: "moonlit_courtyard",
    previewClass: "moonlit-courtyard",
  },
  {
    value: "navy_gilt",
    previewClass: "navy-gilt",
  },
  {
    value: "heritage_classic",
    previewClass: "heritage-classic",
    previewImage: "/assets/book_covers/heritage_classic.jpg",
  },
  {
    value: "warm_journal",
    previewClass: "warm-journal",
    previewImage: "/assets/book_covers/warm_journal.jpg",
  },
  {
    value: "modern_minimal",
    previewClass: "modern-minimal",
    previewImage: "/assets/book_covers/modern_minimal.jpg",
  },
];

export interface BookHomeData {
  loading: boolean;
  needsLogin: boolean;
  generatingPlan: boolean;
  confirmingPlan: boolean;
  updatingPlanStructure: boolean;
  error: string;
  projectId: string;
  projectTitle: string;
  planTitle: string;
  stage: BookStage;
  stageText: string;
  chapters: BookChapterView[];
  chapterStructureEditing: boolean;
  splitSourceSelections: Record<string, string[]>;
  readyDraftCount: number;
  totalChapterCount: number;
  progressText: string;
  titleBusyTarget: string;
  bookCharCount: number;
  bookIncludedChapterCount: number;
  bookPreviewTitle: string;
  bookStaleNotice: string;
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
  automationEventItems: BookAutomationEventView[];
  automationEventBusyTarget: string;
  automationDismissibleCount: number;
  automationEventClearing: boolean;
  automationEventExpandedTargets: Record<string, boolean>;
  automationEventLoading: boolean;
  automationEventError: string;
  imageAssetLoading: boolean;
  imageAssetTotal: number;
  imageAssetSummaryText: string;
  bookContentAvailable: boolean;
}

export function positiveFiniteSeconds(value: number | null | undefined): number {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

export function latestDraftContent(draft: MemoirBookChapterDraft | null): string {
  return draft?.latest_version?.content || "";
}

export function chapterGuidanceText(
  draft: MemoirBookChapterDraft | null,
  changeNotice: string,
  pendingCount = 0,
  removedCount = 0,
): string {
  if (!draft?.latest_version) {
    return "正文还没准备好，进入章节页查看整理进度。";
  }
  if (pendingCount || removedCount) {
    return `${changeNotice}。相关内容待更新，当前正文仍可阅读。`;
  }
  return "正文已更新，可以阅读或修改。";
}

export function sourceChangeNotice(
  pendingCount: number,
  removedCount: number,
  completionCount = 0,
  newCount = 0,
): string {
  const ordinaryCount = newCount || (
    completionCount ? Math.max(0, pendingCount - completionCount) : pendingCount
  );
  const changes: string[] = [];
  if (removedCount) {
    changes.push(`${removedCount} 条讲述已删除`);
  }
  if (completionCount) {
    changes.push(`${completionCount} 条补充回答尚未写入正文`);
  }
  if (ordinaryCount) {
    changes.push(`${ordinaryCount} 条新增讲述尚未写入正文`);
  }
  return changes.join("，");
}

export function draftActionText(
  draft: MemoirBookChapterDraft | null,
  pendingCount: number,
  removedCount = 0,
): string {
  if (!draft?.latest_version) {
    return "查看整理进度";
  }
  if (pendingCount || removedCount) {
    return "查看更新状态";
  }
  return "阅读或修改";
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

export function bookOutputVersions(output: MemoirBookOutput | null): MemoirBookOutputVersionSummary[] {
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
  selectedVersion: MemoirBookOutputVersion | null = null,
): MemoirBookOutputVersion | null {
  return selectedVersion || output?.latest_version || null;
}

export function selectedBookContent(
  output: MemoirBookOutput | null,
  selectedVersion: MemoirBookOutputVersion | null = null,
): string {
  return selectedBookOutputVersion(output, selectedVersion)?.content || "";
}

function bookOutputLatestVersionId(output: MemoirBookOutput | null): string {
  return output?.latest_version?.id || bookOutputVersions(output)[0]?.id || "";
}

function isHistoricalBookVersion(
  output: MemoirBookOutput | null,
  selectedVersion: MemoirBookOutputVersion | null,
): boolean {
  const selectedId = selectedBookOutputVersion(output, selectedVersion)?.id;
  const latestId = bookOutputLatestVersionId(output);
  return Boolean(selectedId && latestId && selectedId !== latestId);
}

export function bookVersionOptions(
  output: MemoirBookOutput | null,
  selectedVersion: MemoirBookOutputVersion | null = null,
): {
  options: BookOutputVersionOptionView[];
  selectedId: string;
  selectedIndex: number;
  viewingHistorical: boolean;
} {
  const versions = bookOutputVersions(output);
  const latestId = bookOutputLatestVersionId(output);
  const selected = selectedBookOutputVersion(output, selectedVersion);
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
    viewingHistorical: isHistoricalBookVersion(output, selectedVersion),
  };
}

function bookSourceCountText(count: number): string {
  return count > 0 ? `${count} 条原始讲述` : "暂无原始讲述计数";
}

function bookOralSourceCountText(count: number): string {
  return count > 0 ? `${count} 条口述` : "暂无口述";
}

export function readingParagraphs(value: string): string[] {
  return trimText(value)
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map(trimText)
    .filter(Boolean);
}

export function bookOutputPreviewTitle(
  output: MemoirBookOutput | null,
  selectedVersion: MemoirBookOutputVersion | null = null,
): string {
  const version = selectedBookOutputVersion(output, selectedVersion);
  return trimText(version?.manuscript?.profile.title || output?.title || "");
}

export function bookOutputTocItems(
  output: MemoirBookOutput | null,
  selectedVersion: MemoirBookOutputVersion | null = null,
): BookOutputTocView[] {
  const toc = selectedBookOutputVersion(output, selectedVersion)?.manuscript?.table_of_contents || [];
  return toc
    .filter((item) => item.chapter_id && item.title)
    .map((item, index) => {
      const chapterIndex = responseCount(item.index) || index + 1;
      const charCount = responseCount(item.char_count);
      const sourceCount = responseCount(item.source_count);
      const sections = (item.sections || [])
        .filter((section) => section.section_id && trimText(section.title))
        .map((section, sectionOffset) => {
          const sectionIndex = responseCount(section.index) || sectionOffset + 1;
          const sectionCharCount = responseCount(section.char_count);
          const sectionSourceCount = responseCount(section.source_count);
          return {
            id: section.section_id,
            index: sectionIndex,
            indexText: `${chapterIndex}.${sectionIndex}`,
            title: trimText(section.title),
            content: "",
            paragraphs: [],
            excerpt: "",
            metaText: [
              `${sectionCharCount} 字`,
              bookOralSourceCountText(sectionSourceCount),
            ].join(" · "),
          };
        });
      return {
        id: `${item.chapter_id}:${chapterIndex}`,
        chapterId: item.chapter_id,
        index: chapterIndex,
        indexText: `${chapterIndex}`,
        title: trimText(item.title),
        metaText: [`${charCount} 字`, bookSourceCountText(sourceCount)].join(" · "),
        sections,
      };
    });
}

export function bookOutputChapterPreviews(
  output: MemoirBookOutput | null,
  selectedVersion: MemoirBookOutputVersion | null = null,
): BookOutputChapterPreviewView[] {
  const chapters = selectedBookOutputVersion(output, selectedVersion)?.manuscript?.chapters || [];
  return chapters
    .filter((chapter) => chapter.chapter_id && chapter.title && trimText(chapter.content))
    .map((chapter, index) => {
      const chapterIndex = responseCount(chapter.index) || index + 1;
      const charCount = trimText(chapter.content).length;
      const sourceCount = chapter.source_refs?.length || 0;
      const sections = (chapter.sections || [])
        .filter((section) => section.section_id && trimText(section.title) && trimText(section.content))
        .map((section, sectionOffset) => {
          const sectionIndex = responseCount(section.index) || sectionOffset + 1;
          const sectionContent = trimText(section.content);
          const sectionSourceCount = responseCount(section.source_count);
          return {
            id: section.section_id,
            index: sectionIndex,
            indexText: `${chapterIndex}.${sectionIndex}`,
            title: trimText(section.title),
            content: sectionContent,
            paragraphs: readingParagraphs(sectionContent),
            excerpt: previewText(sectionContent, 72),
            metaText: [
              `${responseCount(section.char_count) || sectionContent.length} 字`,
              bookOralSourceCountText(sectionSourceCount),
            ].join(" · "),
          };
        });
      return {
        id: `${chapter.chapter_id}:${chapterIndex}`,
        chapterId: chapter.chapter_id,
        index: chapterIndex,
        indexText: `第 ${chapterIndex} 章`,
        title: trimText(chapter.title),
        content: trimText(chapter.content),
        excerpt: previewText(chapter.content, 96),
        metaText: [`${charCount} 字`, bookSourceCountText(sourceCount)].join(" · "),
        sections,
      };
    });
}

function selectedBookManuscript(
  output: MemoirBookOutput | null,
  selectedVersion: MemoirBookOutputVersion | null = null,
): MemoirBookManuscript | null {
  return selectedBookOutputVersion(output, selectedVersion)?.manuscript || null;
}

function manuscriptFrontSectionContent(manuscript: MemoirBookManuscript | null, kind: string): string {
  return trimText((manuscript?.front_matter || []).find((section) => section.kind === kind)?.content || "");
}

function manuscriptBackSectionContent(manuscript: MemoirBookManuscript | null, kind: string): string {
  const section = manuscript?.back_matter?.[kind as keyof MemoirBookManuscript["back_matter"]];
  return section && typeof section === "object" ? trimText(section.content || "") : "";
}

function manuscriptTemplateLabel(template: MemoirBookManuscriptTemplate | undefined): string {
  if (template === "custom") {
    return "自定义版式";
  }
  if (template === "photo_memory") {
    return "家庭纪念版";
  }
  if (template === "oral_archive") {
    return "口述档案版";
  }
  if (template === "heritage_classic") {
    return "典藏文集版";
  }
  if (template === "warm_journal") {
    return "家书手记版";
  }
  if (template === "modern_minimal") {
    return "现代简约版";
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
    return "按已整理章节生成基础书稿";
  }
  const settings = manuscript.render_settings;
  const pageSize = settings.page_size === "a5" ? "A5" : "默认纸张";
  const layout = settings.layout === "print" ? "打印排版" : "阅读排版";
  const header = settings.show_headers ? "含页眉" : "无页眉";
  const appendix = settings.show_source_appendix ? "附上整理记录" : "隐藏整理记录";
  return [pageSize, layout, header, appendix].join(" · ");
}

function manuscriptStructureItems(
  manuscript: MemoirBookManuscript | null,
): BookManuscriptStructureItemView[] {
  if (!manuscript) {
    return [];
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
): BookManuscriptChapterView[] {
  if (!manuscript) {
    return [];
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
      metaText: [
        hintText,
        chapter.sections?.length
          ? `${chapter.sections.length} 个小节`
          : "",
        `${trimText(chapter.content).length} 字`,
        bookSourceCountText(sourceCount),
      ]
        .filter(Boolean)
        .join(" · "),
      contentPreview: previewText(chapter.content, 96),
    };
  });
}

function bookManuscriptDesignData(manuscript: MemoirBookManuscript | null) {
  const structureItems = manuscriptStructureItems(manuscript);
  const chapterItems = manuscriptChapterViews(manuscript);
  return {
    bookManuscriptAvailable: Boolean(manuscript),
    bookManuscriptTemplateLabel: manuscript ? manuscriptTemplateLabel(manuscript.profile.template) : "基础成书版",
    bookManuscriptSettingsText: manuscriptSettingsText(manuscript),
    bookManuscriptStructureItems: structureItems,
    bookManuscriptChapterItems: chapterItems,
    bookManuscriptBackMatterItems: manuscript
      ? backMatterSections(manuscript).map((section, index) => sectionView(section, index, "back"))
      : [],
  };
}

function selectedBookCoverStyle(
  output: MemoirBookOutput | null,
  selectedVersion: MemoirBookOutputVersion | null = null,
): MemoirBookCoverStyle {
  const manuscript = selectedBookManuscript(output, selectedVersion);
  const style = manuscript?.render_settings?.cover_style;
  const option = BOOK_COVER_STYLE_OPTIONS.find((candidate) => candidate.value === style);
  return option ? option.value : "classic_plain";
}

function bookCoverStyleData(
  output: MemoirBookOutput | null,
  selectedVersion: MemoirBookOutputVersion | null = null,
) {
  const style = selectedBookCoverStyle(output, selectedVersion);
  const option = BOOK_COVER_STYLE_OPTIONS.find((candidate) => candidate.value === style)
    || BOOK_COVER_STYLE_OPTIONS[0];
  return {
    bookCoverStyle: option.value,
    bookCoverStylePreviewClass: option.previewClass,
    bookCoverStylePreviewImage: option.previewImage || "",
  };
}

function bookMetadataInputData(
  output: MemoirBookOutput | null,
  selectedVersion: MemoirBookOutputVersion | null = null,
) {
  const manuscript = selectedBookManuscript(output, selectedVersion);
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

function selectedBookOutputVersionText(
  output: MemoirBookOutput | null,
  selectedVersion: MemoirBookOutputVersion | null = null,
): string {
  const selected = selectedBookOutputVersion(output, selectedVersion);
  return selected ? `已修改 ${selected.version} 次` : "尚未生成全书";
}

export function bookOutputStaleNotice(
  output: MemoirBookOutput | null,
  progress: MemoirDraftProgress | null = null,
): string {
  if (!output?.is_stale) {
    return "";
  }
  const titles = (output.stale_chapter_titles || []).filter(Boolean).slice(0, 2);
  const suffix = (output.stale_chapter_titles || []).length > 2 ? "等章节" : "";
  const subject = titles.length ? `「${titles.join("、")}」${suffix}` : "章节内容";
  if (progress?.status === "needs_input") {
    return `${subject}已更新。请点「查看待确认事项」处理，全书会在处理后自动更新。`;
  }
  if (progress?.status === "failed") {
    return `${subject}已更新，后台整理遇到问题。请点「查看待确认事项」查看原因并重试。`;
  }
  if (progress?.status === "processing") {
    return `${subject}已更新，后台正在整理新版全书。`;
  }
  return `${subject}已更新，全书会在后台自动更新；如迟迟未完成，请点「查看待确认事项」。`;
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

export interface BookReadingData {
  bookContent: string;
  bookCharCount: number;
  bookPreviewTitle: string;
  bookPreviewTocItems: BookOutputTocView[];
  bookPreviewChapters: BookOutputChapterPreviewView[];
  bookManuscriptTitle: string;
  bookManuscriptSubtitle: string;
  bookVersionText: string;
  bookViewingHistoricalVersion: boolean;
  bookVersionNotice: string;
  bookStaleNotice: string;
}

export function bookReadingData(
  output: MemoirBookOutput | null,
  selectedVersion: MemoirBookOutputVersion | null = null,
): BookReadingData {
  const content = selectedBookContent(output, selectedVersion);
  const manuscript = selectedBookManuscript(output, selectedVersion);
  const title = bookOutputPreviewTitle(output, selectedVersion);
  const viewingHistorical = isHistoricalBookVersion(output, selectedVersion);
  return {
    bookContent: content,
    bookCharCount: trimText(content).length,
    bookPreviewTitle: title,
    bookPreviewTocItems: bookOutputTocItems(output, selectedVersion),
    bookPreviewChapters: bookOutputChapterPreviews(output, selectedVersion),
    bookManuscriptTitle: trimText(manuscript?.profile.title || title),
    bookManuscriptSubtitle: trimText(manuscript?.profile.subtitle || "根据已有章节编排"),
    bookVersionText: selectedBookOutputVersionText(output, selectedVersion),
    bookViewingHistoricalVersion: viewingHistorical,
    bookVersionNotice: bookVersionNotice(output, viewingHistorical),
    bookStaleNotice: bookOutputStaleNotice(output),
  };
}

export function bookOutputData(
  output: MemoirBookOutput | null,
  selectedVersion: MemoirBookOutputVersion | null = null,
) {
  const versionState = bookVersionOptions(output, selectedVersion);
  const readingData = bookReadingData(output, selectedVersion);
  return {
    ...readingData,
    ...bookManuscriptDesignData(selectedBookManuscript(output, selectedVersion)),
    bookVersionOptions: versionState.options,
    bookSelectedVersionId: versionState.selectedId,
    bookSelectedVersionIndex: versionState.selectedIndex,
    bookTitleInput: readingData.bookManuscriptTitle || readingData.bookPreviewTitle,
    ...bookMetadataInputData(output, selectedVersion),
    bookTitleChanged: false,
    bookDesignSaveStatusText: output?.latest_version
      ? (versionState.viewingHistorical ? "切回最新版后自动保存" : "已保存")
      : "生成时应用",
    bookTitleSuggestions: [] as string[],
    ...bookCoverStyleData(output, selectedVersion),
  };
}

export type BookOutputData = ReturnType<typeof bookOutputData>;

export function bookHomeOutputData(
  output: MemoirBookOutput | null,
  progress: MemoirDraftProgress | null = null,
): Pick<
  BookHomeData,
  | "bookContentAvailable"
  | "bookCharCount"
  | "bookIncludedChapterCount"
  | "bookPreviewTitle"
  | "bookStaleNotice"
> {
  const content = latestBookContent(output);
  const emptyBookContent = `${trimText(output?.title || "")}\n\n目录`;
  const available = Boolean(content && trimText(content) !== emptyBookContent);
  const includedIds = new Set(available ? bookOutputTocItems(output).map((item) => item.chapterId) : []);
  const includedChapterCount = includedIds.size;
  return {
    bookContentAvailable: available,
    bookCharCount: available ? trimText(content).length : 0,
    bookIncludedChapterCount: includedChapterCount,
    bookPreviewTitle: bookOutputPreviewTitle(output),
    bookStaleNotice: bookOutputStaleNotice(output, progress),
  };
}

function bookOmissionFollowUp(
  progress: MemoirDraftProgress | null,
  omittedChapters: BookChapterView[],
): string {
  if (progress?.status === "needs_input") {
    return "请查看待确认事项，处理后书稿会继续更新。";
  }
  if (progress?.status === "failed") {
    return "后台整理遇到问题，请查看待确认事项并重试。";
  }
  if (progress?.status === "processing") {
    return "后台正在整理，完成后请再查看。";
  }
  if (omittedChapters.some((chapter) => !chapter.draftAvailable)) {
    return "请进入对应章节查看正文整理进度。";
  }
  return "如需将其收入书稿，请查看待确认事项。";
}

export function bookChapterInclusionData(
  chapters: BookChapterView[],
  output: MemoirBookOutput | null,
  progress: MemoirDraftProgress | null = null,
): BookChapterView[] {
  const content = latestBookContent(output);
  const available = Boolean(content && trimText(content) !== `${trimText(output?.title || "")}\n\n目录`);
  const includedIds = new Set(available ? bookOutputTocItems(output).map((item) => item.chapterId) : []);
  return chapters.map((chapter) => ({
    ...chapter,
    bookInclusionNotice: available && chapter.include_in_book !== false
      && chapter.draftAvailable && !includedIds.has(chapter.id)
      ? `${progress?.status === "needs_input" ? "本章待确认，暂未收入当前书稿。" : "本章暂未收入当前书稿。"}${bookOmissionFollowUp(progress, [chapter])}${chapter.chapterNeedsAction ? ` ${chapter.chapterGuidanceText}` : ""}`
      : "",
  }));
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

function chapterSourceView(
  ref: MemoirBookChapterSourceRef,
  index: number,
  recordNumbers: Record<string, number>,
  selectedSourceIds: Set<string>,
): BookChapterSourceView {
  const previewText = sourcePreviewText(ref);
  const createdAtText = sourceCreatedAtText(ref);
  const rawCharCount = trimText(ref.quote || "").length;
  const charCount = rawCharCount || previewText.length;
  const key = sourceRefKey(ref);
  const recordNumber = recordNumbers[key] || index + 1;
  return {
    utterance_id: ref.utterance_id,
    sourceIndex: index + 1,
    recordNumber,
    labelText: `语音记录 ${recordNumber} · 素材 ${index + 1} · ${createdAtText} · ${charCount} 字`,
    createdAtText,
    charCount,
    previewText,
    splitSelected: selectedSourceIds.has(ref.utterance_id),
  };
}

function decorateChapterStructureOptions(
  chapters: BookChapterView[],
  chapterSources: BookChapterSources,
  splitSelections: Record<string, string[]> = {},
): BookChapterView[] {
  const options = chapters.map(chapterOption);
  const recordNumbers = planSourceRecordNumbers(
    chapters.map((chapter) => ({ source_refs: chapterSources[chapter.id] || [] })),
  );
  return chapters.map((chapter, index) => {
    const sourceRefs = chapterSources[chapter.id] || [];
    const targetOptions = options.filter((option) => option.value !== chapter.id);
    const selectedSourceIds = new Set(splitSelections[chapter.id] || []);
    const selectedCount = sourceRefs.filter((ref) => selectedSourceIds.has(ref.utterance_id)).length;
    return {
      ...chapter,
      chapterSummaryMetaText: chapterSummaryMetaText(chapter),
      sourceViews: chapter.sourceListExpanded
        ? sourceRefs.map((ref, sourceIndex) => (
          chapterSourceView(ref, sourceIndex, recordNumbers, selectedSourceIds)
        ))
        : [],
      sourceToggleText: chapter.sourceListExpanded ? "收起素材" : `查看 ${sourceRefs.length} 条`,
      mergeTargetOptions: targetOptions,
      splitSelectedCount: selectedCount,
      splitActionText: selectedCount ? `拆出所选 ${selectedCount} 条` : "拆出所选",
      canSplitSelectedSources: selectedCount > 0 && selectedCount < sourceRefs.length,
      canMoveUp: index > 0,
      canMoveDown: index < chapters.length - 1,
    };
  });
}

function chapterStateData(chapters: BookChapterView[]): Pick<
  BookHomeData,
  "readyDraftCount" | "totalChapterCount" | "progressText"
> {
  const includedChapters = chapters.filter((chapter) => chapter.include_in_book !== false);
  const readyDraftCount = includedChapters.filter(
    (chapter) => (
      chapter.draftAvailable
      && !chapter.pendingSourceCount
      && !chapter.removedSourceCount
    ),
  ).length;
  const totalChapterCount = includedChapters.length;
  return {
    readyDraftCount,
    totalChapterCount,
    progressText: bookProgressText(chapters),
  };
}

export function chapterCollectionData(
  chapters: BookChapterView[],
  chapterSources: BookChapterSources,
  splitSelections: Record<string, string[]> = {},
): Pick<
  BookHomeData,
  | "chapters"
  | "readyDraftCount"
  | "totalChapterCount"
  | "progressText"
> {
  const decorated = decorateChapterStructureOptions(chapters, chapterSources, splitSelections);
  return {
    chapters: decorated,
    ...chapterStateData(decorated),
  };
}

export function bookChapterSourceSummary(chapter: Pick<MemoirBookPlanChapter, "source_refs">): string {
  return `本章素材：${chapter.source_refs.length} 条原始讲述`;
}

function chapterSummaryMetaText(
  chapter: Pick<
    BookChapterView,
    | "include_in_book"
    | "sectionCount"
    | "sourceCount"
    | "draftCharCount"
    | "draftAvailable"
  >,
): string {
  const sourceText = bookOralSourceCountText(chapter.sourceCount);
  const inclusionText = chapter.include_in_book === false ? "不收入本书 · " : "";
  const sectionText = chapter.sectionCount ? `${chapter.sectionCount} 个小节 · ` : "";
  if (!chapter.draftAvailable) {
    return `${inclusionText}${sectionText}${sourceText}`;
  }
  return `${inclusionText}${sectionText}${sourceText} · ${chapter.draftCharCount} 字`;
}

export function bookOutlineNarrationText(
  planTitle: string,
  chapters: Pick<BookChapterView, "include_in_book" | "title" | "sourceCount">[],
): string {
  const title = trimText(planTitle) || "回忆录章节规划";
  const lines = [`《${title}》现在有 ${chapters.length} 章。`];
  chapters.forEach((chapter, index) => {
    const chapterTitle = trimText(chapter.title) || "未命名章节";
    lines.push(`第 ${index + 1} 章，${chapterTitle}。本章收录 ${chapter.sourceCount} 条语音素材。`);
    if (chapter.include_in_book === false) {
      lines.push("本章目前保留整理，但不收入最终成书。");
    }
  });
  lines.push("你可以继续调整章节顺序、合并章节，或者说出想修改的地方。");
  return lines.join("\n");
}

export function buildChapterViews(
  plan: MemoirBookPlan,
  draftsByChapterId: Record<string, MemoirBookChapterDraft | null> = {},
  splitSelections: Record<string, string[]> = {},
): BookChapterView[] {
  const sortedChapters = [...plan.chapters]
    .sort((left, right) => left.sort_order - right.sort_order)
  const chapterSources = Object.fromEntries(
    sortedChapters.map((chapter) => [chapter.id, chapter.source_refs]),
  );
  const chapters = sortedChapters.map((chapter) => {
      const draft = draftsByChapterId[chapter.id] || null;
      const content = latestDraftContent(draft);
      const assignedPendingCount = responseCount(chapter.pending_source_count);
      const assignedCompletionCount = responseCount(
        chapter.pending_completion_source_count,
      );
      const assignedNewCount = responseCount(chapter.pending_new_source_count);
      const assignedRemovedCount = responseCount(chapter.removed_source_count);
      const sectionCount = (chapter.sections || []).filter(
        (section) => section.status === "active" && section.include_in_book !== false,
      ).length;
      const draftCharCount = trimText(content).length;
      const changeNotice = sourceChangeNotice(
        assignedPendingCount,
        assignedRemovedCount,
        assignedCompletionCount,
        assignedNewCount,
      );
      const viewBase = {
        include_in_book: chapter.include_in_book !== false,
        sectionCount,
        sourceCount: chapter.source_refs.length,
        draftCharCount,
        draftAvailable: Boolean(draft?.latest_version?.content?.trim()),
      };
      return {
        id: chapter.id,
        title: chapter.title,
        sort_order: chapter.sort_order,
        include_in_book: chapter.include_in_book !== false,
        editingTitle: chapter.title,
        titleChanged: false,
        ttsTarget: `chapter:${chapter.id}`,
        sourceCount: chapter.source_refs.length,
        sectionCount,
        sourceSummary: bookChapterSourceSummary(chapter),
        chapterSummaryMetaText: chapterSummaryMetaText(viewBase),
        sourceViews: [],
        sourceListExpanded: false,
        sourceToggleText: "",
        mergeTargetOptions: [],
        splitSelectedCount: 0,
        splitActionText: "拆出所选",
        canSplitSelectedSources: false,
        canMoveUp: false,
        canMoveDown: false,
        pendingSourceCount: assignedPendingCount,
        pendingCompletionSourceCount: assignedCompletionCount,
        pendingNewSourceCount: assignedNewCount,
        removedSourceCount: assignedRemovedCount,
        draftAvailable: Boolean(draft?.latest_version?.content?.trim()),
        draftCharCount,
        draftChanged: false,
        chapterGuidanceText: chapterGuidanceText(
          draft,
          changeNotice,
          assignedPendingCount,
          assignedRemovedCount,
        ),
        chapterNeedsAction: Boolean(
          assignedPendingCount || assignedRemovedCount || !draft?.latest_version?.content?.trim(),
        ),
        draftVersionText: draftVersionText(draft),
        draftActionText: draftActionText(
          draft,
          assignedPendingCount,
          assignedRemovedCount,
        ),
        bookInclusionNotice: "",
        draftBusy: false,
        draftError: "",
      };
    });
  return decorateChapterStructureOptions(chapters, chapterSources, splitSelections);
}

export function bookProgressText(chapters: BookChapterView[]): string {
  const includedChapters = chapters.filter((chapter) => chapter.include_in_book !== false);
  if (!includedChapters.length) {
    return "还没有章节";
  }
  const ready = includedChapters.filter(
    (chapter) => (
      chapter.draftAvailable
      && !chapter.pendingSourceCount
      && !chapter.removedSourceCount
    ),
  ).length;
  const generated = includedChapters.filter((chapter) => chapter.draftAvailable).length;
  return `${ready}/${includedChapters.length} 章正文已更新，${generated} 章已有正文`;
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
  return "书稿管理";
}

export function guestBookData(): Partial<BookHomeData> {
  return {
    needsLogin: true,
    projectId: "",
    projectTitle: "我的回忆录",
    planTitle: "",
    stage: "empty",
    stageText: stageText("empty"),
    chapters: [],
    chapterStructureEditing: false,
    readyDraftCount: 0,
    totalChapterCount: 0,
    progressText: "登录后可以开始整理",
    splitSourceSelections: {},
    titleBusyTarget: "",
    bookContentAvailable: false,
    bookCharCount: 0,
    bookIncludedChapterCount: 0,
    bookPreviewTitle: "",
    bookStaleNotice: "",
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
    automationEventItems: [],
    automationEventBusyTarget: "",
    automationDismissibleCount: 0,
    automationEventClearing: false,
    automationEventExpandedTargets: {},
    automationEventLoading: false,
    automationEventError: "",
    imageAssetLoading: false,
    imageAssetTotal: 0,
    imageAssetSummaryText: "还没有图片素材",
  };
}

export function toPlanInput(
  chapters: BookChapterView[],
  chapterSources: BookChapterSources,
): MemoirBookPlanChapterInput[] {
  return chapters.map((chapter) => ({
    title: trimText(chapter.editingTitle),
    include_in_book: chapter.include_in_book !== false,
    source_refs: chapterSources[chapter.id] || [],
  }));
}
