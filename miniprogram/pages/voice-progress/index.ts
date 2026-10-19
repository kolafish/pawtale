import { narrationModeForSession, NARRATION_MODES } from "../../domain/memoir-narration-mode";
import type {
  MemoirTopicMap,
  MemoirTopicMapItem,
  MemoirTopicMapModule,
  MemoirTopicMapUserResolution,
} from "../../domain/topic-map";
import type {
  MemoirVoiceProgressResponse,
  MemoirVoiceSessionSummary,
} from "../../domain/memoir-voice-session";
import {
  accessibilityPageClass,
  accessibilityPagePatch,
} from "../../services/accessibility-settings";
import { getMemoirIdentity } from "../../services/auth";
import {
  getMemoirTopicMap,
  updateMemoirTopicMapResolution,
} from "../../services/memoir-topic-map-api";
import {
  createMemoirVoiceSession,
  getMemoirVoiceProgress,
  listMemoirVoiceSessions,
} from "../../services/memoir-voice-session-api";
import { returnToHomeVoice, routeAuthError } from "../../services/navigation";
import { definePage } from "../../services/page-definition";
import { formatBeijingDateTimeShort } from "../../services/time";
import {
  datasetString,
  pageUrl,
  type PageDatasetEvent,
} from "../../services/page-input";
import {
  readVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "../../services/voice-authorization";

interface VoiceSessionSummaryView extends MemoirVoiceSessionSummary {
  titleText: string;
  statusText: string;
  updatedText: string;
  durationText: string;
  canContinue: boolean;
}

interface TopicMapItemView extends MemoirTopicMapItem {
  itemClass: string;
  actionText: string;
  actionDisabled: boolean;
  startText: string;
  startDisabled: boolean;
}

interface TopicMapModuleView extends Omit<MemoirTopicMapModule, "items"> {
  items: TopicMapItemView[];
}

interface OverviewData {
  pageClass: string;
  loading: boolean;
  error: string;
  activeProjectId: string;
  userId: string;
  projectTitle: string;
  progress: MemoirVoiceProgressResponse;
  sessions: VoiceSessionSummaryView[];
  topicHeadline: string;
  topicModules: TopicMapModuleView[];
  savingItemId: string;
  creatingItemId: string;
}

interface OverviewDataset {
  id?: string;
  itemId?: string;
}

interface ResolutionChoice {
  label: string;
  value: MemoirTopicMapUserResolution;
}

type DatasetEvent = PageDatasetEvent<OverviewDataset>;

const PAGE_CLASS = "overview-page ui-page-shell ui-accessible-page";
const MODULE_LABELS: Record<string, string> = {
  person: "人物",
  event: "事件",
  place: "地点",
  period: "时间段",
  theme: "主题",
};
const RESOLUTION_CHOICES: readonly ResolutionChoice[] = [
  { label: "目前讲这些就够了", value: "enough_for_now" },
  { label: "不适合我的经历", value: "not_applicable" },
  { label: "不想聊这个", value: "do_not_discuss" },
];
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

export function sessionTitle(item: MemoirVoiceSessionSummary): string {
  const mode = narrationModeForSession(item);
  const topic = mode === "guided_interview" && item.current_guidance_module
    ? MODULE_LABELS[item.current_guidance_module] : "";
  return `${NARRATION_MODES[mode].label}${topic ? ` · ${topic}` : ""}`;
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
    titleText: sessionTitle(item),
    statusText: statusText(item.status),
    updatedText: formatBeijingDateTimeShort(item.updated_at || item.created_at),
    durationText: formatDuration(item.total_audio_seconds),
    canContinue: canContinueVoiceSession(item.status),
  };
}

function topicMapItemView(
  item: MemoirTopicMapItem,
  savingItemId = "",
  creatingItemId = "",
): TopicMapItemView {
  const actionDisabled = savingItemId === item.id;
  const startDisabled = Boolean(
    savingItemId || creatingItemId || item.user_resolution !== "none",
  );
  return {
    ...item,
    itemClass: `topic-item ${item.display_state}`,
    actionText: actionDisabled
      ? "正在保存"
      : (item.user_resolution === "none" ? "调整这个话题" : "修改我的选择"),
    actionDisabled,
    startText: creatingItemId === item.id
      ? "正在准备"
      : (item.evidence_state === "has_material" ? "从这里继续聊" : "从这个话题聊起"),
    startDisabled,
  };
}

export function topicMapViewPatch(
  topicMap: MemoirTopicMap,
  savingItemId = "",
  creatingItemId = "",
): Pick<OverviewData, "topicHeadline" | "topicModules"> {
  return {
    topicHeadline: topicMap.headline,
    topicModules: topicMap.modules.map((module) => ({
      ...module,
      items: module.items.map((item) => topicMapItemView(
        item,
        savingItemId,
        creatingItemId,
      )),
    })),
  };
}

export function topicMapResolutionChoices(
  current: MemoirTopicMapUserResolution,
): ResolutionChoice[] {
  const choices = RESOLUTION_CHOICES.filter((choice) => choice.value !== current);
  return current === "none"
    ? [...choices]
    : [{ label: "撤销这个选择", value: "none" }, ...choices];
}

export function replaceTopicMapItem(
  modules: TopicMapModuleView[],
  updated: MemoirTopicMapItem,
  savingItemId = "",
  creatingItemId = "",
): TopicMapModuleView[] {
  return modules.map((module) => ({
    ...module,
    items: module.items.map((item) => topicMapItemView(
      item.id === updated.id ? updated : item,
      savingItemId,
      creatingItemId,
    )),
  }));
}

