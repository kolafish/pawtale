import type {
  MemoirBookPlan,
  MemoirGuidanceMode,
  MemoirVoiceTranscriptIncludedFilter,
  MemoirVoiceTranscriptItem,
  MemoirVoiceSearchQueryFilters,
  MemoirVoiceSearchQueryParseResponse,
} from "../../domain/voice-chat";
import { definePage } from "../../services/page-definition";
import type { ChapterPickerOption } from "../../services/memoir-book-source-actions";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  assignMemoirBookSources,
  deleteMemoirVoiceTranscript,
  getMemoirBookPlan,
  listMemoirVoiceTranscripts,
  moveMemoirBookSource,
  parseMemoirVoiceSearchQuery,
  previewMemoirVoiceTranscriptVoiceEdit,
  recoverMemoirVoiceTranscript,
  updateMemoirVoiceTranscript,
} from "../../services/memoir-voice-api";
import { getMemoirIdentity } from "../../services/auth";
import {
  sourceAssignmentRequest,
  transcriptChapterAssignOptions,
  transcriptChapterMoveOptions,
  transcriptSourceMoveRequest,
} from "../../services/memoir-book-source-actions";
import { ApiError } from "../../services/request";
import {
  isAuthError,
  routeAuthError,
  switchToHome,
  switchToMe,
} from "../../services/navigation";
import {
  clearTranscriptNavigationTarget,
  readTranscriptNavigationTarget,
} from "../../services/transcript-navigation";
import {
  recordMemoirVoiceSearchQuery,
  stopMemoirVoiceSearchRecording,
} from "../../services/memoir-voice-search";
import {
  inlineTranscriptVoiceEditDiffSegments,
  transcriptAudioText as baseTranscriptAudioText,
  transcriptBookInclusionText,
  transcriptCreatedAtText,
  transcriptDurationText,
  transcriptTitleText,
  transcriptVoiceEditPreviewView,
  type TranscriptTextSegment,
  type TranscriptVoiceEditPreviewView,
} from "../../services/transcript-view-model";
import {
  datasetString,
  pageUrl,
  pickerIndex,
  type PageDatasetEvent,
  type PagePickerEvent,
  type PageTextInputEvent,
} from "../../services/page-input";

export {
  inlineTranscriptVoiceEditDiffSegments,
  transcriptBookInclusionText,
  transcriptCreatedAtText,
} from "../../services/transcript-view-model";

interface TranscriptDataset {
  id?: string;
}

type DatasetEvent = PageDatasetEvent<TranscriptDataset>;
type TextAreaEvent = PageTextInputEvent<TranscriptDataset>;

interface SearchInputEvent {
  detail: {
    value: string;
  };
}

type PickerEvent = PagePickerEvent<TranscriptDataset>;

interface TranscriptTextView {
  segments: TranscriptTextSegment[];
  collapsible: boolean;
  expanded: boolean;
  toggleText: string;
}

interface TranscriptSearchFilters {
  included: MemoirVoiceTranscriptIncludedFilter;
  guidanceMode: MemoirGuidanceMode | "";
  module: string;
}

interface SearchChipView {
  text: string;
}

interface TranscriptItemView extends MemoirVoiceTranscriptItem {
  recordNumber: number;
  summaryTitleText: string;
  summaryPreviewText: string;
  summarySegments: TranscriptTextSegment[];
  editingText: string;
  editingCharCount: number;
  createdAtText: string;
  durationText: string;
  anchorId: string;
  audioText: string;
  recordMetaText: string;
  recordCountText: string;
  recordCountClass: string;
  transcriptionStatusText: string;
  canRecoverTranscription: boolean;
  recoverButtonLoading: boolean;
  recoverButtonDisabled: boolean;
  recoverButtonText: string;
  bookInclusionText: string;
  isIncludedInBook: boolean;
  canAssignChapterSource: boolean;
  chapterAssignOptions: ChapterPickerOption[];
  canMoveChapterSource: boolean;
  chapterMoveOptions: ChapterPickerOption[];
  questionSegments: TranscriptTextSegment[];
  rawSegments: TranscriptTextSegment[];
  displaySegments: TranscriptTextSegment[];
  detailsCollapsible: boolean;
  detailsExpanded: boolean;
  detailsToggleText: string;
  changed: boolean;
  isEditing: boolean;
  cardClass: string;
  bookStatusClass: string;
  deleteButtonLoading: boolean;
  deleteButtonDisabled: boolean;
  deleteButtonText: string;
  assignPickerDisabled: boolean;
  assignPickerActionClass: string;
  assignPickerText: string;
  editButtonDisabled: boolean;
}

interface TranscriptsData {
  loading: boolean;
  needsLogin: boolean;
  savingId: string;
  deletingId: string;
  recoveringId: string;
  assigningSourceId: string;
  movingSourceId: string;
  error: string;
  items: TranscriptItemView[];
  total: number;
  limit: number;
  offset: number;
  searchInput: string;
  searchQuery: string;
  searchResultText: string;
  searchFilters: TranscriptSearchFilters;
  searchChips: SearchChipView[];
  hasSearchCriteria: boolean;
  searchDisplayText: string;
  voiceSearchRecording: boolean;
  voiceSearchTranscribing: boolean;
  voiceSearchStatusText: string;
  voiceEditTargetId: string;
  voiceEditRecording: boolean;
  voiceEditLoading: boolean;
  voiceEditSaving: boolean;
  voiceEditInstructionText: string;
  voiceEditStatusText: string;
  voiceEditPreview: TranscriptVoiceEditPreviewView | null;
  activeProjectId: string;
  projectTitle: string;
  bookPlan: MemoirBookPlan | null;
  bookPlanProjectId: string;
  highlightedId: string;
  pageMetaText: string;
  showSearchClear: boolean;
  showSearchChips: boolean;
  voiceSearchButtonClass: string;
  voiceSearchButtonText: string;
  voiceSearchDisabled: boolean;
  loadingEmptyText: string;
  showNoProjectPanel: boolean;
  showLoadingEmpty: boolean;
  showSearchEmpty: boolean;
  showGenericEmpty: boolean;
  showLoadMore: boolean;
}

