import type { MemoirGuidanceMode } from "../../domain/voice-chat";
import { getMemoirIdentity } from "../../services/auth";
import { createMemoirVoiceSession } from "../../services/memoir-voice-api";
import { guidanceModeLabel, memoirProjectProfile } from "../../services/memoir-project-profile";
import { ApiError } from "../../services/request";
import {
  readVoiceAuthorizationConsent,
  saveVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "../../services/voice-authorization";

const WECHAT_PLACEHOLDER_DISPLAY_NAME = "微信用户";
const VOICE_AUTHORIZATION_VALUE = "voice_authorization";

interface HomeData {
  loading: boolean;
  creatingVoice: boolean;
  needsLogin: boolean;
  error: string;
  displayName: string;
  projectId: string;
  projectTitle: string;
  selectedMode: MemoirGuidanceMode;
  selectedModule: string;
  guidanceLabel: string;
  voiceAuthorizationAgreed: boolean;
}

interface CheckboxChangeEvent {
  detail: {
    value: string[];
  };
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

function goToMeLogin(): void {
  wx.switchTab({ url: "/pages/me/index" });
}

Page<HomeData, {
  onShow(): Promise<void>;
  loadHome(): Promise<void>;
  refreshVoiceAuthorizationConsent(): void;
  changeVoiceAuthorization(event: CheckboxChangeEvent): void;
  openVoiceAuthorization(): void;
  startVoiceChat(): Promise<void>;
}>({
  data: {
    loading: false,
    creatingVoice: false,
    needsLogin: false,
    error: "",
    displayName: "",
    projectId: "",
    projectTitle: "",
    selectedMode: "free_narration",
    selectedModule: "event",
    guidanceLabel: "自由讲述",
    voiceAuthorizationAgreed: false,
  },

  async onShow() {
    this.refreshVoiceAuthorizationConsent();
    if (!this.data.loading) {
      await this.loadHome();
    }
  },

  refreshVoiceAuthorizationConsent() {
    this.setData({ voiceAuthorizationAgreed: readVoiceAuthorizationConsent() });
  },

  async loadHome() {
    this.setData({ loading: true, error: "" });
    try {
      const identity = await getMemoirIdentity();
      if (!identity.password_login_enabled) {
        this.setData({
          needsLogin: true,
          displayName: "作者",
          projectId: "",
          projectTitle: "我的回忆录",
          selectedMode: "free_narration",
          guidanceLabel: guidanceModeLabel("free_narration"),
        });
        return;
      }
      const profile = memoirProjectProfile(identity.active_project);
      this.setData({
        needsLogin: false,
        displayName: userDisplayName(identity.display_name, identity.email),
        projectId: identity.active_project_id,
        projectTitle: identity.active_project?.title || "我的回忆录",
        selectedMode: profile.defaultGuidanceMode,
        guidanceLabel: guidanceModeLabel(profile.defaultGuidanceMode),
      });
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        this.setData({
          needsLogin: true,
          displayName: "作者",
          projectId: "",
          projectTitle: "我的回忆录",
          selectedMode: "free_narration",
          guidanceLabel: guidanceModeLabel("free_narration"),
        });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "首页加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  changeVoiceAuthorization(event) {
    const agreed = event.detail.value.includes(VOICE_AUTHORIZATION_VALUE);
    const consent = saveVoiceAuthorizationConsent(agreed);
    this.setData({
      voiceAuthorizationAgreed: consent.agreed,
      error: agreed ? "" : this.data.error,
    });
  },

  openVoiceAuthorization() {
    wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
  },

  async startVoiceChat() {
    if (this.data.loading || this.data.creatingVoice) {
      return;
    }
    if (!this.data.voiceAuthorizationAgreed) {
      this.setData({ error: "请先阅读并同意《声纹授权协议》后再开始语音聊天" });
      wx.showToast({ title: "请先同意声纹授权协议", icon: "none" });
      return;
    }
    if (this.data.needsLogin) {
      goToMeLogin();
      return;
    }
    this.setData({ creatingVoice: true, error: "" });
    try {
      const currentGuidanceModule = this.data.selectedMode === "guided_interview"
        ? this.data.selectedModule
        : "";
      const detail = await createMemoirVoiceSession({
        guidance_mode: this.data.selectedMode,
        current_guidance_module: currentGuidanceModule || undefined,
        provider: "volc_streaming_asr",
        input_format: "pcm_s16le_16000_mono",
        metadata_json: {
          entry: "miniprogram_home",
          first_version: true,
        },
      });
      const params = [
        `voiceSessionId=${encodeURIComponent(detail.session.id)}`,
        `mode=${encodeURIComponent(this.data.selectedMode)}`,
      ];
      if (currentGuidanceModule) {
        params.push(`module=${encodeURIComponent(currentGuidanceModule)}`);
      }
      wx.navigateTo({ url: `/pages/voice-chat/index?${params.join("&")}` });
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        goToMeLogin();
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "语音访谈创建失败",
      });
    } finally {
      this.setData({ creatingVoice: false });
    }
  },
});
