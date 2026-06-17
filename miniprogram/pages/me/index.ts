import type { MemoirVoiceProgressResponse } from "../../domain/voice-chat";
import type { TTSVoiceOption } from "../../services/tts-settings";
import {
  getMemoirIdentity,
  loginWithEmail,
  loginWithWeChat,
  logoutBackendSession,
  setAccountCredentials,
  updateBackendProfile,
} from "../../services/auth";
import { getMemoirVoiceProgress } from "../../services/memoir-voice-api";
import { ApiError } from "../../services/request";
import {
  readTTSSettings,
  saveTTSSettings,
  TTS_VOICE_OPTIONS,
  ttsVoiceLabel,
} from "../../services/tts-settings";
import { replaceMemoirStore } from "../../stores/session";

const WECHAT_PLACEHOLDER_DISPLAY_NAME = "微信用户";

interface MeData {
  loading: boolean;
  loggedIn: boolean;
  savingName: boolean;
  editingName: boolean;
  voiceStatsLoading: boolean;
  wechatLoading: boolean;
  credentialsLoading: boolean;
  error: string;
  displayName: string;
  nameDraft: string;
  avatarText: string;
  email: string;
  emailInput: string;
  password: string;
  credentialEmail: string;
  credentialPassword: string;
  credentialDisplayName: string;
  accountMode: boolean;
  credentialsMode: boolean;
  projectTitle: string;
  projectCount: number;
  sessionCount: number;
  utteranceCount: number;
  totalUserCharCount: number;
  totalAudioText: string;
  ttsVoice: string;
  ttsVoiceLabel: string;
  ttsVoiceOptions: TTSVoiceOption[];
}

interface VoiceDatasetEvent {
  currentTarget: {
    dataset: {
      voice?: string;
    };
  };
}

interface InputEvent {
  detail: {
    value: string;
  };
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
  return {
    sessionCount: progress.session_count,
    utteranceCount: progress.utterance_count,
    totalUserCharCount: progress.total_user_char_count,
    totalAudioText: formatAudioDuration(progress.total_audio_seconds),
  };
}

function credentialDisplayName(displayName: string): string {
  const normalizedName = displayName.trim();
  return normalizedName && normalizedName !== WECHAT_PLACEHOLDER_DISPLAY_NAME ? normalizedName : "";
}

function loggedOutData(credentialsMode = false): Partial<MeData> {
  return {
    loggedIn: false,
    editingName: false,
    voiceStatsLoading: false,
    displayName: "",
    nameDraft: "",
    avatarText: "作",
    email: "",
    projectTitle: "",
    projectCount: 0,
    sessionCount: 0,
    utteranceCount: 0,
    totalUserCharCount: 0,
    totalAudioText: "0 秒",
    credentialsMode,
  };
}