const DEFAULT_LIMIT = 30;
const SEARCH_DEBOUNCE_MS = 300;
const COLLAPSED_TRANSCRIPT_CHAR_LIMIT = 180;
const DEFAULT_SEARCH_FILTERS: TranscriptSearchFilters = {
  included: "all",
  guidanceMode: "",
  module: "",
};

const MODULE_LABELS: Record<string, string> = {
  person: "人物",
  event: "事情",
  place: "地点",
  period: "时间段",
  theme: "主题",
  family: "家庭",
  work: "工作",
  chat: "闲聊",
};

type PageScrollTo = (options: {
  selector: string;
  offsetTop?: number;
  duration?: number;
}) => void;

type WxWithPageScrollTo = typeof wx & {
  pageScrollTo?: PageScrollTo;
};

type TranscriptsViewPatch = Pick<
  TranscriptsData,
  | "items"
  | "pageMetaText"
  | "showSearchClear"
  | "showSearchChips"
  | "voiceSearchButtonClass"
  | "voiceSearchButtonText"
  | "voiceSearchDisabled"
  | "loadingEmptyText"
  | "showNoProjectPanel"
  | "showLoadingEmpty"
  | "showSearchEmpty"
  | "showGenericEmpty"
  | "showLoadMore"
>;

type TranscriptsContext = {
  data: TranscriptsData;
  setData(patch: Partial<TranscriptsData>): void;
};

export function transcriptAudioText(item: MemoirVoiceTranscriptItem): string {
  return baseTranscriptAudioText(item, { includeDurationLabel: true });
}

export function transcriptRecordNumber(total: number, offset: number, index: number): number {
  return Math.max(1, total - offset - index);
}

function withTranscriptRuntimeState(
  item: TranscriptItemView,
  state: Pick<
    TranscriptsData,
    "savingId" | "deletingId" | "assigningSourceId" | "movingSourceId" | "highlightedId" | "recoveringId"
  >,
): TranscriptItemView {
  const editBusy = Boolean(state.savingId || state.deletingId || state.recoveringId);
  const chapterBusy = Boolean(
    state.savingId || state.deletingId || state.recoveringId || state.assigningSourceId || state.movingSourceId,
  );
  const baseCardClass = item.canRecoverTranscription ? "transcript-card recovery-pending" : "transcript-card";
  return {
    ...item,
    cardClass: state.highlightedId === item.id ? `${baseCardClass} highlighted` : baseCardClass,
    bookStatusClass: item.canRecoverTranscription
      ? "book-status pending"
      : (item.isIncludedInBook ? "book-status included" : "book-status"),
    deleteButtonLoading: state.deletingId === item.id,
    deleteButtonDisabled: editBusy,
    deleteButtonText: state.deletingId === item.id ? "删除中" : "删除",
    assignPickerDisabled: chapterBusy,
    assignPickerActionClass: chapterBusy ? "small-primary picker-action disabled" : "small-primary picker-action",
    assignPickerText: state.assigningSourceId === item.id ? "放入中" : "放入章节",
    recoverButtonLoading: state.recoveringId === item.id,
    recoverButtonDisabled: Boolean(state.savingId || state.deletingId || state.recoveringId),
    recoverButtonText: state.recoveringId === item.id ? "转写中" : "恢复转写",
    editButtonDisabled: editBusy || item.canRecoverTranscription,
  };
}

export function transcriptsViewPatch(state: TranscriptsData): TranscriptsViewPatch {
  const itemCount = state.items.length;
  const showNoProjectPanel = !state.loading && !state.needsLogin && !state.activeProjectId;
  const showListEmpty = !state.loading && !state.needsLogin && Boolean(state.activeProjectId) && itemCount === 0;
  return {
    items: state.items.map((item) => withTranscriptRuntimeState(item, state)),
    pageMetaText: `${state.projectTitle || "当前回忆录"} · ${state.total} 条讲述`,
    showSearchClear: Boolean(state.searchInput || state.hasSearchCriteria),
    showSearchChips: state.searchChips.length > 0,
    voiceSearchButtonClass: state.voiceSearchRecording ? "voice-search-button recording" : "voice-search-button",
    voiceSearchButtonText: state.voiceSearchRecording ? "停止" : (state.voiceSearchTranscribing ? "识别中" : "语音"),
    voiceSearchDisabled: state.voiceSearchTranscribing,
    loadingEmptyText: state.hasSearchCriteria ? "正在搜索" : "正在加载",
    showNoProjectPanel,
    showLoadingEmpty: state.loading && itemCount === 0,
    showSearchEmpty: showListEmpty && state.hasSearchCriteria,
    showGenericEmpty: showListEmpty && !state.hasSearchCriteria,
    showLoadMore: itemCount < state.total,
  };
}

function setTranscriptsData(context: TranscriptsContext, patch: Partial<TranscriptsData>): void {
  const nextData: TranscriptsData = { ...context.data, ...patch };
  context.setData({
    ...patch,
    ...transcriptsViewPatch(nextData),
  });
}

function transcriptSearchTerms(searchQuery: string): string[] {
  return searchQuery
    .trim()
    .split(/\s+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 8);
}

