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
import { routeAuthError } from "../../services/navigation";
import {
  datasetString,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
} from "../../services/page-input";
import { definePage } from "../../services/page-definition";

interface TopicMapItemView extends MemoirTopicMapItem {
  itemClass: string;
  actionText: string;
  actionDisabled: boolean;
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
): TopicMapItemView {
  const actionDisabled = savingItemId === item.id;
  return {
    ...item,
    itemClass: `topic-item ${item.display_state}`,
    actionText: actionDisabled
      ? "正在保存"
      : (item.user_resolution === "none" ? "调整这个话题" : "修改我的选择"),
    actionDisabled,
  };
}

export function topicMapViewPatch(
  topicMap: MemoirTopicMap,
  savingItemId = "",
): Pick<TopicMapData, "headline" | "detail" | "modules"> {
  return {
    headline: topicMap.headline,
    detail: topicMap.detail,
    modules: topicMap.modules.map((module) => ({
      ...module,
      items: module.items.map((item) => topicMapItemView(item, savingItemId)),
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
): TopicMapModuleView[] {
  return modules.map((module) => ({
    ...module,
    items: module.items.map((item) => (
      item.id === updated.id
        ? topicMapItemView(updated, savingItemId)
        : topicMapItemView(item, savingItemId)
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
    if (this.data.savingItemId) {
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
