import type { MemoirVoiceProgressResponse } from "../../domain/voice-chat";
import type { TTSSpeedOption, TTSSettingsScope, TTSVoiceOption } from "../../services/tts-settings";
import {
  getMemoirIdentity,
  isValidEmail,
  loginWithEmail,
  loginWithWeChat,
  logoutBackendSession,
  setAccountCredentials,
  updateBackendProfile,
} from "../../services/auth";
import { getMemoirVoiceProgress } from "../../services/memoir-voice-api";
import { ApiError } from "../../services/request";
import {
  AI_MODEL_OPTIONS,
  aiModelLabel,
  readSelectedAIModel,
  saveSelectedAIModel,
  type AIModelOption,
} from "../../services/ai-model-settings";
import { formatBeijingDateTime } from "../../services/time";
import {
  readScopedTTSSettings,
  readTTSSettingsProfiles,
  saveScopedTTSSettings,
  TTS_SPEED_OPTIONS,
  TTS_VOICE_OPTIONS,
  ttsSpeedLabel,
  ttsVoiceLabel,
} from "../../services/tts-settings";
import {
  readVoiceAuthorizationConsentRecord,
  VOICE_AUTHORIZATION_PAGE_URL,
  VOICE_AUTHORIZATION_VERSION,
} from "../../services/voice-authorization";
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
  avatarUrl: string;
  email: string;
  accountEmailText: string;
  passwordLoginEnabled: boolean;
  emailInput: string;
  password: string;
  credentialEmail: string;
  credentialPassword: string;
  accountMode: boolean;
  projectTitle: string;
  projectCount: number;
  voiceStatsProjectId: string;
  sessionCount: number;
  utteranceCount: number;
  totalUserCharCount: number;
  totalAudioText: string;
  aiModel: string;
  aiModelLabel: string;
  aiModelOptions: AIModelOption[];
  voiceAuthorizationAgreed: boolean;
  voiceAuthorizationStatusText: string;
  voiceAuthorizationDetailText: string;
  chatTTSVoice: string;
  chatTTSVoiceLabel: string;
  bookTTSVoice: string;
  bookTTSVoiceLabel: string;
  ttsVoiceOptions: TTSVoiceOption[];
  chatTTSSpeedRatio: number;
  chatTTSSpeedLabel: string;
  bookTTSSpeedRatio: number;
  bookTTSSpeedLabel: string;
  ttsSpeedOptions: TTSSpeedOption[];
}

interface VoiceDatasetEvent {
  currentTarget: {
    dataset: {
      scope?: TTSSettingsScope;
      voice?: string;
    };
  };
}

interface SpeedDatasetEvent {
  currentTarget: {
    dataset: {
      scope?: TTSSettingsScope;
      speed?: string | number;
    };
  };
}

