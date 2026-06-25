import type {
  MemoirBookPlan,
  MemoirGuidanceMode,
  MemoirVoiceTranscriptIncludedFilter,
  MemoirVoiceTranscriptItem,
  MemoirVoiceSearchQueryFilters,
  MemoirVoiceSearchQueryParseResponse,
} from "../../domain/voice-chat";
import type { ChapterPickerOption } from "../../services/memoir-book-source-actions";
import {
  assignMemoirBookSources,
  deleteMemoirVoiceTranscript,
  getMemoirBookPlan,
  listMemoirVoiceTranscripts,
  moveMemoirBookSource,
  parseMemoirVoiceSearchQuery,
  updateMemoirVoiceTranscript,
} from "../../services/memoir-voice-api";
import { AudioPlaybackController } from "../../services/audio-playback";
import { getMemoirIdentity } from "../../services/auth";
import {
  sourceAssignmentRequest,
  transcriptChapterAssignOptions,
  transcriptChapterMoveOptions,
  transcriptSourceMoveRequest,
} from "../../services/memoir-book-source-actions";
import { ApiError } from "../../services/request";
import { formatBeijingDateTimeShort } from "../../services/time";
import {
  clearTranscriptNavigationTarget,
  readTranscriptNavigationTarget,
} from "../../services/transcript-navigation";
import {
  recordMemoirVoiceSearchQuery,
  stopMemoirVoiceSearchRecording,
} from "../../services/memoir-voice-search";

interface DatasetEvent {
  currentTarget: {
    dataset: {
      id?: string;
    };
  };
}

interface TextAreaEvent extends DatasetEvent {
  detail: {
    value: string;
  };
}

interface SearchInputEvent {
  detail: {
    value: string;
  };
}

interface PickerEvent extends DatasetEvent {
  detail: {
    value: string | number;
  };
}

interface TranscriptTextSegment {
  text: string;
  highlight: boolean;
}

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
  editingText: string;
  editingCharCount: number;
  createdAtText: string;
  anchorId: string;
  audioText: string;
  bookInclusionText: string;
  isIncludedInBook: boolean;
  canAssignChapterSource: boolean;
  chapterAssignOptions: ChapterPickerOption[];
  canMoveChapterSource: boolean;
  chapterMoveOptions: ChapterPickerOption[];
  canPlayAudio: boolean;
  questionSegments: TranscriptTextSegment[];
  rawSegments: TranscriptTextSegment[];
  displaySegments: TranscriptTextSegment[];
  detailsCollapsible: boolean;
  detailsExpanded: boolean;
  detailsToggleText: string;
  changed: boolean;
  isEditing: boolean;
}

