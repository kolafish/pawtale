import type { MemoirShareVideoJob, MemoirShareVideoListItem } from "../../domain/memoir-transcript";
import { MEMOIR_SHARE_VIDEO_STYLES, normalizeMemoirShareVideoStyle } from "../../domain/memoir-transcript";
import { accessibilityPageClass } from "../../services/accessibility-settings";
import { getMemoirIdentity } from "../../services/auth";
import { definePage, pageRuntime } from "../../services/page-definition";
import {
  deleteMemoirShareVideoJob,
  getMemoirShareVideoJob,
  listMemoirShareVideos,
  memoirSharedVideoUrl,
} from "../../services/memoir-transcript-api";
import { isAuthError, navigateToLogin, navigateToVideoClipPicker } from "../../services/navigation";
import {
  datasetString,
  pageUrl,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
} from "../../services/page-input";
import { formatBeijingDateTimeShort, timestampMs } from "../../services/time";
import { formatTranscriptDurationSeconds } from "../../services/transcript-view-model";
import { PollingTimer } from "../../services/polling-timer";
import { shareVideoJobStatusText } from "../../services/transcript-share-video";
import { shareVideoDeleteConfirmation } from "../../services/share-video-delete";

const PAGE_SIZE = 20;

interface ShareVideoItemView extends MemoirShareVideoListItem {
  styleLabel: string;
  durationText: string;
  completedAtText: string;
  thumbnailUrl: string;
}

interface ShareVideoTaskView extends ShareVideoItemView {
  status: MemoirShareVideoJob["status"];
  statusText: string;
}

interface ShareVideosData {
  pageClass: string;
  projectId: string;
  loading: boolean;
  needsLogin: boolean;
  error: string;
  items: ShareVideoItemView[];
  taskItems: ShareVideoTaskView[];
  total: number;
  hasMore: boolean;
  deletingId: string;
}

type VideoEvent = PageDatasetEvent<{ id?: string; utteranceId?: string }>;

function toViewItem(item: MemoirShareVideoListItem): ShareVideoItemView {
  return {
    ...item,
    styleLabel: MEMOIR_SHARE_VIDEO_STYLES.find(
      (style) => style.value === normalizeMemoirShareVideoStyle(item.video_style),
    )!.label,
    durationText: formatTranscriptDurationSeconds(item.duration_ms / 1000),
    completedAtText: formatBeijingDateTimeShort(item.completed_at || item.created_at),
    thumbnailUrl: item.share_code ? memoirSharedVideoUrl(item.share_code, "thumbnail") : "",
  };
}

function toTaskView(job: MemoirShareVideoJob): ShareVideoTaskView {
  return {
    ...toViewItem({ ...job, completed_at: job.completed_at || null, share_code: "" }),
    status: job.status,
    statusText: shareVideoJobStatusText(job),
  };
}

