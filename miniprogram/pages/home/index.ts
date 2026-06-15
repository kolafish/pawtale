import { getMemoirIdentity, logoutBackendSession } from "../../services/auth";
import { ApiError } from "../../services/request";
import { replaceMemoirStore } from "../../stores/session";

interface HomeData {
  loading: boolean;
  error: string;
  displayName: string;
  projectId: string;
  projectTitle: string;
  projectCount: number;
}

function userDisplayName(displayName: string, email: string): string {
  return displayName || email.split("@", 1)[0] || "作者";
}

Page<HomeData, {
  onShow(): Promise<void>;
  loadHome(): Promise<void>;
  startVoiceChat(): void;
  openTranscripts(): void;
  openProjects(): void;
  logout(): Promise<void>;
}>({
  data: {
    loading: false,
    error: "",
    displayName: "",
    projectId: "",
    projectTitle: "",
    projectCount: 0,
  },

  async onShow() {
    if (!this.data.loading) {
      await this.loadHome();
    }
  },

  async loadHome() {
    this.setData({ loading: true, error: "" });
    try {
      const identity = await getMemoirIdentity();
      this.setData({
        displayName: userDisplayName(identity.display_name, identity.email),
        projectId: identity.active_project_id,
        projectTitle: identity.active_project?.title || "我的回忆录",
        projectCount: identity.projects.length,
      });
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "首页加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  startVoiceChat() {
    if (this.data.loading) {
      return;
    }
    wx.navigateTo({ url: "/pages/voice-start/index" });
  },

  openTranscripts() {
    wx.navigateTo({ url: "/pages/transcripts/index" });
  },

  openProjects() {
    wx.navigateTo({ url: "/pages/projects/index" });
  },

  async logout() {
    if (this.data.loading) {
      return;
    }
    this.setData({ loading: true, error: "" });
    try {
      await logoutBackendSession();
    } catch (error) {
      console.warn("backend logout failed", error);
    } finally {
      replaceMemoirStore({});
      this.setData({ loading: false });
      wx.reLaunch({ url: "/pages/login/index" });
    }
  },
});