interface TranscriptsData {
  loading: boolean;
  needsLogin: boolean;
  savingId: string;
  deletingId: string;
  assigningSourceId: string;
  movingSourceId: string;
  playingId: string;
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
  bookPlan: MemoirBookPlan | null;
  bookPlanProjectId: string;
  highlightedId: string;
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

function formatDurationSeconds(value: number): string {
  const seconds = Math.max(0, Math.round(value));
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return remainingSeconds ? `${minutes} 分 ${remainingSeconds} 秒` : `${minutes} 分`;
}

function formatDuration(item: MemoirVoiceTranscriptItem): string {
  if (item.audio_duration_seconds !== null && item.audio_duration_seconds !== undefined) {
    return formatDurationSeconds(item.audio_duration_seconds);
  }
  if (item.start_ms !== null && item.start_ms !== undefined && item.end_ms !== null && item.end_ms !== undefined) {
    const seconds = Math.max(0, Math.round((item.end_ms - item.start_ms) / 1000));
    return formatDurationSeconds(seconds);
  }
  return "";
}

export function transcriptAudioText(item: MemoirVoiceTranscriptItem): string {
  const duration = formatDuration(item);
  return duration ? `时长 ${duration}` : "时长未知";
}

export function transcriptCreatedAtText(item: Pick<MemoirVoiceTranscriptItem, "created_at">): string {
  return formatBeijingDateTimeShort(item.created_at) || "讲述记录";
}

export function transcriptRecordNumber(total: number, offset: number, index: number): number {
  return Math.max(1, total - offset - index);
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
    chips.push({ text: "自由讲述" });
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

export function transcriptBookInclusionText(item: MemoirVoiceTranscriptItem): string {
  const chapters = [...(item.included_chapters || [])]
    .sort((left, right) => left.sort_order - right.sort_order)
    .map((chapter) => chapter.title.trim())
    .filter(Boolean);
  if (!chapters.length) {
    return "未收录到章节";
  }
  const visible = chapters.slice(0, 2).join("、");
  const suffix = chapters.length > 2 ? "等章节" : "";
  return `已收录：${visible}${suffix}`;
}

function toViewItem(
  item: MemoirVoiceTranscriptItem,
  searchQuery = "",
  bookPlan: MemoirBookPlan | null = null,
  recordNumber = 1,
): TranscriptItemView {
  const editingText = item.transcript_edited || item.transcript_raw;
  const isIncludedInBook = Boolean(item.is_included_in_book || item.included_chapters?.length);
  const rawView = transcriptTextView(item.transcript_raw, searchQuery);
  const displayView = transcriptTextView(item.display_text, searchQuery);
  const chapterAssignOptions = transcriptChapterAssignOptions(item, bookPlan);
  const chapterMoveOptions = transcriptChapterMoveOptions(item, bookPlan);
  return {
    ...item,
    recordNumber,
    editingText,
    editingCharCount: editingText.trim().length,
    createdAtText: transcriptCreatedAtText(item),
    anchorId: transcriptAnchorId(item.id),
    audioText: transcriptAudioText(item),
    bookInclusionText: transcriptBookInclusionText(item),
    isIncludedInBook,
    canAssignChapterSource: chapterAssignOptions.length > 0,
    chapterAssignOptions,
    canMoveChapterSource: chapterMoveOptions.length > 0,
    chapterMoveOptions,
    canPlayAudio: Boolean(item.audio_file_id || item.question_audio_file_id),
    questionSegments: highlightTranscriptText(item.question_text, searchQuery),
    rawSegments: rawView.segments,
    displaySegments: displayView.segments,
    detailsCollapsible: rawView.collapsible || displayView.collapsible,
    detailsExpanded: false,
    detailsToggleText: "展开全部",
    changed: editingText !== (item.transcript_edited || item.transcript_raw),
    isEditing: false,
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
    const preview = item.display_text || item.transcript_raw || "这条讲述记录";
    wx.showModal({
      title: "删除讲述记录",
      content: `确定删除“${preview.slice(0, 24)}${preview.length > 24 ? "..." : ""}”？删除后不会再出现在讲述记录和访谈上下文中。`,
      confirmText: "删除",
      confirmColor: "#b4234a",
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    });
  });
}

Page<TranscriptsData, {
  onShow(): Promise<void>;
  loadTranscripts(reset?: boolean): Promise<void>;
  loadMore(): Promise<void>;
  onSearchInput(event: SearchInputEvent): void;
  onSearchConfirm(): Promise<void>;
  clearSearch(): Promise<void>;
  applySearch(
    rawQuery?: string,
    filters?: Partial<TranscriptSearchFilters>,
    parsed?: MemoirVoiceSearchQueryParseResponse,
  ): Promise<void>;
  startVoiceSearch(): Promise<void>;
  toggleTranscriptText(event: DatasetEvent): void;
  assignTranscriptChapter(event: PickerEvent): Promise<void>;
  moveTranscriptChapter(event: PickerEvent): Promise<void>;
  beginEdit(event: DatasetEvent): void;
  changeText(event: TextAreaEvent): void;
  cancelEdit(event: DatasetEvent): void;
  saveTranscript(event: DatasetEvent): Promise<void>;
  resetToRaw(event: DatasetEvent): void;
  deleteTranscript(event: DatasetEvent): Promise<void>;
  playAudio(event: DatasetEvent): Promise<void>;
  stopPlayback(): void;
  onUnload(): void;
  openHome(): void;
  openLogin(): void;
  createProject(): void;
  revealStoredTranscriptTarget(): Promise<void>;
  ensureTranscriptTargetLoaded(targetId: string): Promise<TranscriptItemView | null>;
  scrollToTranscript(item: TranscriptItemView): void;
  audioPlayback: AudioPlaybackController;
  loadToken: number;
  searchDebounceTimer: number | null;
}>({
  audioPlayback: new AudioPlaybackController(),
  loadToken: 0,
  searchDebounceTimer: null,

  data: {
    loading: false,
    needsLogin: false,
    savingId: "",
    deletingId: "",
    assigningSourceId: "",
    movingSourceId: "",
    playingId: "",
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
    bookPlan: null,
    bookPlanProjectId: "",
    highlightedId: "",
  },

  async onShow() {
    await this.loadTranscripts(true);
    await this.revealStoredTranscriptTarget();
  },

  async loadTranscripts(reset = false) {
    if (this.data.loading && !reset) {
      return;
    }
    this.loadToken += 1;
    const requestToken = this.loadToken;
    const offset = reset ? 0 : this.data.offset;
    const searchQuery = this.data.searchQuery;
    const searchFilters = normalizedSearchFilters(this.data.searchFilters);
    const hasSearchCriteria = searchCriteriaActive(searchQuery, searchFilters);
    this.setData({ loading: true, error: "" });
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
        this.setData({
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
      this.setData({
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
    } catch (error) {
      if (requestToken !== this.loadToken) {
        return;
      }
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        this.setData({
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
      this.setData({ error: error instanceof Error ? error.message : "讲述记录加载失败" });
    } finally {
      if (requestToken === this.loadToken) {
        this.setData({ loading: false });
      }
    }
  },

  async loadMore() {
    if (this.data.items.length >= this.data.total) {
      return;
    }
    await this.loadTranscripts(false);
  },

  onSearchInput(event) {
    const value = event.detail.value;
    this.setData({ searchInput: value });
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

  async applySearch(rawQuery, filters, parsed) {
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
    this.stopPlayback();
    this.setData({
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
    this.stopPlayback();
    this.setData({
      voiceSearchRecording: true,
      voiceSearchTranscribing: false,
      voiceSearchStatusText: "正在听你说搜索内容",
      error: "",
    });
    try {
      const query = await recordMemoirVoiceSearchQuery({
        onRecordingEnd: () => {
          this.setData({
            voiceSearchRecording: false,
            voiceSearchTranscribing: true,
            voiceSearchStatusText: "正在识别语音搜索",
          });
        },
      });
      this.setData({ voiceSearchStatusText: "正在理解搜索需求" });
      const parsed = await parseMemoirVoiceSearchQuery(query);
      const parsedFilters = filtersFromParsedSearch(parsed);
      await this.applySearch(parsed.query || query, parsedFilters, parsed);
      wx.showToast({ title: "已识别", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "语音搜索失败" });
    } finally {
      this.setData({
        voiceSearchRecording: false,
        voiceSearchTranscribing: false,
        voiceSearchStatusText: "",
      });
    }
  },

  toggleTranscriptText(event) {
    const id = event.currentTarget.dataset.id || "";
    if (!id) {
      return;
    }
    const items = this.data.items.map((item) => {
      if (item.id !== id) {
        return item;
      }
      return withTranscriptExpansion(item, this.data.searchQuery, !item.detailsExpanded);
    });
    this.setData({ items });
  },

  async assignTranscriptChapter(event) {
    const utteranceId = event.currentTarget.dataset.id || "";
    const item = this.data.items.find((candidate) => candidate.id === utteranceId);
    const optionIndex = Number(event.detail.value);
    const option = item?.chapterAssignOptions[optionIndex];
    if (
      !utteranceId
      || !item
      || !option
      || !this.data.activeProjectId
      || this.data.assigningSourceId
      || this.data.movingSourceId
    ) {
      return;
    }
    this.stopPlayback();
    this.setData({ assigningSourceId: utteranceId, error: "" });
    try {
      const updatedPlan = await assignMemoirBookSources(
        this.data.activeProjectId,
        sourceAssignmentRequest(option.value, utteranceId),
      );
      this.setData({ bookPlan: updatedPlan, bookPlanProjectId: this.data.activeProjectId });
      await this.loadTranscripts(true);
      wx.showToast({ title: "已归入章节", icon: "success" });
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "归入章节失败" });
    } finally {
      this.setData({ assigningSourceId: "" });
    }
  },

  async moveTranscriptChapter(event) {
    const utteranceId = event.currentTarget.dataset.id || "";
    const item = this.data.items.find((candidate) => candidate.id === utteranceId);
    const optionIndex = Number(event.detail.value);
    const option = item?.chapterMoveOptions[optionIndex];
    if (
      !utteranceId
      || !item
      || !option
      || !this.data.activeProjectId
      || this.data.assigningSourceId
      || this.data.movingSourceId
    ) {
      return;
    }
    this.stopPlayback();
    this.setData({ movingSourceId: utteranceId, error: "" });
    try {
      const plan = this.data.bookPlan || await loadTranscriptBookPlan(this.data.activeProjectId);
      const moveRequest = transcriptSourceMoveRequest(item, plan, option.value);
      if (!moveRequest.request) {
        this.setData({ error: moveRequest.error });
        return;
      }
      const updatedPlan = await moveMemoirBookSource(this.data.activeProjectId, moveRequest.request);
      this.setData({ bookPlan: updatedPlan, bookPlanProjectId: this.data.activeProjectId });
      await this.loadTranscripts(true);
      wx.showToast({ title: "素材已移动", icon: "success" });
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "素材移动失败" });
    } finally {
      this.setData({ movingSourceId: "" });
    }
  },

  beginEdit(event) {
    const id = event.currentTarget.dataset.id || "";
    const index = this.data.items.findIndex((item) => item.id === id);
    if (index < 0 || this.data.savingId || this.data.deletingId) {
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
    this.setData({ items, error: "" });
  },

  changeText(event) {
    const id = event.currentTarget.dataset.id || "";
    const index = this.data.items.findIndex((item) => item.id === id);
    if (index < 0) {
      return;
    }
    const items = [...this.data.items];
    items[index] = changedItem(items[index], event.detail.value);
    this.setData({ items });
  },

  cancelEdit(event) {
    const id = event.currentTarget.dataset.id || "";
    const index = this.data.items.findIndex((item) => item.id === id);
    if (index < 0 || this.data.savingId || this.data.deletingId) {
      return;
    }
    const items = [...this.data.items];
    items[index] = toViewItem(items[index], this.data.searchQuery, this.data.bookPlan, items[index].recordNumber);
    this.setData({ items, error: "" });
  },

  async saveTranscript(event) {
    const id = event.currentTarget.dataset.id || "";
    const item = this.data.items.find((candidate) => candidate.id === id);
    if (!item || this.data.savingId || this.data.deletingId) {
      return;
    }
    const content = item.editingText.trim();
    if (!content) {
      this.setData({ error: "整理文字不能为空" });
      return;
    }
    this.setData({ savingId: id, error: "" });
    try {
      const updated = await updateMemoirVoiceTranscript(id, content);
      this.setData({ items: replaceItem(this.data.items, updated, this.data.searchQuery, this.data.bookPlan) });
      wx.showToast({ title: "已保存", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "保存失败" });
    } finally {
      this.setData({ savingId: "" });
    }
  },

  resetToRaw(event) {
    const id = event.currentTarget.dataset.id || "";
    const index = this.data.items.findIndex((item) => item.id === id);
    if (index < 0) {
      return;
    }
    const items = [...this.data.items];
    items[index] = changedItem(items[index], items[index].transcript_raw);
    this.setData({ items });
  },

  async deleteTranscript(event) {
    const id = event.currentTarget.dataset.id || "";
    const item = this.data.items.find((candidate) => candidate.id === id);
    if (!id || !item || this.data.savingId || this.data.deletingId) {
      return;
    }
    const confirmed = await confirmDelete(item);
    if (!confirmed) {
      return;
    }
    if (this.data.playingId === id) {
      this.stopPlayback();
    }
    this.setData({ deletingId: id, error: "" });
    try {
      await deleteMemoirVoiceTranscript(id);
      this.setData({
        items: removeItem(this.data.items, id),
        total: Math.max(0, this.data.total - 1),
        offset: Math.max(0, this.data.offset - 1),
      });
      wx.showToast({ title: "已删除", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "删除失败" });
    } finally {
      this.setData({ deletingId: "" });
    }
  },

  async playAudio(event) {
    const id = event.currentTarget.dataset.id || "";
    const item = this.data.items.find((candidate) => candidate.id === id);
    const audioFileIds = [item?.question_audio_file_id, item?.audio_file_id].filter(Boolean) as string[];
    if (!audioFileIds.length) {
      this.setData({ error: "这条记录没有可播放的音频" });
      return;
    }
    if (this.data.playingId === id) {
      this.stopPlayback();
      return;
    }
    if (this.data.savingId || this.data.deletingId) {
      return;
    }
    this.stopPlayback();
    const token = this.audioPlayback.nextToken();
    this.setData({ playingId: id, error: "" });
    try {
      for (const audioFileId of audioFileIds) {
        if (!this.audioPlayback.isCurrentToken(token)) {
          return;
        }
        await this.audioPlayback.playFile(audioFileId, token);
      }
      if (this.audioPlayback.isCurrentToken(token)) {
        this.setData({ playingId: "" });
      }
    } catch (error) {
      if (this.audioPlayback.isCurrentToken(token)) {
        this.setData({
          playingId: "",
          error: error instanceof Error ? error.message : "音频播放失败",
        });
      }
    }
  },

  stopPlayback() {
    this.audioPlayback.stop();
    if (this.data.playingId) {
      this.setData({ playingId: "" });
    }
  },

  onUnload() {
    if (this.searchDebounceTimer !== null) {
      clearTimeout(this.searchDebounceTimer);
      this.searchDebounceTimer = null;
    }
    if (this.data.voiceSearchRecording) {
      stopMemoirVoiceSearchRecording();
    }
    this.stopPlayback();
  },

  openHome() {
    this.stopPlayback();
    wx.switchTab({ url: "/pages/home/index" });
  },

  openLogin() {
    this.stopPlayback();
    wx.switchTab({ url: "/pages/me/index" });
  },

  createProject() {
    this.stopPlayback();
    wx.navigateTo({ url: "/pages/project-edit/index" });
  },

  async revealStoredTranscriptTarget() {
    const targetId = readTranscriptNavigationTarget();
    if (!targetId) {
      return;
    }
    clearTranscriptNavigationTarget();
    if (this.data.hasSearchCriteria) {
      this.setData({
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
    this.setData({ highlightedId: item.id });
    this.scrollToTranscript(item);
  },

  async ensureTranscriptTargetLoaded(targetId) {
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

  scrollToTranscript(item) {
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
