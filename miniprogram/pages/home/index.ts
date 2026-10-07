import { narrationModeForSession, NARRATION_MODES, type NarrationMode } from "../../domain/memoir-narration-mode";
import type {
  VoiceSessionEntry,
  VoiceSessionExitEvent,
} from "../../components/voice-session/events";
import type {
  MemoirGuidanceMode,
  MemoirInterviewType,
  MemoirSessionGoal,
} from "../../domain/memoir-voice-session";
import type {
  MemoirShareVideoCandidate,
  MemoirShareVideoJob,
  MemoirVoiceTranscriptItem,
} from "../../domain/memoir-transcript";
import type { MemoirHomeSnapshot } from "../../domain/memoir-snapshots";
import { definePage, pageRuntime } from "../../services/page-definition";
import { trackAuditAction } from "../../services/audit-tracker";
import { memoirStoryTitle } from "../../services/memoir-display-copy";
import { readLargeTextEnabled } from "../../services/accessibility-settings";
import {
  createMemoirVoiceSession,
} from "../../services/memoir-voice-session-api";
import { getMemoirStorySnapshot, retryMemoirDraftProgress } from "../../services/memoir-snapshot-api";
import {
  dismissMemoirShareVideoCandidate,
  downloadMemoirShareVideoThumbnail,
  getMemoirShareVideoCandidate,
  getMemoirShareVideoJob,
  listMemoirShareVideos,
} from "../../services/memoir-transcript-api";
import { formatTranscriptDurationSeconds } from "../../services/transcript-view-model";
import { acknowledgeMemoirAutomationEvent } from "../../services/memoir-voice-session-api";
import { writeMemoirIdentitySnapshot } from "../../stores/memoir-cache";
import { PollingTimer } from "../../services/polling-timer";
import { emptyHomeStoryData, homeStorySnapshotData, clearStoryGrowthAnimation, HomeGrowthTracker, type HomeStoryData } from "./home-story-state";
import type { StoryTreeChapterEvent, StoryTreeHandle, StoryTreeFocusEvent } from "../../components/story-tree/events";
import {
  dismissHomeLaunch,
  homeBookOpened,
  markHomeBookOpened,
  resolveHomeLaunch,
  type HomeLaunchVariant,
} from "../../services/home-launch-state";
import { memoirProjectProfile } from "../../services/memoir-project-profile";
import {
  isAuthError,
  CHAPTER_WORKSPACE_PAGE_URL,
  consumeHomeStoryTarget,
  consumeHomeVoiceTarget,
  type HomeVoiceTarget,
  type HomeStoryTarget,
  navigateToBook,
  navigateToMe,
  navigateToTranscripts,
  navigateToLogin,
} from "../../services/navigation";
import { pageUrl } from "../../services/page-input";
import {
  readVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_VERSION,
  VOICE_AUTHORIZATION_PAGE_URL,
  type VoiceAuthorizationConsent,
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
  lastStoryHint: string;
  hasLastStoryHint: boolean;
  launchVariant: HomeLaunchVariant;
  showBookHint: boolean;
  hasProject: boolean;
  selectedMode: NarrationMode;
  voiceAuthorizationAgreed: boolean;
  voiceAuthorizationRequested: boolean;
  voiceAuthorizationSaving: boolean;
  greetingText: string;
  headerSubtitleText: string;
  voiceStartButtonClass: string;
  voiceStartButtonHoverClass: string;
  primaryVoiceButtonTitle: string;
  showNarrationChoiceCard: boolean;
  showVoiceAuthorizationPanel: boolean;
  largeTextEnabled: boolean;
  homePageClass: string;
  showContinueNarration: boolean;
  shareCandidate: MemoirShareVideoCandidate | null;
  shareCandidateDurationText: string;
  shareCandidateDismissing: boolean;
  shareCandidateError: string;
  shareCandidateJob: MemoirShareVideoJob | null;
  shareCandidateThumbnailPath: string;
  shareVideoTotal: number;
  shareVideoActiveCount: number;
  shareVideoFailedCount: number;
}

interface CheckboxChangeEvent {
  detail: {
    value: string[];
  };
}

