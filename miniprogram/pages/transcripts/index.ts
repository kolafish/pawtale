import type { MemoirGuidanceMode } from "../../domain/memoir-voice-session";
import type {
  MemoirVoiceTranscriptIncludedFilter,
  MemoirVoiceTranscriptItem,
  MemoirVoiceSearchQueryFilters,
  MemoirVoiceSearchQueryParseResponse,
} from "../../domain/memoir-transcript";
import { definePage } from "../../services/page-definition";
import { accessibilityPageClass } from "../../services/accessibility-settings";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  deleteMemoirVoiceTranscript,
  listMemoirVoiceTranscripts,
  parseMemoirVoiceSearchQuery,
  recoverMemoirVoiceTranscript,
} from "../../services/memoir-transcript-api";
import { getMemoirIdentity } from "../../services/auth";
import {
  isAuthError,
  navigateToLogin,
} from "../../services/navigation";
import {
  clearTranscriptNavigationTarget,
  readTranscriptNavigationTarget,
} from "../../services/transcript-navigation";
import {
  cancelMemoirVoiceSearchRecording,
  recordMemoirVoiceSearchQuery,
  stopMemoirVoiceSearchRecording,
} from "../../services/memoir-voice-search";
import {
  confirmDeleteTranscript,
  transcriptCreatedAtText,
  transcriptDurationText,
  transcriptTitleText,
  type TranscriptTextSegment,
} from "../../services/transcript-view-model";
import {
  datasetString,
  pageUrl,
  type PageDatasetEvent,
} from "../../services/page-input";

interface TranscriptDataset {
  id?: string;
}

type DatasetEvent = PageDatasetEvent<TranscriptDataset>;

interface SearchInputEvent {
  detail: {
    value: string;
  };
}

interface TranscriptTouchEvent extends DatasetEvent {
  touches: Array<{ pageX: number; pageY: number }>;
  changedTouches: Array<{ pageX: number; pageY: number }>;
}

interface TranscriptSearchFilters {
  included: MemoirVoiceTranscriptIncludedFilter;
  guidanceMode: MemoirGuidanceMode | "";
  module: string;
}

interface SearchChipView {
  text: string;
}

interface TranscriptItemView {
  id: string;
  summaryTitleText: string;
  summaryPreviewText: string;
  summarySegments: TranscriptTextSegment[];
  anchorId: string;
  recordMetaText: string;
  canRecoverTranscription: boolean;
  recoverButtonLoading: boolean;
  recoverButtonDisabled: boolean;
  recoverButtonText: string;
  storyStatusText: string;
  cardClass: string;
  cardAriaLabel: string;
}