Page<MeData, {
  onShow(): Promise<void>;
  loadMe(): Promise<void>;
  loadVoiceStats(projectId: string): Promise<void>;
  changeEmailInput(event: InputEvent): void;
  changePassword(event: InputEvent): void;
  changeCredentialEmail(event: InputEvent): void;
  changeCredentialPassword(event: InputEvent): void;
  changeCredentialDisplayName(event: InputEvent): void;
  toggleAccountMode(): void;
  submitWeChat(): Promise<void>;
  submitAccount(): Promise<void>;
  submitCredentials(): Promise<void>;
  startEditName(): void;
  changeNameDraft(event: InputEvent): void;
  cancelEditName(): void;
  saveDisplayName(): Promise<void>;
  selectTTSVoice(event: VoiceDatasetEvent): void;
  openProjects(): void;
  openVoiceProgress(): void;
  logout(): Promise<void>;
}>({
  data: {
    loading: false,
    loggedIn: false,
    savingName: false,
    editingName: false,
    voiceStatsLoading: false,
    wechatLoading: false,
    credentialsLoading: false,
    error: "",
    displayName: "",
    nameDraft: "",
    avatarText: "作",
    email: "",
    emailInput: "",
    password: "",
    credentialEmail: "",
    credentialPassword: "",
    credentialDisplayName: "",
    accountMode: false,
    credentialsMode: false,
    projectTitle: "",
    projectCount: 0,
    sessionCount: 0,
    utteranceCount: 0,
    totalUserCharCount: 0,
    totalAudioText: "0 秒",
    ttsVoice: "",
    ttsVoiceLabel: "默认音色",
    ttsVoiceOptions: TTS_VOICE_OPTIONS,
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
        this.setData({
          ...loggedOutData(true),
          accountMode: false,
          credentialDisplayName: credentialDisplayName(identity.display_name),
        });
        return;
      }
      const displayName = displayNameForUser(identity.display_name, identity.email);
      const ttsSettings = readTTSSettings();
      this.setData({
        loggedIn: true,
        accountMode: false,
        credentialsMode: false,
        emailInput: "",
        password: "",
        credentialEmail: "",
        credentialPassword: "",
        credentialDisplayName: "",
        displayName,
        nameDraft: displayName,
        editingName: false,
        avatarText: Array.from(displayName)[0] || "作",
        email: identity.email,
        projectTitle: identity.active_project?.title || "我的回忆录",
        projectCount: identity.projects.length,
        ttsVoice: ttsSettings.voice,
        ttsVoiceLabel: ttsVoiceLabel(ttsSettings.voice),
      });
      await this.loadVoiceStats(identity.active_project_id);
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        this.setData(loggedOutData(false));
        return;
      }
      this.setData({
        ...loggedOutData(false),
        error: error instanceof Error ? error.message : "我的页面加载失败",
      });
    } finally {
      this.setData({ loading: false });
    }
  },

  changeEmailInput(event) {
    this.setData({ emailInput: event.detail.value, error: "" });
  },

  changePassword(event) {
    this.setData({ password: event.detail.value, error: "" });
  },

  changeCredentialEmail(event) {
    this.setData({ credentialEmail: event.detail.value, error: "" });
  },

  changeCredentialPassword(event) {
    this.setData({ credentialPassword: event.detail.value, error: "" });
  },

  changeCredentialDisplayName(event) {
    this.setData({ credentialDisplayName: event.detail.value, error: "" });
  },

  toggleAccountMode() {
    this.setData({
      accountMode: !this.data.accountMode,
      error: "",
    });
  },

  async submitWeChat() {
    if (this.data.loading || this.data.wechatLoading || this.data.credentialsLoading) {
      return;
    }
    this.setData({ wechatLoading: true, error: "" });
    try {
      const response = await loginWithWeChat();
      if (response.needs_credentials || !response.user.password_login_enabled) {
        this.setData({
          ...loggedOutData(true),
          accountMode: false,
          credentialDisplayName: credentialDisplayName(response.user.display_name),
          error: "",
        });
        return;
      }
      await this.loadMe();
      if (this.data.loggedIn) {
        wx.showToast({ title: "已登录", icon: "success" });
      }
    } catch (error) {
      this.setData({
        error: error instanceof Error ? error.message : "微信登录失败",
      });
    } finally {
      this.setData({ wechatLoading: false });
    }
  },

  async submitAccount() {
    if (this.data.loading || this.data.wechatLoading || this.data.credentialsLoading) {
      return;
    }
    const email = this.data.emailInput.trim();
    const password = this.data.password;
    if (!email || !password) {
      this.setData({ error: "请输入邮箱和密码" });
      return;
    }
    this.setData({ loading: true, error: "" });
    try {
      await loginWithEmail({ email, password });
      await this.loadMe();
      if (this.data.loggedIn) {
        wx.showToast({ title: "已登录", icon: "success" });
      }
    } catch (error) {
      this.setData({
        error: error instanceof Error ? error.message : "登录失败",
      });
    } finally {
      this.setData({ loading: false });
    }
  },

  async submitCredentials() {
    if (this.data.loading || this.data.wechatLoading || this.data.credentialsLoading) {
      return;
    }
    const email = this.data.credentialEmail.trim();
    const password = this.data.credentialPassword;
    const displayName = this.data.credentialDisplayName.trim();
    if (!email || !password) {
      this.setData({ error: "请输入邮箱和密码" });
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
        display_name: displayName || undefined,
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

  changeNameDraft(event) {
    this.setData({ nameDraft: event.detail.value, error: "" });
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
      const normalizedName = displayNameForUser(user.display_name, user.email);
      this.setData({
        displayName: normalizedName,
        nameDraft: normalizedName,
        avatarText: Array.from(normalizedName)[0] || "作",
        email: user.email,
        editingName: false,
      });
      wx.showToast({ title: "已保存", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "昵称保存失败" });
    } finally {
      this.setData({ savingName: false });
    }
  },

  async loadVoiceStats(projectId) {
    if (!projectId) {
      return;
    }
    this.setData({ voiceStatsLoading: true });
    try {
      const progress = await getMemoirVoiceProgress(projectId);
      this.setData(voiceStatsData(progress));
    } catch (error) {
      console.warn("load memoir voice stats failed", error);
    } finally {
      this.setData({ voiceStatsLoading: false });
    }
  },

  selectTTSVoice(event) {
    const voice = event.currentTarget.dataset.voice || "";
    const settings = saveTTSSettings({ voice });
    this.setData({
      ttsVoice: settings.voice,
      ttsVoiceLabel: ttsVoiceLabel(settings.voice),
    });
    wx.showToast({ title: "已保存", icon: "success" });
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
      this.setData({
        ...loggedOutData(false),
        loading: false,
        error: "",
        emailInput: "",
        password: "",
        credentialEmail: "",
        credentialPassword: "",
        credentialDisplayName: "",
        accountMode: false,
      });
      wx.showToast({ title: "已退出", icon: "success" });
    }
  },
});
