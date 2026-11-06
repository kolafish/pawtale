import { INITIAL_READING_SHARE_DATA, readingShareMethods, type ReadingShareData } from "../../services/reading-share";
import type {
  MemoirBookChapterDraft,
  MemoirBookOutput,
  MemoirBookPlan,
  MemoirBookPlanStructureCommandRequest,
} from "../../domain/memoir-book";
import type { MemoirBookSnapshot, MemoirDraftProgress } from "../../domain/memoir-snapshots";
import { definePage, pageRuntime } from "../../services/page-definition";
import { accessibilityPageClass } from "../../services/accessibility-settings";
import type { BookChapterSources, BookChapterView, BookHomeData } from "./book-view-model";

import {
  applyMemoirBookPlanStructureCommand,
  memoirAudioPlanLoader,
  getMemoirBookChapterDraft,
  getMemoirBookOutput,
} from "../../services/memoir-book-api";
import { getMemoirBookSnapshot, retryMemoirDraftProgress } from "../../services/memoir-snapshot-api";
import { ApiError, optionalApiResource } from "../../services/request";
import {
  isAuthError,
  navigateToTranscripts,
  navigateToLogin,
  routeAuthError,
  returnToHome,
} from "../../services/navigation";
import {
  datasetChoice,
  datasetString,
  pageUrl,
  type PageDatasetEvent,
} from "../../services/page-input";
import { readBookTTSRequest } from "../../services/tts-settings";
import { timestampMs } from "../../services/time";
import { AudioPlaybackController } from "../../services/audio-playback";
import {
  VoiceAssistantTTSProgressRuntime,
  type VoiceAssistantTTSProgressPatch,
} from "../../services/voice-assistant-tts-progress";
import { writeTranscriptNavigationTarget } from "../../services/transcript-navigation";
import { writeMemoirIdentitySnapshot } from "../../stores/memoir-cache";
import { hideMiniProgramShareMenu } from "../../services/share";
import {
  applyBookTTSProgressPatch as applyBookTTSPlaybackProgressPatch,
  playBookAudioPlan,
  stopBookPlayback,
  stopBookTTSProgressTimer as stopBookTTSPlaybackProgressTimer,
} from "./book-tts-player";
import {
  CHAPTER_MOVE_DIRECTIONS,
  bookChapterDetailUrl,
  bookExportPageUrl,
  chapterMoveCommand,
  findBookChapter,
} from "./book-page-plan-helpers";
import {
  bookChapterInclusionData,
  bookHomeOutputData,
  buildChapterViews,
  chapterCollectionData,
  guestBookData,
  planStage,
  stageText,
  bookOutlineNarrationText,
} from "./book-view-model";
import {
  readCachedMemoirBook,
  readCachedMemoirIdentity,
  writeCachedMemoirBook,
} from "../../stores/memoir-read-cache";
export {
  bookChapterDetailUrl,
  bookExportPageUrl,
} from "./book-page-plan-helpers";
export {
  bookChapterSourceSummary,
  bookOutlineNarrationText,
  bookProgressText,
  buildChapterViews,
} from "./book-view-model";

export function bookCompletionPageUrl(projectId: string, projectTitle: string): string {
  return pageUrl("/pages/book-completion/index", {
    projectId,
    projectTitle,
  });
}

export function bookReaderPageUrl(projectId: string): string {
  return pageUrl("/pages/book-reader/index", { projectId });
}

interface BookDataset {
  id?: string;
  action?: string;
  direction?: string;
  utteranceId?: string;
}

type DatasetEvent = PageDatasetEvent<BookDataset>;

type BookPageData = BookHomeData & ReadingShareData & {
  pageClass: string;
  userId: string;
  loadError: string;
  showingCachedBook: boolean;
  bookActivityNotice: string;
  bookPendingMaterialCount: number;
  bookCharCountText: string;
  planStructureBusyChapterId: string;
  planStructureBusyDirection: string;
};