export function highlightTranscriptText(text: string, searchQuery: string): TranscriptTextSegment[] {
  if (!text) {
    return [];
  }
  const terms = transcriptSearchTerms(searchQuery);
  if (!terms.length) {
    return [{ text, highlight: false }];
  }
  const normalizedTerms = terms.map((term) => term.toLocaleLowerCase());
  const normalizedText = text.toLocaleLowerCase();
  const segments: TranscriptTextSegment[] = [];
  let cursor = 0;
  while (cursor < text.length) {
    let nextIndex = -1;
    let nextTerm = "";
    for (const term of normalizedTerms) {
      const index = normalizedText.indexOf(term, cursor);
      if (index >= 0 && (nextIndex < 0 || index < nextIndex || (index === nextIndex && term.length > nextTerm.length))) {
        nextIndex = index;
        nextTerm = term;
      }
    }
    if (nextIndex < 0) {
      segments.push({ text: text.slice(cursor), highlight: false });
      break;
    }
    if (nextIndex > cursor) {
      segments.push({ text: text.slice(cursor, nextIndex), highlight: false });
    }
    segments.push({ text: text.slice(nextIndex, nextIndex + nextTerm.length), highlight: true });
    cursor = nextIndex + nextTerm.length;
  }
  return segments;
}

export function transcriptTextView(
  text: string,
  searchQuery = "",
  expanded = false,
): TranscriptTextView {
  const content = text || "";
  const collapsible = content.length > COLLAPSED_TRANSCRIPT_CHAR_LIMIT;
  const showExpanded = collapsible && expanded;
  const visibleText = collapsible && !showExpanded
    ? `${content.slice(0, COLLAPSED_TRANSCRIPT_CHAR_LIMIT)}...`
    : content;
  return {
    segments: highlightTranscriptText(visibleText, searchQuery),
    collapsible,
    expanded: showExpanded,
    toggleText: showExpanded ? "收起" : "展开全部",
  };
}

function normalizedSearchFilters(filters?: Partial<TranscriptSearchFilters>): TranscriptSearchFilters {
  return {
    included: filters?.included || "all",
    guidanceMode: filters?.guidanceMode || "",
    module: filters?.module || "",
  };
}

function filtersFromParsedSearch(parsed: MemoirVoiceSearchQueryParseResponse): TranscriptSearchFilters {
  const filters: MemoirVoiceSearchQueryFilters = parsed.filters || { included: "all" };
  return normalizedSearchFilters({
    included: filters.included || "all",
    guidanceMode: filters.guidance_mode || "",
    module: filters.module || "",
  });
}

function filtersActive(filters: TranscriptSearchFilters): boolean {
  return filters.included !== "all" || Boolean(filters.guidanceMode) || Boolean(filters.module);
}

function filtersEqual(left: TranscriptSearchFilters, right: TranscriptSearchFilters): boolean {
  return left.included === right.included
    && left.guidanceMode === right.guidanceMode
    && left.module === right.module;
}

function searchCriteriaActive(query: string, filters: TranscriptSearchFilters): boolean {
  return Boolean(query.trim()) || filtersActive(filters);
}

function moduleLabel(module: string): string {
  return MODULE_LABELS[module] || module;
}

function searchChips(filters: TranscriptSearchFilters, parsed?: MemoirVoiceSearchQueryParseResponse): SearchChipView[] {
  const chips: SearchChipView[] = [];
  for (const keyword of (parsed?.keywords || []).slice(0, 4)) {
    const text = keyword.trim();
    if (text) {
      chips.push({ text });
    }
  }
  if (filters.included === "included") {
    chips.push({ text: "已收录" });
  } else if (filters.included === "not_included") {
    chips.push({ text: "未收录" });
  }
  if (filters.guidanceMode === "free_narration") {
    chips.push({ text: "慢慢讲" });
  } else if (filters.guidanceMode === "guided_interview") {
    chips.push({ text: "引导访谈" });
  }
  if (filters.module) {
    chips.push({ text: moduleLabel(filters.module) });
  }
  return chips;
}

function searchDisplayText(query: string, filters: TranscriptSearchFilters, parsed?: MemoirVoiceSearchQueryParseResponse): string {
  const displayText = parsed?.display_text?.trim();
  if (displayText) {
    return displayText;
  }
  if (query.trim()) {
    return query.trim();
  }
  const chips = searchChips(filters).map((chip) => chip.text);
  return chips.join("、") || "筛选条件";
}

function transcriptAnchorId(id: string): string {
  return `transcript-${id.replace(/[^A-Za-z0-9_-]/g, "-")}`;
}

async function loadTranscriptBookPlan(projectId: string): Promise<MemoirBookPlan | null> {
  try {
    return await getMemoirBookPlan(projectId);
  } catch (error) {
    if (error instanceof ApiError && error.statusCode === 404) {
      return null;
    }
    throw error;
  }
}

function transcriptSummaryPreview(item: MemoirVoiceTranscriptItem): string {
  const summary = (item.summary_text || "").trim();
  const fallback = (item.display_text || item.transcript_edited || item.transcript_raw || "").trim();
  const text = summary || fallback;
  if (text.length <= 96) {
    return text;
  }
  return `${text.slice(0, 96)}...`;
}

function isPendingTranscription(item: MemoirVoiceTranscriptItem): boolean {
  return Boolean(item.can_recover_transcription || item.transcription_status === "pending_transcription");
}

