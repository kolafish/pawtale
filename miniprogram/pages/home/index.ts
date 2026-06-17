import type { MemoirGuidanceMode } from "../../domain/voice-chat";
import { getMemoirIdentity } from "../../services/auth";
import { createMemoirVoiceSession } from "../../services/memoir-voice-api";
import { guidanceModeLabel, memoirProjectProfile } from "../../services/memoir-project-profile";
import { ApiError } from "../../services/request";

const WECHAT_PLACEHOLDER_DISPLAY_NAME = "微信用户";

interface HomeData {
  loading: boolean;
  creatingVoice: boolean;
  error: string;
  displayName: string;
  projectId: string;
  projectTitle: string;
  selectedMode: MemoirGuidanceMode;
  selectedModule: string;
  guidanceLabel: string;
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

Page<HomeData, {
  onShow(): Promise<void>;
  loadHome(): Promise<void>;
  startVoiceChat(): Promise<void>;
}>({
  data: {
    loading: false,
    creatingVoice: false,
    error: "",
    displayName: "",
    projectId: "",
    projectTitle: "",
    selectedMode: "free_narration",
    selectedModule: "event",
    guidanceLabel: "自由讲述",
  },

  async onShow() {
    if (!this.data.loading) {
      await this.loadHome();
    }
  },

  async loadHome() {
    this.setData({ loading: true, error: "" });
    try {
      const identity = await getMemoirIdentity();
      if (!identity.password_login_enabled) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      const profile = memoirProjectProfile(identity.active_project);
      this.setData({
        displayName: userDisplayName(identity.display_name, identity.email),
        projectId: identity.active_project_id,
        projectTitle: identity.active_project?.title || "我的回忆录",
        selectedMode: profile.defaultGuidanceMode,
        guidanceLabel: guidanceModeLabel(profile.defaultGuidanceMode),
      });
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "首页加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async startVoiceChat() {
    if (this.data.loading || this.data.creatingVoice) {
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
        wx.reLaunch({ url: "/pages/login/index" });
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