interface TranscriptsData {
  pageClass: string;
  loading: boolean;
  needsLogin: boolean;
  recoveringId: string;
  deletingId: string;
  openSwipeId: string;
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
  activeProjectId: string;
  projectTitle: string;
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

function transcriptRequestLimit(
  state: Pick<TranscriptsData, "items" | "limit" | "offset">,
  reset: boolean,
): number {
  if (!reset) {
    return state.limit;
  }
  return Math.max(state.limit, state.offset, state.items.length);
}

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

function withTranscriptRuntimeState(
  item: TranscriptItemView,
  state: Pick<
    TranscriptsData,
    "highlightedId" | "recoveringId"
  >,
): TranscriptItemView {
  const transcriptBusy = Boolean(state.recoveringId);
  const baseCardClass = item.canRecoverTranscription ? "transcript-card recovery-pending" : "transcript-card";
  return {
    ...item,
    cardClass: state.highlightedId === item.id ? `${baseCardClass} highlighted` : baseCardClass,
    recoverButtonLoading: state.recoveringId === item.id,
    recoverButtonDisabled: transcriptBusy,
    recoverButtonText: state.recoveringId === item.id ? "正在转成文字" : "把录音转成文字",
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
): TranscriptItemView {
  const canRecoverTranscription = isPendingTranscription(item);
  const isIncludedInBook = !canRecoverTranscription && Boolean(
    item.is_included_in_book
    || item.included_chapters?.some((chapter) => chapter.include_in_book !== false),
  );
  const summaryTitleText = canRecoverTranscription ? "录音已保存，等待生成文字" : transcriptTitleText(item);
  const summaryPreviewText = canRecoverTranscription
    ? "点击下方按钮，把已保存的录音转成文字。"
    : transcriptSummaryPreview(item);
  const createdAtText = transcriptCreatedAtText(item);
  const durationText = transcriptDurationText(item);
  const includedChapter = [...(item.included_chapters || [])]
    .sort((left, right) => left.sort_order - right.sort_order)[0];
  const includedLocationText = item.section_assignment?.section_title.trim()
    || item.section_assignment?.chapter_title.trim()
    || includedChapter?.title.trim()
    || "";
  const storyStatusText = canRecoverTranscription
    ? "等待生成文字"
    : (isIncludedInBook
      ? `已收进：${includedLocationText || "故事树"}`
      : (item.section_assignment
        ? `已收进：${includedLocationText || "故事树"}`
        : "尚未整理"));
  const recordMetaText = [
    createdAtText,
    durationText,
    canRecoverTranscription ? "" : `${item.display_char_count} 字`,
  ].filter(Boolean).join(" · ");
  return {
    id: item.id,
    summaryTitleText,
    summaryPreviewText,
    summarySegments: highlightTranscriptText(summaryPreviewText, searchQuery),
    anchorId: transcriptAnchorId(item.id),
    recordMetaText,
    canRecoverTranscription,
    recoverButtonLoading: false,
    recoverButtonDisabled: false,
    recoverButtonText: "把录音转成文字",
    storyStatusText,
    cardClass: canRecoverTranscription ? "transcript-card recovery-pending" : "transcript-card",
    cardAriaLabel: `${summaryTitleText}，${recordMetaText}，${storyStatusText}，打开详情`,
  };
}

function replaceItem(
  items: TranscriptItemView[],
  next: MemoirVoiceTranscriptItem,
  searchQuery = "",
): TranscriptItemView[] {
  return items.map((item) => (
    item.id === next.id
      ? toViewItem(next, searchQuery)
      : item
  ));
}

definePage<TranscriptsData>()({
  loadToken: 0,
  searchDebounceTimer: null as number | null,
  swipeStart: null as { id: string; x: number; y: number } | null,
  swipeTapUntil: 0,
  voiceSearchToken: 0,
  voiceSearchVisible: true,

  data: {
    pageClass: "transcripts-page",
    loading: false,
    needsLogin: false,
    recoveringId: "",
    deletingId: "",
    openSwipeId: "",
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
    activeProjectId: "",
    projectTitle: "",
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
    this.setData({ openSwipeId: "" });
    this.voiceSearchVisible = true;
    this.setData({ pageClass: accessibilityPageClass("transcripts-page") });
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
    const requestLimit = transcriptRequestLimit(this.data, reset);
    const searchQuery = this.data.searchQuery;
    const searchFilters = normalizedSearchFilters(this.data.searchFilters);
    const hasSearchCriteria = searchCriteriaActive(searchQuery, searchFilters);
    setTranscriptsData(this, { loading: true, error: "", ...(reset ? { openSwipeId: "" } : {}) });
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
          items: [],
          total: 0,
          offset: 0,
          searchResultText: "",
          hasSearchCriteria,
          highlightedId: "",
        });
        return;
      }
      const response = await listMemoirVoiceTranscripts({
          projectId: activeProjectId,
          query: searchQuery || undefined,
          included: searchFilters.included,
          guidanceMode: searchFilters.guidanceMode || undefined,
          module: searchFilters.module || undefined,
          limit: requestLimit,
          offset,
        });
      const nextItems = response.items.map((item) => toViewItem(item, searchQuery));
      if (requestToken !== this.loadToken) {
        return;
      }
      setTranscriptsData(this, {
        needsLogin: false,
        activeProjectId,
        projectTitle,
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
    if (this.data.loading || this.data.items.length >= this.data.total) {
      return;
    }
    await this.loadTranscripts(false);
  },

  openTranscriptDetail(event: DatasetEvent) {
    const id = datasetString(event, "id");
    if (!id) {
      return;
    }
    if (Date.now() < this.swipeTapUntil) {
      return;
    }
    if (this.data.openSwipeId) {
      this.setData({ openSwipeId: "" });
      return;
    }
    wx.navigateTo({ url: pageUrl("/pages/transcript-detail/index", { id }) });
  },

  onCardTouchStart(event: TranscriptTouchEvent) {
    const id = datasetString(event, "id");
    const touch = event.touches[0];
    this.swipeStart = id && touch ? { id, x: touch.pageX, y: touch.pageY } : null;
  },

  onCardTouchEnd(event: TranscriptTouchEvent) {
    const start = this.swipeStart;
    this.swipeStart = null;
    const touch = event.changedTouches[0];
    if (!start || !touch || start.id !== datasetString(event, "id")) {
      return;
    }
    const dx = touch.pageX - start.x;
    const dy = touch.pageY - start.y;
    if (Math.abs(dx) < 44 || Math.abs(dx) <= Math.abs(dy) * 1.2) {
      return;
    }
    this.swipeTapUntil = Date.now() + 350;
    this.setData({ openSwipeId: dx > 0 ? start.id : "" });
  },

  onCardTouchCancel() {
    this.swipeStart = null;
  },

  async deleteTranscript(event: DatasetEvent) {
    const id = datasetString(event, "id");
    const item = this.data.items.find((candidate) => candidate.id === id);
    if (!item || this.data.deletingId || this.data.recoveringId) {
      return;
    }
    this.setData({ deletingId: id });
    try {
      const confirmed = await confirmDeleteTranscript(item.summaryTitleText);
      if (!confirmed) {
        return;
      }
      await deleteMemoirVoiceTranscript(id);
      const total = Math.max(0, this.data.total - 1);
      setTranscriptsData(this, {
        items: this.data.items.filter((candidate) => candidate.id !== id),
        total,
        offset: Math.max(0, this.data.offset - 1),
        openSwipeId: "",
        searchResultText: this.data.hasSearchCriteria ? `找到 ${total} 条相关记录` : "",
      });
      wx.showToast({ title: "已删除", icon: "success" });
      await this.loadTranscripts(true);
    } catch (error) {
      setTranscriptsData(this, { error: error instanceof Error ? error.message : "删除失败" });
    } finally {
      this.setData({ deletingId: "" });
    }
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
    if (this.data.voiceSearchRecording) {
      stopMemoirVoiceSearchRecording();
      return;
    }
    const token = ++this.voiceSearchToken;
    const isCurrent = () => this.voiceSearchVisible && token === this.voiceSearchToken;
    setTranscriptsData(this, {
      voiceSearchRecording: true,
      voiceSearchTranscribing: false,
      voiceSearchStatusText: "正在听你说搜索内容",
      error: "",
    });
    try {
      const query = await recordMemoirVoiceSearchQuery({
        isCancelled: () => !isCurrent(),
        onRecordingEnd: () => {
          if (!isCurrent()) return;
          setTranscriptsData(this, {
            voiceSearchRecording: false,
            voiceSearchTranscribing: true,
            voiceSearchStatusText: "正在识别语音搜索",
          });
        },
      });
      if (!isCurrent()) return;
      setTranscriptsData(this, { voiceSearchStatusText: "正在理解搜索需求" });
      const parsed = await parseMemoirVoiceSearchQuery(query);
      if (!isCurrent()) return;
      const parsedFilters = filtersFromParsedSearch(parsed);
      await this.applySearch(parsed.query || query, parsedFilters, parsed);
      if (!isCurrent()) return;
      trackAuditAction(
        "transcript.voice_search",
        { has_filters: searchCriteriaActive(parsed.query || query, parsedFilters) },
        { projectId: this.data.activeProjectId },
      );
      wx.showToast({ title: "已识别", icon: "success" });
    } catch (error) {
      if (isCurrent()) {
        setTranscriptsData(this, { error: error instanceof Error ? error.message : "语音搜索失败" });
      }
    } finally {
      if (isCurrent()) {
        setTranscriptsData(this, {
          voiceSearchRecording: false,
          voiceSearchTranscribing: false,
          voiceSearchStatusText: "",
        });
      }
    }
  },

  cancelVoiceSearch() {
    this.voiceSearchVisible = false;
    this.voiceSearchToken += 1;
    if (this.data.voiceSearchRecording || this.data.voiceSearchTranscribing) {
      cancelMemoirVoiceSearchRecording();
      setTranscriptsData(this, {
        voiceSearchRecording: false,
        voiceSearchTranscribing: false,
        voiceSearchStatusText: "",
      });
    }
  },

  onHide() {
    this.cancelVoiceSearch();
  },

  async recoverTranscript(event: DatasetEvent) {
    const id = datasetString(event, "id");
    const item = this.data.items.find((candidate) => candidate.id === id);
    if (
      !id ||
      !item ||
      !item.canRecoverTranscription ||
      this.data.recoveringId
    ) {
      return;
    }
    setTranscriptsData(this, { recoveringId: id, error: "" });
    try {
      const recovered = await recoverMemoirVoiceTranscript(id);
      setTranscriptsData(this, {
        items: replaceItem(this.data.items, recovered, this.data.searchQuery),
      });
      trackAuditAction("transcript.recover_transcription", {}, { projectId: recovered.project_id });
      wx.showToast({ title: "录音已转成文字", icon: "success" });
    } catch (error) {
      setTranscriptsData(this, { error: error instanceof Error ? error.message : "录音转文字失败" });
    } finally {
      setTranscriptsData(this, { recoveringId: "" });
    }
  },

  onUnload() {
    if (this.searchDebounceTimer !== null) {
      clearTimeout(this.searchDebounceTimer);
      this.searchDebounceTimer = null;
    }
    this.cancelVoiceSearch();
  },

  openLogin() {
    navigateToLogin();
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
