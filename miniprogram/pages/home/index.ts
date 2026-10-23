import { narrationModeForSession, type NarrationMode } from "../../domain/memoir-narration-mode";
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
import { createMemoirProject } from "../../services/memoir";
import { getMemoirDemoStory, getMemoirStorySnapshot, retryMemoirDraftProgress } from "../../services/memoir-snapshot-api";
import { storyViewData } from "../../components/story-tree/story-view-adapter";
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
import {
  emptyHomeStoryData,
  homeStorySnapshotData,
  clearStoryGrowthAnimation,
  HomeGrowthTracker,
  homeNoticeQueue,
  readHomeNoticeReadState,
  rememberHomeNoticeReadState,
  type HomeNoticeReadState,
  type HomeStoryData,
  type HomeNotice,
} from "./home-story-state";
import type { StoryTreeChapterEvent } from "../../components/story-tree/events";
import {
  dismissHomeLaunch,
  homeBookOpened,
  markHomeBookOpened,
  resolveHomeLaunch,
  type HomeLaunchVariant,
} from "../../services/home-launch-state";
import { memoirProjectProfile, narratorProfileLine } from "../../services/memoir-project-profile";
import {
  isAuthError,
  CHAPTER_WORKSPACE_PAGE_URL,
  consumeHomeStoryTarget,
  consumeHomeVoiceTarget,
  consumeNarrationRequest,
  type HomeVoiceTarget,
  type HomeStoryTarget,
  navigateToBook,
  navigateToMe,
  navigateToTranscripts,
  navigateToLogin,
  navigateToLoginThenNarrate,
  navigateToVideoClipPicker,
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
import { loadShareLikeNotice, markShareLikeNoticeSeen, type ShareLikeNotice } from "../../services/share-likes";

const VOICE_AUTHORIZATION_VALUE = "voice_authorization";
const RESUME_HINT_CHAR_LIMIT = 28;
// The first story starts without a setup form; the title can be changed later.
const FIRST_PROJECT_TITLE = "我的回忆录";

type HomeVoiceEntry = "home" | "home_launch" | "home_resume";

interface HomeData extends HomeStoryData {
  loading: boolean;
  creatingVoice: boolean;
  voiceEntry: VoiceSessionEntry | null;
  needsLogin: boolean;
  demoReadableChapterIds: string[];
  error: string;
  userId: string;
  displayName: string;
  projectId: string;
  projectTitle: string;
  projectDisplayTitle: string;
  narratorProfileText: string;
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
  memoirCard: { label: string; title: string; action: string; showProgress: boolean };
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
  // Family likes on forwarded stories that the author has not acknowledged yet.
  shareLikeNotice: ShareLikeNotice | null;
  notices: HomeNotice[];
  notice: HomeNotice | null;
  noticeReadState: HomeNoticeReadState;
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
  | "memoirCard"
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
  const primaryVoiceButtonTitle = state.loading ? "正在加载"
    : state.voiceAuthorizationSaving ? "正在保存授权"
    : state.creatingVoice ? "正在准备"
    : "开始讲述";
  const greetingName = state.displayName.trim();
  return {
    homePageClass: `home-page${state.largeTextEnabled ? " large-text" : ""}${state.voiceEntry ? " is-narrating" : ""}`,
    greetingText: greetingName && greetingName !== "作者" ? `你好，${greetingName}` : "你好",
    headerSubtitleText: "",
    voiceStartButtonClass: blocked ? "button-blocked" : "",
    voiceStartButtonHoverClass: blocked ? "none" : "voice-start-button-hover",
    primaryVoiceButtonTitle,
    memoirCard: {
      label: state.needsLogin ? "示例回忆录" : "我的回忆录",
      title: state.needsLogin || state.hasProject ? state.story.memoir.title : "还没有回忆录",
      action: state.needsLogin ? "翻开看看" : state.hasProject ? "翻开我的书" : "看看示例",
      showProgress: state.needsLogin || state.hasProject,
    },
    showNarrationChoiceCard: false,
    showVoiceAuthorizationPanel: !state.needsLogin && !state.loading
      && !state.voiceAuthorizationAgreed && state.voiceAuthorizationRequested,
  };
}

function setHomeData(context: HomeContext, patch: Partial<HomeData>, callback?: () => void): void {
  const nextData: HomeData = { ...context.data, ...patch };
  // Processing can temporarily mask an unresolved failure; only a settled snapshot clears it.
  // Required confirmations keep their own identity-based read marker across processing.
  if (patch.progress && patch.progress.status !== "failed" && patch.progress.status !== "processing"
    && nextData.noticeReadState.attentionId.startsWith('attention:["failed",')) {
    nextData.noticeReadState = { ...nextData.noticeReadState, attentionId: "" };
    rememberHomeNoticeReadState(nextData.userId, nextData.projectId, nextData.noticeReadState);
  }
  const notices = homeNoticeQueue(nextData);
  context.setData({
    ...patch,
    ...homeViewPatch(nextData),
    noticeReadState: nextData.noticeReadState,
    notices, notice: notices[0] || null,
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
    demoReadableChapterIds: [],
    userId: identity.id,
    displayName: userDisplayName(identity.display_name, identity.email),
    projectId: identity.active_project_id,
    projectTitle: identity.active_project?.title || "还没有回忆录",
    projectDisplayTitle,
    narratorProfileText: hasProject && identity.active_project ? narratorProfileLine(identity.active_project) : "",
    ...(changedProject ? {
      ...emptyHomeStoryData(),
      notices: [],
      noticeReadState: readHomeNoticeReadState(identity.id, identity.active_project_id),
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
      shareLikeNotice: null,
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
  shareLikeNoticeRequestSequence: 0,
  homeVisible: false,
  homeLaunchScope: "",
  homeRefreshTimer: pageRuntime(() => new PollingTimer(5000)),
  storyNavigationTarget: null as HomeStoryTarget | null,
  voiceNavigationTarget: null as HomeVoiceTarget | null,
  // The narration the user tapped before the voice consent box appeared; agreeing carries on with it.
  pendingNarrationEntry: null as HomeVoiceEntry | null,
  growthTracker: pageRuntime(() => new HomeGrowthTracker()),
  growthAnimationTimer: pageRuntime(() => new PollingTimer(800)),
  homeScrollTop: 0,
  narrationBottom: Infinity,

  data: {
    loading: false,
    creatingVoice: false,
    voiceEntry: null,
    needsLogin: false,
    demoReadableChapterIds: [],
    error: "",
    userId: "",
    displayName: "",
    projectId: "",
    projectTitle: "",
    projectDisplayTitle: "",
    narratorProfileText: "",
    lastStoryHint: "",
    hasLastStoryHint: false,
    launchVariant: "none",
    showBookHint: false,
    hasProject: false,
    selectedMode: "guided_interview",
    voiceAuthorizationAgreed: false,
    voiceAuthorizationRequested: false,
    voiceAuthorizationSaving: false,
    greetingText: "你好",
    headerSubtitleText: "从一段回忆开始。",
    voiceStartButtonClass: "",
    voiceStartButtonHoverClass: "voice-start-button-hover",
    primaryVoiceButtonTitle: "开始讲述",
    memoirCard: { label: "我的回忆录", title: "还没有回忆录", action: "看看示例", showProgress: false },
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
    shareLikeNotice: null,
    notices: [],
    notice: null,
    noticeReadState: { readableSeen: false, attentionId: "" },
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
    const narrationRequested = consumeNarrationRequest();
    this.refreshAccessibilitySettings();
    const agreedEntry = this.refreshVoiceAuthorizationConsent();
    await this.loadHome();
    this.openPendingVoiceSession();
    if (!this.homeVisible) return;
    if (this.data.needsLogin) {
      // A direct start can discover an expired session; a return from cancelled login stays public.
      if (narrationRequested === "start") navigateToLoginThenNarrate();
      return;
    }
    if (narrationRequested) await this.startPrimaryVoiceChat();
    else if (agreedEntry) await this.startSelectedNarration(agreedEntry);
  },

  onHide() {
    this.homeVisible = false;
    this.homeLoadSequence += 1;
    this.shareCandidateRequestSequence += 1;
    this.shareVideosRequestSequence += 1;
    this.shareLikeNoticeRequestSequence += 1;
    this.homeRefreshTimer.cancel();
    this.growthAnimationTimer.cancel();
    setHomeData(this, { story: clearStoryGrowthAnimation(this.data.story) });
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

  /** Returns the narration to carry on with when consent was just given on the authorization page. */
  refreshVoiceAuthorizationConsent(): HomeVoiceEntry | null {
    const voiceAuthorizationAgreed = readVoiceAuthorizationConsent();
    const agreedEntry = voiceAuthorizationAgreed ? this.takePendingNarration() : null;
    setHomeData(this, {
      voiceAuthorizationAgreed,
      voiceAuthorizationRequested: voiceAuthorizationAgreed ? false : this.data.voiceAuthorizationRequested,
    });
    return agreedEntry;
  },

  takePendingNarration(): HomeVoiceEntry | null {
    const entry = this.data.voiceAuthorizationRequested ? this.pendingNarrationEntry : null;
    this.pendingNarrationEntry = null;
    return entry;
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
        ...homeStorySnapshotData(snapshot, freshEvents, this.growthTracker.pending(snapshot.growth_events)),
      }, () => {
        if (loadSequence !== this.homeLoadSequence) return;
        if (!background) this.measureNarration();
        if (validTarget) this.openStoryProgress();
      });
      void this.refreshShareCandidate(this.data.projectId);
      void this.refreshShareVideos(this.data.projectId);
      if (!background) void this.refreshShareLikeNotice(this.data.userId, this.data.projectId);
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
          notices: [],
          noticeReadState: { readableSeen: false, attentionId: "" },
          needsLogin: true,
          demoReadableChapterIds: [],
          voiceEntry: null,
          showContinueNarration: false,
          userId: "",
          displayName: "作者",
          projectId: "",
          projectTitle: "",
          projectDisplayTitle: "",
          narratorProfileText: "",
          lastStoryHint: "",
          hasLastStoryHint: false,
          launchVariant: "none",
          hasProject: false,
          selectedMode: "guided_interview",
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
          shareLikeNotice: null,
        });
        await this.loadDemoStory(loadSequence);
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

  async refreshShareLikeNotice(userId: string, projectId: string) {
    const requestSequence = ++this.shareLikeNoticeRequestSequence;
    if (!this.homeVisible || !userId || !projectId || this.data.needsLogin) return;
    try {
      const notice = await loadShareLikeNotice(userId, projectId);
      if (requestSequence !== this.shareLikeNoticeRequestSequence || this.data.userId !== userId || this.data.projectId !== projectId) return;
      setHomeData(this, { shareLikeNotice: notice });
    } catch (error) {
      if (requestSequence === this.shareLikeNoticeRequestSequence) {
        console.warn("share like notice refresh failed", error);
      }
    }
  },

  // Opens the same settings page as a hand-picked clip, with the recommended range already selected.
  async openShareCandidateEditor() {
    const candidate = this.data.shareCandidate;
    if (!candidate || candidate.status !== "ready" || !candidate.utterance_id
      || this.data.voiceEntry || this.data.shareCandidateDismissing) return;
    // A click consumes the suggestion even if the author leaves without making a video.
    if (!await this.dismissShareCandidate() || !this.homeVisible
      || this.data.projectId !== candidate.project_id || this.data.voiceEntry) return;
    wx.navigateTo({ url: pageUrl("/pages/transcript-media/index", {
      id: candidate.utterance_id,
      candidateId: candidate.id,
    }) });
    trackAuditAction("home.share_video_candidate_open", {}, {
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

  pickShareVideoClip() {
    if (!this.data.projectId || this.data.voiceEntry || this.data.shareCandidateDismissing) return;
    navigateToVideoClipPicker();
  },

  openShareCard() {
    if (!this.data.projectId) {
      this.canStartVoiceChat();
      return;
    }
    // Recommendations use their own catchtap buttons; the row always keeps the library/picker reachable.
    if (this.data.shareCandidate?.status === "accepted" || this.data.shareVideoTotal
      || this.data.shareVideoActiveCount || this.data.shareVideoFailedCount) {
      this.openShareVideoLibrary();
    } else {
      this.pickShareVideoClip();
    }
  },

  openShareVideoLibrary() {
    const projectId = this.data.projectId;
    if (!projectId || this.data.voiceEntry) return;
    wx.navigateTo({ url: pageUrl("/pages/share-videos/index", { projectId }) });
  },

  async dismissShareCandidate() {
    const candidate = this.data.shareCandidate;
    const projectId = this.data.projectId;
    if (!candidate || this.data.voiceEntry || this.data.shareCandidateDismissing) return false;
    this.shareCandidateRequestSequence += 1;
    this.setData({ shareCandidateDismissing: true, shareCandidateError: "" });
    try {
      await dismissMemoirShareVideoCandidate(projectId, candidate.id);
      if (this.homeVisible && this.data.projectId === projectId && this.data.shareCandidate?.id === candidate.id) {
        this.shareCandidateRequestSequence += 1;
        this.setData({ shareCandidate: null, shareCandidateDurationText: "", shareCandidateJob: null, shareCandidateThumbnailPath: "" });
        void this.refreshShareVideos(projectId);
      }
      return true;
    } catch (error) {
      if (this.homeVisible && this.data.projectId === projectId) {
        this.setData({ shareCandidateError: error instanceof Error ? error.message : "暂时无法关闭提示" });
        wx.showToast({ title: this.data.shareCandidateError, icon: "none" });
      }
      return false;
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

  async continueNarration() {
    await this.startPrimaryVoiceChat();
  },

  openSettings() {
    navigateToMe();
  },

  openStoryProgress() {
    wx.pageScrollTo({ selector: "#home-memoir", duration: 250 });
  },

  async refreshStory() {
    await this.loadHome();
  },

  async dismissNotice() {
    const notice = this.data.notice;
    const { userId, projectId } = this.data;
    if (!notice || !projectId || this.data.dismissingGrowth) return;
    if (notice.kind === "progress" || notice.kind === "readable") {
      const noticeReadState = { ...this.data.noticeReadState,
        ...(notice.kind === "readable" ? { readableSeen: true } : { attentionId: notice.id }),
      };
      rememberHomeNoticeReadState(userId, projectId, noticeReadState);
      setHomeData(this, { noticeReadState });
    } else if (notice.kind === "like") {
      // Invalidate a like refresh started before this acknowledgement.
      this.shareLikeNoticeRequestSequence += 1;
      markShareLikeNoticeSeen(userId, projectId, notice.people.latestAt);
      setHomeData(this, { shareLikeNotice: null });
    } else {
      this.setData({ dismissingGrowth: true });
      try {
        await acknowledgeMemoirAutomationEvent(projectId, notice.eventId);
        if (this.data.userId === userId && this.data.projectId === projectId) {
          this.growthTracker.acknowledge(notice.eventId);
          setHomeData(this, {
            storyError: "",
            growthNotices: this.data.growthNotices.filter(item => item.id !== notice.id),
          });
        }
      } catch (error) {
        if (this.data.userId === userId && this.data.projectId === projectId) {
          this.setData({ storyError: error instanceof Error ? error.message : "提醒确认失败，请重试" });
        }
      } finally {
        if (this.data.userId === userId && this.data.projectId === projectId) this.setData({ dismissingGrowth: false });
      }
    }
  },

  async retryDraft() {
    const projectId = this.data.projectId;
    if (!projectId || this.data.retryingDraft || !this.data.progress?.can_retry) return;
    this.setData({ retryingDraft: true, storyError: "" });
    try {
      const progress = await retryMemoirDraftProgress(projectId);
      if (this.homeVisible && projectId === this.data.projectId) {
        setHomeData(this, { progress });
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
    if (this.data.needsLogin || !this.data.hasProject) {
      wx.navigateTo({ url: pageUrl("/pages/book-reader/index", { demo: "1" }) });
      return;
    }
    if (!this.data.projectId) return;
    markHomeBookOpened(this.data.userId);
    if (this.data.showBookHint) setHomeData(this, { showBookHint: false });
    navigateToBook();
  },

  openTranscripts() {
    if (!this.canStartVoiceChat()) return;
    navigateToTranscripts();
  },

  openVoiceEdit() {
    if (!this.data.projectId) {
      this.canStartVoiceChat();
      return;
    }
    if (this.data.voiceEntry) {
      wx.showToast({ title: "请先结束当前讲述", icon: "none" });
      return;
    }
    wx.navigateTo({ url: pageUrl("/pages/story-voice-edit/index", { projectId: this.data.projectId, projectTitle: this.data.projectTitle }) });
  },

  /** Visitors browse a sample memoir first and log in only when they start their own. */
  async loadDemoStory(loadSequence: number) {
    try {
      const demo = await getMemoirDemoStory();
      if (loadSequence !== this.homeLoadSequence || !this.data.needsLogin) return;
      if (!demo.chapters.length) {
        setHomeData(this, { storyError: "示例回忆录暂未开放，请稍后再来。" });
        return;
      }
      setHomeData(this, {
        demoReadableChapterIds: demo.chapters.filter(chapter => chapter.readable).map(chapter => chapter.id),
        story: storyViewData({ ...demo, identity: { active_project: { title: demo.title } } }),
        storyLoaded: true,
      });
    } catch (error) {
      if (loadSequence !== this.homeLoadSequence || !this.data.needsLogin) return;
      setHomeData(this, { storyError: "示例回忆录暂时无法加载，请重新读取故事。" });
      console.warn("load demo memoir failed", error);
    }
  },

  openChapter(event: StoryTreeChapterEvent) {
    const { chapterId } = event.detail;
    if (this.data.needsLogin) {
      if (this.data.demoReadableChapterIds.includes(chapterId)) {
        wx.navigateTo({ url: pageUrl("/pages/book-reader/index", { demo: "1" }) });
      } else {
        wx.showToast({ title: "示例只开放了部分章节", icon: "none" });
      }
      return;
    }
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
    let agreedEntry: HomeVoiceEntry | null = null;
    try {
      await syncVoiceAuthorizationConsent({ consent });
      if (userId !== this.data.userId || projectId !== this.data.projectId) return;
      agreedEntry = agreed ? this.takePendingNarration() : null;
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
    if (agreedEntry) await this.startSelectedNarration(agreedEntry);
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

  openNarratorProfile() {
    wx.navigateTo({ url: pageUrl("/pages/project-edit/index", { projectId: this.data.projectId }) });
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
    const entry = this.data.launchVariant === "none" ? "home_resume" : "home_launch";
    await this.startSelectedNarration(entry);
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
    if (!this.canStartVoiceChat()) return;
    this.openReviewIssues();
  },

  ensureVoiceAuthorization(entry: HomeVoiceEntry) {
    if (this.data.voiceAuthorizationAgreed) {
      return true;
    }
    this.pendingNarrationEntry = entry;
    setHomeData(this, { voiceAuthorizationRequested: true, error: "" }, () => {
      wx.pageScrollTo({ selector: "#home-voice-authorization", duration: 250 });
    });
    return false;
  },

  voiceStartBlocked() {
    return this.data.loading || this.data.creatingVoice || this.data.voiceAuthorizationSaving || Boolean(this.data.voiceEntry);
  },

  canStartVoiceChat() {
    if (this.voiceStartBlocked()) {
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
    return true;
  },

  async createFirstProject() {
    const project = await createMemoirProject({ title: FIRST_PROJECT_TITLE });
    trackAuditAction("project.create", { source: "home_first_story" }, { projectId: project.id });
    await this.loadHome(true);
    if (this.data.projectId !== project.id) throw new Error("回忆录已经建好，请再点一次开始讲述");
  },

  async startVoiceChatWithMode(
    mode: MemoirGuidanceMode,
    entry: HomeVoiceEntry,
    interviewType: MemoirInterviewType = "ai_interview",
    sessionGoal: MemoirSessionGoal = "open_narration",
  ) {
    if (this.voiceStartBlocked()) {
      return;
    }
    if (this.data.needsLogin) {
      navigateToLoginThenNarrate();
      return;
    }
    if (!this.ensureVoiceAuthorization(entry)) {
      return;
    }
    const { userId, projectId } = this.data;
    setHomeData(this, { creatingVoice: true, error: "" });
    try {
      await syncVoiceAuthorizationConsent();
      if (!this.homeVisible || userId !== this.data.userId || projectId !== this.data.projectId) return;
      if (!projectId) await this.createFirstProject();
      if (!this.homeVisible || userId !== this.data.userId) return;
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
      guidance_mode: target.entry.mode || "guided_interview", interview_type: target.entry.interviewType || "ai_interview",
      session_goal: target.entry.sessionGoal || "open_narration",
    }), showContinueNarration: false }, () => {
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
