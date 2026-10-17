import {
  loginWithWeChat,
  updateBackendProfile,
} from "../../services/auth";
import {
  accessibilityPageClass,
  accessibilityPagePatch,
} from "../../services/accessibility-settings";
import { relaunchHome } from "../../services/navigation";
import { queryParam, type PageFormSubmitEvent, type PageQuery } from "../../services/page-input";
import { definePage } from "../../services/page-definition";
import { openWechatPrivacyContract } from "../../services/wechat-privacy";
import { uploadBackendAvatar } from "../../services/user-profile";

const PAGE_CLASS = "login-page ui-page-shell ui-accessible-page";

interface ChooseAvatarEvent {
  detail: {
    avatarUrl?: string;
  };
}

interface LoginData {
  pageClass: string;
  wechatLoading: boolean;
  // Opened right after a family member's first like; finishing returns to what they liked.
  // Logging in to tell a story never stops here: avatar and nickname only matter to likes.
  profileMode: boolean;
  profileSaving: boolean;
  profileAvatarPath: string;
  error: string;
}

definePage<LoginData>()({
  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    wechatLoading: false,
    profileMode: false,
    profileSaving: false,
    profileAvatarPath: "",
    error: "",
  },

  onLoad(query: PageQuery = {}) {
    if (queryParam(query, "from") === "like") {
      this.setData({ profileMode: true });
    }
  },

  onShow() {
    this.setData(accessibilityPagePatch(PAGE_CLASS));
  },

  finishProfile() {
    wx.navigateBack();
  },

  clearProfileError() {
    this.setData({ error: "" });
  },

  chooseWeChatAvatar(event: ChooseAvatarEvent) {
    const avatarPath = String(event.detail.avatarUrl || "").trim();
    if (!avatarPath) {
      return;
    }
    this.setData({ profileAvatarPath: avatarPath, error: "" });
  },

  async openPrivacyContract() {
    try {
      await openWechatPrivacyContract();
    } catch (error) {
      wx.showToast({ title: error instanceof Error ? error.message : "暂时无法打开隐私保护指引", icon: "none" });
    }
  },

  async submitWeChat() {
    if (this.data.wechatLoading) {
      return;
    }
    this.setData({ wechatLoading: true, error: "" });
    try {
      await loginWithWeChat();
      relaunchHome();
    } catch (error) {
      this.setData({
        error: error instanceof Error ? error.message : "微信登录失败",
      });
    } finally {
      this.setData({ wechatLoading: false });
    }
  },

  async saveWeChatProfile(event: PageFormSubmitEvent<{ nickname: string }>) {
    if (this.data.profileSaving) {
      return;
    }
    const displayName = event.detail.value.nickname.trim();
    if (!displayName) {
      this.setData({ error: "请填写昵称" });
      return;
    }
    this.setData({ profileSaving: true, error: "" });
    try {
      await updateBackendProfile({ display_name: displayName });
      if (this.data.profileAvatarPath && !/^https?:\/\//i.test(this.data.profileAvatarPath)) {
        await uploadBackendAvatar(this.data.profileAvatarPath);
      }
      this.finishProfile();
    } catch (error) {
      this.setData({
        error: error instanceof Error ? error.message : "微信资料保存失败",
      });
    } finally {
      this.setData({ profileSaving: false });
    }
  },

  skipWeChatProfile() {
    if (this.data.profileSaving) {
      return;
    }
    this.finishProfile();
  },
});
