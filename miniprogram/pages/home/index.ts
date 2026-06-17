import { getMemoirIdentity } from "../../services/auth";
import { ApiError } from "../../services/request";

const WECHAT_PLACEHOLDER_DISPLAY_NAME = "微信用户";

interface HomeData {
  loading: boolean;
  error: string;
  displayName: string;
  projectId: string;
  projectTitle: string;
}

function isProvisionalWeChatEmail(email: string): boolean {
  return email.trim().toLowerCase().endsWith("@wechat.local");
}

export function userDisplayName(displayName: string, email: string): string {
  const normalizedName = displayName.trim();
  if (normalizedName && normalizedName !== WECHAT_PLACEHOLDER_DISPLAY_NAME) {
    return normalizedName;
  }
  if (isProvisionalWeChatEmail(email)) {
    return "作者";
  }
  return email.split("@", 1)[0].trim() || "作者";
}

Page<HomeData, {
  onShow(): Promise<void>;
  loadHome(): Promise<void>;
  startVoiceChat(): void;
}>({
  data: {
    loading: false,
    error: "",
    displayName: "",
    projectId: "",
    projectTitle: "",
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
      if (!identity.password_login_enabled) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({
        displayName: userDisplayName(identity.display_name, identity.email),
        projectId: identity.active_project_id,
        projectTitle: identity.active_project?.title || "我的回忆录",
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
    wx.switchTab({ url: "/pages/voice-start/index" });
  },
});
