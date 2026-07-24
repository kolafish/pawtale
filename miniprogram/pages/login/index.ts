import {
  type BackendAuthUser,
  isValidEmail,
  loginWithEmail,
  loginWithWeChat,
  updateBackendProfile,
} from "../../services/auth";
import { relaunchHome } from "../../services/navigation";
import { definePage } from "../../services/page-definition";
import {
  resolveBackendAvatarUrl,
  uploadBackendAvatar,
} from "../../services/user-profile";

const WECHAT_PLACEHOLDER_DISPLAY_NAME = "微信用户";

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
  email: string;
  password: string;
  accountMode: boolean;
  loading: boolean;
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
    email: "",
    password: "",
    accountMode: false,
    loading: false,
    wechatLoading: false,
    profileMode: false,
    profileSaving: false,
    profileDisplayName: "",
    profileAvatarPath: "",
    error: "",
  },

  changeEmail(event: InputEvent) {
    this.setData({ email: event.detail.value, error: "" });
  },

  changePassword(event: InputEvent) {
    this.setData({ password: event.detail.value, error: "" });
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

  toggleAccountMode() {
    this.setData({
      accountMode: !this.data.accountMode,
      error: "",
    });
  },

  async submitWeChat() {
    if (this.data.loading || this.data.wechatLoading) {
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

  async submitAccount() {
    if (this.data.loading || this.data.wechatLoading) {
      return;
    }
    const email = this.data.email.trim();
    const password = this.data.password;
    if (!email || !password) {
      this.setData({ error: "请输入邮箱和密码" });
      return;
    }
    if (!isValidEmail(email)) {
      this.setData({ error: "请输入正确的邮箱地址" });
      return;
    }
    this.setData({ loading: true, error: "" });
    try {
      await loginWithEmail({ email, password });
      relaunchHome();
    } catch (error) {
      this.setData({
        error: error instanceof Error ? error.message : "登录失败",
      });
    } finally {
      this.setData({ loading: false });
    }
  },
});