interface ModelDatasetEvent {
  currentTarget: {
    dataset: {
      model?: string;
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

function ttsScopeData(scope: TTSSettingsScope, settings: { voice: string; speedRatio: number }): Partial<MeData> {
  if (scope === "book") {
    return {
      bookTTSVoice: settings.voice,
      bookTTSVoiceLabel: ttsVoiceLabel(settings.voice),
      bookTTSSpeedRatio: settings.speedRatio,
      bookTTSSpeedLabel: ttsSpeedLabel(settings.speedRatio),
    };
  }
  return {
    chatTTSVoice: settings.voice,
    chatTTSVoiceLabel: ttsVoiceLabel(settings.voice),
    chatTTSSpeedRatio: settings.speedRatio,
    chatTTSSpeedLabel: ttsSpeedLabel(settings.speedRatio),
  };
}

function ttsProfilesData(): Partial<MeData> {
  const profiles = readTTSSettingsProfiles();
  return {
    ...ttsScopeData("chat", profiles.chat),
    ...ttsScopeData("book", profiles.book),
  };
}

function aiModelSettingsData(): Pick<MeData, "aiModel" | "aiModelLabel" | "aiModelOptions"> {
  const model = readSelectedAIModel();
  return {
    aiModel: model,
    aiModelLabel: aiModelLabel(model),
    aiModelOptions: AI_MODEL_OPTIONS,
  };
}

function voiceAuthorizationSettingsData(): Pick<
  MeData,
  "voiceAuthorizationAgreed" | "voiceAuthorizationStatusText" | "voiceAuthorizationDetailText"
> {
  const consent = readVoiceAuthorizationConsentRecord();
  if (consent?.agreed) {
    const agreedAtText = formatBeijingDateTime(consent.agreedAt);
    return {
      voiceAuthorizationAgreed: true,
      voiceAuthorizationStatusText: "已同意",
      voiceAuthorizationDetailText: `${agreedAtText ? `${agreedAtText} 已同意 · ` : ""}协议版本 ${consent.version}`,
    };
  }
  return {
    voiceAuthorizationAgreed: false,
    voiceAuthorizationStatusText: "未同意",
    voiceAuthorizationDetailText: `开始语音前需要阅读并同意当前版本协议 ${VOICE_AUTHORIZATION_VERSION}`,
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
    email: "",
    accountEmailText: "",
    passwordLoginEnabled: false,
    emailInput: "",
    password: "",
    credentialEmail: "",
    credentialPassword: "",
    accountMode: false,
    credentialsLoading: false,
    projectTitle: "",
    projectCount: 0,
    voiceStatsProjectId: "",
    sessionCount: 0,
    utteranceCount: 0,
    totalUserCharCount: 0,
    totalAudioText: "0 秒",
    ...voiceAuthorizationSettingsData(),
  };
}

Page<MeData, {
  onShow(): Promise<void>;
  loadMe(): Promise<void>;
  loadVoiceStats(projectId: string): Promise<void>;
  refreshVoiceAuthorizationConsent(): void;
  changeEmailInput(event: InputEvent): void;
  changePassword(event: InputEvent): void;
  changeCredentialEmail(event: InputEvent): void;
  changeCredentialPassword(event: InputEvent): void;
  toggleAccountMode(): void;
  submitWeChat(): Promise<void>;
  submitAccount(): Promise<void>;
  submitCredentials(): Promise<void>;
  startEditName(): void;
  changeNameDraft(event: InputEvent): void;
  cancelEditName(): void;
  saveDisplayName(): Promise<void>;
  selectTTSVoice(event: VoiceDatasetEvent): void;
  selectTTSSpeed(event: SpeedDatasetEvent): void;
  selectAIModel(event: ModelDatasetEvent): void;
  openVoiceAuthorization(): void;
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
    avatarUrl: "",
    email: "",
    accountEmailText: "",
    passwordLoginEnabled: false,
    emailInput: "",
    password: "",
    credentialEmail: "",
    credentialPassword: "",
    accountMode: false,
    projectTitle: "",
    projectCount: 0,
    voiceStatsProjectId: "",
    sessionCount: 0,
    utteranceCount: 0,
    totalUserCharCount: 0,
    totalAudioText: "0 秒",
    ...aiModelSettingsData(),
    ...voiceAuthorizationSettingsData(),
    chatTTSVoice: "",
    chatTTSVoiceLabel: "默认音色",
    bookTTSVoice: "",
    bookTTSVoiceLabel: "默认音色",
    ttsVoiceOptions: TTS_VOICE_OPTIONS,
    chatTTSSpeedRatio: 1,
    chatTTSSpeedLabel: "正常 1.0x",
    bookTTSSpeedRatio: 1,
    bookTTSSpeedLabel: "正常 1.0x",
    ttsSpeedOptions: TTS_SPEED_OPTIONS,
  },

  async onShow() {
    this.refreshVoiceAuthorizationConsent();
    if (!this.data.loading) {
      await this.loadMe();
    }
  },

  refreshVoiceAuthorizationConsent() {
    this.setData(voiceAuthorizationSettingsData());
  },

  async loadMe() {
    this.setData({ loading: true, error: "" });
    try {
      const identity = await getMemoirIdentity();
      const displayName = displayNameForUser(identity.display_name, identity.email);
      const activeProjectId = identity.active_project_id || "";
      const keepVoiceStats = Boolean(activeProjectId && this.data.voiceStatsProjectId === activeProjectId);
      this.setData({
        loggedIn: true,
        accountMode: false,
        emailInput: "",
        password: "",
        credentialEmail: "",
        credentialPassword: "",
        displayName,
        nameDraft: displayName,
        editingName: false,
        avatarText: Array.from(displayName)[0] || "作",
        avatarUrl: identity.avatar_url || "",
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
        ...ttsProfilesData(),
      });
      if (activeProjectId) {
        await this.loadVoiceStats(activeProjectId);
      }
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
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
      await loginWithWeChat();
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
    if (!isValidEmail(email)) {
      this.setData({ error: "请输入正确的邮箱地址" });
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

  async loadVoiceStats(projectId) {
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

  selectTTSVoice(event) {
    const scope = event.currentTarget.dataset.scope === "book" ? "book" : "chat";
    const voice = event.currentTarget.dataset.voice || "";
    const settings = saveScopedTTSSettings(scope, { ...readScopedTTSSettings(scope), voice });
    this.setData(ttsScopeData(scope, settings));
    wx.showToast({ title: "已保存", icon: "success" });
  },

  selectTTSSpeed(event) {
    const scope = event.currentTarget.dataset.scope === "book" ? "book" : "chat";
    const speedRatio = Number(event.currentTarget.dataset.speed);
    const settings = saveScopedTTSSettings(scope, { ...readScopedTTSSettings(scope), speedRatio });
    this.setData(ttsScopeData(scope, settings));
    wx.showToast({ title: "已保存", icon: "success" });
  },

  selectAIModel(event) {
    const model = saveSelectedAIModel(event.currentTarget.dataset.model || "");
    this.setData({
      aiModel: model,
      aiModelLabel: aiModelLabel(model),
      aiModelOptions: AI_MODEL_OPTIONS,
    });
    wx.showToast({ title: "已保存", icon: "success" });
  },

  openVoiceAuthorization() {
    wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
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
        ...loggedOutData(),
        loading: false,
        error: "",
      });
      wx.showToast({ title: "已退出", icon: "success" });
    }
  },
});
