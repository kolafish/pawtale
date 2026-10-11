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
  buyCreditPack,
  creditPurchaseBlockedReason,
  getAICreditSummary,
  getAIUsageDetails,
  getCreditPacks,
  type AICreditSummary,
  type AIUsageDay,
  type CreditPack,
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

interface CreditUsageCreditEntry {
  id: string;
  label: string;
  note: string;
  amountText: string;
}

interface CreditUsageDayGroup {
  dateText: string;
  amountText: string;
  purposes: CreditUsagePurposeGroup[];
  credits: CreditUsageCreditEntry[];
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
  creditOverdrawn: boolean;
  creditProgressPercent: string;
  creditUsageExpanded: boolean;
  creditUsageLoading: boolean;
  creditUsageError: string;
  creditUsageDays: CreditUsageDayGroup[];
  creditUsageHasMore: boolean;
  creditPacks: CreditPackOption[];
  creditSheetVisible: boolean;
  creditSelectedPackId: string;
  creditPurchasing: boolean;
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

function formatUsageAmount(value: unknown): string {
  const amount = Number(value);
  const safe = Number.isFinite(amount) ? Math.abs(amount) : 0;
  return safe.toFixed(6).replace(/(\.\d{2}\d*?)0+$/, "$1");
}

function creditUsageDayGroup(day: AIUsageDay): CreditUsageDayGroup {
  return {
    dateText: day.day,
    amountText: formatUsageAmount(day.cost_rmb),
    purposes: day.purposes.map((group) => ({
      purpose: group.purpose,
      count: group.count,
      amountText: formatUsageAmount(group.cost_rmb),
    })),
    credits: day.credits.map((entry) => ({
      id: entry.id,
      label: entry.label,
      note: entry.note,
      amountText: `${Number(entry.amount_rmb) < 0 ? "-" : "+"}¥${formatUsageAmount(entry.amount_rmb)}`,
    })),
  };
}

function creditData(summary: AICreditSummary): Pick<
  MeData,
  | "creditAvailable"
  | "creditError"
  | "creditTotalText"
  | "creditUsedText"
  | "creditRemainingText"
  | "creditOverdrawn"
  | "creditProgressPercent"
> {
  const total = creditAmount(summary.total_credit_rmb);
  const used = creditAmount(summary.used_rmb);
  return {
    creditAvailable: true,
    creditError: "",
    creditTotalText: formatCreditAmount(total),
    creditUsedText: formatCreditAmount(used),
    creditRemainingText: Math.abs(Number(summary.remaining_rmb) || 0).toFixed(2),
    creditOverdrawn: Number(summary.remaining_rmb) < 0,
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

interface CreditPackOption extends CreditPack {
  title: string;
  description: string;
  note: string;
  priceText: string;
  iconPath: string;
}

// Titles and artwork for the 道具 configured in the mini program admin.
const CREDIT_PACK_DISPLAY: Record<string, { title: string; note: string; iconPath: string }> = {
  "9_9_0": { title: "体验包", note: "先试试讲述和配图", iconPath: "/assets/credit-packs/9_9_0.jpg" },
  "29_9_0": { title: "进阶包", note: "持续讲述，整理成书", iconPath: "/assets/credit-packs/29_9_0.jpg" },
  "198_0": { title: "典藏包", note: "长期讲述，做视频和书稿", iconPath: "/assets/credit-packs/198_0.jpg" },
};

function creditPackOption(pack: CreditPack): CreditPackOption {
  const display = CREDIT_PACK_DISPLAY[pack.product_id];
  return {
    ...pack,
    title: display?.title || pack.name,
    description: `增加 ${Number(pack.credit_rmb)} 元 AI 使用额度`,
    note: display?.note || "",
    priceText: String(Number(pack.price_rmb)),
    iconPath: display?.iconPath || "",
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
    creditOverdrawn: false,
    creditProgressPercent: "0.0",
    creditUsageExpanded: false,
    creditUsageLoading: false,
    creditUsageError: "",
    creditUsageDays: [],
    creditUsageHasMore: false,
    creditPacks: [],
    creditSheetVisible: false,
    creditSelectedPackId: "",
    creditPurchasing: false,
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
    creditOverdrawn: false,
    creditProgressPercent: "0.0",
    creditUsageExpanded: false,
    creditUsageLoading: false,
    creditUsageError: "",
    creditUsageDays: [],
    creditUsageHasMore: false,
    creditPacks: [],
    creditSheetVisible: false,
    creditSelectedPackId: "",
    creditPurchasing: false,
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
    void this.loadCreditPacks();
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

  async loadCreditPacks() {
    try {
      const { enabled, packs } = await getCreditPacks();
      this.setData({ creditPacks: enabled ? packs.map(creditPackOption) : [] });
    } catch (error) {
      console.warn("load credit packs failed", error);
      this.setData({ creditPacks: [] });
    }
  },

  openCreditPurchase() {
    if (this.data.creditPurchasing || !this.data.creditPacks.length) {
      return;
    }
    const blockedReason = creditPurchaseBlockedReason();
    if (blockedReason) {
      wx.showModal({ title: "暂时无法充值", content: blockedReason, showCancel: false });
      return;
    }
    const packs = this.data.creditPacks;
    const selected = packs.some((pack) => pack.product_id === this.data.creditSelectedPackId)
      ? this.data.creditSelectedPackId
      : packs[Math.min(1, packs.length - 1)].product_id;
    this.setData({ creditSheetVisible: true, creditSelectedPackId: selected });
  },

  noop() {},

  closeCreditPurchase() {
    this.setData({ creditSheetVisible: false });
  },

  selectCreditPack(event: { currentTarget: { dataset: { productId?: string } } }) {
    const productId = event.currentTarget.dataset.productId || "";
    if (productId) {
      this.setData({ creditSelectedPackId: productId });
    }
  },

  confirmCreditPurchase() {
    const productId = this.data.creditSelectedPackId;
    if (this.data.creditPurchasing || !productId) {
      return;
    }
    this.setData({ creditSheetVisible: false });
    void this.purchaseCreditPack(productId);
  },

  async purchaseCreditPack(productId: string) {
    this.setData({ creditPurchasing: true });
    try {
      const result = await buyCreditPack(productId);
      if (result === "paid") {
        wx.showToast({ title: "充值成功", icon: "success" });
      } else if (result === "pending") {
        wx.showModal({
          title: "支付已完成",
          content: "额度正在到账，请稍后重新打开本页查看。",
          showCancel: false,
        });
      }
      if (result !== "cancelled") {
        await this.loadAICreditSummary();
        if (this.data.creditUsageExpanded) {
          await this.loadCreditUsageDetails(true);
        }
      }
    } catch (error) {
      wx.showToast({ title: error instanceof Error ? error.message : "充值失败，请稍后重试", icon: "none" });
    } finally {
      this.setData({ creditPurchasing: false });
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
    const offset = reset ? 0 : this.data.creditUsageDays.length;
    this.setData({
      creditUsageLoading: true,
      creditUsageError: "",
      ...(reset ? { creditUsageDays: [], creditUsageHasMore: false } : {}),
    });
    try {
      const page = await getAIUsageDetails(offset);
      if (this.data.email !== email || !this.data.creditUsageExpanded) {
        return;
      }
      const days = page.days.map(creditUsageDayGroup);
      this.setData({
        creditUsageDays: reset ? days : [...this.data.creditUsageDays, ...days],
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
        content: "注销后，账号、回忆录及录音资料将从在线服务中删除，无法在小程序恢复；AI 剩余额度会保留，用同一微信重新登录即可继续使用，不会重复赠送。请确认是否继续。",
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