definePage<BookPageData>()({
  audioPlayback: pageRuntime(() => new AudioPlaybackController()),
  ttsProgress: pageRuntime(() => new VoiceAssistantTTSProgressRuntime()),
  playToken: 0,
  bookLoadSequence: 0,
  bookPlan: null as MemoirBookPlan | null,
  bookChapterSources: {} as BookChapterSources,
  bookDraftsByChapterId: {} as Record<string, MemoirBookChapterDraft | null>,
  bookOutput: null as MemoirBookOutput | null,
  bookDraftProgress: null as MemoirDraftProgress | null,
  bookShareToken: "",
  bookShareStatusSequence: 0,

  data: {
    pageClass: "book-page",
    userId: "",
    showingCachedBook: false,
    ...INITIAL_READING_SHARE_DATA,
    bookActivityNotice: "",
    bookPendingMaterialCount: 0,
    bookCharCountText: "0",
    loading: true,
    loadError: "",
    needsLogin: false,
    retryingDraft: false,
    planNeedsActivation: false,
    updatingPlanStructure: false,
    planStructureBusyChapterId: "",
    planStructureBusyDirection: "",
    error: "",
    projectId: "",
    projectTitle: "我的回忆录",
    planTitle: "",
    stage: "empty",
    stageText: "等待生成章节",
    chapters: [],
    chapterStructureEditing: false,
    splitSourceSelections: {},
    readyDraftCount: 0,
    totalChapterCount: 0,
    progressText: "还没有章节",
    titleBusyTarget: "",
    bookContentAvailable: false,
    bookCharCount: 0,
    blockingIssueCount: 0,
    bookIncludedChapterCount: 0,
    bookPreviewTitle: "",
    bookStaleNotice: "",
    ttsBusyTarget: "",
    playingTarget: "",
    bookTTSActiveTarget: "",
    bookTTSActiveText: "",
    bookTTSSpokenText: "",
    bookTTSCurrentText: "",
    bookTTSRemainingText: "",
    bookTTSProgressPercent: 0,
    bookTTSDurationSeconds: 0,
    bookTTSAlignment: [],
  },

  async onShow() {
    this.setData({ pageClass: accessibilityPageClass("book-page") });
    hideMiniProgramShareMenu();
    await this.loadBookHome();
    if (this.readingShareTarget().available) await this.refreshBookShareStatus();
  },

  ...readingShareMethods,

  readingShareTarget() {
    return {
      projectId: this.data.projectId,
      title: this.data.bookPreviewTitle,
      available: this.data.bookContentAvailable,
      stale: Boolean(this.data.bookStaleNotice),
    };
  },


  async loadBookHome() {
    const loadSequence = ++this.bookLoadSequence;
    const initialIdentity = readCachedMemoirIdentity();
    const cached = readCachedMemoirBook(initialIdentity);
    const localInvalidationRevision = cached.localInvalidationRevision || 0;
    const showedCachedData = Boolean(cached.payload);
    if (cached.payload) {
      this.applyBookSnapshot(cached.payload);
    } else {
      this.setData({ loading: true, loadError: "", error: "", showingCachedBook: false });
    }
    try {
      const snapshot = await getMemoirBookSnapshot();
      if (loadSequence !== this.bookLoadSequence) {
        return;
      }
      const identity = snapshot.identity;
      const currentCache = readCachedMemoirBook(identity);
      if (
        initialIdentity && initialIdentity.id === identity.id
        && (initialIdentity.storage_owner_id || "") === (identity.storage_owner_id || "")
        && initialIdentity.active_project_id === identity.active_project_id
        && currentCache.status !== "miss"
        && (currentCache.localInvalidationRevision || 0) !== localInvalidationRevision
      ) {
        if (!showedCachedData && currentCache.payload) {
          this.applyBookSnapshot(currentCache.payload);
          this.setData({ showingCachedBook: currentCache.status === "stale" });
        }
        return;
      }
      writeMemoirIdentitySnapshot(identity);
      writeCachedMemoirBook(snapshot);
      this.applyBookSnapshot(snapshot);
      this.setData({ showingCachedBook: false });
    } catch (error) {
      if (loadSequence !== this.bookLoadSequence) {
        return;
      }
      if (isAuthError(error)) {
        this.bookPlan = null;
        this.bookChapterSources = {};
        this.bookDraftsByChapterId = {};
        this.bookOutput = null;
        this.bookDraftProgress = null;
        this.bookShareToken = "";
        this.setData(guestBookData());
        this.setData({
          userId: "",
          showingCachedBook: false,
          bookActivityNotice: "",
          bookPendingMaterialCount: 0,
          bookShareLikeCount: 0,
          bookShareLikers: [],
          bookShareActive: false,
          bookShareBusy: false,
          bookShareImageUrl: "",
          bookShareReady: false,
          bookShareStale: false,
          bookShareStatusKnown: false,
          bookShareDescription: INITIAL_READING_SHARE_DATA.bookShareDescription,
        });
        return;
      }
      if (showedCachedData) {
        this.setData({ showingCachedBook: true });
        console.warn("refresh memoir book snapshot failed", error);
      } else {
        this.setData({ loadError: error instanceof Error && error.message ? error.message : "请稍后重试。" });
      }
    } finally {
      if (loadSequence === this.bookLoadSequence) {
        this.setData({ loading: false });
      }
    }
  },

  applyBookSnapshot(snapshot: MemoirBookSnapshot) {
    this.bookDraftProgress = snapshot.progress || null;
    const identity = snapshot.identity;
    const projectId = identity.active_project_id;
    const projectTitle = identity.active_project?.title || "尚未创建回忆录";
    if (projectId !== this.data.projectId || identity.id !== this.data.userId) {
      this.bookShareToken = "";
      this.setData({
        bookShareLikeCount: 0,
        bookShareLikers: [],
        bookShareActive: false,
        bookShareBusy: false,
        bookShareImageUrl: "",
        bookShareReady: false,
        bookShareStale: false,
        bookShareStatusKnown: false,
        bookShareDescription: INITIAL_READING_SHARE_DATA.bookShareDescription,
      });
    }
    this.setData({
      loading: false,
      loadError: "",
      error: "",
      needsLogin: false,
      projectId,
      projectTitle,
      userId: identity.id,
      blockingIssueCount: snapshot.blocking_issue_count || 0,
    });
    this.applyPlan(snapshot.plan || null, snapshot.drafts_by_chapter_id || {});
    this.applyBookOutput(snapshot.output || null, snapshot.progress || null);
    const events = snapshot.automation_events.items || [];
    const progress = snapshot.progress;
    const bookPendingMaterialCount = progress?.status === "partial_ready" || progress?.status === "no_change"
      ? progress.pending_material_count || 0 : 0;
    this.setData({
      bookPendingMaterialCount,
      bookActivityNotice: bookPendingMaterialCount
        ? `还有 ${bookPendingMaterialCount} 段讲述待整理`
        : progress && progress.status !== "idle" && progress.status !== "ready"
          ? progress.title || progress.detail : "",
      chapters: this.data.chapters.map((chapter) => {
        const latest = events.filter((event) => (
          event.chapter_id === chapter.id && event.event_type === "chapter_draft_generated"
        ))
          .sort((a, b) => timestampMs(b.updated_at) - timestampMs(a.updated_at))[0];
        const status = latest?.metadata_json?.acknowledged_at ? undefined : latest?.status;
        return {
          ...chapter,
          chapterActivityText: status === "pending"
            ? "正在整理"
            : status === "failed"
              ? `整理失败${chapter.draftAvailable ? '，当前正文仍可阅读' : '，进入本章查看原因'}`
              : "",
        };
      }),
    });
  },

  async loadDraftsForPlan(plan: MemoirBookPlan) {
    const entries = await Promise.all(
      plan.chapters.map(async (chapter) => {
        try {
          const draft = await getMemoirBookChapterDraft(plan.project_id, chapter.id);
          return [chapter.id, draft] as const;
        } catch (error) {
          if (error instanceof ApiError && error.statusCode === 404) {
            return [chapter.id, null] as const;
          }
          throw error;
        }
      }),
    );
    return entries.reduce<Record<string, MemoirBookChapterDraft | null>>((memo, [chapterId, draft]) => {
      memo[chapterId] = draft;
      return memo;
    }, {});
  },

  applyPlan(plan: MemoirBookPlan | null, draftsByChapterId: Record<string, MemoirBookChapterDraft | null> = {}) {
    this.bookPlan = plan;
    this.setData({ planNeedsActivation: plan?.status === "draft" });
    this.bookChapterSources = Object.fromEntries(
      (plan?.chapters || []).map((chapter) => [chapter.id, chapter.source_refs]),
    );
    this.bookDraftsByChapterId = draftsByChapterId;
    const chapters = plan
      ? buildChapterViews(plan, draftsByChapterId, this.data.splitSourceSelections)
      : [];
    const stage = planStage(plan);
    if (stage !== "drafting") {
      this.bookOutput = null;
    }
    this.setData({
      planTitle: plan?.title || `${this.data.projectTitle || "我的回忆录"}章节规划`,
      stage,
      stageText: stageText(stage),
      chapterStructureEditing: stage === "drafting" ? this.data.chapterStructureEditing : false,
      ...chapterCollectionData(chapters, this.bookChapterSources, this.data.splitSourceSelections),
      ...(stage === "drafting" ? {} : bookHomeOutputData(null)),
    });
  },

  applyBookOutput(output: MemoirBookOutput | null, progress: MemoirDraftProgress | null = null) {
    const previousVersionId = this.bookOutput?.latest_version?.id;
    const previousStale = Boolean(this.data.bookStaleNotice);
    const bookData = bookHomeOutputData(output, progress);
    const shareStateChanged = previousVersionId !== output?.latest_version?.id
      || previousStale !== Boolean(bookData.bookStaleNotice);
    this.bookOutput = output;
    this.setData({
      ...bookData,
      bookCharCountText: bookData.bookCharCount.toLocaleString("en-US"),
      chapters: bookChapterInclusionData(this.data.chapters, output, progress),
      ...(shareStateChanged ? {
        bookShareReady: false,
        bookShareStale: false,
        bookShareStatusKnown: false,
      } : {}),
    });
    if (shareStateChanged && this.data.projectId) {
      void this.refreshBookShareStatus();
    }
  },

  async applyUpdatedPlan(plan: MemoirBookPlan, toastTitle: string) {
    const [draftsByChapterId, output] = plan.status === "confirmed"
      ? await Promise.all([
        this.loadDraftsForPlan(plan),
        optionalApiResource(getMemoirBookOutput(plan.project_id)),
      ])
      : [
        {},
        null,
      ] as const;
    this.applyPlan(plan, draftsByChapterId);
    this.applyBookOutput(output);
    wx.showToast({ title: toastTitle, icon: "success" });
  },

  async continueBookDraft() {
    if (!this.data.projectId || this.data.retryingDraft) return;
    this.setData({ retryingDraft: true, error: "" });
    try {
      await retryMemoirDraftProgress(this.data.projectId);
      await this.loadBookHome();
      wx.showToast({ title: "已开始后台整理", icon: "success" });
    } catch (error) {
      if (!routeAuthError(error, "login")) {
        this.setData({ error: error instanceof Error ? error.message : "暂时无法继续整理" });
      }
    } finally {
      this.setData({ retryingDraft: false });
    }
  },

  openChapterEditor(event: DatasetEvent) {
    const chapterId = datasetString(event, "id");
    const chapter = findBookChapter(this.data.chapters, chapterId);
    if (!chapterId || !this.data.projectId) {
      this.setData({ error: "缺少章节信息" });
      return;
    }
    if (this.data.updatingPlanStructure || chapter?.draftBusy) {
      return;
    }
    this.stopPlayback();
    wx.navigateTo({ url: bookChapterDetailUrl(this.data.projectId, chapterId) });
  },

  toggleChapterStructureEditing() {
    if (this.data.stage !== "drafting" || !this.bookPlan || this.data.updatingPlanStructure) {
      return;
    }
    this.stopPlayback();
    this.setData({
      chapterStructureEditing: !this.data.chapterStructureEditing,
      error: "",
    });
  },

  openBookExportPage(event: DatasetEvent) {
    if (!this.data.projectId) {
      return;
    }
    const action = datasetChoice(event, "action", ["files", "settings"] as const, "files");
    if (action !== "settings" && !this.data.bookContentAvailable) return;
    wx.navigateTo({ url: bookExportPageUrl(this.data.projectId, action) });
  },

  openBookReader() {
    if (!this.data.projectId || !this.data.bookContentAvailable) {
      return;
    }
    this.stopPlayback();
    wx.navigateTo({ url: bookReaderPageUrl(this.data.projectId) });
  },

  async openBookActivity() {
    const projectId = this.data.projectId;
    if (!projectId || this.data.retryingDraft) return;
    const progress = this.bookDraftProgress;
    if (!this.data.bookPendingMaterialCount || !progress) {
      this.openBookCompletion();
      return;
    }
    this.stopPlayback();
    const confirmed = await new Promise<boolean>(resolve => wx.showModal({
      title: this.data.bookActivityNotice,
      content: progress.detail,
      confirmText: progress.can_retry ? "继续整理" : "知道了",
      showCancel: progress.can_retry,
      success: result => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    }));
    if (confirmed && progress.can_retry && projectId === this.data.projectId
      && this.bookDraftProgress?.can_retry) {
      await this.continueBookDraft();
    }
  },

  openBookCompletion() {
    if (!this.data.projectId || this.data.stage !== "drafting") {
      return;
    }
    this.stopPlayback();
    wx.navigateTo({
      url: bookCompletionPageUrl(
        this.data.projectId,
        this.data.projectTitle,
      ),
    });
  },

  async moveChapter(event: DatasetEvent) {
    const chapterId = datasetString(event, "id");
    const direction = datasetChoice(event, "direction", CHAPTER_MOVE_DIRECTIONS, "down");
    if (!chapterId || this.data.updatingPlanStructure) {
      return;
    }
    const command = chapterMoveCommand(this.data.chapters, chapterId, direction);
    if (!command) {
      return;
    }
    const previousChapters = this.data.chapters;
    const fromIndex = previousChapters.findIndex((chapter) => chapter.id === chapterId);
    const toIndex = direction === "up" ? fromIndex - 1 : fromIndex + 1;
    const chapters = [...previousChapters];
    [chapters[fromIndex], chapters[toIndex]] = [chapters[toIndex], chapters[fromIndex]];
    this.setData(chapterCollectionData(chapters, this.bookChapterSources, this.data.splitSourceSelections));
    const plan = await this.applyPlanStructureCommand(command, "章节顺序已调整");
    if (!plan) {
      this.setData(chapterCollectionData(previousChapters, this.bookChapterSources, this.data.splitSourceSelections));
    }
  },

  async applyPlanStructureCommand(command: MemoirBookPlanStructureCommandRequest, toastTitle: string) {
    if (!this.data.projectId || !this.bookPlan || this.data.updatingPlanStructure) {
      return null;
    }
    this.stopPlayback();
    const movingChapter = command.type === "move_chapter";
    this.setData({
      updatingPlanStructure: true,
      planStructureBusyChapterId: movingChapter ? command.chapter_id || "" : "",
      planStructureBusyDirection: movingChapter ? command.direction || "" : "",
      error: "",
    });
    try {
      const plan = await applyMemoirBookPlanStructureCommand(this.data.projectId, command);
      if (movingChapter) {
        const previousSortOrderByChapterId = new Map(
          this.data.chapters.map((chapter) => [chapter.id, chapter.sort_order]),
        );
        const movedChapterTitles = plan.chapters
          .filter((chapter) => previousSortOrderByChapterId.get(chapter.id) !== chapter.sort_order)
          .map((chapter) => chapter.title);
        this.applyPlan(plan, this.bookDraftsByChapterId);
        if (this.bookOutput?.latest_version && movedChapterTitles.length) {
          this.applyBookOutput({
            ...this.bookOutput,
            is_stale: true,
            stale_chapter_titles: Array.from(new Set([
              ...(this.bookOutput.stale_chapter_titles || []),
              ...movedChapterTitles,
            ])),
          });
        }
        wx.showToast({ title: toastTitle, icon: "success" });
      } else {
        await this.applyUpdatedPlan(plan, toastTitle);
      }
      return plan;
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return null;
      }
      this.setData({ error: error instanceof Error ? error.message : "目录调整失败" });
      return null;
    } finally {
      this.setData({
        updatingPlanStructure: false,
        planStructureBusyChapterId: "",
        planStructureBusyDirection: "",
      });
    }
  },

  async playBookOutlineAudio() {
    const target = "outline";
    if (this.data.playingTarget === target || this.data.ttsBusyTarget === target) {
      this.stopPlayback();
      return;
    }
    if (!this.bookPlan || !this.data.chapters.length) {
      wx.showToast({ title: "先生成章节规划", icon: "none" });
      return;
    }
    if (
      !this.data.projectId
      || this.data.ttsBusyTarget
      || this.data.updatingPlanStructure
    ) {
      return;
    }
    this.stopPlayback();
    const token = this.audioPlayback.nextToken();
    this.playToken = token;
    const activeText = bookOutlineNarrationText(this.bookPlan.title, this.data.chapters);
    this.setData({
      ttsBusyTarget: target,
      error: "",
    });
    try {
      const prepare = memoirAudioPlanLoader(this.data.projectId, { kind: "outline" }, readBookTTSRequest());
      const plan = await prepare();
      if (token !== this.playToken) {
        return;
      }
      this.setData({ ttsBusyTarget: "" });
      await playBookAudioPlan(this, plan, token, activeText, prepare, target);
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      if (token === this.playToken) {
        this.setData({
          error: error instanceof Error ? error.message : "目录朗读失败",
        });
      }
    } finally {
      if (token === this.playToken && this.data.ttsBusyTarget === target) {
        this.setData({ ttsBusyTarget: "" });
      }
    }
  },

  applyBookTTSProgressPatch(patch: VoiceAssistantTTSProgressPatch) {
    applyBookTTSPlaybackProgressPatch(this, patch);
  },

  stopBookTTSProgressTimer() {
    stopBookTTSPlaybackProgressTimer(this);
  },

  stopPlayback() {
    stopBookPlayback(this);
  },

  onHide() {
    this.bookLoadSequence++;
    this.stopPlayback();
  },

  onUnload() {
    this.bookLoadSequence++;
    this.stopPlayback();
  },

  openTranscripts(event?: DatasetEvent) {
    const utteranceId = event?.currentTarget?.dataset?.id || "";
    if (utteranceId) {
      writeTranscriptNavigationTarget(utteranceId);
    }
    navigateToTranscripts();
  },

  openHome() {
    this.stopPlayback();
    returnToHome();
  },

  openLogin() {
    this.stopPlayback();
    navigateToLogin();
  },

  createProject() {
    this.stopPlayback();
    wx.navigateTo({ url: "/pages/project-edit/index" });
  },

  patchChapter(chapterId: string, patch: Partial<BookChapterView>) {
    if (!chapterId) {
      return;
    }
    const chapters = this.data.chapters.map((chapter) => (
      chapter.id === chapterId ? { ...chapter, ...patch } : chapter
    ));
    this.setData({
      ...chapterCollectionData(
        chapters,
        this.bookChapterSources,
        this.data.splitSourceSelections,
      ),
    });
  },

});
