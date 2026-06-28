import type {
  MemoirBookChapterDraft,
  MemoirBookChapterSourceRef,
  MemoirBookManuscript,
  MemoirBookManuscriptSection,
  MemoirBookManuscriptTemplate,
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
  MemoirAutomationEvent,
  TTSAlignmentSegment,
  VoiceChatTranscriptLine,
} from "../../domain/voice-chat";
import {
  sourceRefKey,
  uniqueSourceRefs,
} from "../../services/memoir-book-sources";
import { formatBeijingDateTimeShort } from "../../services/time";

export {
  uniqueSourceRefs,
} from "../../services/memoir-book-sources";

export type BookStage = "empty" | "plan_draft" | "drafting";

const TIMEZONE_OFFSET_RE = /(?:[zZ]|[+-]\d{2}:?\d{2})$/;
export const NEW_CHAPTER_PICKER_VALUE = "__new_chapter__";
export const BOOK_EXPORT_LAYOUT_OPTIONS: BookExportLayoutOptionView[] = [
  {
    label: "标准阅读",
    value: "standard",
    detailText: "适合手机预览和普通分享",
  },
  {
    label: "大字打印",
    value: "large_print",
    detailText: "字号更大，页边距更宽",
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
  splitSelected: boolean;
}

export interface BookAutomationEventView {
  id: string;
  eventIds: string[];
  eventIdsText: string;
  eventType: MemoirAutomationEvent["event_type"];
  actionKind: "acknowledge" | "acknowledge_group" | "review_source" | "none";
  primaryUtteranceId: string;
  titleText: string;
  summaryText: string;
  detailText: string;
  detailItems: BookAutomationEventDetailView[];
  detailExpanded: boolean;
  detailToggleText: string;
  hasDetails: boolean;
  metaText: string;
  statusText: string;
  actionText: string;
  canAcknowledge: boolean;
  canAct: boolean;
  busy: boolean;
}

export interface BookAutomationEventDetailView {
  id: string;
  titleText: string;
  chapterText: string;
  metaText: string;
  previewText: string;
}

export interface BookChapterView extends MemoirBookPlanChapter {
  editingTitle: string;
  titleChanged: boolean;
  ttsTarget: string;
  sourceCount: number;
  sourceSummary: string;
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
  removedSourceCount: number;
  sourceChangeNotice: string;
  draft: MemoirBookChapterDraft | null;
  draftContent: string;
  draftCharCount: number;
  draftChanged: boolean;
  draftStatusText: string;
  draftVersionText: string;
  draftActionText: string;
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
  metaText: string;
  contentPreview: string;
}

export interface BookExportLayoutOptionView {
  label: string;
  value: MemoirBookOutputExportLayout;
  detailText: string;
}

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
  plan: MemoirBookPlan | null;
  planTitle: string;
  stage: BookStage;
  stageText: string;
  chapters: BookChapterView[];
  splitSourceSelections: Record<string, string[]>;
  confirmedDraftCount: number;
  totalChapterCount: number;
  allChaptersConfirmed: boolean;
  hasDraftChanges: boolean;
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
}

export function trimText(value: string): string {
  return value.trim();
}

function automationEventTitle(event: MemoirAutomationEvent): string {
  if (event.event_type === "source_assigned") {
    return "已自动归章";
  }
  if (event.event_type === "source_held") {
    return event.status === "needs_review" ? "待整理讲述" : "整理建议已处理";
  }
  if (event.event_type === "chapter_auto_revised") {
    return "已自动补写";
  }
  if (event.event_type === "audit_completed") {
    return "章节审计完成";
  }
  return "AI 自动处理";
}

function automationEventKindText(event: MemoirAutomationEvent): string {
  if (event.event_type === "source_assigned") {
    return "归章";
  }
  if (event.event_type === "source_held") {
    return "待整理";
  }
  if (event.event_type === "chapter_auto_revised") {
    return "补写";
  }
  if (event.event_type === "audit_completed") {
    return "审计";
  }
  return "自动处理";
}

function automationEventStatusText(event: MemoirAutomationEvent): string {
  if (event.status === "needs_review") {
    return "待确认";
  }
  if (event.status === "failed") {
    return "失败";
  }
  if (event.status === "pending") {
    return "处理中";
  }
  return "已完成";
}

