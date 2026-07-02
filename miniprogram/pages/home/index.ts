import type { MemoirGuidanceMode } from "../../domain/voice-chat";
import { getMemoirIdentity } from "../../services/auth";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  memoirHomePrompt,
  memoirStoryTitle,
} from "../../services/memoir-display-copy";
import { createMemoirVoiceSession } from "../../services/memoir-voice-api";
import {
  guidanceModeLabel,
  memoirProjectProfile,
} from "../../services/memoir-project-profile";
import { ApiError } from "../../services/request";
import {
  readVoiceAuthorizationConsent,
  saveVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
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
  projectDisplayTitle: string;
  projectPromptText: string;
  hasProject: boolean;
  selectedMode: MemoirGuidanceMode;
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

function guidancePatch(mode: MemoirGuidanceMode): Pick<HomeData, "selectedMode" | "guidanceLabel"> {
  return {
    selectedMode: mode,
    guidanceLabel: guidanceModeLabel(mode),
  };
}

Page<HomeData, {
  onShow(): Promise<void>;
  loadHome(): Promise<void>;
  refreshVoiceAuthorizationConsent(): void;
  changeVoiceAuthorization(event: CheckboxChangeEvent): void;
  openVoiceAuthorization(): void;
  createProject(): void;
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
    projectDisplayTitle: "",
    projectPromptText: "不用讲完整，先说一件您想起来的事。",
    hasProject: false,
    selectedMode: "free_narration",
    guidanceLabel: "慢慢讲",
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
      const hasProject = Boolean(identity.active_project_id && identity.active_project);
      const profile = memoirProjectProfile(identity.active_project);
      this.setData({
        needsLogin: false,
        displayName: userDisplayName(identity.display_name, identity.email),
        projectId: identity.active_project_id,
        projectTitle: identity.active_project?.title || "还没有回忆录",
        projectDisplayTitle: hasProject ? memoirStoryTitle(identity.active_project?.title || "") : "还没有回忆录",
        projectPromptText: memoirHomePrompt(identity.active_project?.title || ""),
        hasProject,
        ...guidancePatch(profile.defaultGuidanceMode),
      });
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        this.setData({
          needsLogin: true,
          displayName: "作者",
          projectId: "",
          projectTitle: "登录后创建回忆录",
          projectDisplayTitle: "登录后创建回忆录",
          projectPromptText: "登录后，我在这里等你。",
          hasProject: false,
          ...guidancePatch("free_narration"),
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
    void syncVoiceAuthorizationConsent({ consent }).catch((error) => {
      console.warn("sync voice authorization consent change failed", error);
    });
  },

  openVoiceAuthorization() {
    wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
  },

  createProject() {
    wx.navigateTo({ url: "/pages/project-edit/index" });
  },

  async startVoiceChat() {
    if (this.data.loading || this.data.creatingVoice) {
      return;
    }
    if (this.data.needsLogin) {
      goToMeLogin();
      return;
    }
    if (!this.data.projectId) {
      this.createProject();
      return;
    }
    if (!this.data.voiceAuthorizationAgreed) {
      this.setData({ error: "请先阅读并同意《声纹授权协议》后再开始语音聊天" });
      wx.showToast({ title: "请先同意声纹授权协议", icon: "none" });
      return;
    }
    this.setData({ creatingVoice: true, error: "" });
    try {
      await syncVoiceAuthorizationConsent();
      const detail = await createMemoirVoiceSession({
        guidance_mode: this.data.selectedMode,
        provider: "volc_streaming_asr",
        input_format: "pcm_s16le_16000_mono",
        metadata_json: {
          entry: "miniprogram_home",
          first_version: true,
        },
      }, {
        projectId: this.data.projectId,
      });
      trackAuditAction(
        "voice_chat.start",
        { guidance_mode: this.data.selectedMode, entry: "home" },
        { projectId: this.data.projectId, voiceSessionId: detail.session.id },
      );
      const params = [
        `voiceSessionId=${encodeURIComponent(detail.session.id)}`,
        `mode=${encodeURIComponent(this.data.selectedMode)}`,
      ];
      if (this.data.projectTitle) {
        params.push(`projectTitle=${encodeURIComponent(this.data.projectDisplayTitle || this.data.projectTitle)}`);
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
