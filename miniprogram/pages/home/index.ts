import type {
  MemoirGuidanceMode,
  MemoirInterviewType,
  MemoirSessionGoal,
  MemoirVoiceTranscriptItem,
} from "../../domain/voice-chat";
import { definePage } from "../../services/page-definition";
import { getMemoirIdentity } from "../../services/auth";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  memoirHomePrompt,
  memoirStoryTitle,
} from "../../services/memoir-display-copy";
import { readLargeTextEnabled } from "../../services/accessibility-settings";
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
import { isAuthError, navigateToLogin } from "../../services/navigation";
import { pageUrl } from "../../services/page-input";
import {
  readVoiceAuthorizationConsent,
  saveVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "../../services/voice-authorization";
import {
  enableMiniProgramShareMenu,
  homeShareAppMessage,
  homeShareTimeline,
} from "../../services/share";

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
  greetingText: string;
  launchModeTagText: string;
  launchVoiceButtonTitle: string;
  panelLabelText: string;
  modeTagText: string;
  projectHeadingText: string;
  projectMetaText: string;
  voiceStartButtonClass: string;
  voiceStartButtonHoverClass: string;
  primaryVoiceButtonTitle: string;
  voiceStartNoteText: string;
  resumeAskButtonClass: string;
  resumeAskButtonHoverClass: string;
  completionButtonTitle: string;
  showResumeActions: boolean;
  showMoreActions: boolean;
  moreActionsToggleText: string;
  moreActionsToggleAriaLabel: string;
  alternateModeButtonTitle: string;
  showVoiceAuthorizationPanel: boolean;
  largeTextEnabled: boolean;
  homePageClass: string;
}

interface CheckboxChangeEvent {
  detail: {
    value: string[];
  };
}

type HomeViewPatch = Pick<
  HomeData,
  | "greetingText"
  | "launchModeTagText"
  | "launchVoiceButtonTitle"
  | "panelLabelText"
  | "modeTagText"
  | "projectHeadingText"
  | "projectMetaText"
  | "voiceStartButtonClass"
  | "voiceStartButtonHoverClass"
  | "primaryVoiceButtonTitle"
  | "voiceStartNoteText"
  | "resumeAskButtonClass"
  | "resumeAskButtonHoverClass"
  | "completionButtonTitle"
  | "showResumeActions"
  | "moreActionsToggleText"
  | "moreActionsToggleAriaLabel"
  | "alternateModeButtonTitle"
  | "showVoiceAuthorizationPanel"
  | "homePageClass"
>;

type HomeContext = {
  data: HomeData;
  setData(patch: Partial<HomeData>): void;
};

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

export function homeViewPatch(state: HomeData): HomeViewPatch {
  const blocked = state.loading || state.creatingVoice;
  const voiceStartButtonClass = blocked ? "voice-start-button button-blocked" : "voice-start-button";
  const voiceStartButtonHoverClass = blocked ? "none" : "voice-start-button-hover";
  return {
    homePageClass: state.largeTextEnabled ? "home-page large-text" : "home-page",
    greetingText: `你好，${state.displayName || "作者"}`,
    launchModeTagText: state.launchVariant === "new_project" ? "新故事本" : "第一次来",
    launchVoiceButtonTitle: state.creatingVoice ? "正在准备" : state.launchPrimaryText,
    panelLabelText: state.loading ? "正在准备" : (state.hasProject ? "今天接着聊" : "准备开始"),
    modeTagText: state.loading ? "加载中" : (state.hasProject ? state.guidanceLabel : "待创建"),
    projectHeadingText: state.loading
      ? "正在加载回忆录"
      : (
          state.hasProject
            ? (state.projectDisplayTitle || state.projectTitle || "这段回忆")
            : (state.projectDisplayTitle || state.projectTitle || "还没有回忆录")
        ),
    projectMetaText: state.loading
      ? "我先确认当前回忆录。"
      : (
          state.needsLogin
            ? "登录后，我在这里等你。"
            : (!state.hasProject ? "先创建一本回忆录，我再听你讲。" : (state.hasLastStoryHint ? state.lastStoryText : state.projectPromptText))
        ),
    voiceStartButtonClass,
    voiceStartButtonHoverClass,
    primaryVoiceButtonTitle: state.loading
      ? "正在加载"
      : (
          state.needsLogin
            ? "登录后开始"
            : (
                !state.hasProject
                  ? "创建回忆录"
                  : (
                      !state.voiceAuthorizationAgreed
                        ? "同意协议后开始"
                        : (
                            state.creatingVoice
                              ? "正在准备"
                              : (state.selectedMode === "guided_interview" ? "让小余陪着问" : "随便讲一段")
                          )
                    )
              )
        ),
    voiceStartNoteText: state.loading
      ? "马上就好。"
      : (
          state.needsLogin
            ? "先登录，再继续讲回忆。"
            : (
                !state.hasProject
                  ? "先起个名字，我再听您讲。"
                  : (
                      !state.voiceAuthorizationAgreed
                        ? "同意后才会打开麦克风。"
                        : (
                            state.creatingVoice
                              ? "我马上开始听。"
                              : (
                                  state.selectedMode === "guided_interview"
                                    ? "小余会从一件具体的事开始问起。"
                                    : "说一两句也可以，我会帮您记下来。"
                                )
                          )
                    )
              )
        ),
    resumeAskButtonClass: blocked ? "resume-ask-button button-blocked" : "resume-ask-button",
    resumeAskButtonHoverClass: blocked ? "none" : "resume-ask-button-hover",
    completionButtonTitle: "帮我补全这本书",
    showResumeActions: state.hasProject && !state.needsLogin,
    moreActionsToggleText: state.showMoreActions ? "收起其他方式" : "其他讲述与整理方式",
    moreActionsToggleAriaLabel: state.showMoreActions ? "收起其他讲述与整理方式" : "展开其他讲述与整理方式",
    alternateModeButtonTitle: state.selectedMode === "guided_interview" ? "我想自己讲" : "让小余陪着问",
    showVoiceAuthorizationPanel: !state.voiceAuthorizationAgreed,
  };
}

