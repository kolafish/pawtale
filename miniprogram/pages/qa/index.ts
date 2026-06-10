import type { MemoirStoryCard, MemoirTtsSourceType } from "../../domain/memoir";
import { deleteMemoirAnswer, getMemoirStories } from "../../services/memoir";
import {
  readMemoirStoriesSnapshot,
  removeMemoirCachedStoryAnswers,
  shouldRefreshMemoirStoriesSnapshot,
  type MemoirStoriesSnapshot,
  writeMemoirStoriesSnapshot,
} from "../../stores/memoir-cache";
import {
  cycleMemoirTtsPlaybackSpeed,
  memoirTtsSourceKey,
  pauseMemoirTtsPlayback,
  playMemoirTtsPlayback,
  readMemoirTtsPlaybackState,
  subscribeMemoirTtsPlayback,
} from "../../services/memoir-tts-player";
import { memoirTtsViewPatch } from "../../services/memoir-tts-view";

interface DatasetEvent {
  currentTarget: {
    dataset: {
      id?: string;
    };
  };
}

interface TtsSourceEvent {
  currentTarget: {
    dataset: {
      sourceType?: MemoirTtsSourceType;
      sourceId?: string;
      label?: string;
    };
  };
}

interface QaRecord {
  id: string;
  questionId: string;
  question: string;
  sessionTitle: string;
  answerPreview: string;
  audioLabel: string;
  hasAudio: boolean;
  createdAtText: string;
  selected: boolean;
  questionTtsSourceKey: string;
  answerTtsSourceKey: string;
}

interface QaData {
  loading: boolean;
  refreshing: boolean;
  deleting: boolean;
  selectionMode: boolean;
  error: string;
  totalCount: number;
  audioCount: number;
  selectedCount: number;
  allSelected: boolean;
  records: QaRecord[];
  ttsStatus: string;
  ttsSourceKey: string;
  ttsMessage: string;
  ttsError: string;
  ttsSpeed: number;
  ttsSpeedLabel: string;
  ttsPlaying: boolean;
  ttsBusy: boolean;
}

interface QaMethods {
  unsubscribeTtsPlayback?: () => void;
  onLoad(): Promise<void>;
  onShow(): void;
  onUnload(): void;
  registerTtsPlayback(): void;
  hydrateFromCache(): MemoirStoriesSnapshot;
  loadQaRecords(): Promise<void>;
  openRecord(event: DatasetEvent): void;
  toggleSelectionMode(): void;
  toggleRecordSelection(event: DatasetEvent): void;
  selectAllRecords(): void;
  deleteSelectedRecords(): Promise<void>;
  applyRecords(records: QaRecord[]): void;
  toggleTtsSource(event: TtsSourceEvent): Promise<void>;
  cycleTtsSpeed(): void;
  openHome(): void;
  openWorkbench(): void;
  openQa(): void;
  openProfile(): void;
}

function formatDate(value: string): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const month = date.getMonth() + 1;
  const day = date.getDate();
  const hour = date.getHours();
  const minute = date.getMinutes();
  return `${month}月${day}日 ${hour}:${minute < 10 ? "0" : ""}${minute}`;
}

