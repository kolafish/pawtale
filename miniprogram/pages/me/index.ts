import type { MemoirVoiceProgressResponse } from "../../domain/memoir-voice-session";
import { definePage } from "../../services/page-definition";
import {
  getMemoirIdentity,
  isValidEmail,
  logoutBackendSession,
  setAccountCredentials,
  updateBackendProfile,
} from "../../services/auth";
import { getMemoirVoiceProgress } from "../../services/memoir-voice-session-api";
import { isAuthError, navigateToLogin } from "../../services/navigation";
import { type PageTextInputEvent } from "../../services/page-input";
import {
  aiModelLabel,
  readSelectedAIModel,
} from "../../services/ai-model-settings";
import {
  accessibilityPageClass,
  readAccessibilitySettings,
  saveLargeTextEnabled,
} from "../../services/accessibility-settings";
import {
  readVoiceAuthorizationConsentRecord,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "../../services/voice-authorization";
import {
  resolveBackendAvatarUrl,
  uploadBackendAvatar,
} from "../../services/user-profile";
import { clearCurrentUserMemoirSnapshots } from "../../stores/memoir-read-cache";
import { replaceMemoirStore } from "../../stores/session";
import {
  isProvisionalWeChatEmail,
  userDisplayName,
} from "../../services/user-display-name";

interface MeData {
  pageClass: string;
  loading: boolean;
  loggedIn: boolean;
  savingName: boolean;
  avatarSaving: boolean;
  editingName: boolean;
  voiceStatsLoading: boolean;
  credentialsLoading: boolean;
  error: string;
  displayName: string;
  nameDraft: string;
  avatarText: string;
  avatarUrl: string;
  email: string;
  accountEmailText: string;
  passwordLoginEnabled: boolean;
  credentialEmail: string;
  credentialPassword: string;
  projectTitle: string;
  projectCount: number;
  voiceStatsProjectId: string;
  sessionCount: number;
  utteranceCount: number;
  totalUserCharCount: number;
  totalAudioText: string;
  aiModelLabel: string;
  voiceAuthorizationAgreed: boolean;
  voiceAuthorizationStatusText: string;
  largeTextEnabled: boolean;
  showAccountSettings: boolean;
}

type InputEvent = PageTextInputEvent;

interface SwitchChangeEvent {
  detail: {
    value: boolean;
  };
}

interface ChooseAvatarEvent {
  detail: {
    avatarUrl?: string;
  };
}

function accountEmailText(email: string, passwordLoginEnabled: boolean): string {
  if (!passwordLoginEnabled && isProvisionalWeChatEmail(email)) {
    return "微信账号登录";
  }
  return email;
}

function formatAudioDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds || 0));
  const minutes = Math.floor(seconds / 60);
  const restSeconds = seconds % 60;
  if (!minutes) {
    return `${restSeconds} 秒`;
  }
  return `${minutes} 分 ${restSeconds} 秒`;
}

function voiceStatsData(progress: MemoirVoiceProgressResponse) {
  const snapshot = progress.snapshot;
  return {
    sessionCount: snapshot?.session_count ?? progress.session_count,
    utteranceCount: snapshot?.valid_utterance_count ?? progress.utterance_count,
    totalUserCharCount: snapshot?.source_char_count ?? progress.total_user_char_count,
    totalAudioText: formatAudioDuration(snapshot?.total_audio_seconds ?? progress.total_audio_seconds),
  };
}

function aiModelSettingsData(): Pick<MeData, "aiModelLabel"> {
  const model = readSelectedAIModel();
  return {
    aiModelLabel: aiModelLabel(model),
  };
}

function voiceAuthorizationSettingsData(): Pick<
  MeData,
  "voiceAuthorizationAgreed" | "voiceAuthorizationStatusText"
> {
  const consent = readVoiceAuthorizationConsentRecord();
  if (consent?.agreed) {
    return {
      voiceAuthorizationAgreed: true,
      voiceAuthorizationStatusText: "已同意",
    };
  }
  return {
    voiceAuthorizationAgreed: false,
    voiceAuthorizationStatusText: "未同意",
  };
}

function accessibilitySettingsData(): Pick<MeData, "pageClass" | "largeTextEnabled"> {
  const settings = readAccessibilitySettings();
  return {
    pageClass: accessibilityPageClass("me-page", settings.largeTextEnabled),
    largeTextEnabled: settings.largeTextEnabled,
  };
}

