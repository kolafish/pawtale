import { isNarrationMode, narrationModeForSession, NARRATION_MODES, type NarrationMode, type NarrationModeEvent } from "../../domain/memoir-narration-mode";
import type { VoiceSessionEntry, VoiceSessionExitEvent } from "../../components/voice-session/events";
import type {
  MemoirGuidanceMode,
  MemoirInterviewType,
  MemoirSessionGoal,
} from "../../domain/memoir-voice-session";
import type { MemoirVoiceTranscriptItem } from "../../domain/memoir-transcript";
import type { MemoirHomeSnapshot } from "../../domain/memoir-snapshots";
import { definePage, pageRuntime } from "../../services/page-definition";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  memoirHomePrompt,
  memoirStoryTitle,
} from "../../services/memoir-display-copy";
import { readLargeTextEnabled } from "../../services/accessibility-settings";
import {
  createMemoirVoiceSession,
} from "../../services/memoir-voice-session-api";
import { getMemoirStorySnapshot, retryMemoirDraftProgress } from "../../services/memoir-snapshot-api";
import { acknowledgeMemoirAutomationEvent } from "../../services/memoir-voice-session-api";
import { writeMemoirIdentitySnapshot } from "../../stores/memoir-cache";
import { PollingTimer } from "../../services/polling-timer";
import { emptyHomeStoryData, homeStorySnapshotData, clearStoryGrowthAnimation, HomeGrowthTracker, type HomeStoryData } from "./home-story-state";
import { toggleStoryChapter } from "../../components/story-tree/story-view-data";
import type { StoryTreeChapterEvent, StoryTreeMediaEvent, StoryTreeHandle, StoryTreeFocusEvent } from "../../components/story-tree/events";
import {
  dismissHomeLaunch,
  resolveHomeLaunch,
  type HomeLaunchVariant,
} from "../../services/home-launch-state";
import { memoirProjectProfile } from "../../services/memoir-project-profile";
import {
  isAuthError,
  CHAPTER_WORKSPACE_PAGE_URL,
  consumeHomeStoryTarget,
  consumeHomeVoiceTarget,
  consumeHomeNarrationMode,
  type HomeVoiceTarget,
  type HomeStoryTarget,
  navigateToMe,
  navigateToTranscripts,
  navigateToBook,
  navigateToLogin,
} from "../../services/navigation";
import { pageUrl } from "../../services/page-input";
import { writeBookWorkspaceNavigationTarget } from "../../services/book-workspace-navigation";
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
import {
  readCachedMemoirIdentity,
  readCachedMemoirHome,
  writeCachedMemoirHome,
} from "../../stores/memoir-read-cache";
import { userDisplayName } from "../../services/user-display-name";

const VOICE_AUTHORIZATION_VALUE = "voice_authorization";
const RESUME_HINT_CHAR_LIMIT = 28;

type HomeVoiceEntry = "home" | "home_launch" | "home_resume";

