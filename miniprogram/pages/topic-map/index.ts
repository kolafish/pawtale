import type {
  MemoirTopicMap,
  MemoirTopicMapItem,
  MemoirTopicMapModule,
  MemoirTopicMapUserResolution,
} from "../../domain/topic-map";
import { readLargeTextEnabled } from "../../services/accessibility-settings";
import {
  getMemoirTopicMap,
  updateMemoirTopicMapResolution,
} from "../../services/memoir-topic-map-api";
import { createMemoirVoiceSession } from "../../services/memoir-voice-session-api";
import { routeAuthError } from "../../services/navigation";
import {
  datasetString,
  pageUrl,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
} from "../../services/page-input";
import { definePage } from "../../services/page-definition";
import {
  readVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "../../services/voice-authorization";

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

interface TopicMapData {
  pageClass: string;
  projectId: string;
  loading: boolean;
  error: string;
  headline: string;
  detail: string;
  modules: TopicMapModuleView[];
  savingItemId: string;
  creatingItemId: string;
}

interface TopicMapItemDataset {
  itemId?: string;
}

interface ResolutionChoice {
  label: string;
  value: MemoirTopicMapUserResolution;
}

type TopicMapItemTapEvent = PageDatasetEvent<TopicMapItemDataset>;

const RESOLUTION_CHOICES: readonly ResolutionChoice[] = [
  {
    label: "目前讲这些就够了",
    value: "enough_for_now",
  },
  {
    label: "不适合我的经历",
    value: "not_applicable",
  },
  {
    label: "不想聊这个",
    value: "do_not_discuss",
  },
];

function topicMapItemView(
  item: MemoirTopicMapItem,
  savingItemId = "",
  creatingItemId = "",
): TopicMapItemView {
  const actionDisabled = savingItemId === item.id;
  const startDisabled = Boolean(
    savingItemId
    || creatingItemId
    || item.user_resolution !== "none",
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
      : (item.evidence_state === "has_material"
        ? "从这里继续聊"
        : "从这个话题聊起"),
    startDisabled,
  };
}

export function topicMapViewPatch(
  topicMap: MemoirTopicMap,
  savingItemId = "",
  creatingItemId = "",
): Pick<TopicMapData, "headline" | "detail" | "modules"> {
  return {
    headline: topicMap.headline,
    detail: topicMap.detail,
    modules: topicMap.modules.map((module) => ({
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
  if (current !== "none") {
    return [
      {
        label: "撤销这个选择",
        value: "none",
      },
      ...choices,
    ];
  }
  return [...choices];
}

export function replaceTopicMapItem(
  modules: TopicMapModuleView[],
  updated: MemoirTopicMapItem,
  savingItemId = "",
  creatingItemId = "",
): TopicMapModuleView[] {
  return modules.map((module) => ({
    ...module,
    items: module.items.map((item) => (
      item.id === updated.id
        ? topicMapItemView(updated, savingItemId, creatingItemId)
        : topicMapItemView(item, savingItemId, creatingItemId)
    )),
  }));
}

definePage<TopicMapData>()({
  data: {
    pageClass: "topic-map-page",
    projectId: "",
    loading: true,
    error: "",
    headline: "这些是聊过的人生话题",
    detail: "它不是章节目录，也没有必须全部讲完。",
    modules: [],
    savingItemId: "",
    creatingItemId: "",
  },

  onLoad(options: PageQuery) {
    this.setData({
      projectId: queryParam(options, "projectId"),
    });
  },

  async onShow() {
    this.setData({
      pageClass: readLargeTextEnabled()
        ? "topic-map-page large-text"
        : "topic-map-page",
    });
    await this.loadTopicMap();
  },

  async onPullDownRefresh() {
    await this.loadTopicMap();
    wx.stopPullDownRefresh();
  },

  async loadTopicMap() {
    if (!this.data.projectId) {
      this.setData({
        loading: false,
        error: "没有找到这本回忆录",
      });
      return;
    }
    this.setData({ loading: true, error: "" });
    try {
      const topicMap = await getMemoirTopicMap(this.data.projectId);
      this.setData({
        ...topicMapViewPatch(topicMap),
        loading: false,
        error: "",
      });
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        loading: false,
        error: error instanceof Error ? error.message : "话题地图加载失败",
      });
    }
  },

  chooseResolution(event: TopicMapItemTapEvent) {
    if (this.data.savingItemId || this.data.creatingItemId) {
      return;
    }
    const itemId = datasetString(event, "itemId");
    const item = this.data.modules
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

  async startFromTopic(event: TopicMapItemTapEvent) {
    if (this.data.savingItemId || this.data.creatingItemId) {
      return;
    }
    const itemId = datasetString(event, "itemId");
    const item = this.data.modules
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
      modules: this.data.modules.map((module) => ({
        ...module,
        items: module.items.map((candidate) => topicMapItemView(
          candidate,
          "",
          item.id,
        )),
      })),
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
          entry: "miniprogram_topic_map",
          topic_map_item_id: item.id,
        },
      }, {
        projectId: this.data.projectId,
      });
      wx.navigateTo({
        url: pageUrl("/pages/voice-chat/index", {
          voiceSessionId: detail.session.id,
          mode: "guided_interview",
          interviewType: "ai_interview",
          sessionGoal: "open_narration",
          projectTitle: item.title,
        }),
      });
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
        modules: this.data.modules.map((module) => ({
          ...module,
          items: module.items.map((candidate) => topicMapItemView(candidate)),
        })),
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
      modules: this.data.modules.map((module) => ({
        ...module,
        items: module.items.map((item) => topicMapItemView(item, itemId)),
      })),
    });
    try {
      const updated = await updateMemoirTopicMapResolution(
        this.data.projectId,
        itemId,
        resolution,
      );
      this.setData({
        savingItemId: "",
        modules: replaceTopicMapItem(this.data.modules, updated),
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
        modules: this.data.modules.map((module) => ({
          ...module,
          items: module.items.map((item) => topicMapItemView(item)),
        })),
      });
    }
  },
});