function setHomeData(context: HomeContext, patch: Partial<HomeData>): void {
  const nextData: HomeData = { ...context.data, ...patch };
  context.setData({
    ...patch,
    ...homeViewPatch(nextData),
  });
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

definePage<HomeData>()({
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
    greetingText: "你好，作者",
    launchModeTagText: "第一次来",
    launchVoiceButtonTitle: "今天先聊一段",
    panelLabelText: "准备开始",
    modeTagText: "待创建",
    projectHeadingText: "还没有回忆录",
    projectMetaText: "先创建一本回忆录，我再听你讲。",
    voiceStartButtonClass: "voice-start-button",
    voiceStartButtonHoverClass: "voice-start-button-hover",
    primaryVoiceButtonTitle: "创建回忆录",
    voiceStartNoteText: "先起个名字，我再听您讲。",
    resumeAskButtonClass: "resume-ask-button",
    resumeAskButtonHoverClass: "resume-ask-button-hover",
    completionButtonTitle: "帮我补全这本书",
    showResumeActions: false,
    showMoreActions: false,
    moreActionsToggleText: "其他讲述与整理方式",
    moreActionsToggleAriaLabel: "展开其他讲述与整理方式",
    alternateModeButtonTitle: "让小余陪着问",
    showVoiceAuthorizationPanel: true,
    largeTextEnabled: false,
    homePageClass: "home-page",
  },

  async onShow() {
    enableMiniProgramShareMenu();
    this.refreshAccessibilitySettings();
    this.refreshVoiceAuthorizationConsent();
    if (!this.data.loading) {
      await this.loadHome();
    }
  },

  onShareAppMessage() {
    return homeShareAppMessage();
  },

  onShareTimeline() {
    return homeShareTimeline();
  },

  refreshAccessibilitySettings() {
    setHomeData(this, { largeTextEnabled: readLargeTextEnabled() });
  },

  refreshVoiceAuthorizationConsent() {
    setHomeData(this, { voiceAuthorizationAgreed: readVoiceAuthorizationConsent() });
  },

  async loadHome() {
    setHomeData(this, { loading: true, error: "" });
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
      setHomeData(this, {
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
        showMoreActions: false,
        ...guidancePatch(profile.defaultGuidanceMode),
      });
    } catch (error) {
      if (isAuthError(error)) {
        setHomeData(this, {
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
          showMoreActions: false,
          ...guidancePatch("free_narration"),
        });
        return;
      }
      setHomeData(this, { error: error instanceof Error ? error.message : "首页加载失败" });
    } finally {
      setHomeData(this, { loading: false });
    }
  },

  changeVoiceAuthorization(event: CheckboxChangeEvent) {
    const agreed = event.detail.value.includes(VOICE_AUTHORIZATION_VALUE);
    const consent = saveVoiceAuthorizationConsent(agreed);
    setHomeData(this, {
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
    setHomeData(this, {
      launchVariant: "none",
      ...launchCopy("none", this.data.projectDisplayTitle),
    });
  },

  dismissReturnPrompt() {
    setHomeData(this, { launchVariant: "none", showReturnPrompt: false });
  },

  createProject() {
    wx.navigateTo({ url: "/pages/project-edit/index" });
  },

  openProjects() {
    if (this.data.needsLogin) {
      navigateToLogin();
      return;
    }
    if (!this.data.projectId) {
      this.createProject();
      return;
    }
    wx.navigateTo({ url: "/pages/projects/index" });
  },

  async startVoiceChat() {
    await this.startVoiceChatWithMode(this.data.selectedMode, "home");
  },

  async startPrimaryVoiceChat() {
    await this.startVoiceChatWithMode(this.data.selectedMode, "home_resume");
  },

  async startLaunchVoiceChat() {
    await this.startVoiceChatWithMode(this.data.selectedMode, "home_launch");
  },

  async startFreeNarration() {
    await this.startVoiceChatWithMode("free_narration", "home_resume");
  },

  async startAlternateGuidance() {
    const mode = this.data.selectedMode === "guided_interview"
      ? "free_narration"
      : "guided_interview";
    await this.startVoiceChatWithMode(mode, "home_resume");
  },

  toggleMoreActions() {
    setHomeData(this, { showMoreActions: !this.data.showMoreActions });
  },

  startBookCompletion() {
    if (!this.canStartVoiceChat(false)) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/book-completion/index", {
        projectId: this.data.projectId,
        projectTitle: this.data.projectDisplayTitle || this.data.projectTitle,
      }),
    });
  },

  openTopicMap() {
    if (!this.canStartVoiceChat(false)) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/topic-map/index", {
        projectId: this.data.projectId,
      }),
    });
  },

  async startHumanInterview() {
    await this.startVoiceChatWithMode("free_narration", "home_resume", "human_interview");
  },

  ensureVoiceAuthorization() {
    if (this.data.voiceAuthorizationAgreed) {
      return true;
    }
    setHomeData(this, { error: "请先阅读并同意《声纹授权协议》后再开始语音聊天" });
    wx.showToast({ title: "请先同意声纹授权协议", icon: "none" });
    return false;
  },

  canStartVoiceChat(requireVoiceAuthorization = true) {
    if (this.data.loading || this.data.creatingVoice) {
      return false;
    }
    if (this.data.needsLogin) {
      navigateToLogin();
      return false;
    }
    if (!this.data.projectId) {
      this.createProject();
      return false;
    }
    if (requireVoiceAuthorization && !this.ensureVoiceAuthorization()) {
      return false;
    }
    return true;
  },

  async startVoiceChatWithMode(
    mode: MemoirGuidanceMode,
    entry: HomeVoiceEntry,
    interviewType: MemoirInterviewType = "ai_interview",
    sessionGoal: MemoirSessionGoal = "open_narration",
  ) {
    if (!this.canStartVoiceChat()) {
      return;
    }
    setHomeData(this, { creatingVoice: true, error: "" });
    try {
      await syncVoiceAuthorizationConsent();
      await this.createAndOpenVoiceChat(mode, entry, interviewType, sessionGoal);
    } catch (error) {
      if (isAuthError(error)) {
        navigateToLogin();
        return;
      }
      setHomeData(this, {
        error: error instanceof Error ? error.message : "语音访谈创建失败",
      });
    } finally {
      setHomeData(this, { creatingVoice: false });
    }
  },

  async createAndOpenVoiceChat(
    mode: MemoirGuidanceMode,
    entry: HomeVoiceEntry,
    interviewType: MemoirInterviewType,
    sessionGoal: MemoirSessionGoal,
    currentCompletionIssueId = "",
  ) {
    const detail = await createMemoirVoiceSession({
      guidance_mode: mode,
      interview_type: interviewType,
      session_goal: sessionGoal,
      ...(currentCompletionIssueId
        ? { current_completion_issue_id: currentCompletionIssueId }
        : {}),
      provider: "volc_streaming_asr",
      input_format: "pcm_s16le_16000_mono",
      metadata_json: {
        entry: entry === "home" ? "miniprogram_home" : `miniprogram_${entry}`,
        interview_type: interviewType,
        session_goal: sessionGoal,
        first_version: true,
        ...(this.data.launchVariant !== "none" ? { launch_variant: this.data.launchVariant } : {}),
        ...(entry === "home_resume" && this.data.hasLastStoryHint ? { resume_hint: this.data.lastStoryHint } : {}),
      },
    }, {
      projectId: this.data.projectId,
    });
    trackAuditAction(
      "voice_chat.start",
      {
        guidance_mode: mode,
        interview_type: interviewType,
        session_goal: sessionGoal,
        entry,
        launch_variant: this.data.launchVariant,
      },
      { projectId: this.data.projectId, voiceSessionId: detail.session.id },
    );
    if (entry === "home_launch") {
      this.dismissLaunchPanel();
    }
    wx.navigateTo({
      url: pageUrl("/pages/voice-chat/index", {
        voiceSessionId: detail.session.id,
        mode,
        interviewType,
        sessionGoal,
        projectTitle: this.data.projectTitle ? this.data.projectDisplayTitle || this.data.projectTitle : "",
      }),
    });
  },
});
