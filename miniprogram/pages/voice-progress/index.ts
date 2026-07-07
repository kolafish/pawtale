import type {
  MemoirGuidanceMode,
  MemoirVoiceModuleProgress,
  MemoirVoiceProgressResponse,
  MemoirVoiceSessionSummary,
} from "../../domain/voice-chat";
import { definePage } from "../../services/page-definition";
import {
  getMemoirVoiceProgress,
  listMemoirVoiceSessions,
} from "../../services/memoir-voice-api";
import { getMemoirIdentity } from "../../services/auth";
import { routeAuthError } from "../../services/navigation";
import {
  datasetString,
  pageUrl,
  type PageDatasetEvent,
} from "../../services/page-input";

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
  activeProjectId: string;
  projectTitle: string;
  progress: MemoirVoiceProgressResponse;
  sessions: VoiceSessionSummaryView[];
  modules: ModuleProgressView[];
}

interface ProgressDataset {
  id?: string;
}

type DatasetEvent = PageDatasetEvent<ProgressDataset>;

const MODULE_LABELS: Record<string, string> = {
  person: "人物",
  event: "事件",
  place: "地点",
  period: "时间段",
  theme: "主题",
  free_narration: "慢慢讲",
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
  return mode === "guided_interview" ? "小余陪着问" : "慢慢讲";
}

function statusText(status: string): string {
  const labels: Record<string, string> = {
    created: "可继续",
    listening: "进行中",
    speaking: "小余在问",
    stopped: "已保存",
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

export function canContinueVoiceSession(status: string): boolean {
  return status === "created" || status === "listening" || status === "speaking";
}

function toSessionView(item: MemoirVoiceSessionSummary): VoiceSessionSummaryView {
  return {
    ...item,
    modeText: modeText(item.guidance_mode),
    statusText: statusText(item.status),
    moduleText: item.guidance_mode === "free_narration" ? "慢慢讲" : moduleText(item.current_guidance_module),
    updatedText: formatDate(item.updated_at || item.created_at),
    durationText: formatDuration(item.total_audio_seconds),
    canContinue: canContinueVoiceSession(item.status),
  };
}

function toModuleView(item: MemoirVoiceModuleProgress): ModuleProgressView {
  return {
    ...item,
    label: moduleText(item.module),
  };
}

definePage<ProgressData>()({
  data: {
    loading: false,
    error: "",
    activeProjectId: "",
    projectTitle: "",
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
      const identity = await getMemoirIdentity();
      const activeProjectId = identity.active_project_id || identity.active_project?.id || "";
      if (!activeProjectId) {
        this.setData({
          activeProjectId: "",
          projectTitle: "",
          progress: EMPTY_PROGRESS,
          sessions: [],
          modules: [],
        });
        return;
      }
      const [progress, sessionList] = await Promise.all([
        getMemoirVoiceProgress(activeProjectId),
        listMemoirVoiceSessions({ projectId: activeProjectId, effectiveOnly: true, limit: 30 }),
      ]);
      this.setData({
        activeProjectId,
        projectTitle: identity.active_project?.title || "当前回忆录",
        progress,
        sessions: sessionList.items.map(toSessionView),
        modules: progress.modules.map(toModuleView),
      });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "讲述进度加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  continueSession(event: DatasetEvent) {
    const id = datasetString(event, "id");
    const item = this.data.sessions.find((candidate) => candidate.id === id);
    if (!item) {
      return;
    }
    if (!item.canContinue) {
      this.setData({
        error: item.status === "failed" ? "这次讲述保存异常，请返回首页重新开始" : "这次讲述已保存",
      });
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/voice-chat/index", {
        voiceSessionId: item.id,
        mode: item.guidance_mode,
        module: item.current_guidance_module,
      }),
    });
  },
});
