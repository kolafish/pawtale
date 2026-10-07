import type { MemoirVoiceProgressResponse } from "../../domain/memoir-voice-session";
import { definePage } from "../../services/page-definition";
import {
  getMemoirIdentity,
  deleteAccountWithWeChat,
  logoutBackendSession,
  updateBackendProfile,
} from "../../services/auth";
import { getMemoirVoiceProgress } from "../../services/memoir-voice-session-api";
import {
  getAICreditSummary,
  getAIUsageDetails,
  type AICreditSummary,
  type AIUsageDetail,
} from "../../services/billing";
import { isAuthError, navigateToLogin, relaunchLogin } from "../../services/navigation";
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
  clearVoiceAuthorizationConsentRecord,
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

interface CreditUsagePurposeGroup {
  purpose: string;
  count: number;
  amountText: string;
}

interface CreditUsageDayGroup {
  dateText: string;
  amountText: string;
  purposes: CreditUsagePurposeGroup[];
}

interface MeData {
  pageClass: string;
  loading: boolean;
  deletingAccount: boolean;
  loggedIn: boolean;
  savingName: boolean;
  avatarSaving: boolean;
  editingName: boolean;
  voiceStatsLoading: boolean;
  creditLoading: boolean;
  creditAvailable: boolean;
  creditError: string;
  creditTotalText: string;
  creditUsedText: string;
  creditRemainingText: string;
  creditProgressPercent: string;
  creditUsageExpanded: boolean;
  creditUsageLoading: boolean;
  creditUsageError: string;
  creditUsageItems: AIUsageDetail[];
  creditUsageDays: CreditUsageDayGroup[];
  creditUsageHasMore: boolean;
  rechargeQrVisible: boolean;
  error: string;
  displayName: string;
  nameDraft: string;
  avatarText: string;
  avatarUrl: string;
  email: string;
  accountEmailText: string;
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
    return `${restSeconds}秒`;
  }
  return `${minutes}分${restSeconds}秒`;
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

function creditAmount(value: unknown): number {
  const amount = Number(value);
  return Number.isFinite(amount) && amount >= 0 ? amount : 0;
}

function formatCreditAmount(value: unknown): string {
  return creditAmount(value).toFixed(2);
}

function formatUsageAmount(micros: number): string {
  return (micros / 1_000_000).toFixed(6).replace(/(\.\d{2}\d*?)0+$/, "$1");
}

