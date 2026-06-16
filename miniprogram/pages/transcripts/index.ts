import type { MemoirVoiceTranscriptItem } from "../../domain/voice-chat";
import {
  deleteMemoirVoiceTranscript,
  downloadMemoirVoiceAudio,
  listMemoirVoiceTranscripts,
  updateMemoirVoiceTranscript,
} from "../../services/memoir-voice-api";
import { configureAudiblePlayback } from "../../services/audio-playback";
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
  audioText: string;
  canPlayAudio: boolean;
  changed: boolean;
  isEditing: boolean;
}

interface TranscriptsData {
  loading: boolean;
  savingId: string;
  deletingId: string;
  playingId: string;
  error: string;
  items: TranscriptItemView[];
  total: number;
  limit: number;
  offset: number;
}

const DEFAULT_LIMIT = 30;

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
    audioText: audioText(item),
    canPlayAudio: Boolean(item.audio_file_id || item.question_audio_file_id),
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

function replaceItem(items: TranscriptItemView[], next: MemoirVoiceTranscriptItem): TranscriptItemView[] {
  return items.map((item) => (item.id === next.id ? toViewItem(next) : item));
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
  onLoad(): Promise<void>;
  loadTranscripts(reset?: boolean): Promise<void>;
  loadMore(): Promise<void>;
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
  audio: WechatMiniprogram.InnerAudioContext | null;
  playToken: number;
}>({
  audio: null,
  playToken: 0,

  data: {
    loading: false,
    savingId: "",
    deletingId: "",
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
    items[index] = toViewItem(items[index]);
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
      this.setData({ items: replaceItem(this.data.items, updated) });
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
    this.stopPlayback();
  },

  openHome() {
    this.stopPlayback();
    wx.switchTab({ url: "/pages/home/index" });
  },
});
