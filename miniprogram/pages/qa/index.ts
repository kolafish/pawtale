import type { MemoirStoryCard } from "../../domain/memoir";
import { deleteMemoirAnswer, getMemoirStories } from "../../services/memoir";
import {
  readMemoirStoriesSnapshot,
  removeMemoirCachedStoryAnswers,
  shouldRefreshMemoirStoriesSnapshot,
  type MemoirStoriesSnapshot,
  writeMemoirStoriesSnapshot,
} from "../../stores/memoir-cache";

interface DatasetEvent {
  currentTarget: {
    dataset: {
      id?: string;
    };
  };
}

interface QaRecord {
  id: string;
  question: string;
  sessionTitle: string;
  answerPreview: string;
  audioLabel: string;
  hasAudio: boolean;
  createdAtText: string;
  selected: boolean;
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
}

interface QaMethods {
  onLoad(): Promise<void>;
  onShow(): void;
  hydrateFromCache(): MemoirStoriesSnapshot;
  loadQaRecords(): Promise<void>;
  openRecord(event: DatasetEvent): void;
  toggleSelectionMode(): void;
  toggleRecordSelection(event: DatasetEvent): void;
  selectAllRecords(): void;
  deleteSelectedRecords(): Promise<void>;
  applyRecords(records: QaRecord[]): void;
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
    question: story.question_prompt,
    sessionTitle: story.session_title,
    answerPreview: story.answer_preview,
    audioLabel: audioLabel(hasAudio, audioDurationText),
    hasAudio,
    createdAtText: formatDate(story.created_at),
    selected: false,
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
  },

  async onLoad() {
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
