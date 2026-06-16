import { getMemoirIdentity, logoutBackendSession } from "../../services/auth";
import { ApiError } from "../../services/request";
import { replaceMemoirStore } from "../../stores/session";

const WECHAT_PLACEHOLDER_DISPLAY_NAME = "微信用户";

interface MeData {
  loading: boolean;
  error: string;
  displayName: string;
  avatarText: string;
  email: string;
  projectTitle: string;
  projectCount: number;
}

function isProvisionalWeChatEmail(email: string): boolean {
  return email.trim().toLowerCase().endsWith("@wechat.local");
}

function displayNameForUser(displayName: string, email: string): string {
  const normalizedName = displayName.trim();
  if (normalizedName && normalizedName !== WECHAT_PLACEHOLDER_DISPLAY_NAME) {
    return normalizedName;
  }
  if (isProvisionalWeChatEmail(email)) {
    return "作者";
  }
  return email.split("@", 1)[0].trim() || "作者";
}

Page<MeData, {
  onShow(): Promise<void>;
  loadMe(): Promise<void>;
  openProjects(): void;
  openVoiceProgress(): void;
  logout(): Promise<void>;
}>({
  data: {
    loading: false,
    error: "",
    displayName: "",
    avatarText: "作",
    email: "",
    projectTitle: "",
    projectCount: 0,
  },

  async onShow() {
    if (!this.data.loading) {
      await this.loadMe();
    }
  },

  async loadMe() {
    this.setData({ loading: true, error: "" });
    try {
      const identity = await getMemoirIdentity();
      if (!identity.password_login_enabled) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      const displayName = displayNameForUser(identity.display_name, identity.email);
      this.setData({
        displayName,
        avatarText: Array.from(displayName)[0] || "作",
        email: identity.email,
        projectTitle: identity.active_project?.title || "我的回忆录",
        projectCount: identity.projects.length,
      });
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "我的页面加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  openProjects() {
    wx.navigateTo({ url: "/pages/projects/index" });
  },

  openVoiceProgress() {
    wx.navigateTo({ url: "/pages/voice-progress/index" });
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
