import { downloadAuthenticatedFile } from "../../services/authenticated-media";
import { ApiError } from "../../services/request";
import type { MemoirSharedVideo } from "../../domain/memoir-transcript";
import { accessibilityPageClass } from "../../services/accessibility-settings";
import { memoirShareVideoDownloadUrl, memoirSharedVideoUrl, readMemoirSharedVideo, previewMemoirSharedVideo } from "../../services/memoir-transcript-api";
import { definePage } from "../../services/page-definition";
import { queryParam, type PageQuery } from "../../services/page-input";
import { setPageNavigationTitle } from "../../services/page-navigation";
import { enableMiniProgramShareMenu, hideMiniProgramShareMenu, homeShareAppMessage, sharedVideoShareMessage } from "../../services/share";
import { INITIAL_SHARE_LIKE_DATA, shareLikeMethods, type ShareLikeData } from "../../services/share-likes";
import type { MemoirShareLikeTarget } from "../../domain/memoir-share-likes";
import { initialVideoPlaybackData, videoPlaybackMethods, type VideoPlaybackData } from "../../services/video-playback";

interface SharedVideoPlayerData extends ShareLikeData, VideoPlaybackData {
  pageClass: string;
  sharePreview: boolean;
  retryable: boolean;
  loading: boolean;
  error: string;
  video: MemoirSharedVideo | null;
  videoUrl: string;
  posterUrl: string;
  // After playback the page holds the closing card so its mini program code can be scanned.
  endFrameUrl: string;
  ended: boolean;
  portrait: boolean;
}

definePage<SharedVideoPlayerData>()({
  shareCode: "",
  previewJobId: "",
  shareLikeTarget: null as MemoirShareLikeTarget | null,

  data: {
    pageClass: "detail-page",
    sharePreview: false, retryable: false,
    loading: true,
    error: "",
    video: null,
    videoUrl: "",
    posterUrl: "",
    endFrameUrl: "",
    ended: false,
    portrait: true,
    ...initialVideoPlaybackData(),
    ...INITIAL_SHARE_LIKE_DATA,
  },

  ...shareLikeMethods,
  ...videoPlaybackMethods,

  async onLoad(options?: PageQuery) {
    // Cards pass code; the printed mini program code passes it as scene.
    this.shareCode = queryParam(options, "code") || queryParam(options, "scene");
    this.previewJobId = queryParam(options, "previewJobId");
    this.setData({ sharePreview: Boolean(this.previewJobId) });
    await this.loadVideo();
  },

  onShow() {
    this.setData({ pageClass: accessibilityPageClass("detail-page") });
    if (this.previewJobId || !this.data.video || this.data.error) hideMiniProgramShareMenu();
    // Back from choosing an avatar after a like.
    this.refreshShareLikes();
  },

  onShareUnavailable(error: Error) {
    hideMiniProgramShareMenu();
    this.setData({ video: null, videoUrl: "", posterUrl: "", endFrameUrl: "", ended: false, retryable: !(error instanceof ApiError && [404, 410].includes(error.statusCode)), error: error.message });
  },

  onShareAppMessage() {
    const video = this.data.video;
    return video && this.shareCode && !this.data.sharePreview && !this.data.error
      ? sharedVideoShareMessage(video.title, this.shareCode, memoirSharedVideoUrl(this.shareCode, "card"))
      : homeShareAppMessage();
  },

  async loadVideo() {
    if (!this.shareCode && !this.previewJobId) {
      this.setData({ loading: false, error: "这个链接不完整，请让家人重新转发" });
      return;
    }
    hideMiniProgramShareMenu();
    this.setData({ loading: true, error: "", video: null, videoUrl: "", ended: false, videoSpeedMenuOpen: false });
    try {
      const video = this.previewJobId ? await previewMemoirSharedVideo(this.previewJobId) : await readMemoirSharedVideo(this.shareCode);
      const mediaUrl = (url: string): Promise<string> => this.previewJobId
        ? downloadAuthenticatedFile(memoirShareVideoDownloadUrl(url), { failureMessage: "预览暂时打不开", domainLabel: "分享视频" })
        : Promise.resolve(memoirShareVideoDownloadUrl(url));
      const [videoUrl, posterUrl, endFrameUrl] = await Promise.all([mediaUrl(video.video_url), mediaUrl(video.poster_url).catch(() => ""), video.end_frame_url ? mediaUrl(video.end_frame_url).catch(() => "") : Promise.resolve("")]);
      // The backend answers byte ranges, so the player streams instead of waiting for the whole file.
      this.setData({
        video,
        videoUrl,
        posterUrl,
        endFrameUrl,
      });
      setPageNavigationTitle(video.book_title || video.title);
      if (this.previewJobId) hideMiniProgramShareMenu();
      else { enableMiniProgramShareMenu(); void this.loadShareLikes({ video_code: this.shareCode }); }
    } catch (error) {
      this.setData({ video: null, videoUrl: "", posterUrl: "", endFrameUrl: "", shareLikeVisible: false, retryable: !(error instanceof ApiError && [404, 410].includes(error.statusCode)), error: error instanceof Error ? error.message : "这段视频暂时打不开" });
    } finally {
      this.setData({ loading: false });
    }
  },

  holdEndFrame() {
    if (this.data.endFrameUrl) {
      this.setData({ ended: true, videoSpeedMenuOpen: false });
    }
  },

  replayVideo() {
    // Remounting the autoplaying video starts it again from the beginning.
    this.setData({ ended: false });
  },

  markVideoError() {
    this.setData({ retryable: true, error: "视频没有加载出来，请检查网络后重新加载" });
  },

  markVideoShape(event: { detail: { width: number; height: number } }) {
    this.setData({ portrait: event.detail.height >= event.detail.width });
  },

  openHome() {
    wx.reLaunch({ url: "/pages/home/index" });
  },
});
