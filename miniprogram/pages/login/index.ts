import {
  type BackendAuthUser,
  loginWithWeChat,
  updateBackendProfile,
} from "../../services/auth";
import {
  accessibilityPageClass,
  accessibilityPagePatch,
} from "../../services/accessibility-settings";
import { relaunchHome } from "../../services/navigation";
import { definePage } from "../../services/page-definition";
import {
  resolveBackendAvatarUrl,
  uploadBackendAvatar,
} from "../../services/user-profile";

const WECHAT_PLACEHOLDER_DISPLAY_NAME = "微信用户";
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

interface LoginData {
  pageClass: string;
  wechatLoading: boolean;
  profileMode: boolean;
  profileSaving: boolean;
  profileDisplayName: string;
  profileAvatarPath: string;
  error: string;
}

function hasUsableDisplayName(user: BackendAuthUser): boolean {
  const name = user.display_name.trim();
  return Boolean(name && name !== WECHAT_PLACEHOLDER_DISPLAY_NAME);
}

export function needsWeChatProfileSetup(user: BackendAuthUser): boolean {
  return !hasUsableDisplayName(user) || !String(user.avatar_url || "").trim();
}

definePage<LoginData>()({
  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    wechatLoading: false,
    profileMode: false,
    profileSaving: false,
    profileDisplayName: "",
    profileAvatarPath: "",
    error: "",
  },

  onShow() {
    this.setData(accessibilityPagePatch(PAGE_CLASS));
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

  async submitWeChat() {
    if (this.data.wechatLoading) {
      return;
    }
    this.setData({ wechatLoading: true, error: "" });
    try {
      const response = await loginWithWeChat();
      if (needsWeChatProfileSetup(response.user)) {
        this.setData({
          profileMode: true,
          profileDisplayName: hasUsableDisplayName(response.user) ? response.user.display_name.trim() : "",
          profileAvatarPath: resolveBackendAvatarUrl(response.user.avatar_url),
        });
      } else {
        relaunchHome();
      }
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
      relaunchHome();
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
    relaunchHome();
  },
});