function formatDuration(seconds?: number | null): string {
  if (typeof seconds !== "number" || seconds <= 0) {
    return "";
  }
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}:${rest < 10 ? "0" : ""}${rest}`;
}

function audioLabel(hasAudio: boolean, durationText: string): string {
  if (!hasAudio) {
    return "无录音";
  }
  return durationText ? `录音 ${durationText}` : "有录音";
}

function recordFromStory(story: MemoirStoryCard): QaRecord {
  const audioDurationText = formatDuration(story.duration_seconds);
  const hasAudio = Boolean(story.audio_file_id || story.duration_seconds);
  return {
    id: story.answer_id,
    questionId: story.question_id,
    question: story.question_prompt,
    sessionTitle: story.session_title,
    answerPreview: story.answer_preview,
    audioLabel: audioLabel(hasAudio, audioDurationText),
    hasAudio,
    createdAtText: formatDate(story.created_at),
    selected: false,
    questionTtsSourceKey: memoirTtsSourceKey({ source_type: "question", source_id: story.question_id }),
    answerTtsSourceKey: memoirTtsSourceKey({ source_type: "answer", source_id: story.answer_id }),
  };
}

Page<QaData, QaMethods>({
  data: {
    loading: false,
    refreshing: false,
    deleting: false,
    selectionMode: false,
    error: "",
    totalCount: 0,
    audioCount: 0,
    selectedCount: 0,
    allSelected: false,
    records: [],
    ...memoirTtsViewPatch(readMemoirTtsPlaybackState()),
  },

  async onLoad() {
    this.registerTtsPlayback();
    const snapshot = this.hydrateFromCache();
    if (shouldRefreshMemoirStoriesSnapshot(snapshot)) {
      await this.loadQaRecords();
    }
  },

  onShow() {
    const snapshot = this.hydrateFromCache();
    if (!this.data.refreshing && shouldRefreshMemoirStoriesSnapshot(snapshot)) {
      void this.loadQaRecords();
    }
  },

  onUnload() {
    if (this.unsubscribeTtsPlayback) {
      this.unsubscribeTtsPlayback();
      this.unsubscribeTtsPlayback = undefined;
    }
  },

  registerTtsPlayback() {
    if (this.unsubscribeTtsPlayback) {
      return;
    }
    this.unsubscribeTtsPlayback = subscribeMemoirTtsPlayback((playbackState) => {
      this.setData(memoirTtsViewPatch(playbackState));
    });
  },

  hydrateFromCache() {
    const snapshot = readMemoirStoriesSnapshot();
    if (!snapshot.hasStories) {
      return snapshot;
    }
    const records = snapshot.stories.map(recordFromStory);
    this.applyRecords(records);
    this.setData({
      totalCount: snapshot.total,
      audioCount: records.filter((record) => record.hasAudio).length,
    });
    return snapshot;
  },

  async loadQaRecords() {
    if (this.data.refreshing) {
      return;
    }
    const loading = !this.data.records.length;
    this.setData({ loading, refreshing: true, error: "" });
    try {
      const result = await getMemoirStories();
      const records = result.stories.map(recordFromStory);
      writeMemoirStoriesSnapshot(result.stories);
      this.applyRecords(records);
      this.setData({
        totalCount: result.total,
        audioCount: records.filter((record) => record.hasAudio).length,
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "问答记录加载失败" });
    } finally {
      this.setData({ loading: false, refreshing: false });
    }
  },

  openRecord(event) {
    const answerId = event.currentTarget.dataset.id;
    if (!answerId) {
      return;
    }
    if (this.data.selectionMode) {
      this.toggleRecordSelection(event);
      return;
    }
    wx.navigateTo({ url: `/pages/qa-detail/index?answerId=${encodeURIComponent(answerId)}` });
  },

  toggleSelectionMode() {
    const selectionMode = !this.data.selectionMode;
    const records = selectionMode
      ? this.data.records
      : this.data.records.map((record) => ({ ...record, selected: false }));
    this.applyRecords(records);
    this.setData({ selectionMode });
  },

  toggleRecordSelection(event) {
    const answerId = event.currentTarget.dataset.id;
    if (!answerId) {
      return;
    }
    const records = this.data.records.map((record) => (
      record.id === answerId ? { ...record, selected: !record.selected } : record
    ));
    this.applyRecords(records);
  },

  selectAllRecords() {
    const shouldSelectAll = this.data.selectedCount < this.data.records.length;
    this.applyRecords(this.data.records.map((record) => ({ ...record, selected: shouldSelectAll })));
  },

  async deleteSelectedRecords() {
    const selected = this.data.records.filter((record) => record.selected);
    if (!selected.length || this.data.deleting) {
      return;
    }
    const confirmed = await new Promise<boolean>((resolve) => {
      wx.showModal({
        title: "删除问答",
        content: `确定删除选中的 ${selected.length} 条问答吗？删除后问题会回到待回答。`,
        confirmText: "删除",
        confirmColor: "#be123c",
        success: (result) => resolve(result.confirm),
        fail: () => resolve(false),
      });
    });
    if (!confirmed) {
      return;
    }

    this.setData({ deleting: true, error: "" });
    try {
      for (const record of selected) {
        await deleteMemoirAnswer(record.id);
      }
      const stories = removeMemoirCachedStoryAnswers(selected.map((record) => record.id));
      wx.showToast({ title: "已删除", icon: "success" });
      this.setData({ selectionMode: false });
      if (stories.length || this.data.records.length === selected.length) {
        const records = stories.map(recordFromStory);
        this.applyRecords(records);
        this.setData({
          totalCount: stories.length,
          audioCount: records.filter((record) => record.hasAudio).length,
        });
        return;
      }
      await this.loadQaRecords();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "删除失败" });
    } finally {
      this.setData({ deleting: false });
    }
  },

  applyRecords(records) {
    const selectedCount = records.filter((record) => record.selected).length;
    this.setData({
      records,
      selectedCount,
      allSelected: Boolean(records.length && selectedCount === records.length),
    });
  },

  async toggleTtsSource(event) {
    const sourceType = event.currentTarget.dataset.sourceType;
    const sourceId = event.currentTarget.dataset.sourceId || "";
    if (!sourceType || !sourceId || this.data.ttsBusy) {
      return;
    }
    const sourceKey = memoirTtsSourceKey({ source_type: sourceType, source_id: sourceId });
    if (this.data.ttsSourceKey === sourceKey && this.data.ttsPlaying) {
      pauseMemoirTtsPlayback();
      return;
    }
    await playMemoirTtsPlayback({
      source_type: sourceType,
      source_id: sourceId,
      label: event.currentTarget.dataset.label || "",
    });
  },

  cycleTtsSpeed() {
    cycleMemoirTtsPlaybackSpeed();
  },

  openHome() {
    wx.redirectTo({ url: "/pages/home/index" });
  },

  openWorkbench() {
    wx.redirectTo({ url: "/pages/workbench/index" });
  },

  openQa() {
  },

  openProfile() {
    wx.redirectTo({ url: "/pages/profile/index" });
  },
});