definePage<ShareVideosData>()({
  requestToken: 0,
  pageVisible: false,
  progressPoller: pageRuntime(() => new PollingTimer(5000)),

  data: {
    pageClass: "detail-page",
    projectId: "",
    loading: false,
    needsLogin: false,
    error: "",
    items: [],
    taskItems: [],
    total: 0,
    hasMore: false,
    deletingId: "",
  },

  onLoad(options?: PageQuery) {
    this.setData({ projectId: queryParam(options, "projectId") });
  },

  onShow() {
    this.pageVisible = true;
    this.setData({ pageClass: accessibilityPageClass("detail-page") });
    void this.loadVideos(true);
  },

  onHide() {
    this.pageVisible = false;
    this.progressPoller.cancel();
    this.requestToken += 1;
  },

  onUnload() {
    this.onHide();
  },

  async loadVideos(reset = true) {
    if (this.data.loading && !reset) return;
    this.progressPoller.cancel();
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
        this.setData({ items: [], taskItems: [], total: 0, hasMore: false });
        return;
      }
      const offset = reset ? 0 : this.data.items.length;
      const response = await listMemoirShareVideos(projectId, { limit: PAGE_SIZE, offset });
      if (requestToken !== this.requestToken) return;
      const items = reset
        ? response.items.map(toViewItem)
        : [...this.data.items, ...response.items
          .filter((item) => !this.data.items.some((existing) => existing.id === item.id))
          .map(toViewItem)];
      const tasks = [...response.active_jobs, ...response.failed_jobs];
      const taskIds = new Set(tasks.map((job) => job.id));
      this.setData({
        needsLogin: false,
        items,
        taskItems: [...tasks.map(toTaskView),
          ...(!reset ? this.data.taskItems.filter((item) => !taskIds.has(item.id)) : [])],
        total: response.total,
        hasMore: response.items.length > 0 && items.length < response.total,
      });
    } catch (error) {
      if (requestToken !== this.requestToken) return;
      if (isAuthError(error)) {
        this.setData({ needsLogin: true, items: [], taskItems: [], total: 0, hasMore: false });
      } else {
        this.setData({ error: error instanceof Error ? error.message : "分享视频加载失败" });
      }
    } finally {
      if (requestToken === this.requestToken) {
        this.setData({ loading: false });
        this.scheduleProgressPolling();
      }
    }
  },

  scheduleProgressPolling() {
    if (!this.pageVisible || this.data.loading || this.data.error || this.data.needsLogin
      || !this.data.taskItems.some((item) => item.status === "pending" || item.status === "running")) {
      return;
    }
    this.progressPoller.schedule(() => { void this.refreshVideoProgress(); });
  },

  async refreshVideoProgress() {
    if (!this.pageVisible || this.data.loading) return;
    const tasks = this.data.taskItems.filter((item) => item.status === "pending" || item.status === "running");
    if (!tasks.length) return;
    this.progressPoller.cancel();
    const requestToken = ++this.requestToken;
    try {
      const jobs = await Promise.all(tasks.map((item) => getMemoirShareVideoJob(item.id)));
      if (requestToken !== this.requestToken) return;
      const updates = new Map(jobs.map((job) => [job.id, job]));
      const completed = jobs
        .filter((job) => job.status === "succeeded" && !this.data.items.some((item) => item.id === job.id))
        .sort((left, right) => timestampMs(right.completed_at || right.created_at) - timestampMs(left.completed_at || left.created_at)
          || right.id.localeCompare(left.id))
        .map((job) => toViewItem({
          ...job,
          completed_at: job.completed_at || null,
          share_code: job.output?.share_code || "",
        }));
      const total = completed.length
        ? (await listMemoirShareVideos(this.data.projectId, { limit: 1, offset: 0 })).total
        : this.data.total;
      if (requestToken !== this.requestToken) return;
      const items = [...completed, ...this.data.items];
      this.setData({
        taskItems: this.data.taskItems
          .map((item) => updates.has(item.id) ? toTaskView(updates.get(item.id)!) : item)
          .filter((item) => item.status !== "succeeded"),
        items,
        total,
        hasMore: completed.length ? items.length < total : this.data.hasMore,
        error: "",
      });
      this.scheduleProgressPolling();
    } catch (error) {
      if (requestToken !== this.requestToken) return;
      if (isAuthError(error)) {
        this.setData({ needsLogin: true, items: [], taskItems: [], total: 0, hasMore: false });
      } else {
        this.setData({ error: error instanceof Error ? error.message : "视频制作进度更新失败" });
      }
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

  async deleteFailedVideo(event: VideoEvent) {
    const task = this.data.taskItems.find((item) => item.id === datasetString(event, "id"));
    if (!task || task.status !== "failed" || this.data.deletingId) return;
    const confirmed = await new Promise<boolean>((resolve) => {
      wx.showModal({
        ...shareVideoDeleteConfirmation(task),
        confirmColor: "#b64545",
        success: (result) => resolve(Boolean(result.confirm)),
        fail: () => resolve(false),
      });
    });
    if (!confirmed) return;
    this.setData({ deletingId: task.id, error: "" });
    try {
      await deleteMemoirShareVideoJob(task.id);
      this.setData({ taskItems: this.data.taskItems.filter((item) => item.id !== task.id) });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "删除失败，请稍后重试" });
    } finally {
      this.setData({ deletingId: "" });
    }
  },

  pickClip() {
    navigateToVideoClipPicker();
  },

  openLogin() {
    navigateToLogin();
  },
});
