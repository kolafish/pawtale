import {
  loginWithWeChat,
  updateBackendProfile,
} from "../../services/auth";
import {
  accessibilityPageClass,
  accessibilityPagePatch,
} from "../../services/accessibility-settings";
import { relaunchHome } from "../../services/navigation";
import { queryParam, type PageQuery } from "../../services/page-input";
import { definePage } from "../../services/page-definition";
import { openWechatPrivacyContract } from "../../services/wechat-privacy";
import { completeShareLikeLogin } from "../../services/share-likes";
import type { MemoirShareLikeTarget } from "../../domain/memoir-share-likes";
import { uploadBackendAvatar } from "../../services/user-profile";

const PAGE_CLASS = "login-page ui-page-shell ui-accessible-page";

interface InputEvent {
  detail: {
    value: string;
  };
}

interface ChooseAvatarEvent {
  detail: {
    avatarUrl?: string;
  };
}

interface CheckboxChangeEvent {
  detail: {
    value: string[];
  };
}

interface LoginData {
  pageClass: string;
  wechatLoading: boolean;
  privacyAgreed: boolean;
  // Opened right after a family member's first like; finishing returns to what they liked.
  // Logging in to tell a story never stops here: avatar and nickname only matter to likes.
  profileMode: boolean;
  profileSaving: boolean;
  profileDisplayName: string;
  profileAvatarPath: string;
  error: string;
}

definePage<LoginData>()({
  shareLikeTarget: null as MemoirShareLikeTarget | null,
  shareLikeValue: false,

  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    wechatLoading: false,
    privacyAgreed: false,
    profileMode: false,
    profileSaving: false,
    profileDisplayName: "",
    profileAvatarPath: "",
    error: "",
  },

  onLoad(query: PageQuery = {}) {
    if (queryParam(query, "from") === "like") {
      const videoCode = queryParam(query, "video_code");
      const readingToken = queryParam(query, "reading_token");
      this.shareLikeTarget = videoCode ? { video_code: videoCode }
        : readingToken ? { reading_token: readingToken } : null;
      this.shareLikeValue = queryParam(query, "liked") === "1";
      this.setData({ profileMode: !this.shareLikeTarget });
    }
  },

  onShow() {
    this.setData(accessibilityPagePatch(PAGE_CLASS));
  },

  finishProfile() {
    wx.navigateBack();
  },

  changeProfileDisplayName(event: InputEvent) {
    this.setData({ profileDisplayName: event.detail.value, error: "" });
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

  changePrivacyConsent(event: CheckboxChangeEvent) {
    this.setData({
      privacyAgreed: event.detail.value.includes("privacy_contract"),
      error: "",
    });
  },

  async submitWeChat() {
    if (this.data.wechatLoading) {
      return;
    }
    if (!this.data.privacyAgreed) {
      this.setData({ error: "请先阅读并勾选同意《用户隐私保护指引》后再登录" });
      return;
    }
    this.setData({ wechatLoading: true, error: "" });
    try {
      const { user } = await loginWithWeChat();
      if (this.shareLikeTarget) {
        const profileMode = await completeShareLikeLogin(this.shareLikeTarget, this.shareLikeValue, user);
        this.shareLikeTarget = null;
        if (profileMode) {
          this.setData({ profileMode: true });
        } else {
          this.finishProfile();
        }
        return;
      }
      relaunchHome();
    } catch (error) {
      this.setData({
        error: error instanceof Error ? error.message : "微信登录失败",
      });
    } finally {
      this.setData({ wechatLoading: false });
    }
  },

  async saveWeChatProfile() {
    if (this.data.profileSaving) {
      return;
    }
    const displayName = this.data.profileDisplayName.trim();
    if (!displayName) {
      this.setData({ error: "请填写昵称；点击输入框可直接选择微信昵称" });
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