function refreshTopicItems(
  modules: TopicMapModuleView[],
  savingItemId = "",
  creatingItemId = "",
): TopicMapModuleView[] {
  return modules.map((module) => ({
    ...module,
    items: module.items.map((item) => topicMapItemView(item, savingItemId, creatingItemId)),
  }));
}

definePage<OverviewData>()({
  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    loading: false,
    error: "",
    activeProjectId: "",
    userId: "",
    projectTitle: "",
    progress: EMPTY_PROGRESS,
    sessions: [],
    topicHeadline: "人生话题",
    topicModules: [],
    savingItemId: "",
    creatingItemId: "",
  },

  async onShow() {
    this.setData(accessibilityPagePatch(PAGE_CLASS));
    await this.loadOverview();
  },

  async onPullDownRefresh() {
    await this.loadOverview();
    wx.stopPullDownRefresh();
  },

  async loadOverview() {
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
          userId: identity.id,
          projectTitle: "",
          progress: EMPTY_PROGRESS,
          sessions: [],
          topicModules: [],
        });
        return;
      }
      const [progress, sessionList, topicMap] = await Promise.all([
        getMemoirVoiceProgress(activeProjectId),
        listMemoirVoiceSessions({ projectId: activeProjectId, effectiveOnly: true, limit: 30 }),
        getMemoirTopicMap(activeProjectId),
      ]);
      this.setData({
        activeProjectId,
        userId: identity.id,
        projectTitle: identity.active_project?.title || "当前回忆录",
        progress,
        sessions: sessionList.items.map(toSessionView),
        ...topicMapViewPatch(topicMap),
      });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "讲述概览加载失败" });
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
    const entry = { voiceSessionId: item.id, projectId: item.project_id,
      mode: item.guidance_mode, module: item.current_guidance_module || "",
      interviewType: item.interview_type, sessionGoal: item.session_goal };
    if (item.session_goal === "book_completion") {
      wx.navigateTo({ url: pageUrl("/pages/voice-chat/index", entry) });
    } else {
      returnToHomeVoice({ userId: this.data.userId, projectId: item.project_id, entry });
    }
  },

  chooseResolution(event: DatasetEvent) {
    if (this.data.savingItemId || this.data.creatingItemId) {
      return;
    }
    const itemId = datasetString(event, "itemId");
    const item = this.data.topicModules
      .flatMap((module) => module.items)
      .find((candidate) => candidate.id === itemId);
    if (!item) {
      return;
    }
    const choices = topicMapResolutionChoices(item.user_resolution);
    wx.showActionSheet({
      itemList: choices.map((choice) => choice.label),
      success: ({ tapIndex }) => {
        const choice = choices[tapIndex];
        if (choice) {
          void this.saveResolution(item.id, choice.value);
        }
      },
    });
  },

  async startFromTopic(event: DatasetEvent) {
    if (this.data.savingItemId || this.data.creatingItemId) {
      return;
    }
    const itemId = datasetString(event, "itemId");
    const item = this.data.topicModules
      .flatMap((module) => module.items)
      .find((candidate) => candidate.id === itemId);
    if (!item || item.user_resolution !== "none") {
      return;
    }
    if (!readVoiceAuthorizationConsent()) {
      wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
      return;
    }
    this.setData({
      creatingItemId: item.id,
      error: "",
      topicModules: refreshTopicItems(this.data.topicModules, "", item.id),
    });
    try {
      await syncVoiceAuthorizationConsent();
      const detail = await createMemoirVoiceSession({
        guidance_mode: "guided_interview",
        interview_type: "ai_interview",
        session_goal: "open_narration",
        anchor_framework_item_id: item.id,
        provider: "volc_streaming_asr",
        input_format: "pcm_s16le_16000_mono",
        metadata_json: {
          entry: "miniprogram_voice_overview",
          topic_map_item_id: item.id,
        },
      }, {
        projectId: this.data.activeProjectId,
      });
      returnToHomeVoice({ userId: this.data.userId, projectId: this.data.activeProjectId, entry: {
        voiceSessionId: detail.session.id, projectId: this.data.activeProjectId,
        mode: "guided_interview", interviewType: "ai_interview", sessionGoal: "open_narration",
        projectTitle: item.title,
      } });
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "话题访谈创建失败",
      });
    } finally {
      this.setData({
        creatingItemId: "",
        topicModules: refreshTopicItems(this.data.topicModules),
      });
    }
  },

  async saveResolution(
    itemId: string,
    resolution: MemoirTopicMapUserResolution,
  ) {
    this.setData({
      savingItemId: itemId,
      error: "",
      topicModules: refreshTopicItems(this.data.topicModules, itemId),
    });
    try {
      const updated = await updateMemoirTopicMapResolution(
        this.data.activeProjectId,
        itemId,
        resolution,
      );
      this.setData({
        savingItemId: "",
        topicModules: replaceTopicMapItem(this.data.topicModules, updated),
      });
      wx.showToast({
        title: resolution === "none" ? "已撤销选择" : "已保存",
        icon: "success",
      });
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        savingItemId: "",
        error: error instanceof Error ? error.message : "选择保存失败",
        topicModules: refreshTopicItems(this.data.topicModules),
      });
    }
  },
});