function creditUsageDayGroups(items: AIUsageDetail[]): CreditUsageDayGroup[] {
  const pad = (value: number) => String(value).padStart(2, "0");
  const days = new Map<string, {
    amountMicros: number;
    purposes: Map<string, { count: number; amountMicros: number }>;
  }>();
  for (const item of items) {
    const date = new Date(item.created_at);
    const dateText = Number.isNaN(date.getTime())
      ? "日期未知"
      : `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    const amountMicros = Math.round(creditAmount(item.cost_rmb) * 1_000_000);
    const day = days.get(dateText) || { amountMicros: 0, purposes: new Map() };
    const purpose = day.purposes.get(item.purpose) || { count: 0, amountMicros: 0 };
    purpose.count += 1;
    purpose.amountMicros += amountMicros;
    day.amountMicros += amountMicros;
    day.purposes.set(item.purpose, purpose);
    days.set(dateText, day);
  }
  return Array.from(days, ([dateText, day]) => ({
    dateText,
    amountText: formatUsageAmount(day.amountMicros),
    purposes: Array.from(day.purposes, ([purpose, group]) => ({
      purpose,
      count: group.count,
      amountText: formatUsageAmount(group.amountMicros),
    })),
  }));
}

function creditData(summary: AICreditSummary): Pick<
  MeData,
  | "creditAvailable"
  | "creditError"
  | "creditTotalText"
  | "creditUsedText"
  | "creditRemainingText"
  | "creditProgressPercent"
> {
  const total = creditAmount(summary.total_credit_rmb);
  const used = creditAmount(summary.used_rmb);
  return {
    creditAvailable: true,
    creditError: "",
    creditTotalText: formatCreditAmount(total),
    creditUsedText: formatCreditAmount(used),
    creditRemainingText: formatCreditAmount(summary.remaining_rmb),
    creditProgressPercent: (total > 0 ? Math.min(100, Math.max(0, (used / total) * 100)) : 0).toFixed(1),
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
    deletingAccount: false,
    editingName: false,
    voiceStatsLoading: false,
    creditLoading: false,
    creditAvailable: false,
    creditError: "",
    creditTotalText: "0.00",
    creditUsedText: "0.00",
    creditRemainingText: "0.00",
    creditProgressPercent: "0.0",
    creditUsageExpanded: false,
    creditUsageLoading: false,
    creditUsageError: "",
    creditUsageItems: [],
    creditUsageDays: [],
    creditUsageHasMore: false,
    rechargeQrVisible: false,
    displayName: "",
    nameDraft: "",
    avatarText: "作",
    avatarUrl: "",
    avatarSaving: false,
    email: "",
    accountEmailText: "",
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
    deletingAccount: false,
    loggedIn: false,
    savingName: false,
    avatarSaving: false,
    editingName: false,
    voiceStatsLoading: false,
    creditLoading: false,
    creditAvailable: false,
    creditError: "",
    creditTotalText: "0.00",
    creditUsedText: "0.00",
    creditRemainingText: "0.00",
    creditProgressPercent: "0.0",
    creditUsageExpanded: false,
    creditUsageLoading: false,
    creditUsageError: "",
    creditUsageItems: [],
    creditUsageDays: [],
    creditUsageHasMore: false,
    rechargeQrVisible: false,
    error: "",
    displayName: "",
    nameDraft: "",
    avatarText: "作",
    avatarUrl: "",
    email: "",
    accountEmailText: "",
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
        displayName,
        nameDraft: displayName,
        editingName: false,
        avatarText: Array.from(displayName)[0] || "作",
        avatarUrl: resolveBackendAvatarUrl(identity.avatar_url),
        email: identity.email,
        accountEmailText: accountEmailText(identity.email, identity.password_login_enabled),
        projectTitle: identity.active_project?.title || "尚未创建回忆录",
        projectCount: identity.projects.length,
        creditUsageExpanded: false,
        creditUsageLoading: false,
        creditUsageError: "",
        creditUsageItems: [],
        creditUsageDays: [],
        creditUsageHasMore: false,
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
        await Promise.all([this.loadVoiceStats(activeProjectId), this.loadAICreditSummary()]);
      } else {
        await this.loadAICreditSummary();
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

  async loadAICreditSummary() {
    this.setData({ creditLoading: true, creditError: "" });
    try {
      const summary = await getAICreditSummary();
      this.setData(creditData(summary));
    } catch (error) {
      console.warn("load AI credit summary failed", error);
      this.setData({
        creditAvailable: false,
        creditError: error instanceof Error ? error.message : "额度暂时无法加载",
      });
    } finally {
      this.setData({ creditLoading: false });
    }
  },

  async toggleCreditUsageDetails() {
    if (this.data.creditUsageExpanded) {
      this.setData({ creditUsageExpanded: false });
      return;
    }
    this.setData({ creditUsageExpanded: true });
    await this.loadCreditUsageDetails(true);
  },

  async loadMoreCreditUsageDetails() {
    await this.loadCreditUsageDetails();
  },

  async loadCreditUsageDetails(reset = false) {
    if (this.data.creditUsageLoading) {
      return;
    }
    const email = this.data.email;
    const offset = reset ? 0 : this.data.creditUsageItems.length;
    this.setData({
      creditUsageLoading: true,
      creditUsageError: "",
      ...(reset ? { creditUsageItems: [], creditUsageDays: [], creditUsageHasMore: false } : {}),
    });
    try {
      const page = await getAIUsageDetails(offset);
      if (this.data.email !== email || !this.data.creditUsageExpanded) {
        return;
      }
      const items = reset ? page.items : [...this.data.creditUsageItems, ...page.items];
      this.setData({
        creditUsageItems: items,
        creditUsageDays: creditUsageDayGroups(items),
        creditUsageHasMore: page.has_more,
      });
    } catch (error) {
      if (this.data.email === email && this.data.creditUsageExpanded) {
        this.setData({
          creditUsageError: error instanceof Error ? error.message : "使用明细暂时无法加载",
        });
      }
    } finally {
      if (this.data.email === email) {
        this.setData({ creditUsageLoading: false });
      }
    }
  },

  openRechargeQr() {
    this.setData({ rechargeQrVisible: true });
  },

  closeRechargeQr() {
    this.setData({ rechargeQrVisible: false });
  },

  stopRechargeQrTap() {},

  changeLargeText(event: SwitchChangeEvent) {
    const settings = saveLargeTextEnabled(event.detail.value);
    this.setData({
      pageClass: accessibilityPageClass("me-page", settings.largeTextEnabled),
      largeTextEnabled: settings.largeTextEnabled,
    });
    wx.showToast({ title: settings.largeTextEnabled ? "大字模式已打开" : "大字模式已关闭", icon: "none" });
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
      clearCurrentUserMemoirSnapshots();
      replaceMemoirStore({});
      this.setData({ ...loggedOutData(), error: "" });
      wx.showToast({ title: "已退出", icon: "success" });
      relaunchLogin();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "退出失败，请检查网络后重试" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async deleteAccount() {
    if (this.data.loading) return;
    const confirmation = await new Promise<{ confirm: boolean }>((resolve) => {
      wx.showModal({
        title: "注销账号",
        content: "注销后，账号、回忆录及录音资料将从在线服务中删除，无法在小程序恢复。请确认是否继续。",
        confirmText: "确认注销",
        confirmColor: "#b0473d",
        success: resolve,
        fail: () => resolve({ confirm: false }),
      });
    });
    if (!confirmation.confirm) return;
    this.setData({ loading: true, deletingAccount: true, error: "" });
    try {
      await deleteAccountWithWeChat();
      clearCurrentUserMemoirSnapshots();
      clearVoiceAuthorizationConsentRecord();
      replaceMemoirStore({});
      this.setData({ ...loggedOutData(), error: "" });
      wx.showToast({ title: "账号已注销", icon: "success" });
      relaunchLogin();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "注销失败，请稍后重试" });
    } finally {
      this.setData({ loading: false, deletingAccount: false });
    }
  },
});
