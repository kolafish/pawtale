import type {
  MemoirGuidanceMode,
  MemoirVoiceModuleProgress,
  MemoirVoiceProgressResponse,
  MemoirVoiceSessionSummary,
} from "../../domain/voice-chat";
import {
  getMemoirVoiceProgress,
  listMemoirVoiceSessions,
} from "../../services/memoir-voice-api";
import { ApiError } from "../../services/request";

interface VoiceSessionSummaryView extends MemoirVoiceSessionSummary {
  modeText: string;
  statusText: string;
  moduleText: string;
  updatedText: string;
  durationText: string;
  canContinue: boolean;
}

interface ModuleProgressView extends MemoirVoiceModuleProgress {
  label: string;
}

interface ProgressData {
  loading: boolean;
  error: string;
  progress: MemoirVoiceProgressResponse;
  sessions: VoiceSessionSummaryView[];
  modules: ModuleProgressView[];
}

interface DatasetEvent {
  currentTarget: {
    dataset: {
      id?: string;
    };
  };
}

const MODULE_LABELS: Record<string, string> = {
  person: "人物",
  event: "事件",
  place: "地点",
  period: "时间段",
  theme: "主题",
  free_narration: "自由讲述",
  unassigned: "未分配",
};

const EMPTY_PROGRESS: MemoirVoiceProgressResponse = {
  session_count: 0,
  active_session_count: 0,
  stopped_session_count: 0,
  failed_session_count: 0,
  utterance_count: 0,
  user_message_count: 0,
  assistant_message_count: 0,
  total_user_char_count: 0,
  total_audio_seconds: 0,
  modules: [],
};

function modeText(mode: MemoirGuidanceMode): string {
  return mode === "guided_interview" ? "AI 引导" : "自由讲述";
}

function statusText(status: string): string {
  const labels: Record<string, string> = {
    created: "可继续",
    listening: "进行中",
    speaking: "AI 追问中",
    stopped: "已结束",
    failed: "异常",
  };
  return labels[status] || status;
}

function moduleText(value?: string | null): string {
  if (!value) {
    return "未分配";
  }
  return MODULE_LABELS[value] || value;
}

function formatDate(value?: string | null): string {
  if (!value) {
    return "";
  }
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

function formatDuration(seconds: number): string {
  if (!seconds) {
    return "0s";
  }
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

function toSessionView(item: MemoirVoiceSessionSummary): VoiceSessionSummaryView {
  return {
    ...item,
    modeText: modeText(item.guidance_mode),
    statusText: statusText(item.status),
    moduleText: item.guidance_mode === "free_narration" ? "自由讲述" : moduleText(item.current_guidance_module),
    updatedText: formatDate(item.updated_at || item.created_at),
    durationText: formatDuration(item.total_audio_seconds),
    canContinue: item.status !== "failed",
  };
}

function toModuleView(item: MemoirVoiceModuleProgress): ModuleProgressView {
  return {
    ...item,
    label: moduleText(item.module),
  };
}

Page<ProgressData, {
  onShow(): Promise<void>;
  loadProgress(): Promise<void>;
  continueSession(event: DatasetEvent): void;
  openTranscripts(): void;
  startNewSession(): void;
}>({
  data: {
    loading: false,
    error: "",
    progress: EMPTY_PROGRESS,
    sessions: [],
    modules: [],
  },

  async onShow() {
    await this.loadProgress();
  },

  async loadProgress() {
    if (this.data.loading) {
      return;
    }
    this.setData({ loading: true, error: "" });
    try {
      const [progress, sessionList] = await Promise.all([
        getMemoirVoiceProgress(),
        listMemoirVoiceSessions({ limit: 30 }),
      ]);
      this.setData({
        progress,
        sessions: sessionList.items.map(toSessionView),
        modules: progress.modules.map(toModuleView),
      });
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        wx.switchTab({ url: "/pages/me/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "访谈进度加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  continueSession(event) {
    const id = String(event.currentTarget.dataset.id || "");
    const item = this.data.sessions.find((candidate) => candidate.id === id);
    if (!item || !item.canContinue) {
      return;
    }
    const query = [
      `voiceSessionId=${encodeURIComponent(item.id)}`,
      `mode=${encodeURIComponent(item.guidance_mode)}`,
    ];
    if (item.current_guidance_module) {
      query.push(`module=${encodeURIComponent(item.current_guidance_module)}`);
    }
    wx.navigateTo({ url: `/pages/voice-chat/index?${query.join("&")}` });
  },

  openTranscripts() {
    wx.switchTab({ url: "/pages/transcripts/index" });
  },

  startNewSession() {
    wx.switchTab({ url: "/pages/home/index" });
  },
});
