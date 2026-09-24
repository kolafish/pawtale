import type { MemoirShareVideoListItem } from "../../domain/memoir-transcript";
import { accessibilityPageClass } from "../../services/accessibility-settings";
import { getMemoirIdentity } from "../../services/auth";
import { definePage } from "../../services/page-definition";
import { listMemoirShareVideos } from "../../services/memoir-transcript-api";
import { isAuthError, navigateToLogin } from "../../services/navigation";
import {
  datasetString,
  pageUrl,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
} from "../../services/page-input";
import { formatBeijingDateTimeShort } from "../../services/time";
import { formatTranscriptDurationSeconds } from "../../services/transcript-view-model";

const PAGE_SIZE = 20;

interface ShareVideoItemView extends MemoirShareVideoListItem {
  durationText: string;
  completedAtText: string;
}

interface ShareVideosData {
  pageClass: string;
  projectId: string;
  loading: boolean;
  needsLogin: boolean;
  error: string;
  items: ShareVideoItemView[];
  total: number;
  hasMore: boolean;
}

type VideoEvent = PageDatasetEvent<{ id?: string; utteranceId?: string }>;

function toViewItem(item: MemoirShareVideoListItem): ShareVideoItemView {
  return {
    ...item,
    durationText: formatTranscriptDurationSeconds(item.duration_ms / 1000),
    completedAtText: formatBeijingDateTimeShort(item.completed_at || item.created_at),
  };
}

definePage<ShareVideosData>()({
  requestToken: 0,

  data: {
    pageClass: "detail-page",
    projectId: "",
    loading: false,
    needsLogin: false,
    error: "",
    items: [],
    total: 0,
    hasMore: false,
  },

  onLoad(options?: PageQuery) {
    this.setData({ projectId: queryParam(options, "projectId") });
  },

  onShow() {
    this.setData({ pageClass: accessibilityPageClass("detail-page") });
    void this.loadVideos(true);
  },

  onHide() {
    this.requestToken += 1;
  },

  onUnload() {
    this.requestToken += 1;
  },

  async loadVideos(reset = true) {
    if (this.data.loading && !reset) return;
    const requestToken = ++this.requestToken;
    this.setData({ loading: true, error: "" });
    try {
      let projectId = this.data.projectId;
      if (!projectId) {
        const identity = await getMemoirIdentity();
        if (requestToken !== this.requestToken) return;
        projectId = identity.active_project_id || "";
        this.setData({ projectId });
      }
      if (!projectId) {
        this.setData({ items: [], total: 0, hasMore: false });
        return;
      }
      const offset = reset ? 0 : this.data.items.length;
      const response = await listMemoirShareVideos(projectId, { limit: PAGE_SIZE, offset });
      if (requestToken !== this.requestToken) return;
      const items = reset
        ? response.items.map(toViewItem)
        : [...this.data.items, ...response.items.map(toViewItem)];
      this.setData({
        needsLogin: false,
        items,
        total: response.total,
        hasMore: response.items.length > 0 && items.length < response.total,
      });
    } catch (error) {
      if (requestToken !== this.requestToken) return;
      if (isAuthError(error)) {
        this.setData({ needsLogin: true, items: [], total: 0, hasMore: false });
      } else {
        this.setData({ error: error instanceof Error ? error.message : "分享视频加载失败" });
      }
    } finally {
      if (requestToken === this.requestToken) this.setData({ loading: false });
    }
  },

  onReachBottom() {
    if (this.data.hasMore) void this.loadVideos(false);
  },

  loadMore() {
    if (this.data.hasMore) void this.loadVideos(false);
  },

  retryLoad() {
    void this.loadVideos(true);
  },

  openVideo(event: VideoEvent) {
    const id = datasetString(event, "id");
    const utteranceId = datasetString(event, "utteranceId");
    if (!id || !utteranceId) return;
    wx.navigateTo({
      url: pageUrl("/pages/transcript-media/index", { id: utteranceId, jobId: id }),
    });
  },

  openLogin() {
    navigateToLogin();
  },
});
