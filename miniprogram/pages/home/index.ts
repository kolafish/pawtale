import type { MemoirGuidanceMode, MemoirGuidanceModule } from "../../domain/voice-chat";
import { getMemoirIdentity } from "../../services/auth";
import { createMemoirVoiceSession } from "../../services/memoir-voice-api";
import {
  type GuidanceOption,
  guidanceModeLabel,
  guidanceModuleLabel,
  MEMOIR_GUIDANCE_MODE_OPTIONS,
  MEMOIR_GUIDANCE_MODULE_OPTIONS,
  memoirProjectProfile,
  normalizeGuidanceModule,
  voiceGuidanceLabel,
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
  hasProject: boolean;
  selectedMode: MemoirGuidanceMode;
  selectedModule: MemoirGuidanceModule;
  selectedModuleLabel: string;
  guidanceLabel: string;
  sessionGuidanceLabel: string;
  showGuidanceModules: boolean;
  guidanceOptions: Array<GuidanceOption<MemoirGuidanceMode>>;
  moduleOptions: Array<GuidanceOption<MemoirGuidanceModule>>;
  voiceAuthorizationAgreed: boolean;
}

interface CheckboxChangeEvent {
  detail: {
    value: string[];
  };
}

interface DatasetEvent {
  currentTarget: {
    dataset: {
      mode?: MemoirGuidanceMode;
      module?: MemoirGuidanceModule;
    };
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

function guidancePatch(
  mode: MemoirGuidanceMode,
  module: string,
): Pick<HomeData, "selectedMode" | "selectedModule" | "selectedModuleLabel" | "guidanceLabel" | "sessionGuidanceLabel" | "showGuidanceModules"> {
  const selectedModule = normalizeGuidanceModule(module);
  return {
    selectedMode: mode,
    selectedModule,
    selectedModuleLabel: guidanceModuleLabel(selectedModule),
    guidanceLabel: guidanceModeLabel(mode),
    sessionGuidanceLabel: voiceGuidanceLabel(mode, selectedModule),
    showGuidanceModules: mode === "guided_interview",
  };
}

Page<HomeData, {
  onShow(): Promise<void>;
  loadHome(): Promise<void>;
  refreshVoiceAuthorizationConsent(): void;
  changeVoiceAuthorization(event: CheckboxChangeEvent): void;
  selectGuidanceMode(event: DatasetEvent): void;
  selectGuidanceModule(event: DatasetEvent): void;
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
    hasProject: false,
    selectedMode: "free_narration",
    selectedModule: "person",
    selectedModuleLabel: "人物",
    guidanceLabel: "自由讲述",
    sessionGuidanceLabel: "自由讲述",
    showGuidanceModules: false,
    guidanceOptions: MEMOIR_GUIDANCE_MODE_OPTIONS,
    moduleOptions: MEMOIR_GUIDANCE_MODULE_OPTIONS,
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
        hasProject,
        ...guidancePatch(profile.defaultGuidanceMode, this.data.selectedModule),
      });
    } catch (error) {
      if (error instanceof ApiError && [401, 403].includes(error.statusCode)) {
        this.setData({
          needsLogin: true,
          displayName: "作者",
          projectId: "",
          projectTitle: "登录后创建回忆录",
          hasProject: false,
          ...guidancePatch("free_narration", this.data.selectedModule),
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

  selectGuidanceMode(event) {
    const mode = event.currentTarget.dataset.mode === "guided_interview" ? "guided_interview" : "free_narration";
    this.setData({
      ...guidancePatch(mode, this.data.selectedModule),
      error: "",
    });
  },

  selectGuidanceModule(event) {
    const module = normalizeGuidanceModule(event.currentTarget.dataset.module);
    this.setData({
      ...guidancePatch("guided_interview", module),
      error: "",
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
      }, {
        projectId: this.data.projectId,
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
