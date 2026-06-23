import type { MemoirVoiceTranscriptItem } from "../../domain/voice-chat";
import {
  deleteMemoirVoiceTranscript,
  downloadMemoirVoiceAudio,
  listMemoirVoiceTranscripts,
  updateMemoirVoiceTranscript,
} from "../../services/memoir-voice-api";
import { configureAudiblePlayback } from "../../services/audio-playback";
import { getMemoirIdentity } from "../../services/auth";
import { ApiError } from "../../services/request";
import { formatBeijingDateTime } from "../../services/time";
import {
  clearTranscriptNavigationTarget,
  readTranscriptNavigationTarget,
} from "../../services/transcript-navigation";

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

interface TranscriptTextSegment {
  text: string;
  highlight: boolean;
}

interface TranscriptItemView extends MemoirVoiceTranscriptItem {
  editingText: string;
  editingCharCount: number;
  createdAtText: string;
  anchorId: string;
  audioText: string;
  bookInclusionText: string;
  isIncludedInBook: boolean;
  canPlayAudio: boolean;
  questionSegments: TranscriptTextSegment[];
  rawSegments: TranscriptTextSegment[];
  displaySegments: TranscriptTextSegment[];
  changed: boolean;
  isEditing: boolean;
}

interface TranscriptsData {
  loading: boolean;
  needsLogin: boolean;
  savingId: string;
  deletingId: string;
  playingId: string;
  error: string;
  items: TranscriptItemView[];
  total: number;
  limit: number;
  offset: number;
  searchInput: string;
  searchQuery: string;
  searchResultText: string;
  activeProjectId: string;
  projectTitle: string;
  highlightedId: string;
}

const DEFAULT_LIMIT = 30;
const SEARCH_DEBOUNCE_MS = 300;

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
  return formatBeijingDateTime(item.created_at) || "转写记录";
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

