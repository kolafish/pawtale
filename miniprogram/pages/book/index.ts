import { getMemoirIdentity } from "../../services/auth";
import { ApiError } from "../../services/request";

interface BookData {
  loading: boolean;
  error: string;
  projectTitle: string;
}

Page<BookData, {
  onShow(): Promise<void>;
  loadBookHome(): Promise<void>;
  openTranscripts(): void;
}>({
  data: {
    loading: false,
    error: "",
    projectTitle: "我的回忆录",
  },

  async onShow() {
    await this.loadBookHome();
  },

  async loadBookHome() {
    if (this.data.loading) {
      return;
    }
    this.setData({ loading: true, error: "" });
    try {
      const identity = await getMemoirIdentity();
      if (!identity.password_login_enabled) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({
        projectTitle: identity.active_project?.title || "我的回忆录",
      });
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "成书页面加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  openTranscripts() {
    wx.switchTab({ url: "/pages/transcripts/index" });
  },
});