type HomeViewPatch = Pick<
  HomeData,
  | "greetingText"
  | "headerSubtitleText"
  | "voiceStartButtonClass"
  | "voiceStartButtonHoverClass"
  | "primaryVoiceButtonTitle"
  | "showNarrationChoiceCard"
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

export function homeViewPatch(state: HomeData): HomeViewPatch {
  const blocked = state.loading || state.creatingVoice || state.voiceAuthorizationSaving || Boolean(state.voiceEntry);
  const choice = NARRATION_MODES[state.selectedMode];
  const primaryVoiceButtonTitle = state.loading ? "正在加载"
    : state.needsLogin ? "登录后开始"
    : !state.hasProject ? "种下我的故事"
    : state.voiceAuthorizationSaving ? "正在保存授权"
    : state.creatingVoice ? "正在准备"
    : state.selectedMode === "book_completion" ? choice.action
    : state.hasStories ? "讲个新故事" : "讲第一个故事";
  return {
    homePageClass: `home-page${state.largeTextEnabled ? " large-text" : ""}${state.voiceEntry ? " is-narrating" : ""}`,
    greetingText: `你好，${state.displayName || "作者"}`,
    headerSubtitleText: state.loading ? "正在看看您的故事树。"
      : state.needsLogin ? "登录后，把想说的故事慢慢留下来。"
      : !state.hasProject ? "每一个故事，都从一颗种子开始。"
      : state.hasStories ? ""
      : "从一段回忆开始，让故事慢慢发芽。",
    voiceStartButtonClass: blocked ? "button-blocked" : "",
    voiceStartButtonHoverClass: blocked ? "none" : "voice-start-button-hover",
    primaryVoiceButtonTitle,
    showNarrationChoiceCard: !state.needsLogin && state.hasProject && state.hasStories
      && state.hasLastStoryHint && state.selectedMode !== "book_completion",
    showVoiceAuthorizationPanel: state.hasProject && !state.needsLogin && !state.loading
      && state.selectedMode !== "book_completion" && !state.voiceAuthorizationAgreed
      && state.voiceAuthorizationRequested,
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
    ...(changedProject ? {
      ...emptyHomeStoryData(),
      showContinueNarration: false,
      voiceEntry: null,
      voiceAuthorizationRequested: false,
      voiceAuthorizationSaving: false,
      shareCandidate: null,
      shareCandidateDurationText: "",
      shareCandidateDismissing: false,
      shareCandidateError: "",
      shareCandidateJob: null,
      shareCandidateThumbnailPath: "",
      shareVideoTotal: 0,
      shareVideoActiveCount: 0,
      shareVideoFailedCount: 0,
    } : {}),
    lastStoryHint: latestHint,
    hasLastStoryHint: Boolean(latestHint),
    launchVariant,
    showBookHint: !homeBookOpened(identity.id),
    hasProject,
    selectedMode: changedProject || !context.data.voiceEntry
      ? profile.defaultNarrationMode
      : context.data.selectedMode,
  });
}