function transcriptAnchorId(id: string): string {
  return `transcript-${id.replace(/[^A-Za-z0-9_-]/g, "-")}`;
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

function toViewItem(item: MemoirVoiceTranscriptItem, searchQuery = ""): TranscriptItemView {
  const editingText = item.transcript_edited || item.transcript_raw;
  const isIncludedInBook = Boolean(item.is_included_in_book || item.included_chapters?.length);
  return {
    ...item,
    editingText,
    editingCharCount: editingText.trim().length,
    createdAtText: transcriptCreatedAtText(item),
    anchorId: transcriptAnchorId(item.id),
    audioText: transcriptAudioText(item),
    bookInclusionText: transcriptBookInclusionText(item),
    isIncludedInBook,
    canPlayAudio: Boolean(item.audio_file_id || item.question_audio_file_id),
    questionSegments: highlightTranscriptText(item.question_text, searchQuery),
    rawSegments: highlightTranscriptText(item.transcript_raw, searchQuery),
    displaySegments: highlightTranscriptText(item.display_text, searchQuery),
    changed: editingText !== (item.transcript_edited || item.transcript_raw),
    isEditing: false,
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

function replaceItem(items: TranscriptItemView[], next: MemoirVoiceTranscriptItem, searchQuery = ""): TranscriptItemView[] {
  return items.map((item) => (item.id === next.id ? toViewItem(next, searchQuery) : item));
}

function removeItem(items: TranscriptItemView[], id: string): TranscriptItemView[] {
  return items.filter((item) => item.id !== id);
}

function confirmDelete(item: TranscriptItemView): Promise<boolean> {
  return new Promise((resolve) => {
    const preview = item.display_text || item.transcript_raw || "这条转写记录";
    wx.showModal({
      title: "删除转写",
      content: `确定删除“${preview.slice(0, 24)}${preview.length > 24 ? "..." : ""}”？删除后不会再出现在转写记录和访谈上下文中。`,
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
  applySearch(rawQuery?: string): Promise<void>;
  beginEdit(event: DatasetEvent): void;
  changeText(event: TextAreaEvent): void;
  cancelEdit(event: DatasetEvent): void;
  saveTranscript(event: DatasetEvent): Promise<void>;
  resetToRaw(event: DatasetEvent): void;
  deleteTranscript(event: DatasetEvent): Promise<void>;
  playAudio(event: DatasetEvent): Promise<void>;
  playAudioFile(audioFileId: string, token: number): Promise<void>;
  stopPlayback(): void;
  onUnload(): void;
  openHome(): void;
  openLogin(): void;
  createProject(): void;
  revealStoredTranscriptTarget(): Promise<void>;
  ensureTranscriptTargetLoaded(targetId: string): Promise<TranscriptItemView | null>;
  scrollToTranscript(item: TranscriptItemView): void;
  audio: WechatMiniprogram.InnerAudioContext | null;
  playToken: number;
  searchDebounceTimer: number | null;
}>({
  audio: null,
  playToken: 0,
  searchDebounceTimer: null,

  data: {
    loading: false,
    needsLogin: false,
    savingId: "",
    deletingId: "",
    playingId: "",
    error: "",
    items: [],
    total: 0,
    limit: DEFAULT_LIMIT,
    offset: 0,
    searchInput: "",
    searchQuery: "",
    searchResultText: "",
    activeProjectId: "",
    projectTitle: "",
    highlightedId: "",
  },

  async onShow() {
    await this.loadTranscripts(true);
    await this.revealStoredTranscriptTarget();
  },

  async loadTranscripts(reset = false) {
    if (this.data.loading) {
      return;
    }
    const offset = reset ? 0 : this.data.offset;
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
        this.setData({
          activeProjectId: "",
          projectTitle: projectTitle || "尚未创建回忆录",
          items: [],
          total: 0,
          offset: 0,
          searchResultText: "",
          highlightedId: "",
        });
        return;
      }
      const searchQuery = this.data.searchQuery;
      const response = await listMemoirVoiceTranscripts({
        projectId: activeProjectId,
        query: searchQuery || undefined,
        limit: this.data.limit,
        offset,
      });
      const nextItems = response.items.map((item) => toViewItem(item, searchQuery));
      this.setData({
        needsLogin: false,
        activeProjectId,
        projectTitle,
        items: reset ? nextItems : [...this.data.items, ...nextItems],
        total: response.total,
        offset: offset + nextItems.length,
        searchResultText: searchQuery ? `找到 ${response.total} 条相关记录` : "",
        highlightedId: reset ? "" : this.data.highlightedId,
      });
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        this.setData({
          needsLogin: true,
          activeProjectId: "",
          projectTitle: "我的回忆录",
          items: [],
          total: 0,
          offset: 0,
          searchResultText: "",
          highlightedId: "",
        });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "转写记录加载失败" });
    } finally {
      this.setData({ loading: false });
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

  async applySearch(rawQuery) {
    if (this.searchDebounceTimer !== null) {
      clearTimeout(this.searchDebounceTimer);
      this.searchDebounceTimer = null;
    }
    const nextInput = rawQuery ?? this.data.searchInput;
    const searchQuery = nextInput.trim();
    if (searchQuery === this.data.searchQuery && nextInput === this.data.searchInput) {
      return;
    }
    this.stopPlayback();
    this.setData({
      searchInput: nextInput,
      searchQuery,
      searchResultText: "",
      items: [],
      total: 0,
      offset: 0,
      highlightedId: "",
    });
    await this.loadTranscripts(true);
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
    items[index] = toViewItem(items[index], this.data.searchQuery);
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
      this.setData({ error: "转写内容不能为空" });
      return;
    }
    this.setData({ savingId: id, error: "" });
    try {
      const updated = await updateMemoirVoiceTranscript(id, content);
      this.setData({ items: replaceItem(this.data.items, updated, this.data.searchQuery) });
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
    this.playToken += 1;
    const token = this.playToken;
    this.setData({ playingId: id, error: "" });
    try {
      for (const audioFileId of audioFileIds) {
        if (token !== this.playToken) {
          return;
        }
        await this.playAudioFile(audioFileId, token);
      }
      if (token === this.playToken) {
        this.setData({ playingId: "" });
      }
    } catch (error) {
      if (token === this.playToken) {
        this.setData({
          playingId: "",
          error: error instanceof Error ? error.message : "音频播放失败",
        });
      }
    }
  },

  async playAudioFile(audioFileId, token) {
    const tempFilePath = await downloadMemoirVoiceAudio(audioFileId);
    if (token !== this.playToken) {
      return;
    }
    await new Promise<void>((resolve, reject) => {
      const audio = wx.createInnerAudioContext();
      this.audio = audio;
      const cleanup = () => {
        audio.destroy();
        if (this.audio === audio) {
          this.audio = null;
        }
      };
      configureAudiblePlayback(audio);
      audio.src = tempFilePath;
      audio.onEnded(() => {
        cleanup();
        resolve();
      });
      audio.onStop(() => {
        if (this.audio === audio) {
          this.audio = null;
        }
        resolve();
      });
      audio.onError((error) => {
        cleanup();
        reject(new Error(error.errMsg || "音频播放失败"));
      });
      audio.play();
    });
  },

  stopPlayback() {
    this.playToken += 1;
    if (this.audio) {
      this.audio.stop();
      this.audio.destroy();
      this.audio = null;
    }
    if (this.data.playingId) {
      this.setData({ playingId: "" });
    }
  },

  onUnload() {
    if (this.searchDebounceTimer !== null) {
      clearTimeout(this.searchDebounceTimer);
      this.searchDebounceTimer = null;
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
    if (this.data.searchQuery) {
      this.setData({ searchInput: "", searchQuery: "", searchResultText: "" });
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