function toViewItem(
  item: MemoirVoiceTranscriptItem,
  searchQuery = "",
  bookPlan: MemoirBookPlan | null = null,
  recordNumber = 1,
): TranscriptItemView {
  const canRecoverTranscription = isPendingTranscription(item);
  const editingText = item.transcript_edited || item.transcript_raw;
  const isIncludedInBook = !canRecoverTranscription && Boolean(item.is_included_in_book || item.included_chapters?.length);
  const rawView = transcriptTextView(item.transcript_raw, searchQuery);
  const displayView = transcriptTextView(item.display_text, searchQuery);
  const chapterAssignOptions = canRecoverTranscription ? [] : transcriptChapterAssignOptions(item, bookPlan);
  const chapterMoveOptions = canRecoverTranscription ? [] : transcriptChapterMoveOptions(item, bookPlan);
  const summaryTitleText = transcriptTitleText(item);
  const summaryPreviewText = transcriptSummaryPreview(item);
  const createdAtText = transcriptCreatedAtText(item);
  const durationText = transcriptDurationText(item);
  const bookInclusionText = canRecoverTranscription ? "等待转写后再入章" : transcriptBookInclusionText(item);
  const recordMetaText = [createdAtText, durationText].filter(Boolean).join(" · ");
  return {
    ...item,
    recordNumber,
    summaryTitleText,
    summaryPreviewText,
    summarySegments: highlightTranscriptText(summaryPreviewText, searchQuery),
    editingText,
    editingCharCount: editingText.trim().length,
    createdAtText,
    durationText,
    anchorId: transcriptAnchorId(item.id),
    audioText: transcriptAudioText(item),
    recordMetaText,
    recordCountText: canRecoverTranscription ? "待转写" : `${item.display_char_count} 字`,
    recordCountClass: canRecoverTranscription ? "record-count pending" : "record-count",
    transcriptionStatusText: canRecoverTranscription ? "音频已保存" : "",
    canRecoverTranscription,
    recoverButtonLoading: false,
    recoverButtonDisabled: false,
    recoverButtonText: "恢复转写",
    bookInclusionText,
    isIncludedInBook,
    canAssignChapterSource: chapterAssignOptions.length > 0,
    chapterAssignOptions,
    canMoveChapterSource: chapterMoveOptions.length > 0,
    chapterMoveOptions,
    questionSegments: highlightTranscriptText(item.question_text, searchQuery),
    rawSegments: rawView.segments,
    displaySegments: displayView.segments,
    detailsCollapsible: rawView.collapsible || displayView.collapsible,
    detailsExpanded: false,
    detailsToggleText: "展开全部",
    changed: editingText !== (item.transcript_edited || item.transcript_raw),
    isEditing: false,
    cardClass: canRecoverTranscription ? "transcript-card recovery-pending" : "transcript-card",
    bookStatusClass: canRecoverTranscription
      ? "book-status pending"
      : (isIncludedInBook ? "book-status included" : "book-status"),
    deleteButtonLoading: false,
    deleteButtonDisabled: false,
    deleteButtonText: "删除",
    assignPickerDisabled: false,
    assignPickerActionClass: "small-primary picker-action",
    assignPickerText: "放入章节",
    editButtonDisabled: canRecoverTranscription,
  };
}

function withTranscriptExpansion(
  item: TranscriptItemView,
  searchQuery: string,
  expanded: boolean,
): TranscriptItemView {
  const rawView = transcriptTextView(item.transcript_raw, searchQuery, expanded);
  const displayView = transcriptTextView(item.display_text, searchQuery, expanded);
  const detailsCollapsible = rawView.collapsible || displayView.collapsible;
  const detailsExpanded = detailsCollapsible && expanded;
  return {
    ...item,
    rawSegments: rawView.segments,
    displaySegments: displayView.segments,
    detailsCollapsible,
    detailsExpanded,
    detailsToggleText: detailsExpanded ? "收起" : "展开全部",
  };
}

function changedItem(item: TranscriptItemView, editingText: string): TranscriptItemView {
  return {
    ...item,
    editingText,
    editingCharCount: editingText.trim().length,
    changed: editingText.trim() !== (item.transcript_edited || item.transcript_raw).trim(),
    isEditing: true,
  };
}

function replaceItem(
  items: TranscriptItemView[],
  next: MemoirVoiceTranscriptItem,
  searchQuery = "",
  bookPlan: MemoirBookPlan | null = null,
): TranscriptItemView[] {
  return items.map((item) => (
    item.id === next.id
      ? toViewItem(next, searchQuery, bookPlan, item.recordNumber)
      : item
  ));
}

function removeItem(items: TranscriptItemView[], id: string): TranscriptItemView[] {
  return items.filter((item) => item.id !== id);
}

function confirmDelete(item: TranscriptItemView): Promise<boolean> {
  return new Promise((resolve) => {
    const preview = item.summaryTitleText || item.display_text || item.transcript_raw || "这条讲述记录";
    wx.showModal({
      title: "删除讲述记录",
      content: `确定删除“${preview.slice(0, 24)}${preview.length > 24 ? "..." : ""}”？删除后，这条原始讲述会从素材库移除；如果已放入章节，相关章节会标记为需重新确认，已生成正文不会自动改写。`,
      confirmText: "删除",
      confirmColor: "#b84a5a",
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    });
  });
}