function automationEventActionText(event: MemoirAutomationEvent): string {
  if (event.status === "pending") {
    return "处理中";
  }
  if (event.status === "needs_review" && event.event_type === "source_held") {
    return "去整理";
  }
  if (event.status === "needs_review") {
    return "待处理";
  }
  return "知道了";
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function quotedTitle(text: string): string {
  const match = text.match(/「([^」]+)」/);
  return match ? match[1].trim() : "";
}

function automationEventChapterTitle(event: MemoirAutomationEvent): string {
  const metadata = objectValue(event.metadata_json);
  const route = objectValue(metadata.route);
  return stringValue(route.target_chapter_title)
    || stringValue(route.suggested_title)
    || quotedTitle(event.summary_text);
}

function automationEventDetailViews(event: MemoirAutomationEvent): BookAutomationEventDetailView[] {
  const details = Array.isArray(event.utterance_details) ? event.utterance_details : [];
  return details.map((detail) => {
    const recordTitle = trimText(detail.record_title || "")
      || (detail.source_index ? `语音记录 ${detail.source_index}` : "讲述记录");
    const chapterTitle = trimText(detail.chapter_title || "") || automationEventChapterTitle(event);
    const createdAtText = detail.created_at ? formatBeijingDateTimeShort(detail.created_at) : "";
    const previewText = trimText(detail.preview_text || "");
    return {
      id: `${event.id}:${detail.utterance_id}`,
      titleText: recordTitle,
      chapterText: chapterTitle ? `归入「${chapterTitle}」` : "归入章节待确认",
      metaText: createdAtText,
      previewText: previewText.length > 90 ? `${previewText.slice(0, 90)}...` : previewText,
    };
  });
}

function sourceAssignmentGroupView(
  events: MemoirAutomationEvent[],
  busyTarget: string,
  expandedTargets: Record<string, boolean>,
): BookAutomationEventView | null {
  const sourceEvents = events.filter((event) => (
    event.event_type === "source_assigned" && event.status === "completed" && !event.metadata_json?.acknowledged_at
  ));
  if (!sourceEvents.length) {
    return null;
  }

  const eventIds = sourceEvents.map((event) => event.id);
  const groupId = `source_assigned:${eventIds.join("|")}`;
  const detailItems = sourceEvents.flatMap((event) => automationEventDetailViews(event));
  const utteranceCount = sourceEvents.reduce((count, event) => count + event.utterance_ids.length, 0)
    || detailItems.length
    || sourceEvents.length;
  const chapterTitles = Array.from(new Set(
    detailItems
      .map((item) => item.chapterText.match(/「([^」]+)」/)?.[1] || "")
      .concat(sourceEvents.map(automationEventChapterTitle))
      .filter(Boolean),
  ));
  const chapterText = chapterTitles.length
    ? `${chapterTitles.length} 个章节：${chapterTitles.slice(0, 2).map((title) => `「${title}」`).join("、")}${chapterTitles.length > 2 ? "等" : ""}`
    : "章节";
  const latestAt = sourceEvents
    .map((event) => event.updated_at || event.created_at)
    .sort((left, right) => createdAtTime(right) - createdAtTime(left))[0] || "";
  const expanded = Boolean(expandedTargets[groupId]);
  return {
    id: groupId,
    eventIds,
    eventIdsText: eventIds.join(","),
    eventType: "source_assigned",
    actionKind: "acknowledge_group",
    primaryUtteranceId: detailItems[0]?.id.split(":").pop() || "",
    titleText: "已自动归章",
    summaryText: `${utteranceCount} 条讲述已自动整理进 ${chapterText}。`,
    detailText: "",
    detailItems,
    detailExpanded: expanded,
    detailToggleText: expanded ? "收起详情" : `查看 ${detailItems.length || utteranceCount} 条详情`,
    hasDetails: detailItems.length > 0,
    metaText: ["归章", latestAt ? formatBeijingDateTimeShort(latestAt) : "", `${utteranceCount} 条讲述`]
      .filter(Boolean)
      .join(" · "),
    statusText: "已完成",
    actionText: "知道了",
    canAcknowledge: true,
    canAct: true,
    busy: busyTarget === groupId,
  };
}

function automationEventDetailText(event: MemoirAutomationEvent): string {
  const metadata = objectValue(event.metadata_json);
  if (event.event_type === "source_held") {
    const route = objectValue(metadata.route);
    const action = stringValue(route.action);
    const title = stringValue(route.target_chapter_title) || stringValue(route.suggested_title);
    const reason = stringValue(route.reason);
    const targetText = action === "suggest_new_chapter" && title
      ? `建议新建「${title}」`
      : (title ? `可能归入「${title}」` : "需要人工确认归属");
    return reason ? `${targetText}：${reason}` : targetText;
  }
  if (event.event_type === "audit_completed") {
    const actions = Array.isArray(metadata.next_actions)
      ? metadata.next_actions.map((item) => stringValue(item)).filter(Boolean)
      : [];
    return actions.slice(0, 2).join("；");
  }
  if (event.event_type === "chapter_auto_revised") {
    const sourceIds = Array.isArray(metadata.pending_source_utterance_ids)
      ? metadata.pending_source_utterance_ids
      : [];
    return sourceIds.length ? `已把 ${sourceIds.length} 条新增讲述写入章节正文。` : "";
  }
  return "";
}

function automationEventPrimaryUtteranceId(event: MemoirAutomationEvent): string {
  const direct = event.utterance_ids.map((item) => trimText(item)).find(Boolean);
  if (direct) {
    return direct;
  }
  const metadata = objectValue(event.metadata_json);
  const route = objectValue(metadata.route);
  return stringValue(route.utterance_id);
}

function automationEventActionKind(event: MemoirAutomationEvent): BookAutomationEventView["actionKind"] {
  if (event.status === "pending") {
    return "none";
  }
  if (event.status === "needs_review") {
    return event.event_type === "source_held" && automationEventPrimaryUtteranceId(event)
      ? "review_source"
      : "none";
  }
  return "acknowledge";
}

function automationEventMetaText(event: MemoirAutomationEvent): string {
  const parts = [
    automationEventKindText(event),
    formatBeijingDateTimeShort(event.updated_at || event.created_at),
  ];
  if (event.utterance_ids.length) {
    parts.push(`${event.utterance_ids.length} 条讲述`);
  }
  if (typeof event.confidence === "number") {
    parts.push(`可信度 ${Math.round(event.confidence * 100)}%`);
  }
  return parts.filter(Boolean).join(" · ");
}

export function buildAutomationEventViews(
  events: MemoirAutomationEvent[] = [],
  busyTarget = "",
  expandedTargets: Record<string, boolean> = {},
): BookAutomationEventView[] {
  const filteredEvents = events.filter((event) => !event.metadata_json?.acknowledged_at);
  const sourceGroup = sourceAssignmentGroupView(filteredEvents, busyTarget, expandedTargets);
  const regularViews = filteredEvents
    .filter((event) => !(event.event_type === "source_assigned" && event.status === "completed"))
    .filter((event) => !event.metadata_json?.acknowledged_at)
    .map((event) => {
      const actionKind = automationEventActionKind(event);
      const detailItems = automationEventDetailViews(event);
      const expanded = Boolean(expandedTargets[event.id]);
      return {
        id: event.id,
        eventIds: [event.id],
        eventIdsText: event.id,
        eventType: event.event_type,
        actionKind,
        primaryUtteranceId: automationEventPrimaryUtteranceId(event),
        titleText: automationEventTitle(event),
        summaryText: trimText(event.summary_text) || "AI 已完成一次自动处理。",
        detailText: automationEventDetailText(event),
        detailItems,
        detailExpanded: expanded,
        detailToggleText: expanded ? "收起详情" : `查看 ${detailItems.length} 条详情`,
        hasDetails: detailItems.length > 0,
        metaText: automationEventMetaText(event),
        statusText: automationEventStatusText(event),
        actionText: automationEventActionText(event),
        canAcknowledge: actionKind === "acknowledge",
        canAct: actionKind !== "none",
        busy: event.id === busyTarget,
      };
    });
  return sourceGroup ? [sourceGroup, ...regularViews] : regularViews;
}

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

export function sourceChangeNotice(pendingCount: number, removedCount: number): string {
  if (removedCount) {
    return `${removedCount} 条素材已删除，章节稿需要更新`;
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
  return draft?.latest_version ? "重新生成" : "生成本章";
}

export function draftVersionText(draft: MemoirBookChapterDraft | null): string {
  const version = draft?.latest_version?.version;
  return version ? `第 ${version} 版` : "暂无正文";
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
    const label = `${isLatest ? "最新版 · " : ""}全书第 ${version.version} 版`;
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
  return count > 0 ? `${count} 条素材` : "暂无素材计数";
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
      const versionText = responseCount(chapter.draft_version) ? `第 ${chapter.draft_version} 版` : "";
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
    interview_records: "访谈",
    version_note: "版本",
    unconfirmed_facts: "待确认",
    source_appendix: "来源",
  };
  return labels[kind] || "书稿";
}

function previewText(text: string, maxLength = 72): string {
  const value = trimText(text).replace(/\s+/g, " ");
  return value.length > maxLength ? `${value.slice(0, maxLength)}...` : value;
}

function sectionView(section: MemoirBookManuscriptSection, index: number, prefix: string): BookManuscriptSectionView {
  return {
    id: `${prefix}:${section.kind}:${index}`,
    title: trimText(section.title) || manuscriptSectionKindText(section.kind),
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
  const appendix = settings.show_source_appendix ? "含来源附录" : "隐藏来源附录";
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
    title: trimText(section.title) || manuscriptSectionKindText(section.kind),
    metaText: manuscriptSectionKindText(section.kind),
    statusText: section.enabled === false ? "已隐藏" : "已生成",
  }));
  const backMatter = backMatterSections(manuscript).map((section, index) => ({
    id: `back:${section.kind}:${index}`,
    title: trimText(section.title) || manuscriptSectionKindText(section.kind),
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
      metaText: chapter.metaText,
      contentPreview: previewText(chapter.content, 96),
    }));
  }
  return (manuscript.chapters || []).map((chapter, index) => {
    const chapterIndex = responseCount(chapter.index) || index + 1;
    const quote = (chapter.pull_quotes || []).map((item) => trimText(item.text)).find(Boolean) || "";
    const sourceCount = chapter.source_refs?.length || 0;
    const hintText = [trimText(chapter.time_hint), trimText(chapter.place_hint)].filter(Boolean).join(" · ");
    return {
      id: `${chapter.chapter_id}:${chapterIndex}`,
      chapterId: chapter.chapter_id,
      indexText: `第 ${chapterIndex} 章`,
      title: trimText(chapter.title) || "未命名章节",
      intro: trimText(chapter.intro),
      quoteText: quote,
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

export function bookOutputVersionText(output: MemoirBookOutput | null): string {
  const version = output?.latest_version?.version;
  return version ? `全书第 ${version} 版` : "尚未生成全书";
}

function selectedBookOutputVersionText(output: MemoirBookOutput | null, selectedVersionId = ""): string {
  const selected = selectedBookOutputVersion(output, selectedVersionId);
  return selected ? `全书第 ${selected.version} 版` : "尚未生成全书";
}

export function bookOutputStaleNotice(output: MemoirBookOutput | null): string {
  if (!output?.is_stale) {
    return "";
  }
  const titles = (output.stale_chapter_titles || []).filter(Boolean).slice(0, 2);
  if (!titles.length) {
    return "章节内容已更新，请重新生成全书。";
  }
  const suffix = (output.stale_chapter_titles || []).length > 2 ? "等章节" : "";
  return `章节「${titles.join("、")}」${suffix}已更新，请重新生成全书。`;
}

function bookActionText(output: MemoirBookOutput | null): string {
  if (output?.is_stale) {
    return "更新全书";
  }
  return output?.latest_version ? "重新生成全书" : "生成全书";
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

function createdAtTime(value: string): number {
  const text = value.trim();
  if (!text) {
    return 0;
  }
  const normalized = TIMEZONE_OFFSET_RE.test(text) ? text : `${text}Z`;
  const time = new Date(normalized).getTime();
  return Number.isFinite(time) ? time : 0;
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
  selectedSourceIds: Set<string>,
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
    splitSelected: selectedSourceIds.has(ref.utterance_id),
  };
}

function decorateChapterStructureOptions(
  chapters: BookChapterView[],
  coverage: MemoirBookSourceCoverage | null = null,
  splitSelections: Record<string, string[]> = {},
): BookChapterView[] {
  const options = chapters.map(chapterOption);
  const recordNumbers = coverageSourceRecordNumbers(coverage);
  const fallbackRecordNumbers = planSourceRecordNumbers(chapters);
  return chapters.map((chapter, index) => {
    const targetOptions = options.filter((option) => option.value !== chapter.id);
    const selectedSourceIds = new Set(splitSelections[chapter.id] || []);
    const selectedCount = chapter.source_refs.filter((ref) => selectedSourceIds.has(ref.utterance_id)).length;
    return {
      ...chapter,
      sourceViews: chapter.source_refs.map((ref, sourceIndex) => (
        chapterSourceView(ref, sourceIndex, recordNumbers, fallbackRecordNumbers, selectedSourceIds)
      )),
      sourceToggleText: chapter.sourceListExpanded ? "收起素材" : `查看 ${chapter.source_refs.length} 条`,
      mergeTargetOptions: targetOptions,
      splitSelectedCount: selectedCount,
      splitActionText: selectedCount ? `拆出所选 ${selectedCount} 条` : "拆出所选",
      canSplitSelectedSources: selectedCount > 0 && selectedCount < chapter.source_refs.length,
      canMoveUp: index > 0,
      canMoveDown: index < chapters.length - 1,
    };
  });
}

function chapterStateData(chapters: BookChapterView[]): Pick<
  BookData,
  "confirmedDraftCount" | "totalChapterCount" | "allChaptersConfirmed" | "hasDraftChanges" | "canGenerateBook" | "progressText"
> {
  const confirmedDraftCount = chapters.filter((chapter) => chapter.draft?.status === "confirmed").length;
  const totalChapterCount = chapters.length;
  const hasDraftChanges = chapters.some((chapter) => chapter.draftChanged);
  const allChaptersConfirmed = totalChapterCount > 0 && confirmedDraftCount === totalChapterCount;
  return {
    confirmedDraftCount,
    totalChapterCount,
    allChaptersConfirmed,
    hasDraftChanges,
    canGenerateBook: allChaptersConfirmed && !hasDraftChanges,
    progressText: bookProgressText(chapters),
  };
}

export function chapterCollectionData(
  chapters: BookChapterView[],
  stage: BookStage,
  coverage: MemoirBookSourceCoverage | null = null,
  splitSelections: Record<string, string[]> = {},
): Pick<
  BookData,
  | "chapters"
  | "confirmedDraftCount"
  | "totalChapterCount"
  | "allChaptersConfirmed"
  | "hasDraftChanges"
  | "canGenerateBook"
  | "progressText"
  | "chapterPickerOptions"
> {
  const decorated = decorateChapterStructureOptions(chapters, coverage, splitSelections);
  return {
    chapters: decorated,
    ...chapterStateData(decorated),
    chapterPickerOptions: buildSourceActionPickerOptions(decorated, stage === "drafting"),
  };
}

export function bookChapterSourceSummary(chapter: Pick<MemoirBookPlanChapter, "source_refs">): string {
  return `本章素材：${chapter.source_refs.length} 条收录讲述`;
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
  lines.push("你可以继续调整章节顺序、合并章节，或者说出想修改的地方。");
  return lines.join("\n");
}

export function canExecuteBookVoiceCommand(preview: MemoirBookVoiceCommandParseResponse | null): boolean {
  if (!preview || !preview.operations.length || preview.intent === "unknown") {
    return false;
  }
  const operation = preview.operations[0];
  if (preview.intent === "read_outline") {
    return true;
  }
  if (preview.intent === "read_chapter") {
    return Boolean(operation.chapter_id);
  }
  if (preview.intent === "rename_chapter") {
    return Boolean(operation.chapter_id && operation.new_title);
  }
  if (preview.intent === "move_chapter") {
    return Boolean(operation.chapter_id && (
      operation.direction === "up"
      || operation.direction === "down"
      || operation.target_position === "first"
      || operation.target_position === "last"
      || (typeof operation.target_index === "number" && operation.target_index > 0)
      || Boolean(
        operation.relative_chapter_id
        && (operation.relative_position === "before" || operation.relative_position === "after"),
      )
    ));
  }
  if (preview.intent === "assign_source") {
    return Boolean(operation.chapter_id && operation.utterance_id);
  }
  return false;
}

export function bookVoiceCommandActionText(preview: MemoirBookVoiceCommandParseResponse | null): string {
  if (!preview) {
    return "确认执行";
  }
  if (preview.intent === "read_outline" || preview.intent === "read_chapter") {
    return "开始朗读";
  }
  return "确认执行";
}

export function buildChapterViews(
  plan: MemoirBookPlan,
  draftsByChapterId: Record<string, MemoirBookChapterDraft | null> = {},
  coverage: MemoirBookSourceCoverage | null = null,
  splitSelections: Record<string, string[]> = {},
): BookChapterView[] {
  const chapters = [...plan.chapters]
    .sort((left, right) => left.sort_order - right.sort_order)
    .map((chapter) => {
      const draft = draftsByChapterId[chapter.id] || null;
      const content = latestDraftContent(draft);
      const assignedPendingCount = responseCount(chapter.pending_source_count);
      const assignedRemovedCount = responseCount(chapter.removed_source_count);
      return {
        ...chapter,
        editingTitle: chapter.title,
        titleChanged: false,
        ttsTarget: `chapter:${chapter.id}`,
        sourceCount: chapter.source_refs.length,
        sourceSummary: bookChapterSourceSummary(chapter),
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
        removedSourceCount: assignedRemovedCount,
        sourceChangeNotice: sourceChangeNotice(assignedPendingCount, assignedRemovedCount),
        draft,
        draftContent: content,
        draftCharCount: trimText(content).length,
        draftChanged: false,
        draftStatusText: draftStatusText(draft),
        draftVersionText: draftVersionText(draft),
        draftActionText: draftActionText(draft, assignedPendingCount, assignedRemovedCount),
        draftBusy: false,
        draftError: "",
      };
    });
  return decorateChapterStructureOptions(chapters, coverage, splitSelections);
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

export function guestBookData(): Partial<BookData> {
  return {
    needsLogin: true,
    projectId: "",
    projectTitle: "我的回忆录",
    plan: null,
    planTitle: "",
    stage: "empty",
    stageText: stageText("empty"),
    chapters: [],
    confirmedDraftCount: 0,
    totalChapterCount: 0,
    allChaptersConfirmed: false,
    hasDraftChanges: false,
    canGenerateBook: false,
    progressText: "登录后可以开始整理",
    splitSourceSelections: {},
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
    bookPreviewTocItems: [],
    bookPreviewChapters: [],
    bookManuscriptAvailable: false,
    bookManuscriptTitle: "",
    bookManuscriptSubtitle: "",
    bookManuscriptTemplateLabel: "基础成书版",
    bookManuscriptSettingsText: "",
    bookManuscriptStructureItems: [],
    bookManuscriptFrontMatterItems: [],
    bookManuscriptChapterItems: [],
    bookManuscriptBackMatterItems: [],
    bookVersionText: "尚未生成全书",
    bookVersionOptions: [],
    bookSelectedVersionId: "",
    bookSelectedVersionIndex: 0,
    bookViewingHistoricalVersion: false,
    bookVersionNotice: "",
    bookStaleNotice: "",
    bookActionText: "生成全书",
    bookExportBusyTarget: "",
    bookExportLayout: "standard",
    bookExportLayoutIndex: 0,
    bookExportLayoutLabel: BOOK_EXPORT_LAYOUT_OPTIONS[0].label,
    bookExportLayoutDetailText: BOOK_EXPORT_LAYOUT_OPTIONS[0].detailText,
    bookExportLayoutOptions: BOOK_EXPORT_LAYOUT_OPTIONS,
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