function loggedOutData(): Partial<MeData> {
  return {
    loggedIn: false,
    editingName: false,
    voiceStatsLoading: false,
    displayName: "",
    nameDraft: "",
    avatarText: "作",
    avatarUrl: "",
    avatarSaving: false,
    email: "",
    accountEmailText: "",
    passwordLoginEnabled: false,
    credentialEmail: "",
    credentialPassword: "",
    credentialsLoading: false,
    showAccountSettings: false,
    projectTitle: "",
    projectCount: 0,
    voiceStatsProjectId: "",
    sessionCount: 0,
    utteranceCount: 0,
    totalUserCharCount: 0,
    totalAudioText: "0 秒",
    ...aiModelSettingsData(),
    ...voiceAuthorizationSettingsData(),
    ...accessibilitySettingsData(),
  };
}

definePage<MeData>()({
  data: {
    pageClass: "me-page",
    loading: false,
    loggedIn: false,
    savingName: false,
    avatarSaving: false,
    editingName: false,
    voiceStatsLoading: false,
    credentialsLoading: false,
    error: "",
    displayName: "",
    nameDraft: "",
    avatarText: "作",
    avatarUrl: "",
    email: "",
    accountEmailText: "",
    passwordLoginEnabled: false,
    credentialEmail: "",
    credentialPassword: "",
    projectTitle: "",
    projectCount: 0,
    voiceStatsProjectId: "",
    sessionCount: 0,
    utteranceCount: 0,
    totalUserCharCount: 0,
    totalAudioText: "0 秒",
    ...aiModelSettingsData(),
    ...voiceAuthorizationSettingsData(),
    largeTextEnabled: false,
    showAccountSettings: false,
  },

  async onShow() {
    this.refreshVoiceAuthorizationConsent();
    this.refreshAccessibilitySettings();
    if (!this.data.loading) {
      await this.loadMe();
    }
  },

  refreshVoiceAuthorizationConsent() {
    this.setData(voiceAuthorizationSettingsData());
  },

  refreshAccessibilitySettings() {
    this.setData(accessibilitySettingsData());
  },

  async loadMe() {
    this.setData({ loading: true, error: "" });
    try {
      const identity = await getMemoirIdentity();
      const displayName = userDisplayName(identity.display_name, identity.email);
      const activeProjectId = identity.active_project_id || "";
      const keepVoiceStats = Boolean(activeProjectId && this.data.voiceStatsProjectId === activeProjectId);
      this.setData({
        loggedIn: true,
        credentialEmail: "",
        credentialPassword: "",
        displayName,
        nameDraft: displayName,
        editingName: false,
        avatarText: Array.from(displayName)[0] || "作",
        avatarUrl: resolveBackendAvatarUrl(identity.avatar_url),
        email: identity.email,
        accountEmailText: accountEmailText(identity.email, identity.password_login_enabled),
        passwordLoginEnabled: identity.password_login_enabled,
        projectTitle: identity.active_project?.title || "尚未创建回忆录",
        projectCount: identity.projects.length,
        ...(keepVoiceStats ? {} : {
          voiceStatsProjectId: activeProjectId,
          sessionCount: 0,
          utteranceCount: 0,
          totalUserCharCount: 0,
          totalAudioText: "0 秒",
        }),
        ...aiModelSettingsData(),
        ...accessibilitySettingsData(),
      });
      if (activeProjectId) {
        await this.loadVoiceStats(activeProjectId);
      }
    } catch (error) {
      if (isAuthError(error)) {
        this.setData(loggedOutData());
        return;
      }
      this.setData({
        ...loggedOutData(),
        error: error instanceof Error ? error.message : "我的页面加载失败",
      });
    } finally {
      this.setData({ loading: false });
    }
  },

  changeCredentialEmail(event: InputEvent) {
    this.setData({ credentialEmail: event.detail.value, error: "" });
  },

  changeCredentialPassword(event: InputEvent) {
    this.setData({ credentialPassword: event.detail.value, error: "" });
  },

  async submitCredentials() {
    if (this.data.loading || this.data.credentialsLoading) {
      return;
    }
    const email = this.data.credentialEmail.trim();
    const password = this.data.credentialPassword;
    if (!email || !password) {
      this.setData({ error: "请输入邮箱和密码" });
      return;
    }
    if (!isValidEmail(email)) {
      this.setData({ error: "请输入正确的邮箱地址" });
      return;
    }
    if (password.length < 8) {
      this.setData({ error: "密码至少需要 8 位" });
      return;
    }
    this.setData({ credentialsLoading: true, error: "" });
    try {
      const response = await setAccountCredentials({
        email,
        password,
      });
      if (response.needs_credentials) {
        this.setData({ error: "账号还没有设置完成，请重新提交" });
        return;
      }
      await this.loadMe();
      if (this.data.loggedIn) {
        wx.showToast({ title: "已登录", icon: "success" });
      }
    } catch (error) {
      this.setData({
        error: error instanceof Error ? error.message : "保存账号失败",
      });
    } finally {
      this.setData({ credentialsLoading: false });
    }
  },

  startEditName() {
    if (this.data.loading || this.data.savingName) {
      return;
    }
    this.setData({ editingName: true, nameDraft: this.data.displayName, error: "" });
  },

  changeNameDraft(event: InputEvent) {
    this.setData({ nameDraft: event.detail.value, error: "" });
  },

  async chooseWeChatAvatar(event: ChooseAvatarEvent) {
    if (this.data.avatarSaving || this.data.loading) {
      return;
    }
    const avatarPath = String(event.detail.avatarUrl || "").trim();
    if (!avatarPath) {
      return;
    }
    this.setData({ avatarSaving: true, error: "" });
    try {
      const user = await uploadBackendAvatar(avatarPath);
      await getMemoirIdentity();
      this.setData({
        avatarUrl: resolveBackendAvatarUrl(user.avatar_url),
      });
      wx.showToast({ title: "头像已更新", icon: "success" });
    } catch (error) {
      this.setData({
        error: error instanceof Error ? error.message : "头像保存失败",
      });
    } finally {
      this.setData({ avatarSaving: false });
    }
  },

  cancelEditName() {
    if (this.data.savingName) {
      return;
    }
    this.setData({ editingName: false, nameDraft: this.data.displayName, error: "" });
  },

  async saveDisplayName() {
    if (this.data.savingName) {
      return;
    }
    const displayName = this.data.nameDraft.trim();
    if (!displayName) {
      this.setData({ error: "昵称不能为空" });
      return;
    }
    this.setData({ savingName: true, error: "" });
    try {
      const user = await updateBackendProfile({ display_name: displayName });
      await getMemoirIdentity();
      const normalizedName = userDisplayName(user.display_name, user.email);
      this.setData({
        displayName: normalizedName,
        nameDraft: normalizedName,
        avatarText: Array.from(normalizedName)[0] || "作",
        email: user.email,
        accountEmailText: accountEmailText(user.email, user.password_login_enabled),
        passwordLoginEnabled: user.password_login_enabled,
        editingName: false,
      });
      wx.showToast({ title: "已保存", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "昵称保存失败" });
    } finally {
      this.setData({ savingName: false });
    }
  },

  async loadVoiceStats(projectId: string) {
    if (!projectId) {
      return;
    }
    this.setData({ voiceStatsLoading: true });
    try {
      const progress = await getMemoirVoiceProgress(projectId);
      this.setData({
        voiceStatsProjectId: projectId,
        ...voiceStatsData(progress),
      });
    } catch (error) {
      console.warn("load memoir voice stats failed", error);
    } finally {
      this.setData({ voiceStatsLoading: false });
    }
  },

  changeLargeText(event: SwitchChangeEvent) {
    const settings = saveLargeTextEnabled(event.detail.value);
    this.setData({
      pageClass: accessibilityPageClass("me-page", settings.largeTextEnabled),
      largeTextEnabled: settings.largeTextEnabled,
    });
    wx.showToast({ title: settings.largeTextEnabled ? "大字模式已打开" : "大字模式已关闭", icon: "none" });
  },

  toggleAccountSettings() {
    this.setData({ showAccountSettings: !this.data.showAccountSettings });
  },

  openVoiceAuthorization() {
    wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
  },

  openLogin() {
    navigateToLogin();
  },

  openProjects() {
    wx.navigateTo({ url: "/pages/projects/index" });
  },

  openVoiceProgress() {
    if (!this.data.projectCount) {
      return;
    }
    wx.navigateTo({ url: "/pages/voice-progress/index" });
  },

  openSpeakerTranscription() {
    wx.navigateTo({ url: "/pages/speaker-transcription/index" });
  },

  openVoiceSettings() {
    wx.navigateTo({ url: "/pages/voice-settings/index" });
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
      clearCurrentUserMemoirSnapshots();
      replaceMemoirStore({});
      this.setData({
        ...loggedOutData(),
        loading: false,
        error: "",
      });
      wx.showToast({ title: "已退出", icon: "success" });
    }
  },
});
