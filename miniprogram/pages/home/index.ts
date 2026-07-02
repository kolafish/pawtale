import type { MemoirGuidanceMode, MemoirVoiceTranscriptItem } from "../../domain/voice-chat";
import { getMemoirIdentity } from "../../services/auth";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  memoirHomePrompt,
  memoirStoryTitle,
} from "../../services/memoir-display-copy";
import {
  createMemoirVoiceSession,
  listMemoirVoiceTranscripts,
} from "../../services/memoir-voice-api";
import {
  dismissHomeLaunch,
  resolveHomeLaunch,
  type HomeLaunchVariant,
} from "../../services/home-launch-state";
import {
  guidanceModeLabel,
  memoirProjectProfile,
} from "../../services/memoir-project-profile";
import { isAuthError, switchToMe } from "../../services/navigation";
import { pageUrl } from "../../services/page-input";
import {
  readVoiceAuthorizationConsent,
  saveVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "../../services/voice-authorization";

const WECHAT_PLACEHOLDER_DISPLAY_NAME = "微信用户";
const VOICE_AUTHORIZATION_VALUE = "voice_authorization";
const RESUME_HINT_CHAR_LIMIT = 28;

type HomeVoiceEntry = "home" | "home_launch" | "home_resume";

interface HomeData {
  loading: boolean;
  creatingVoice: boolean;
  needsLogin: boolean;
  error: string;
  userId: string;
  displayName: string;
  projectId: string;
  projectTitle: string;
  projectDisplayTitle: string;
  projectPromptText: string;
  lastStoryHint: string;
  lastStoryText: string;
  hasLastStoryHint: boolean;
  launchVariant: HomeLaunchVariant;
  showLaunchPanel: boolean;
  showReturnPrompt: boolean;
  launchEyebrow: string;
  launchTitle: string;
  launchBody: string;
  launchPrimaryText: string;
  launchSecondaryText: string;
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

function normalizedResumeText(value: string): string {
  return value
    .trim()
    .replace(/\s+/g, " ")
    .replace(/[。！？!?；;，,：:]+$/g, "");
}

export function homeResumeHint(item?: Pick<
  MemoirVoiceTranscriptItem,
  "summary_title" | "summary_text" | "display_text" | "transcript_edited" | "transcript_raw"
> | null): string {
  const text = [
    item?.summary_title,
    item?.summary_text,
    item?.display_text,
    item?.transcript_edited,
    item?.transcript_raw,
  ].map((value) => normalizedResumeText(value || "")).find(Boolean) || "";
  if (!text) {
    return "";
  }
  return text.length > RESUME_HINT_CHAR_LIMIT ? `${text.slice(0, RESUME_HINT_CHAR_LIMIT)}...` : text;
}

function lastStoryText(hint: string): string {
  if (!hint) {
    return "";
  }
  return `上次您说到：${hint}${hint.endsWith("...") ? "" : "。"}`;
}

function guidancePatch(mode: MemoirGuidanceMode): Pick<HomeData, "selectedMode" | "guidanceLabel"> {
  return {
    selectedMode: mode,
    guidanceLabel: guidanceModeLabel(mode),
  };
}

function launchCopy(
  variant: HomeLaunchVariant,
  projectDisplayTitle: string,
): Pick<
  HomeData,
  "showLaunchPanel" | "showReturnPrompt" | "launchEyebrow" | "launchTitle" | "launchBody" | "launchPrimaryText" | "launchSecondaryText"
> {
  if (variant === "first_visit") {
    return {
      showLaunchPanel: true,
      showReturnPrompt: false,
      launchEyebrow: "聊出一本书",
      launchTitle: "我的故事，慢慢讲",
      launchBody: "不用写，不用想完整。说一段，小余帮您整理成一页。一段段讲下去，就慢慢成了一本书。",
      launchPrimaryText: "今天先聊一段",
      launchSecondaryText: "先看看首页",
    };
  }
  if (variant === "new_project") {
    return {
      showLaunchPanel: true,
      showReturnPrompt: false,
      launchEyebrow: "新的故事本",
      launchTitle: `${projectDisplayTitle || "这本回忆录"}，慢慢讲`,
      launchBody: "不用写，不用想完整。说一段，小余帮您整理成一页。一段段讲下去，就慢慢成了一本书。",
      launchPrimaryText: "今天先聊一段",
      launchSecondaryText: "先看看首页",
    };
  }
  return {
    showLaunchPanel: false,
    showReturnPrompt: variant === "returning_soft",
    launchEyebrow: "",
    launchTitle: "",
    launchBody: "",
    launchPrimaryText: "今天先聊一段",
    launchSecondaryText: "先看看首页",
  };
}

async function loadLastStoryHint(projectId: string): Promise<string> {
  if (!projectId) {
    return "";
  }
  try {
    const response = await listMemoirVoiceTranscripts({ projectId, limit: 1 });
    return homeResumeHint(response.items[0]);
  } catch (error) {
    console.warn("load latest memoir transcript failed", error);
    return "";
  }
}

Page<HomeData, {
  onShow(): Promise<void>;
  loadHome(): Promise<void>;
  refreshVoiceAuthorizationConsent(): void;
  changeVoiceAuthorization(event: CheckboxChangeEvent): void;
  openVoiceAuthorization(): void;
  dismissLaunchPanel(): void;
  dismissReturnPrompt(): void;
  createProject(): void;
  startVoiceChat(): Promise<void>;
  startPrimaryVoiceChat(): Promise<void>;
  startLaunchVoiceChat(): Promise<void>;
  startFreeNarration(): Promise<void>;
  startGuidedInterview(): Promise<void>;
  startVoiceChatWithMode(mode: MemoirGuidanceMode, entry: HomeVoiceEntry): Promise<void>;
}>({
  data: {
    loading: false,
    creatingVoice: false,
    needsLogin: false,
    error: "",
    userId: "",
    displayName: "",
    projectId: "",
    projectTitle: "",
    projectDisplayTitle: "",
    projectPromptText: "不用讲完整，先说一件您想起来的事。",
    lastStoryHint: "",
    lastStoryText: "",
    hasLastStoryHint: false,
    launchVariant: "none",
    showLaunchPanel: false,
    showReturnPrompt: false,
    launchEyebrow: "",
    launchTitle: "",
    launchBody: "",
    launchPrimaryText: "今天先聊一段",
    launchSecondaryText: "先看看首页",
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
      const projectDisplayTitle = hasProject ? memoirStoryTitle(identity.active_project?.title || "") : "还没有回忆录";
      const latestHint = hasProject ? await loadLastStoryHint(identity.active_project_id) : "";
      const launchVariant = resolveHomeLaunch(identity.id, {
        projectId: identity.active_project_id,
        hasProject,
      });
      this.setData({
        needsLogin: false,
        userId: identity.id,
        displayName: userDisplayName(identity.display_name, identity.email),
        projectId: identity.active_project_id,
        projectTitle: identity.active_project?.title || "还没有回忆录",
        projectDisplayTitle,
        projectPromptText: memoirHomePrompt(identity.active_project?.title || ""),
        lastStoryHint: latestHint,
        lastStoryText: lastStoryText(latestHint),
        hasLastStoryHint: Boolean(latestHint),
        launchVariant,
        ...launchCopy(launchVariant, projectDisplayTitle),
        hasProject,
        ...guidancePatch(profile.defaultGuidanceMode),
      });
    } catch (error) {
      if (isAuthError(error)) {
        this.setData({
          needsLogin: true,
          userId: "",
          displayName: "作者",
          projectId: "",
          projectTitle: "登录后创建回忆录",
          projectDisplayTitle: "登录后创建回忆录",
          projectPromptText: "登录后，我在这里等你。",
          lastStoryHint: "",
          lastStoryText: "",
          hasLastStoryHint: false,
          launchVariant: "none",
          ...launchCopy("none", ""),
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

  dismissLaunchPanel() {
    dismissHomeLaunch(this.data.userId, this.data.launchVariant, this.data.projectId);
    this.setData({
      launchVariant: "none",
      ...launchCopy("none", this.data.projectDisplayTitle),
    });
  },

  dismissReturnPrompt() {
    this.setData({ launchVariant: "none", showReturnPrompt: false });
  },

  createProject() {
    wx.navigateTo({ url: "/pages/project-edit/index" });
  },

  async startVoiceChat() {
    await this.startVoiceChatWithMode(this.data.selectedMode, "home");
  },

  async startPrimaryVoiceChat() {
    await this.startVoiceChatWithMode(this.data.hasProject ? "free_narration" : this.data.selectedMode, "home_resume");
  },

  async startLaunchVoiceChat() {
    await this.startVoiceChatWithMode(this.data.selectedMode, "home_launch");
  },

  async startFreeNarration() {
    await this.startVoiceChatWithMode("free_narration", "home_resume");
  },

  async startGuidedInterview() {
    await this.startVoiceChatWithMode("guided_interview", "home_resume");
  },

  async startVoiceChatWithMode(mode, entry) {
    if (this.data.loading || this.data.creatingVoice) {
      return;
    }
    if (this.data.needsLogin) {
      switchToMe();
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
        guidance_mode: mode,
        provider: "volc_streaming_asr",
        input_format: "pcm_s16le_16000_mono",
        metadata_json: {
          entry: entry === "home" ? "miniprogram_home" : `miniprogram_${entry}`,
          first_version: true,
          ...(this.data.launchVariant !== "none" ? { launch_variant: this.data.launchVariant } : {}),
          ...(entry === "home_resume" && this.data.hasLastStoryHint ? { resume_hint: this.data.lastStoryHint } : {}),
        },
      }, {
        projectId: this.data.projectId,
      });
      trackAuditAction(
        "voice_chat.start",
        { guidance_mode: mode, entry, launch_variant: this.data.launchVariant },
        { projectId: this.data.projectId, voiceSessionId: detail.session.id },
      );
      if (entry === "home_launch") {
        this.dismissLaunchPanel();
      }
      wx.navigateTo({
        url: pageUrl("/pages/voice-chat/index", {
          voiceSessionId: detail.session.id,
          mode,
          projectTitle: this.data.projectTitle ? this.data.projectDisplayTitle || this.data.projectTitle : "",
        }),
      });
    } catch (error) {
      if (isAuthError(error)) {
        switchToMe();
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