definePage<TranscriptsData>()({
  loadToken: 0,
  voiceEditToken: 0,
  searchDebounceTimer: null as number | null,

  data: {
    loading: false,
    needsLogin: false,
    savingId: "",
    deletingId: "",
    recoveringId: "",
    assigningSourceId: "",
    movingSourceId: "",
    error: "",
    items: [],
    total: 0,
    limit: DEFAULT_LIMIT,
    offset: 0,
    searchInput: "",
    searchQuery: "",
    searchResultText: "",
    searchFilters: DEFAULT_SEARCH_FILTERS,
    searchChips: [],
    hasSearchCriteria: false,
    searchDisplayText: "",
    voiceSearchRecording: false,
    voiceSearchTranscribing: false,
    voiceSearchStatusText: "",
    voiceEditTargetId: "",
    voiceEditRecording: false,
    voiceEditLoading: false,
    voiceEditSaving: false,
    voiceEditInstructionText: "",
    voiceEditStatusText: "",
    voiceEditPreview: null,
    activeProjectId: "",
    projectTitle: "",
    bookPlan: null,
    bookPlanProjectId: "",
    highlightedId: "",
    pageMetaText: "当前回忆录 · 0 条讲述",
    showSearchClear: false,
    showSearchChips: false,
    voiceSearchButtonClass: "voice-search-button",
    voiceSearchButtonText: "语音",
    voiceSearchDisabled: false,
    loadingEmptyText: "正在加载",
    showNoProjectPanel: true,
    showLoadingEmpty: false,
    showSearchEmpty: false,
    showGenericEmpty: false,
    showLoadMore: false,
  },

  async onShow() {
    await this.loadTranscripts(true);
    await this.revealStoredTranscriptTarget();
  },

  async loadTranscripts(reset: boolean = false) {
    if (this.data.loading && !reset) {
      return;
    }
    this.loadToken += 1;
    const requestToken = this.loadToken;
    const offset = reset ? 0 : this.data.offset;
    const searchQuery = this.data.searchQuery;
    const searchFilters = normalizedSearchFilters(this.data.searchFilters);
    const hasSearchCriteria = searchCriteriaActive(searchQuery, searchFilters);
    setTranscriptsData(this, { loading: true, error: "" });
    try {
      let activeProjectId = this.data.activeProjectId;
      let projectTitle = this.data.projectTitle;
      if (reset || !activeProjectId) {
        const identity = await getMemoirIdentity();
        activeProjectId = identity.active_project_id;
        projectTitle = identity.active_project?.title || "尚未创建回忆录";
      }
      if (!activeProjectId) {
        if (requestToken !== this.loadToken) {
          return;
        }
        setTranscriptsData(this, {
          activeProjectId: "",
          projectTitle: projectTitle || "尚未创建回忆录",
          bookPlan: null,
          bookPlanProjectId: "",
          items: [],
          total: 0,
          offset: 0,
          searchResultText: "",
          hasSearchCriteria,
          highlightedId: "",
        });
        return;
      }
      let bookPlan = this.data.bookPlan;
      const shouldRefreshPlan = reset || this.data.bookPlanProjectId !== activeProjectId;
      const [nextBookPlan, response] = await Promise.all([
        shouldRefreshPlan ? loadTranscriptBookPlan(activeProjectId) : Promise.resolve(bookPlan),
        listMemoirVoiceTranscripts({
          projectId: activeProjectId,
          query: searchQuery || undefined,
          included: searchFilters.included,
          guidanceMode: searchFilters.guidanceMode || undefined,
          module: searchFilters.module || undefined,
          limit: this.data.limit,
          offset,
        }),
      ]);
      bookPlan = nextBookPlan;
      const nextItems = response.items.map((item, index) => (
        toViewItem(item, searchQuery, bookPlan, transcriptRecordNumber(response.total, offset, index))
      ));
      if (requestToken !== this.loadToken) {
        return;
      }
      setTranscriptsData(this, {
        needsLogin: false,
        activeProjectId,
        projectTitle,
        bookPlan,
        bookPlanProjectId: activeProjectId,
        items: reset ? nextItems : [...this.data.items, ...nextItems],
        total: response.total,
        offset: offset + nextItems.length,
        searchResultText: hasSearchCriteria ? `找到 ${response.total} 条相关记录` : "",
        hasSearchCriteria,
        highlightedId: reset ? "" : this.data.highlightedId,
      });
      if (reset) {
        trackAuditAction(
          "transcript.view",
          { total: response.total, has_search_criteria: hasSearchCriteria },
          { projectId: activeProjectId },
        );
      }
    } catch (error) {
      if (requestToken !== this.loadToken) {
        return;
      }
      if (isAuthError(error)) {
        setTranscriptsData(this, {
          needsLogin: true,
          activeProjectId: "",
          projectTitle: "我的回忆录",
          bookPlan: null,
          bookPlanProjectId: "",
          items: [],
          total: 0,
          offset: 0,
          searchResultText: "",
          hasSearchCriteria: false,
          highlightedId: "",
        });
        return;
      }
      setTranscriptsData(this, { error: error instanceof Error ? error.message : "讲述记录加载失败" });
    } finally {
      if (requestToken === this.loadToken) {
        setTranscriptsData(this, { loading: false });
      }
    }
  },

  async loadMore() {
    if (this.data.items.length >= this.data.total) {
      return;
    }
    await this.loadTranscripts(false);
  },

  openTranscriptDetail(event: DatasetEvent) {
    const id = datasetString(event, "id");
    if (!id) {
      return;
    }
    wx.navigateTo({ url: pageUrl("/pages/transcript-detail/index", { id }) });
  },

  openTranscriptEdit(event: DatasetEvent) {
    const id = datasetString(event, "id");
    const item = this.data.items.find((candidate) => candidate.id === id);
    if (!id || item?.canRecoverTranscription) {
      return;
    }
    wx.navigateTo({ url: pageUrl("/pages/transcript-detail/index", { id, mode: "edit" }) });
  },

  stopCardTap() {
  },

  onSearchInput(event: SearchInputEvent) {
    const value = event.detail.value;
    setTranscriptsData(this, { searchInput: value });
    if (this.searchDebounceTimer !== null) {
      clearTimeout(this.searchDebounceTimer);
    }
    this.searchDebounceTimer = setTimeout(() => {
      void this.applySearch(value);
    }, SEARCH_DEBOUNCE_MS);
  },

  async onSearchConfirm() {
    await this.applySearch(this.data.searchInput);
  },

  async clearSearch() {
    await this.applySearch("");
  },

  async applySearch(rawQuery?: string, filters?: Partial<TranscriptSearchFilters>, parsed?: MemoirVoiceSearchQueryParseResponse) {
    if (this.searchDebounceTimer !== null) {
      clearTimeout(this.searchDebounceTimer);
      this.searchDebounceTimer = null;
    }
    const nextInput = rawQuery ?? this.data.searchInput;
    const searchQuery = nextInput.trim();
    const nextFilters = normalizedSearchFilters(filters);
    const hasSearchCriteria = searchCriteriaActive(searchQuery, nextFilters);
    const displayText = hasSearchCriteria ? searchDisplayText(searchQuery, nextFilters, parsed) : "";
    if (
      searchQuery === this.data.searchQuery
      && nextInput === this.data.searchInput
      && filtersEqual(nextFilters, this.data.searchFilters)
    ) {
      return;
    }
    setTranscriptsData(this, {
      searchInput: nextInput,
      searchQuery,
      searchFilters: nextFilters,
      searchChips: hasSearchCriteria ? searchChips(nextFilters, parsed) : [],
      hasSearchCriteria,
      searchDisplayText: displayText,
      searchResultText: "",
      items: [],
      total: 0,
      offset: 0,
      highlightedId: "",
    });
    await this.loadTranscripts(true);
  },

  async startVoiceSearch() {
    if (this.data.voiceSearchTranscribing) {
      return;
    }
    if (this.data.voiceEditRecording || this.data.voiceEditLoading || this.data.voiceEditSaving) {
      setTranscriptsData(this, { error: "请先完成当前语音修正" });
      return;
    }
    if (this.data.voiceSearchRecording) {
      stopMemoirVoiceSearchRecording();
      return;
    }
    setTranscriptsData(this, {
      voiceSearchRecording: true,
      voiceSearchTranscribing: false,
      voiceSearchStatusText: "正在听你说搜索内容",
      error: "",
    });
    try {
      const query = await recordMemoirVoiceSearchQuery({
        onRecordingEnd: () => {
          setTranscriptsData(this, {
            voiceSearchRecording: false,
            voiceSearchTranscribing: true,
            voiceSearchStatusText: "正在识别语音搜索",
          });
        },
      });
      setTranscriptsData(this, { voiceSearchStatusText: "正在理解搜索需求" });
      const parsed = await parseMemoirVoiceSearchQuery(query);
      const parsedFilters = filtersFromParsedSearch(parsed);
      await this.applySearch(parsed.query || query, parsedFilters, parsed);
      trackAuditAction(
        "transcript.voice_search",
        { has_filters: searchCriteriaActive(parsed.query || query, parsedFilters) },
        { projectId: this.data.activeProjectId },
      );
      wx.showToast({ title: "已识别", icon: "success" });
    } catch (error) {
      setTranscriptsData(this, { error: error instanceof Error ? error.message : "语音搜索失败" });
    } finally {
      setTranscriptsData(this, {
        voiceSearchRecording: false,
        voiceSearchTranscribing: false,
        voiceSearchStatusText: "",
      });
    }
  },

  async startVoiceEdit(event: DatasetEvent) {
    const id = datasetString(event, "id");
    const item = this.data.items.find((candidate) => candidate.id === id);
    if (
      !id
      || !item
      || item.canRecoverTranscription
      || this.data.savingId
      || this.data.deletingId
      || this.data.voiceEditLoading
      || this.data.voiceEditSaving
    ) {
      return;
    }
    if (this.data.voiceSearchRecording || this.data.voiceSearchTranscribing) {
      setTranscriptsData(this, { error: "请先完成当前语音搜索" });
      return;
    }
    if (this.data.voiceEditRecording) {
      if (this.data.voiceEditTargetId === id) {
        stopMemoirVoiceSearchRecording();
      } else {
        setTranscriptsData(this, { error: "请先停止当前语音修正" });
      }
      return;
    }
    this.voiceEditToken += 1;
    const requestToken = this.voiceEditToken;
    setTranscriptsData(this, {
      voiceEditTargetId: id,
      voiceEditRecording: true,
      voiceEditLoading: false,
      voiceEditSaving: false,
      voiceEditInstructionText: "",
      voiceEditStatusText: "正在听你说要改哪里",
      voiceEditPreview: null,
      error: "",
    });
    try {
      const instructionText = await recordMemoirVoiceSearchQuery({
        onRecordingEnd: () => {
          if (requestToken !== this.voiceEditToken) {
            return;
          }
          setTranscriptsData(this, {
            voiceEditRecording: false,
            voiceEditLoading: true,
            voiceEditStatusText: "正在识别修正内容",
          });
        },
        emptyTranscriptMessage: "没有识别到修正内容，请再说一次",
        noVoiceTranscriptMessage: "没有听到修正内容，请再说一次",
      });
      if (requestToken !== this.voiceEditToken || this.data.voiceEditTargetId !== id) {
        return;
      }
      setTranscriptsData(this, {
        voiceEditInstructionText: instructionText,
        voiceEditStatusText: "正在生成修正预览",
      });
      const preview = await previewMemoirVoiceTranscriptVoiceEdit(id, instructionText);
      if (requestToken !== this.voiceEditToken || this.data.voiceEditTargetId !== id) {
        return;
      }
      setTranscriptsData(this, {
        voiceEditPreview: transcriptVoiceEditPreviewView(preview),
        voiceEditStatusText: preview.action === "revise_transcript" ? "请确认修正内容" : preview.reply_text,
      });
      trackAuditAction(
        "transcript.voice_edit_preview",
        { action: preview.action },
        { projectId: this.data.activeProjectId },
      );
    } catch (error) {
      if (requestToken !== this.voiceEditToken) {
        return;
      }
      setTranscriptsData(this, {
        error: error instanceof Error ? error.message : "语音修正失败",
        voiceEditStatusText: "",
      });
    } finally {
      if (requestToken === this.voiceEditToken) {
        setTranscriptsData(this, {
          voiceEditRecording: false,
          voiceEditLoading: false,
        });
      }
    }
  },

  cancelVoiceEdit() {
    this.voiceEditToken += 1;
    if (this.data.voiceEditRecording) {
      stopMemoirVoiceSearchRecording();
    }
    setTranscriptsData(this, {
      voiceEditTargetId: "",
      voiceEditRecording: false,
      voiceEditLoading: false,
      voiceEditSaving: false,
      voiceEditInstructionText: "",
      voiceEditStatusText: "",
      voiceEditPreview: null,
      error: "",
    });
  },

  async confirmVoiceEdit() {
    const id = this.data.voiceEditTargetId;
    const preview = this.data.voiceEditPreview;
    if (
      !id
      || !preview
      || !preview.canConfirm
      || this.data.voiceEditSaving
      || this.data.savingId
      || this.data.deletingId
    ) {
      return;
    }
    const content = preview.editedText.trim();
    if (!content) {
      setTranscriptsData(this, { error: "修正后的整理文字不能为空" });
      return;
    }
    setTranscriptsData(this, { voiceEditSaving: true, savingId: id, error: "" });
    try {
      const updated = await updateMemoirVoiceTranscript(id, content);
      setTranscriptsData(this, {
        items: replaceItem(this.data.items, updated, this.data.searchQuery, this.data.bookPlan),
        voiceEditTargetId: "",
        voiceEditInstructionText: "",
        voiceEditStatusText: "",
        voiceEditPreview: null,
      });
      trackAuditAction("transcript.voice_edit_save", {}, { projectId: this.data.activeProjectId });
      wx.showToast({ title: "已保存修正", icon: "success" });
    } catch (error) {
      setTranscriptsData(this, { error: error instanceof Error ? error.message : "保存修正失败" });
    } finally {
      setTranscriptsData(this, { voiceEditSaving: false, savingId: "" });
    }
  },

  toggleTranscriptText(event: DatasetEvent) {
    const id = datasetString(event, "id");
    if (!id) {
      return;
    }
    const items = this.data.items.map((item) => {
      if (item.id !== id) {
        return item;
      }
      return withTranscriptExpansion(item, this.data.searchQuery, !item.detailsExpanded);
    });
    setTranscriptsData(this, { items });
  },

  async assignTranscriptChapter(event: PickerEvent) {
    const utteranceId = datasetString(event, "id");
    const item = this.data.items.find((candidate) => candidate.id === utteranceId);
    const optionIndex = pickerIndex(event);
    const option = item?.chapterAssignOptions[optionIndex];
    if (
      !utteranceId
      || !item
      || !option
      || item.canRecoverTranscription
      || !this.data.activeProjectId
      || this.data.assigningSourceId
      || this.data.movingSourceId
    ) {
      return;
    }
    setTranscriptsData(this, { assigningSourceId: utteranceId, error: "" });
    try {
      const updatedPlan = await assignMemoirBookSources(
        this.data.activeProjectId,
        sourceAssignmentRequest(option.value, utteranceId),
      );
      setTranscriptsData(this, { bookPlan: updatedPlan, bookPlanProjectId: this.data.activeProjectId });
      await this.loadTranscripts(true);
      wx.showToast({ title: "已归入章节", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      setTranscriptsData(this, { error: error instanceof Error ? error.message : "归入章节失败" });
    } finally {
      setTranscriptsData(this, { assigningSourceId: "" });
    }
  },

  async moveTranscriptChapter(event: PickerEvent) {
    const utteranceId = datasetString(event, "id");
    const item = this.data.items.find((candidate) => candidate.id === utteranceId);
    const optionIndex = pickerIndex(event);
    const option = item?.chapterMoveOptions[optionIndex];
    if (
      !utteranceId
      || !item
      || !option
      || item.canRecoverTranscription
      || !this.data.activeProjectId
      || this.data.assigningSourceId
      || this.data.movingSourceId
    ) {
      return;
    }
    setTranscriptsData(this, { movingSourceId: utteranceId, error: "" });
    try {
      const plan = this.data.bookPlan || await loadTranscriptBookPlan(this.data.activeProjectId);
      const moveRequest = transcriptSourceMoveRequest(item, plan, option.value);
      if (!moveRequest.request) {
        setTranscriptsData(this, { error: moveRequest.error });
        return;
      }
      const updatedPlan = await moveMemoirBookSource(this.data.activeProjectId, moveRequest.request);
      setTranscriptsData(this, { bookPlan: updatedPlan, bookPlanProjectId: this.data.activeProjectId });
      await this.loadTranscripts(true);
      wx.showToast({ title: "素材已移动", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      setTranscriptsData(this, { error: error instanceof Error ? error.message : "素材移动失败" });
    } finally {
      setTranscriptsData(this, { movingSourceId: "" });
    }
  },

  async recoverTranscript(event: DatasetEvent) {
    const id = datasetString(event, "id");
    const item = this.data.items.find((candidate) => candidate.id === id);
    if (
      !id ||
      !item ||
      !item.canRecoverTranscription ||
      this.data.recoveringId ||
      this.data.savingId ||
      this.data.deletingId
    ) {
      return;
    }
    setTranscriptsData(this, { recoveringId: id, error: "" });
    try {
      const recovered = await recoverMemoirVoiceTranscript(id);
      setTranscriptsData(this, {
        items: replaceItem(this.data.items, recovered, this.data.searchQuery, this.data.bookPlan),
      });
      trackAuditAction("transcript.recover_transcription", {}, { projectId: recovered.project_id });
      wx.showToast({ title: "已恢复转写", icon: "success" });
    } catch (error) {
      setTranscriptsData(this, { error: error instanceof Error ? error.message : "恢复转写失败" });
    } finally {
      setTranscriptsData(this, { recoveringId: "" });
    }
  },

  beginEdit(event: DatasetEvent) {
    const id = datasetString(event, "id");
    const index = this.data.items.findIndex((item) => item.id === id);
    if (index < 0 || this.data.items[index].canRecoverTranscription || this.data.savingId || this.data.deletingId) {
      return;
    }
    const items = [...this.data.items];
    const current = items[index].transcript_edited || items[index].transcript_raw;
    items[index] = {
      ...items[index],
      editingText: current,
      editingCharCount: current.trim().length,
      changed: false,
      isEditing: true,
    };
    setTranscriptsData(this, { items, error: "" });
  },

  changeText(event: TextAreaEvent) {
    const id = datasetString(event, "id");
    const index = this.data.items.findIndex((item) => item.id === id);
    if (index < 0 || this.data.items[index].canRecoverTranscription) {
      return;
    }
    const items = [...this.data.items];
    items[index] = changedItem(items[index], event.detail.value);
    setTranscriptsData(this, { items });
  },

  cancelEdit(event: DatasetEvent) {
    const id = datasetString(event, "id");
    const index = this.data.items.findIndex((item) => item.id === id);
    if (index < 0 || this.data.savingId || this.data.deletingId) {
      return;
    }
    const items = [...this.data.items];
    items[index] = toViewItem(items[index], this.data.searchQuery, this.data.bookPlan, items[index].recordNumber);
    setTranscriptsData(this, { items, error: "" });
  },

  async saveTranscript(event: DatasetEvent) {
    const id = datasetString(event, "id");
    const item = this.data.items.find((candidate) => candidate.id === id);
    if (!item || item.canRecoverTranscription || this.data.savingId || this.data.deletingId) {
      return;
    }
    const content = item.editingText.trim();
    if (!content) {
      setTranscriptsData(this, { error: "整理文字不能为空" });
      return;
    }
    setTranscriptsData(this, { savingId: id, error: "" });
    try {
      const updated = await updateMemoirVoiceTranscript(id, content);
      setTranscriptsData(this, { items: replaceItem(this.data.items, updated, this.data.searchQuery, this.data.bookPlan) });
      trackAuditAction("transcript.edit_save", {}, { projectId: this.data.activeProjectId });
      wx.showToast({ title: "已保存", icon: "success" });
    } catch (error) {
      setTranscriptsData(this, { error: error instanceof Error ? error.message : "保存失败" });
    } finally {
      setTranscriptsData(this, { savingId: "" });
    }
  },

  resetToRaw(event: DatasetEvent) {
    const id = datasetString(event, "id");
    const index = this.data.items.findIndex((item) => item.id === id);
    if (index < 0) {
      return;
    }
    const items = [...this.data.items];
    items[index] = changedItem(items[index], items[index].transcript_raw);
    setTranscriptsData(this, { items });
  },

  async deleteTranscript(event: DatasetEvent) {
    const id = datasetString(event, "id");
    const item = this.data.items.find((candidate) => candidate.id === id);
    if (!id || !item || this.data.savingId || this.data.deletingId) {
      return;
    }
    const confirmed = await confirmDelete(item);
    if (!confirmed) {
      return;
    }
    setTranscriptsData(this, { deletingId: id, error: "" });
    try {
      await deleteMemoirVoiceTranscript(id);
      setTranscriptsData(this, {
        items: removeItem(this.data.items, id),
        total: Math.max(0, this.data.total - 1),
        offset: Math.max(0, this.data.offset - 1),
      });
      wx.showToast({ title: "已删除", icon: "success" });
    } catch (error) {
      setTranscriptsData(this, { error: error instanceof Error ? error.message : "删除失败" });
    } finally {
      setTranscriptsData(this, { deletingId: "" });
    }
  },

  onUnload() {
    if (this.searchDebounceTimer !== null) {
      clearTimeout(this.searchDebounceTimer);
      this.searchDebounceTimer = null;
    }
    this.voiceEditToken += 1;
    if (this.data.voiceSearchRecording || this.data.voiceEditRecording) {
      stopMemoirVoiceSearchRecording();
    }
  },

  openHome() {
    switchToHome();
  },

  openLogin() {
    switchToMe();
  },

  createProject() {
    wx.navigateTo({ url: "/pages/project-edit/index" });
  },

  async revealStoredTranscriptTarget() {
    const targetId = readTranscriptNavigationTarget();
    if (!targetId) {
      return;
    }
    clearTranscriptNavigationTarget();
    if (this.data.hasSearchCriteria) {
      setTranscriptsData(this, {
        searchInput: "",
        searchQuery: "",
        searchFilters: DEFAULT_SEARCH_FILTERS,
        searchChips: [],
        hasSearchCriteria: false,
        searchDisplayText: "",
        searchResultText: "",
      });
      await this.loadTranscripts(true);
    }
    const item = await this.ensureTranscriptTargetLoaded(targetId);
    if (!item) {
      return;
    }
    setTranscriptsData(this, { highlightedId: item.id });
    this.scrollToTranscript(item);
  },

  async ensureTranscriptTargetLoaded(targetId: string) {
    let item = this.data.items.find((candidate) => candidate.id === targetId) || null;
    while (!item && this.data.items.length < this.data.total) {
      const previousCount = this.data.items.length;
      await this.loadTranscripts(false);
      item = this.data.items.find((candidate) => candidate.id === targetId) || null;
      if (this.data.items.length === previousCount) {
        break;
      }
    }
    return item;
  },

  scrollToTranscript(item: TranscriptItemView) {
    const pageScrollTo = (wx as WxWithPageScrollTo).pageScrollTo;
    if (typeof pageScrollTo !== "function") {
      return;
    }
    pageScrollTo({
      selector: `#${item.anchorId}`,
      offsetTop: 16,
      duration: 240,
    });
  },
});