definePage<HomeData>()({
  homeLoadSequence: 0,
  shareCandidateRequestSequence: 0,
  shareVideosRequestSequence: 0,
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
    lastStoryHint: "",
    hasLastStoryHint: false,
    launchVariant: "none",
    showBookHint: false,
    hasProject: false,
    selectedMode: "free_narration",
    voiceAuthorizationAgreed: false,
    voiceAuthorizationRequested: false,
    voiceAuthorizationSaving: false,
    greetingText: "你好，作者",
    headerSubtitleText: "每一个故事，都从一颗种子开始。",
    voiceStartButtonClass: "",
    voiceStartButtonHoverClass: "voice-start-button-hover",
    primaryVoiceButtonTitle: "种下我的故事",
    showNarrationChoiceCard: false,
    showVoiceAuthorizationPanel: false,
    largeTextEnabled: false,
    homePageClass: "home-page",
    showContinueNarration: false,
    shareCandidate: null,
    shareCandidateDurationText: "",
    shareCandidateDismissing: false,
    shareCandidateError: "",
    shareCandidateJob: null,
    shareCandidateThumbnailPath: "",
    shareVideoTotal: 0,
    shareVideoActiveCount: 0,
    shareVideoFailedCount: 0,
    ...emptyHomeStoryData(),
  },

  onLoad(query?: { section?: string; projectId?: string }) {
    if (query?.section === "story") this.storyNavigationTarget = { projectId: query.projectId || "", narrationSaved: false };
  },

  async onShow() {
    this.homeVisible = true;
    this.homeLaunchScope = "";
    this.storyNavigationTarget = consumeHomeStoryTarget() ?? this.storyNavigationTarget;
    this.voiceNavigationTarget = consumeHomeVoiceTarget() ?? this.voiceNavigationTarget;
    this.homeRefreshTimer.cancel();
    enableMiniProgramShareMenu();
    this.refreshAccessibilitySettings();
    this.refreshVoiceAuthorizationConsent();
    await this.loadHome();
    this.openPendingVoiceSession();
  },

  onHide() {
    this.homeVisible = false;
    this.homeLoadSequence += 1;
    this.shareCandidateRequestSequence += 1;
    this.shareVideosRequestSequence += 1;
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
    const voiceAuthorizationAgreed = readVoiceAuthorizationConsent();
    setHomeData(this, {
      voiceAuthorizationAgreed,
      voiceAuthorizationRequested: voiceAuthorizationAgreed ? false : this.data.voiceAuthorizationRequested,
    });
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
      setHomeData(this, {
        ...homeStorySnapshotData(snapshot, this.data.story, freshEvents),
        ...(validTarget ? { narrationSaved: target.narrationSaved } : {}),
      }, () => {
        if (loadSequence !== this.homeLoadSequence) return;
        if (!background) this.measureNarration();
        if (validTarget) this.openStoryProgress();
      });
      void this.refreshShareCandidate(this.data.projectId);
      void this.refreshShareVideos(this.data.projectId);
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
          lastStoryHint: "",
          hasLastStoryHint: false,
          launchVariant: "none",
          hasProject: false,
          selectedMode: "free_narration",
          voiceAuthorizationRequested: false,
          voiceAuthorizationSaving: false,
          shareCandidate: null,
          shareCandidateDurationText: "",
          shareCandidateError: "",
          shareCandidateJob: null,
          shareCandidateThumbnailPath: "",
          shareVideoTotal: 0,
          shareVideoActiveCount: 0,
          shareVideoFailedCount: 0,
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

  async refreshShareCandidate(projectId: string) {
    const requestSequence = ++this.shareCandidateRequestSequence;
    if (!this.homeVisible || !projectId || this.data.needsLogin || this.data.voiceEntry) return;
    try {
      const response = await getMemoirShareVideoCandidate(projectId);
      if (requestSequence !== this.shareCandidateRequestSequence
        || !this.homeVisible || this.data.projectId !== projectId || this.data.voiceEntry) return;
      const candidate = response.candidate;
      const existingJob = candidate?.video_job_id === this.data.shareCandidateJob?.id
        ? this.data.shareCandidateJob : null;
      this.setData({
        shareCandidate: candidate,
        shareCandidateDurationText: candidate?.duration_ms
          ? formatTranscriptDurationSeconds(candidate.duration_ms / 1000)
          : "",
        shareCandidateError: "",
        shareCandidateJob: existingJob,
        shareCandidateThumbnailPath: existingJob ? this.data.shareCandidateThumbnailPath : "",
      });
      if (candidate?.status === "accepted" && candidate.video_job_id
        && (!existingJob || existingJob.status === "pending" || existingJob.status === "running")) {
        try {
          const job = await getMemoirShareVideoJob(candidate.video_job_id);
          if (requestSequence === this.shareCandidateRequestSequence
            && this.homeVisible && this.data.projectId === projectId
            && this.data.shareCandidate?.id === candidate.id) {
            this.setData({ shareCandidateJob: job });
          }
        } catch (error) {
          if (requestSequence === this.shareCandidateRequestSequence) {
            console.warn("share video job refresh failed", error);
          }
        }
      }
      const job = this.data.shareCandidateJob;
      if (job?.status === "succeeded" && !this.data.shareCandidateThumbnailPath
        && requestSequence === this.shareCandidateRequestSequence) {
        try {
          const path = await downloadMemoirShareVideoThumbnail(job.id);
          if (requestSequence === this.shareCandidateRequestSequence && this.homeVisible
            && this.data.projectId === projectId && this.data.shareCandidate?.id === candidate?.id
            && this.data.shareCandidateJob?.id === job.id) {
            this.setData({ shareCandidateThumbnailPath: path });
          }
        } catch (error) {
          console.warn("share video thumbnail unavailable", error);
        }
      }
    } catch (error) {
      if (requestSequence === this.shareCandidateRequestSequence) {
        console.warn("share video suggestion refresh failed", error);
      }
    }
  },

  async refreshShareVideos(projectId: string) {
    const requestSequence = ++this.shareVideosRequestSequence;
    if (!this.homeVisible || !projectId || this.data.needsLogin || this.data.voiceEntry) return;
    try {
      const response = await listMemoirShareVideos(projectId, { limit: 1, offset: 0 });
      if (requestSequence !== this.shareVideosRequestSequence
        || !this.homeVisible || this.data.projectId !== projectId || this.data.voiceEntry) return;
      this.setData({
        shareVideoTotal: response.total,
        shareVideoActiveCount: response.active_jobs.length,
        shareVideoFailedCount: response.failed_jobs.length,
      });
    } catch (error) {
      if (requestSequence === this.shareVideosRequestSequence) {
        console.warn("share video library refresh failed", error);
      }
    }
  },

  openShareCandidateEditor() {
    const candidate = this.data.shareCandidate;
    if (!candidate || candidate.status !== "ready" || !candidate.utterance_id
      || this.data.voiceEntry || this.data.shareCandidateDismissing) return;
    this.setData({ shareCandidateError: "" });
    wx.navigateTo({ url: pageUrl("/pages/transcript-media/index", {
      id: candidate.utterance_id,
      candidateId: candidate.id,
    }) });
    trackAuditAction("home.share_video_candidate_edit", {}, {
      projectId: this.data.projectId, voiceSessionId: candidate.voice_session_id,
    });
  },

  openShareCandidateVideo() {
    const candidate = this.data.shareCandidate;
    if (!candidate || candidate.status !== "accepted" || !candidate.utterance_id
      || !candidate.video_job_id || this.data.voiceEntry || this.data.shareCandidateDismissing) return;
    const completed = this.data.shareCandidateJob?.status === "succeeded";
    wx.navigateTo({ url: pageUrl("/pages/transcript-media/index", {
      id: candidate.utterance_id,
      jobId: candidate.video_job_id,
    }), success: () => {
      if (completed && this.data.shareCandidate?.id === candidate.id
        && this.data.shareCandidate.video_job_id === candidate.video_job_id) {
        void this.dismissShareCandidate();
      }
    } });
  },

  openShareVideoLibrary() {
    const projectId = this.data.projectId;
    if (!projectId || this.data.voiceEntry) return;
    wx.navigateTo({ url: pageUrl("/pages/share-videos/index", { projectId }) });
  },

  async dismissShareCandidate() {
    const candidate = this.data.shareCandidate;
    const projectId = this.data.projectId;
    if (!candidate || this.data.voiceEntry || this.data.shareCandidateDismissing) return;
    this.shareCandidateRequestSequence += 1;
    this.setData({ shareCandidateDismissing: true, shareCandidateError: "" });
    try {
      await dismissMemoirShareVideoCandidate(projectId, candidate.id);
      if (this.homeVisible && this.data.projectId === projectId && this.data.shareCandidate?.id === candidate.id) {
        this.shareCandidateRequestSequence += 1;
        this.setData({ shareCandidate: null, shareCandidateDurationText: "", shareCandidateJob: null, shareCandidateThumbnailPath: "" });
        void this.refreshShareVideos(projectId);
      }
    } catch (error) {
      if (this.homeVisible && this.data.projectId === projectId) {
        this.setData({ shareCandidateError: error instanceof Error ? error.message : "暂时无法关闭提示" });
      }
    } finally {
      if (this.data.projectId === projectId) this.setData({ shareCandidateDismissing: false });
    }
  },

  onPageScroll(event: { scrollTop: number }) {
    this.homeScrollTop = event.scrollTop;
    const showContinueNarration = !this.data.voiceEntry
      && this.data.hasProject && !this.data.needsLogin && event.scrollTop > this.narrationBottom;
    if (showContinueNarration !== this.data.showContinueNarration) this.setData({ showContinueNarration });
  },

  measureNarration() {
    wx.createSelectorQuery().select("#home-narration-end").boundingClientRect(rect => {
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
    this.selectComponent<StoryTreeHandle>("#story-tree")?.focusGrowth(target.chapterId);
    void this.dismissGrowthNotice();
  },

  focusStoryGrowth(event: StoryTreeFocusEvent) {
    wx.pageScrollTo({ scrollTop: Math.max(0, this.homeScrollTop + event.detail.top - 16), duration: 250 });
  },

  async continueNarration() {
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
      if (this.homeVisible && this.data.projectId === projectId) {
        this.setData({
          growthEvents: [],
          growthNoticeTitle: "",
          growthNoticeText: "",
          growthNoticeContext: "",
          growthTarget: null,
        });
      }
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

  openReviewIssues() {
    if (!this.data.projectId) return;
    wx.navigateTo({ url: pageUrl("/pages/book-completion/index", { projectId: this.data.projectId, projectTitle: this.data.story.memoir.title }) });
  },

  openBook() {
    if (!this.data.projectId) return;
    markHomeBookOpened(this.data.userId);
    if (this.data.showBookHint) setHomeData(this, { showBookHint: false });
    navigateToBook();
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
    const { chapterId } = event.detail;
    if (!this.data.projectId || !chapterId) {
      return;
    }
    wx.navigateTo({
      url: pageUrl(CHAPTER_WORKSPACE_PAGE_URL, {
        projectId: this.data.projectId,
        chapterId,
      }),
    });
  },

  async changeVoiceAuthorization(event: CheckboxChangeEvent) {
    if (this.data.voiceAuthorizationSaving) return;
    const agreed = event.detail.value.includes(VOICE_AUTHORIZATION_VALUE);
    const consent: VoiceAuthorizationConsent = {
      agreed,
      version: VOICE_AUTHORIZATION_VERSION,
      agreedAt: new Date().toISOString(),
    };
    const userId = this.data.userId;
    const projectId = this.data.projectId;
    setHomeData(this, {
      voiceAuthorizationAgreed: consent.agreed,
      voiceAuthorizationSaving: true,
      error: "",
    }, () => this.measureNarration());
    try {
      await syncVoiceAuthorizationConsent({ consent });
      if (userId !== this.data.userId || projectId !== this.data.projectId) return;
      setHomeData(this, {
        voiceAuthorizationRequested: agreed ? false : this.data.voiceAuthorizationRequested,
      });
    } catch (error) {
      if (userId !== this.data.userId || projectId !== this.data.projectId) return;
      setHomeData(this, {
        voiceAuthorizationAgreed: readVoiceAuthorizationConsent(),
        voiceAuthorizationRequested: true,
        error: error instanceof Error ? error.message : "语音授权保存失败",
      });
    } finally {
      if (userId === this.data.userId && projectId === this.data.projectId) {
        setHomeData(this, { voiceAuthorizationSaving: false });
      }
    }
  },

  openVoiceAuthorization() {
    wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
  },

  dismissLaunchExperience() {
    dismissHomeLaunch(this.data.userId, this.data.launchVariant, this.data.projectId);
    setHomeData(this, { launchVariant: "none" });
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

  async startPrimaryVoiceChat() {
    await this.startSelectedNarration(this.data.launchVariant === "none" ? "home" : "home_launch");
  },

  async resumeLastStory() {
    await this.startSelectedNarration("home_resume");
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
    if (!this.canStartVoiceChat(false)) return;
    this.openReviewIssues();
  },

  ensureVoiceAuthorization() {
    if (this.data.voiceAuthorizationAgreed) {
      return true;
    }
    setHomeData(this, { voiceAuthorizationRequested: true, error: "" }, () => {
      wx.pageScrollTo({ selector: "#home-voice-authorization", duration: 250 });
    });
    return false;
  },

  canStartVoiceChat(requireVoiceAuthorization = true) {
    if (this.data.loading || this.data.creatingVoice || this.data.voiceAuthorizationSaving || this.data.voiceEntry) {
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
      this.dismissLaunchExperience();
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
