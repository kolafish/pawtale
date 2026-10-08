import type { MemoirSharedVideo } from "../../domain/memoir-transcript";
import { accessibilityPageClass } from "../../services/accessibility-settings";
import { memoirShareVideoDownloadUrl, memoirSharedVideoUrl, readMemoirSharedVideo } from "../../services/memoir-transcript-api";
import { definePage } from "../../services/page-definition";
import { pageUrl, queryParam, type PageQuery } from "../../services/page-input";
import { enableMiniProgramShareMenu, homeShareAppMessage, sharedVideoShareMessage } from "../../services/share";

interface SharedVideoPlayerData {
  pageClass: string;
  loading: boolean;
  error: string;
  video: MemoirSharedVideo | null;
  videoUrl: string;
  posterUrl: string;
  portrait: boolean;
}

definePage<SharedVideoPlayerData>()({
  shareCode: "",

  data: {
    pageClass: "detail-page",
    loading: true,
    error: "",
    video: null,
    videoUrl: "",
    posterUrl: "",
    portrait: true,
  },

  async onLoad(options?: PageQuery) {
    // Cards pass code; the printed mini program code passes it as scene.
    this.shareCode = queryParam(options, "code") || queryParam(options, "scene");
    await this.loadVideo();
  },

  onShow() {
    this.setData({ pageClass: accessibilityPageClass("detail-page") });
  },

  onShareAppMessage() {
    const video = this.data.video;
    return video && this.shareCode
      ? sharedVideoShareMessage(video.title, this.shareCode, memoirSharedVideoUrl(this.shareCode, "card"))
      : homeShareAppMessage();
  },

  async loadVideo() {
    if (!this.shareCode) {
      this.setData({ loading: false, error: "这个链接不完整，请让家人重新转发" });
      return;
    }
    this.setData({ loading: true, error: "", videoUrl: "" });
    try {
      const video = await readMemoirSharedVideo(this.shareCode);
      // The backend answers byte ranges, so the player streams instead of waiting for the whole file.
      this.setData({
        video,
        videoUrl: memoirShareVideoDownloadUrl(video.video_url),
        posterUrl: memoirShareVideoDownloadUrl(video.poster_url),
      });
      enableMiniProgramShareMenu();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "这段视频暂时打不开" });
    } finally {
      this.setData({ loading: false });
    }
  },

  markVideoError() {
    this.setData({ error: "视频没有加载出来，请检查网络后重新加载" });
  },

  markVideoShape(event: { detail: { width: number; height: number } }) {
    this.setData({ portrait: event.detail.height >= event.detail.width });
  },

  openBook() {
    const token = this.data.video?.book_share_token;
    if (token) {
      wx.navigateTo({ url: pageUrl("/pages/book-reader/index", { shareToken: token }) });
    }
  },

  openHome() {
    wx.reLaunch({ url: "/pages/home/index" });
  },
});
