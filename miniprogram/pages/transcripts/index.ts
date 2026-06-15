import type { MemoirVoiceTranscriptItem } from "../../domain/voice-chat";
import {
  cleanupMemoirVoiceTranscript,
  downloadMemoirVoiceAudio,
  listMemoirVoiceTranscripts,
  updateMemoirVoiceTranscript,
} from "../../services/memoir-voice-api";
import { ApiError } from "../../services/request";

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

interface TranscriptItemView extends MemoirVoiceTranscriptItem {
  editingText: string;
  editingCharCount: number;
  createdText: string;
  audioText: string;
  canPlayAudio: boolean;
  changed: boolean;
}

interface TranscriptsData {
  loading: boolean;
  savingId: string;
  cleaningId: string;
  playingId: string;
  error: string;
  items: TranscriptItemView[];
  total: number;
  limit: number;
  offset: number;
}

const DEFAULT_LIMIT = 30;

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${month}-${day} ${hour}:${minute}`;
}

function formatBytes(value?: number | null): string {
  if (!value || value <= 0) {
    return "";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${Math.round(value / 1024)} KB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatDuration(item: MemoirVoiceTranscriptItem): string {
  if (item.audio_duration_seconds !== null && item.audio_duration_seconds !== undefined) {
    return `${item.audio_duration_seconds}s`;
  }
  if (item.start_ms !== null && item.start_ms !== undefined && item.end_ms !== null && item.end_ms !== undefined) {
    const seconds = Math.max(0, Math.round((item.end_ms - item.start_ms) / 1000));
    return `${seconds}s`;
  }
  return "";
}

function audioText(item: MemoirVoiceTranscriptItem): string {
  if (!item.audio_file_id) {
    return "无音频文件";
  }
  const parts = ["音频已保存"];
  const duration = formatDuration(item);
  const size = formatBytes(item.audio_size_bytes);
  if (duration) {
    parts.push(duration);
  }
  if (size) {
    parts.push(size);
  }
  if (item.audio_mime_type === "audio/pcm") {
    parts.push("PCM");
  }
  return parts.join(" · ");
}

function toViewItem(item: MemoirVoiceTranscriptItem): TranscriptItemView {
  const editingText = item.transcript_edited || item.transcript_raw;
  return {
    ...item,
    editingText,
    editingCharCount: editingText.trim().length,
    createdText: formatDate(item.created_at),
    audioText: audioText(item),
    canPlayAudio: Boolean(item.audio_file_id),
    changed: editingText !== (item.transcript_edited || item.transcript_raw),
  };
}

function changedItem(item: TranscriptItemView, editingText: string): TranscriptItemView {
  return {
    ...item,
    editingText,
    editingCharCount: editingText.trim().length,
    changed: editingText.trim() !== (item.transcript_edited || item.transcript_raw).trim(),
  };
}

function replaceItem(items: TranscriptItemView[], next: MemoirVoiceTranscriptItem): TranscriptItemView[] {
  return items.map((item) => (item.id === next.id ? toViewItem(next) : item));
}

function confirmCleanup(): Promise<boolean> {
  return new Promise((resolve) => {
    wx.showModal({
      title: "AI 清理转写",
      content: "会忠实保留原意，只去掉语气词和无信息重复表达。",
      confirmText: "开始清理",
      confirmColor: "#9f4a3c",
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    });
  });
}

Page<TranscriptsData, {
  onLoad(): Promise<void>;
  loadTranscripts(reset?: boolean): Promise<void>;
  loadMore(): Promise<void>;
  changeText(event: TextAreaEvent): void;
  saveTranscript(event: DatasetEvent): Promise<void>;
  cleanupTranscript(event: DatasetEvent): Promise<void>;
  resetToRaw(event: DatasetEvent): void;
  playAudio(event: DatasetEvent): Promise<void>;
  stopPlayback(): void;
  onUnload(): void;
  openHome(): void;
  audio: WechatMiniprogram.InnerAudioContext | null;
  playToken: number;
}>({
  audio: null,
  playToken: 0,

  data: {
    loading: false,
    savingId: "",
    cleaningId: "",
    playingId: "",
    error: "",
    items: [],
    total: 0,
    limit: DEFAULT_LIMIT,
    offset: 0,
  },

  async onLoad() {
    await this.loadTranscripts(true);
  },

  async loadTranscripts(reset = false) {
    if (this.data.loading) {
      return;
    }
    const offset = reset ? 0 : this.data.offset;
    this.setData({ loading: true, error: "" });
    try {
      const response = await listMemoirVoiceTranscripts({
        limit: this.data.limit,
        offset,
      });
      const nextItems = response.items.map(toViewItem);
      this.setData({
        items: reset ? nextItems : [...this.data.items, ...nextItems],
        total: response.total,
        offset: offset + nextItems.length,
      });
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        wx.reLaunch({ url: "/pages/login/index" });
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

  async saveTranscript(event) {
    const id = event.currentTarget.dataset.id || "";
    const item = this.data.items.find((candidate) => candidate.id === id);
    if (!item || this.data.savingId || this.data.cleaningId) {
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
      this.setData({ items: replaceItem(this.data.items, updated) });
      wx.showToast({ title: "已保存", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "保存失败" });
    } finally {
      this.setData({ savingId: "" });
    }
  },

  async cleanupTranscript(event) {
    const id = event.currentTarget.dataset.id || "";
    const item = this.data.items.find((candidate) => candidate.id === id);
    if (!id || !item || this.data.savingId || this.data.cleaningId) {
      return;
    }
    if (item.changed) {
      this.setData({ error: "请先保存当前修改，再使用 AI 清理" });
      return;
    }
    const confirmed = await confirmCleanup();
    if (!confirmed) {
      return;
    }
    this.setData({ cleaningId: id, error: "" });
    try {
      const updated = await cleanupMemoirVoiceTranscript(id);
      this.setData({ items: replaceItem(this.data.items, updated) });
      wx.showToast({ title: "已清理", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "AI 清理失败" });
    } finally {
      this.setData({ cleaningId: "" });
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

  async playAudio(event) {
    const id = event.currentTarget.dataset.id || "";
    const item = this.data.items.find((candidate) => candidate.id === id);
    if (!item?.audio_file_id) {
      this.setData({ error: "这条记录没有可播放的音频" });
      return;
    }
    if (this.data.playingId === id) {
      this.stopPlayback();
      return;
    }
    this.stopPlayback();
    this.playToken += 1;
    const token = this.playToken;
    this.setData({ playingId: id, error: "" });
    try {
      const tempFilePath = await downloadMemoirVoiceAudio(item.audio_file_id);
      if (token !== this.playToken) {
        return;
      }
      const audio = wx.createInnerAudioContext();
      this.audio = audio;
      audio.obeyMuteSwitch = false;
      audio.src = tempFilePath;
      audio.onEnded(() => {
        if (this.audio === audio && token === this.playToken) {
          this.audio = null;
          this.setData({ playingId: "" });
        }
        audio.destroy();
      });
      audio.onStop(() => {
        if (this.audio === audio && token === this.playToken) {
          this.setData({ playingId: "" });
        }
      });
      audio.onError((error) => {
        if (this.audio === audio && token === this.playToken) {
          this.audio = null;
          this.setData({ playingId: "", error: error.errMsg || "音频播放失败" });
        }
        audio.destroy();
      });
      audio.play();
    } catch (error) {
      if (token === this.playToken) {
        this.setData({
          playingId: "",
          error: error instanceof Error ? error.message : "音频播放失败",
        });
      }
    }
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
    this.stopPlayback();
  },

  openHome() {
    this.stopPlayback();
    wx.redirectTo({ url: "/pages/home/index" });
  },
});