interface HomeData extends HomeStoryData {
  loading: boolean;
  creatingVoice: boolean;
  voiceEntry: VoiceSessionEntry | null;
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
  launchSecondaryText: string;
  hasProject: boolean;
  selectedMode: NarrationMode;
  currentGuidanceText: string;
  voiceAuthorizationAgreed: boolean;
  greetingText: string;
  launchModeTagText: string;
  launchVoiceButtonTitle: string;
  projectHeadingText: string;
  projectMetaText: string;
  voiceStartButtonClass: string;
  voiceStartButtonHoverClass: string;
  primaryVoiceButtonTitle: string;
  voiceStartNoteText: string;
  showResumeActions: boolean;
  showMoreActions: boolean;
  showVoiceAuthorizationPanel: boolean;
  largeTextEnabled: boolean;
  homePageClass: string;
  showContinueNarration: boolean;
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
  | "projectHeadingText"
  | "projectMetaText"
  | "voiceStartButtonClass"
  | "voiceStartButtonHoverClass"
  | "primaryVoiceButtonTitle"
  | "currentGuidanceText"
  | "voiceStartNoteText"
  | "showResumeActions"
  | "showVoiceAuthorizationPanel"
  | "homePageClass"
>;

type HomeContext = {
  data: HomeData;
  homeLaunchScope?: string;
  setData(patch: Partial<HomeData>, callback?: () => void): void;
};

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

function launchCopy(
  variant: HomeLaunchVariant,
  projectDisplayTitle: string,
): Pick<
  HomeData,
  "showLaunchPanel" | "showReturnPrompt" | "launchEyebrow" | "launchTitle" | "launchBody" | "launchSecondaryText"
> {
  if (variant === "first_visit") {
    return {
      showLaunchPanel: true,
      showReturnPrompt: false,
      launchEyebrow: "聊出一本书",
      launchTitle: "我的故事，慢慢讲",
      launchBody: "不用写，不用想完整。说一段，小余帮您整理成一页。一段段讲下去，就慢慢成了一本书。",
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
      launchSecondaryText: "先看看首页",
    };
  }
  return {
    showLaunchPanel: false,
    showReturnPrompt: variant === "returning_soft",
    launchEyebrow: "",
    launchTitle: "",
    launchBody: "",
    launchSecondaryText: "先看看首页",
  };
}

export function homeViewPatch(state: HomeData): HomeViewPatch {
  const blocked = state.loading || state.creatingVoice || Boolean(state.voiceEntry);
  const choice = NARRATION_MODES[state.selectedMode];
  const primaryVoiceButtonTitle = state.loading ? "正在加载"
    : state.needsLogin ? "登录后开始"
    : !state.hasProject ? "创建回忆录"
    : state.creatingVoice ? "正在准备"
    : state.selectedMode !== "book_completion" && !state.voiceAuthorizationAgreed ? "同意协议后开始"
    : choice.action;
  return {
    homePageClass: `home-page${state.largeTextEnabled ? " large-text" : ""}${state.voiceEntry ? " is-narrating" : ""}`,
    greetingText: `你好，${state.displayName || "作者"}`,
    launchModeTagText: state.launchVariant === "new_project" ? "新故事本" : "第一次来",
    launchVoiceButtonTitle: primaryVoiceButtonTitle,
    projectHeadingText: state.loading ? "正在加载回忆录"
      : state.hasProject ? (state.projectDisplayTitle || state.projectTitle || "这段回忆")
      : (state.projectDisplayTitle || state.projectTitle || "还没有回忆录"),
    projectMetaText: state.loading ? "我先确认当前回忆录。"
      : state.needsLogin ? "登录后，我在这里等你。"
      : !state.hasProject ? "先创建一本回忆录，我再听你讲。"
      : state.hasLastStoryHint ? state.lastStoryText : state.projectPromptText,
    voiceStartButtonClass: blocked ? "voice-start-button button-blocked" : "voice-start-button",
    voiceStartButtonHoverClass: blocked ? "none" : "voice-start-button-hover",
    primaryVoiceButtonTitle,
    currentGuidanceText: `当前讲述方式：${choice.label}`,
    voiceStartNoteText: state.loading ? "马上就好。"
      : state.needsLogin ? "先登录，再继续讲回忆。"
      : !state.hasProject ? "先起个名字，我再听您讲。"
      : state.creatingVoice ? "我马上开始听。"
      : state.selectedMode === "book_completion" ? "选定问题后再开始录音。"
      : !state.voiceAuthorizationAgreed ? "同意后才会打开麦克风。"
      : choice.description,
    showResumeActions: state.hasProject && !state.needsLogin && !state.loading,
    showVoiceAuthorizationPanel: state.hasProject && !state.needsLogin && !state.loading
      && state.selectedMode !== "book_completion" && !state.voiceAuthorizationAgreed,
  };
}

function setHomeData(context: HomeContext, patch: Partial<HomeData>, callback?: () => void): void {
  const nextData: HomeData = { ...context.data, ...patch };
  context.setData({
    ...patch,
    ...homeViewPatch(nextData),
  }, callback);
}

function applyHomeSnapshotData(
  context: HomeContext,
  snapshot: MemoirHomeSnapshot,
): void {
  const identity = snapshot.identity;
  const changedProject = identity.id !== context.data.userId || (identity.active_project?.id || "") !== context.data.projectId;
  const hasProject = Boolean(identity.active_project_id && identity.active_project);
  const profile = memoirProjectProfile(identity.active_project);
  const projectDisplayTitle = hasProject
    ? memoirStoryTitle(identity.active_project?.title || "")
    : "还没有回忆录";
  const latestHint = hasProject ? homeResumeHint(snapshot.latest_transcript) : "";
  const launchScope = `${identity.id}/${identity.active_project_id || ""}`;
  const keepLaunch = context.homeLaunchScope === launchScope || (
    Boolean(context.homeLaunchScope) && identity.id === context.data.userId
    && context.data.launchVariant === "first_visit"
  );
  const launchVariant = keepLaunch ? context.data.launchVariant : resolveHomeLaunch(identity.id, {
    projectId: identity.active_project_id,
    hasProject,
  });
  context.homeLaunchScope = launchScope;
  setHomeData(context, {
    loading: false,
    error: "",
    needsLogin: false,
    userId: identity.id,
    displayName: userDisplayName(identity.display_name, identity.email),
    projectId: identity.active_project_id,
    projectTitle: identity.active_project?.title || "还没有回忆录",
    projectDisplayTitle,
    projectPromptText: memoirHomePrompt(identity.active_project?.title || ""),
    ...(changedProject ? { ...emptyHomeStoryData(), showContinueNarration: false, voiceEntry: null } : {}),
    lastStoryHint: latestHint,
    lastStoryText: lastStoryText(latestHint),
    hasLastStoryHint: Boolean(latestHint),
    launchVariant,
    ...launchCopy(launchVariant, projectDisplayTitle),
    hasProject,
    selectedMode: changedProject ? profile.defaultGuidanceMode : context.data.selectedMode,
  });
}

definePage<HomeData>()({
  homeLoadSequence: 0,
  homeVisible: false,
  homeLaunchScope: "",
  homeRefreshTimer: pageRuntime(() => new PollingTimer(5000)),
  storyNavigationTarget: null as HomeStoryTarget | null,
  voiceNavigationTarget: null as HomeVoiceTarget | null,
  growthTracker: pageRuntime(() => new HomeGrowthTracker()),
  growthAnimationTimer: pageRuntime(() => new PollingTimer(800)),
  homeScrollTop: 0,
  narrationBottom: Infinity,

  data: {
    loading: false,
    creatingVoice: false,
    voiceEntry: null,
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
    launchSecondaryText: "先看看首页",
    hasProject: false,
    selectedMode: "free_narration",
    currentGuidanceText: "当前讲述方式：自由讲述",
    voiceAuthorizationAgreed: false,
    greetingText: "你好，作者",
    launchModeTagText: "第一次来",
    launchVoiceButtonTitle: "今天先聊一段",
    projectHeadingText: "还没有回忆录",
    projectMetaText: "先创建一本回忆录，我再听你讲。",
    voiceStartButtonClass: "voice-start-button",
    voiceStartButtonHoverClass: "voice-start-button-hover",
    primaryVoiceButtonTitle: "创建回忆录",
    voiceStartNoteText: "先起个名字，我再听您讲。",
    showResumeActions: false,
    showMoreActions: false,
    showVoiceAuthorizationPanel: false,
    largeTextEnabled: false,
    homePageClass: "home-page",
    showContinueNarration: false,
    ...emptyHomeStoryData(),
  },

  onLoad(query?: { section?: string; projectId?: string }) {
    if (query?.section === "story") this.storyNavigationTarget = { projectId: query.projectId || "", narrationSaved: false };
  },

  async onShow() {
    this.homeVisible = true;
    const modeTarget = consumeHomeNarrationMode();
    this.homeLaunchScope = "";
    this.storyNavigationTarget = consumeHomeStoryTarget() ?? this.storyNavigationTarget;
    this.voiceNavigationTarget = consumeHomeVoiceTarget() ?? this.voiceNavigationTarget;
    this.homeRefreshTimer.cancel();
    enableMiniProgramShareMenu();
    this.refreshAccessibilitySettings();
    this.refreshVoiceAuthorizationConsent();
    await this.loadHome();
    if (this.homeVisible && modeTarget?.projectId === this.data.projectId) {
      this.selectNarrationMode({ detail: { value: modeTarget.mode } });
    }
    this.openPendingVoiceSession();
  },

  onHide() {
    this.homeVisible = false;
    this.homeLoadSequence += 1;
    this.homeRefreshTimer.cancel();
    this.growthAnimationTimer.cancel();
    this.setData({ story: clearStoryGrowthAnimation(this.data.story), narrationSaved: false });
  },

  onUnload() {
    this.onHide();
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

  async loadHome(background = false) {
    this.homeRefreshTimer.cancel();
    const loadSequence = (this.homeLoadSequence || 0) + 1;
    this.homeLoadSequence = loadSequence;
    const cached = readCachedMemoirHome();
    const localInvalidationRevision = cached.localInvalidationRevision || 0;
    let showedCachedData = false;
    if (!background && cached.payload) {
      applyHomeSnapshotData(this, cached.payload);
      showedCachedData = true;
    } else if (!background) {
      const cachedIdentity = readCachedMemoirIdentity();
      if (cachedIdentity) {
        applyHomeSnapshotData(this, {
          server_revision: 0,
          identity: cachedIdentity,
          latest_transcript: null,
        });
        showedCachedData = true;
      }
    }
    if (!background && !showedCachedData) {
      setHomeData(this, { loading: true, error: "" });
    }
    try {
      const snapshot = await getMemoirStorySnapshot();
      if (loadSequence !== this.homeLoadSequence) {
        return;
      }
      const currentCache = readCachedMemoirHome();
      if (
        currentCache.status !== "miss"
        && (currentCache.localInvalidationRevision || 0) !== localInvalidationRevision
      ) {
        return;
      }
      writeMemoirIdentitySnapshot(snapshot.identity);
      writeCachedMemoirHome({ server_revision: snapshot.server_revision, identity: snapshot.identity, latest_transcript: snapshot.latest_transcript });
      applyHomeSnapshotData(this, snapshot);
      const freshEvents = this.growthTracker.observe(snapshot.identity.id, this.data.projectId, snapshot.growth_events);
      const target = this.storyNavigationTarget;
      this.storyNavigationTarget = null;
      const validTarget = target && (!target.projectId || target.projectId === this.data.projectId);
      this.setData({
        ...homeStorySnapshotData(snapshot, this.data.story, freshEvents),
        ...(validTarget ? { narrationSaved: target.narrationSaved } : {}),
      }, () => {
        if (loadSequence !== this.homeLoadSequence) return;
        if (!background) this.measureNarration();
        if (validTarget) this.openStoryProgress();
      });
      if (freshEvents.size) this.growthAnimationTimer.schedule(() => this.clearGrowthAnimation());
    } catch (error) {
      if (loadSequence !== this.homeLoadSequence) {
        return;
      }
      if (isAuthError(error)) {
        this.homeRefreshTimer.cancel();
        this.homeLaunchScope = "";
        this.growthTracker.reset();
        setHomeData(this, {
          ...emptyHomeStoryData(),
          needsLogin: true,
          voiceEntry: null,
          showContinueNarration: false,
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
          selectedMode: "free_narration",
        });
        return;
      }
      if (showedCachedData || background || this.data.storyLoaded) {
        setHomeData(this, { storyError: "进度暂未刷新，已保存的讲述和故事仍在，稍后会再试。" });
        console.warn("refresh memoir home snapshot failed", error);
      } else {
        setHomeData(this, { error: error instanceof Error ? error.message : "首页加载失败" });
      }
    } finally {
      if (loadSequence === this.homeLoadSequence) {
        setHomeData(this, { loading: false });
        if (this.homeVisible && this.data.hasProject && !this.data.needsLogin) {
          this.homeRefreshTimer.schedule(() => { void this.loadHome(true); });
        }
      }
    }
  },

  onPageScroll(event: { scrollTop: number }) {
    this.homeScrollTop = event.scrollTop;
    const showContinueNarration = !this.data.voiceEntry && this.data.hasProject && !this.data.needsLogin && event.scrollTop > this.narrationBottom;
    if (showContinueNarration !== this.data.showContinueNarration) this.setData({ showContinueNarration });
  },

  measureNarration() {
    wx.createSelectorQuery().select("#home-narration").boundingClientRect(rect => {
      if (rect) {
        this.narrationBottom = rect.bottom + this.homeScrollTop;
        this.onPageScroll({ scrollTop: this.homeScrollTop });
      }
    }).exec();
  },

  clearGrowthAnimation() {
    this.growthAnimationTimer.cancel();
    this.setData({ story: clearStoryGrowthAnimation(this.data.story) });
  },

  viewStoryGrowth() {
    const target = this.data.growthTarget;
    if (!target) return;
    this.setData({ story: { ...this.data.story, chapters: this.data.story.chapters.map(chapter => ({
      ...chapter, expanded: chapter.id === target.chapterId ? true : chapter.expanded,
    })) } }, () => {
      this.selectComponent<StoryTreeHandle>("#story-tree")?.focusGrowth(target.chapterId, target.sectionId);
    });
  },

  focusStoryGrowth(event: StoryTreeFocusEvent) {
    wx.pageScrollTo({ scrollTop: Math.max(0, this.homeScrollTop + event.detail.top - 16), duration: 250 });
  },

  async continueNarration() {
    if (!this.data.voiceAuthorizationAgreed) wx.pageScrollTo({ selector: "#home-voice-authorization", duration: 250 });
    await this.startPrimaryVoiceChat();
  },

  openSettings() {
    navigateToMe();
  },

  openStoryProgress() {
    wx.pageScrollTo({ selector: "#home-story", duration: 250 });
  },

  async refreshStory() {
    await this.loadHome();
  },

  async dismissGrowthNotice() {
    const projectId = this.data.projectId;
    const eventIds = this.data.growthEvents.map((event) => event.event_id);
    if (!projectId || !eventIds.length || this.data.dismissingGrowth) return;
    this.setData({ dismissingGrowth: true });
    try {
      await Promise.all(eventIds.map((id) => acknowledgeMemoirAutomationEvent(projectId, id)));
      if (this.homeVisible && this.data.projectId === projectId) await this.loadHome(true);
    } catch (error) {
      if (this.homeVisible && this.data.projectId === projectId) {
        this.setData({ storyError: error instanceof Error ? error.message : "提醒确认失败，请重试" });
      }
    } finally {
      this.setData({ dismissingGrowth: false });
    }
  },

  async retryDraft() {
    const projectId = this.data.projectId;
    if (!projectId || this.data.retryingDraft || !this.data.progress?.can_retry) return;
    this.setData({ retryingDraft: true, storyError: "" });
    try {
      const progress = await retryMemoirDraftProgress(projectId);
      if (this.homeVisible && projectId === this.data.projectId) {
        this.setData({ progress });
        await this.loadHome(true);
      }
    } catch (error) {
      if (this.homeVisible && projectId === this.data.projectId) {
        this.setData({ storyError: error instanceof Error ? error.message : "继续整理失败" });
      }
    } finally {
      this.setData({ retryingDraft: false });
    }
  },

  toggleChapter(event: StoryTreeChapterEvent) {
    const { chapterId } = event.detail;
    if (!chapterId) {
      return;
    }
    this.clearGrowthAnimation();
    this.setData({
      story: { ...this.data.story, chapters: toggleStoryChapter(this.data.story.chapters, chapterId) },
    });
  },

  openBook() {
    if (!this.canStartVoiceChat(false)) return;
    if (this.data.bookReadable) {
      wx.navigateTo({ url: pageUrl("/pages/book-reader/index", { projectId: this.data.projectId }) });
    } else {
      navigateToBook();
    }
  },

  readChapter(event: StoryTreeChapterEvent) {
    const { chapterId } = event.detail;
    if (!this.data.projectId || !this.data.story.chapters.some((chapter) => chapter.id === chapterId && chapter.readable)) return;
    wx.navigateTo({ url: pageUrl("/pages/book-reader/index", { projectId: this.data.projectId, chapterId }) });
  },

  openChapterManager() {
    if (!this.canStartVoiceChat(false)) return;
    writeBookWorkspaceNavigationTarget("chapters");
    navigateToBook();
  },

  openStoryNotice() {
    if (this.data.reviewNoticeAction === "issues") this.openReviewIssues();
    else if (this.data.reviewNoticeAction === "transcripts") this.openTranscripts();
    else if (this.data.reviewNoticeAction === "book") this.openChapterManager();
  },

  openReviewIssues() {
    if (!this.data.projectId) return;
    wx.navigateTo({ url: pageUrl("/pages/book-completion/index", { projectId: this.data.projectId, projectTitle: this.data.story.memoir.title }) });
  },

  openChapterCandidate() {
    if (!this.data.candidateChapterId) return;
    this.openChapter({ detail: { chapterId: this.data.candidateChapterId } });
  },

  openTranscripts() {
    if (!this.canStartVoiceChat(false)) return;
    navigateToTranscripts();
  },

  openVoiceEdit() {
    if (!this.data.projectId) return;
    if (this.data.voiceEntry) {
      wx.showToast({ title: "请先结束当前讲述", icon: "none" });
      return;
    }
    wx.navigateTo({ url: pageUrl("/pages/story-voice-edit/index", { projectId: this.data.projectId, projectTitle: this.data.projectTitle }) });
  },

  openChapter(event: StoryTreeChapterEvent) {
    const { chapterId, sectionId } = event.detail;
    if (!this.data.projectId || !chapterId) {
      return;
    }
    wx.navigateTo({
      url: pageUrl(CHAPTER_WORKSPACE_PAGE_URL, {
        projectId: this.data.projectId,
        chapterId,
        sectionId,
      }),
    });
  },

  openMedia(event: StoryTreeMediaEvent) {
    if (!this.data.projectId) {
      return;
    }
    const { chapterId } = event.detail;
    wx.navigateTo({
      url: pageUrl("/pages/image-assets/index", {
        projectId: this.data.projectId,
        scope: chapterId ? "chapter" : "unassigned",
        chapterId,
      }),
    });
  },

  changeVoiceAuthorization(event: CheckboxChangeEvent) {
    const agreed = event.detail.value.includes(VOICE_AUTHORIZATION_VALUE);
    const consent = saveVoiceAuthorizationConsent(agreed);
    setHomeData(this, {
      voiceAuthorizationAgreed: consent.agreed,
      error: agreed ? "" : this.data.error,
    }, () => this.measureNarration());
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
    }, () => this.measureNarration());
  },

  dismissReturnPrompt() {
    setHomeData(this, { launchVariant: "none", showReturnPrompt: false }, () => this.measureNarration());
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

  selectNarrationMode({ detail }: NarrationModeEvent) {
    if (this.data.loading || this.data.creatingVoice || this.data.voiceEntry || !isNarrationMode(detail.value)) return;
    setHomeData(this, { selectedMode: detail.value, error: "" }, () => this.measureNarration());
  },

  toggleMoreActions() {
    setHomeData(this, { showMoreActions: !this.data.showMoreActions }, () => this.measureNarration());
  },

  async startPrimaryVoiceChat() {
    await this.startSelectedNarration("home_resume");
  },

  async startLaunchVoiceChat() {
    await this.startSelectedNarration("home_launch");
  },

  async startSelectedNarration(entry: HomeVoiceEntry) {
    const mode = this.data.selectedMode;
    if (mode === "book_completion") {
      this.openBookSupplementInterview();
      return;
    }
    const human = mode === "human_interview";
    await this.startVoiceChatWithMode(mode === "human_interview" ? "free_narration" : mode,
      entry, human ? "human_interview" : "ai_interview");
  },

  openBookSupplementInterview() {
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

  openNarrationOverview() {
    if (!this.canStartVoiceChat(false)) {
      return;
    }
    wx.navigateTo({ url: "/pages/voice-progress/index" });
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
    if (this.data.loading || this.data.creatingVoice || this.data.voiceEntry) {
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
    const { userId, projectId } = this.data;
    setHomeData(this, { creatingVoice: true, error: "" });
    try {
      await syncVoiceAuthorizationConsent();
      if (!this.homeVisible || userId !== this.data.userId || projectId !== this.data.projectId) return;
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
    const { userId, projectId } = this.data;
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
      projectId,
    });
    if (userId !== this.data.userId || projectId !== this.data.projectId) return;
    trackAuditAction(
      "voice_chat.start",
      {
        guidance_mode: mode,
        interview_type: interviewType,
        session_goal: sessionGoal,
        entry,
        launch_variant: this.data.launchVariant,
      },
      { projectId, voiceSessionId: detail.session.id },
    );
    if (entry === "home_launch") {
      this.dismissLaunchPanel();
    }
    this.voiceNavigationTarget = { userId, projectId, entry: {
      voiceSessionId: detail.session.id, projectId, mode, interviewType, sessionGoal,
      projectTitle: this.data.projectDisplayTitle || this.data.projectTitle,
    } };
    this.openPendingVoiceSession();
  },

  openPendingVoiceSession() {
    const target = this.voiceNavigationTarget;
    if (!this.homeVisible || !target) return;
    this.voiceNavigationTarget = null;
    if (target.userId !== this.data.userId || target.projectId !== this.data.projectId || this.data.needsLogin) return;
    if (this.data.voiceEntry) return;
    setHomeData(this, { voiceEntry: target.entry, selectedMode: narrationModeForSession({
      guidance_mode: target.entry.mode || "free_narration", interview_type: target.entry.interviewType || "ai_interview",
      session_goal: target.entry.sessionGoal || "open_narration",
    }), showContinueNarration: false, narrationSaved: false }, () => {
      wx.pageScrollTo({ scrollTop: 0, duration: 250 });
    });
  },

  async exitVoiceSession({ detail }: VoiceSessionExitEvent) {
    if (this.data.voiceEntry?.voiceSessionId !== detail.voiceSessionId || !this.homeVisible) return;
    if (detail.destination === "story" && detail.projectId !== this.data.projectId) return;
    if (detail.destination === "story" && !detail.finished) {
      this.openStoryProgress();
      return;
    }
    setHomeData(this, { voiceEntry: null }, () => this.measureNarration());
    if (detail.destination === "transcripts") {
      navigateToTranscripts();
      return;
    }
    this.storyNavigationTarget = { projectId: this.data.projectId, narrationSaved: detail.destination === "story" && detail.narrationSaved };
    await this.loadHome(true);
  },
});
