import type {
  MemoirBookManuscriptChapter,
  MemoirBookOutputSection,
} from "../../domain/memoir-book";
import {
  accessibilityPageClass,
  accessibilityPagePatch,
} from "../../services/accessibility-settings";
import { definePage } from "../../services/page-definition";
import {
  applyMemoirBookRevisionProposal,
  dismissMemoirBookRevisionProposal,
  enqueueMemoirBookRevisionDraft,
  enqueueMemoirBookRevisionFeedback,
  getMemoirBookRevisionDraftJob,
  getMemoirBookRevisionFeedbackJob,
  getMemoirBookRevisionProposal,
  memoirBookRevisionDictationUrl,
  type MemoirBookRevisionProposal,
  type MemoirBookRevisionPreviewVersion,
  type MemoirBookRevisionSectionComparison,
} from "../../services/memoir-book-completion-api";
import { routeAuthError } from "../../services/navigation";
import {
  datasetString,
  pageUrl,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
  type PageTextInputEvent,
} from "../../services/page-input";
import { PollingTimer } from "../../services/polling-timer";
import { StreamingVoiceController } from "../../services/streaming-voice-controller";
import {
  readVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "../../services/voice-authorization";
import { ensureRecorderPrivacyAuthorization } from "../../services/wechat-privacy";


type RevisionMode = "changes" | "whole";
type PreviewVersion = "before" | "after";
type FeedbackVoicePhase = "idle" | "connecting" | "listening" | "stopping";
const FEEDBACK_POLL_INTERVAL_MS = 1500;
const FEEDBACK_VOICE_MAX_DURATION_MS = 30000;
const FEEDBACK_TEXT_MAX_LENGTH = 1000;
const PAGE_CLASS = "revision-page ui-page-shell ui-accessible-page";

interface RevisionSectionView extends MemoirBookRevisionSectionComparison {
  display_content: string;
}

interface RevisionChapterView {
  chapter_id: string;
  title: string;
  sections: RevisionSectionView[];
}

interface WholeChapterView {
  chapter_id: string;
  index_text: string;
  title: string;
  sections: MemoirBookOutputSection[];
  content: string;
}

interface RevisionProposalView extends Pick<
  MemoirBookRevisionProposal,
  | "status"
  | "phase"
  | "revision_plan"
  | "summary"
  | "can_apply"
  | "can_dismiss"
  | "updated_at"
> {
  comparison: Pick<
    MemoirBookRevisionProposal["comparison"],
    | "changed_chapter_count"
    | "changed_section_count"
    | "chapter_order_changed"
    | "before_chapter_order"
    | "after_chapter_order"
  >;
}

interface BookRevisionData {
  pageClass: string;
  projectId: string;
  proposalId: string;
  loading: boolean;
  busy: boolean;
  error: string;
  proposal: RevisionProposalView | null;
  mode: RevisionMode;
  previewVersion: PreviewVersion;
  changedChapters: RevisionChapterView[];
  wholeLoading: boolean;
  wholeChapters: WholeChapterView[];
  feedbackText: string;
  feedbackBusy: boolean;
  draftBusy: boolean;
  feedbackVoicePhase: FeedbackVoicePhase;
}

type RevisionDatasetEvent = PageDatasetEvent<{
  mode?: string;
  version?: string;
}>;

function wholeBookChapters(
  preview: MemoirBookRevisionPreviewVersion,
): WholeChapterView[] {
  const chapters = preview.manuscript?.chapters || [];
  return chapters.map((chapter: MemoirBookManuscriptChapter, index) => ({
    chapter_id: chapter.chapter_id,
    index_text: `第 ${index + 1} 章`,
    title: chapter.title,
    sections: chapter.sections || [],
    content: chapter.content,
  }));
}

export function revisionViewPatch(
  proposal: MemoirBookRevisionProposal,
  previewVersion: PreviewVersion = "after",
): Pick<
  BookRevisionData,
  "proposal" | "previewVersion" | "changedChapters"
> {
  return {
    proposal: {
      status: proposal.status,
      phase: proposal.phase,
      revision_plan: proposal.revision_plan,
      summary: proposal.summary,
      can_apply: proposal.can_apply,
      can_dismiss: proposal.can_dismiss,
      updated_at: proposal.updated_at,
      comparison: {
        changed_chapter_count: proposal.comparison.changed_chapter_count,
        changed_section_count: proposal.comparison.changed_section_count,
        chapter_order_changed: proposal.comparison.chapter_order_changed,
        before_chapter_order: proposal.comparison.before_chapter_order,
        after_chapter_order: proposal.comparison.after_chapter_order,
      },
    },
    previewVersion,
    changedChapters: proposal.comparison.chapters.map((chapter) => ({
      ...chapter,
      sections: chapter.sections.map((section) => ({
        ...section,
        display_content: previewVersion === "before"
          ? section.before_content
          : section.after_content,
      })),
    })),
  };
}

function confirmAction(title: string, content: string): Promise<boolean> {
  return new Promise((resolve) => {
    wx.showModal({
      title,
      content,
      confirmText: "确认",
      cancelText: "再看看",
      success: (result) => resolve(result.confirm),
      fail: () => resolve(false),
    });
  });
}

export function feedbackTextWithTranscript(
  existing: string,
  transcript: string,
): string {
  const base = existing.trim();
  const live = transcript.trim();
  return `${base}${base && live ? "\n" : ""}${live}`.slice(
    0,
    FEEDBACK_TEXT_MAX_LENGTH,
  );
}

definePage<BookRevisionData>()({
  feedbackPoller: new PollingTimer(FEEDBACK_POLL_INTERVAL_MS),
  draftPoller: new PollingTimer(FEEDBACK_POLL_INTERVAL_MS),
  feedbackVoiceStreaming: new StreamingVoiceController(),
  feedbackVoiceTimer: null as ReturnType<typeof setTimeout> | null,
  feedbackVoiceToken: 0,
  feedbackVoiceBaseText: "",
  feedbackVoiceTranscript: "",
  feedbackBaselineUpdatedAt: "",
  revisionProposal: null as MemoirBookRevisionProposal | null,
  wholeChapterCache: {} as Partial<Record<PreviewVersion, WholeChapterView[]>>,
  wholeLoadSequence: 0,
  pageActive: false,

  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    projectId: "",
    proposalId: "",
    loading: true,
    busy: false,
    error: "",
    proposal: null,
    mode: "changes",
    previewVersion: "after",
    changedChapters: [],
    wholeLoading: false,
    wholeChapters: [],
    feedbackText: "",
    feedbackBusy: false,
    draftBusy: false,
    feedbackVoicePhase: "idle",
  },

  async onLoad(query: PageQuery = {}) {
    const projectId = queryParam(query, "projectId");
    const proposalId = queryParam(query, "proposalId");
    this.setData({ projectId, proposalId });
    if (!projectId || !proposalId) {
      this.setData({ loading: false, error: "缺少修改预览信息" });
      return;
    }
    if (await this.loadProposal()) {
      await Promise.all([
        this.refreshFeedbackJob(),
        this.refreshDraftJob(),
      ]);
    }
  },

  onShow() {
    this.setData(accessibilityPagePatch(PAGE_CLASS));
    this.pageActive = true;
    if (
      !this.data.loading
      && this.data.proposal
      && this.data.projectId
      && this.data.proposalId
    ) {
      void this.refreshFeedbackJob();
      void this.refreshDraftJob();
    }
  },

  onHide() {
    this.pageActive = false;
    this.stopFeedbackPolling();
    this.stopDraftPolling();
    this.cancelFeedbackVoice();
  },

  onUnload() {
    this.pageActive = false;
    this.stopFeedbackPolling();
    this.stopDraftPolling();
    this.cancelFeedbackVoice();
  },

  async loadProposal(silent = false) {
    if (!silent) {
      this.setData({ loading: true, error: "" });
    }
    try {
      const proposal = await getMemoirBookRevisionProposal(
        this.data.projectId,
        this.data.proposalId,
      );
      this.revisionProposal = proposal;
      this.wholeChapterCache = {};
      this.wholeLoadSequence += 1;
      this.setData({
        loading: false,
        wholeLoading: false,
        wholeChapters: [],
        ...revisionViewPatch(proposal, this.data.previewVersion),
      });
      if (proposal.phase === "draft_ready" && this.data.mode === "whole") {
        await this.loadWholePreview(this.data.previewVersion);
      }
      return true;
    } catch (error) {
      if (routeAuthError(error)) {
        return false;
      }
      this.setData({
        loading: false,
        error: error instanceof Error ? error.message : "修改预览加载失败",
      });
      return false;
    }
  },

  async loadWholePreview(version: PreviewVersion) {
    const cached = this.wholeChapterCache[version];
    if (cached) {
      this.setData({ wholeChapters: cached, wholeLoading: false });
      return true;
    }
    const sequence = ++this.wholeLoadSequence;
    this.setData({ wholeLoading: true, wholeChapters: [], error: "" });
    try {
      const proposal = await getMemoirBookRevisionProposal(
        this.data.projectId,
        this.data.proposalId,
        version,
      );
      if (sequence !== this.wholeLoadSequence) {
        return false;
      }
      const preview = proposal[version];
      if (!preview.manuscript) {
        throw new Error("整书预览内容不可用");
      }
      const chapters = wholeBookChapters(preview);
      if (proposal.updated_at !== this.revisionProposal?.updated_at) {
        this.wholeChapterCache = {};
      }
      this.revisionProposal = proposal;
      this.wholeChapterCache[version] = chapters;
      this.setData({
        ...revisionViewPatch(proposal, version),
        wholeLoading: false,
        wholeChapters: chapters,
      });
      return true;
    } catch (error) {
      if (sequence !== this.wholeLoadSequence || routeAuthError(error)) {
        return false;
      }
      this.setData({
        wholeLoading: false,
        error: error instanceof Error ? error.message : "整书预览加载失败",
      });
      return false;
    }
  },

  stopFeedbackPolling() {
    this.feedbackPoller.cancel();
  },

  stopDraftPolling() {
    this.draftPoller.cancel();
  },

  scheduleFeedbackPoll() {
    if (!this.pageActive) {
      return;
    }
    this.feedbackPoller.schedule(() => {
      void this.refreshFeedbackJob();
    });
  },

  scheduleDraftPoll() {
    if (!this.pageActive) {
      return;
    }
    this.draftPoller.schedule(() => {
      void this.refreshDraftJob();
    });
  },

  async refreshFeedbackJob() {
    const wasActive = this.data.feedbackBusy;
    try {
      const job = await getMemoirBookRevisionFeedbackJob(
        this.data.projectId,
        this.data.proposalId,
      );
      const active = job.status === "pending" || job.status === "running";
      this.stopFeedbackPolling();
      this.setData({
        feedbackBusy: active,
        ...(job.status === "failed"
          ? { error: job.error_message || "按意见调整失败" }
          : {}),
      });
      if (active) {
        this.scheduleFeedbackPoll();
      } else if (job.status === "succeeded" && wasActive) {
        if (await this.loadProposal(true)) {
          const updated = (
            !this.feedbackBaselineUpdatedAt
            || this.data.proposal?.updated_at !== this.feedbackBaselineUpdatedAt
          );
          if (updated) {
            this.setData({ feedbackText: "", error: "" });
            wx.showToast({
              title: this.data.proposal?.revision_plan
                ? "修改思路已更新"
                : "修改预览已更新",
              icon: "success",
            });
          }
        }
      }
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "调整进度加载失败",
      });
      if (wasActive) {
        this.scheduleFeedbackPoll();
      }
    }
  },

  async refreshDraftJob() {
    const wasActive = this.data.draftBusy;
    try {
      const job = await getMemoirBookRevisionDraftJob(
        this.data.projectId,
        this.data.proposalId,
      );
      const active = job.status === "pending" || job.status === "running";
      this.stopDraftPolling();
      this.setData({
        draftBusy: active,
        ...(job.status === "failed"
          ? { error: job.error_message || "生成修改稿失败" }
          : {}),
      });
      if (active) {
        this.scheduleDraftPoll();
      } else if (job.status === "succeeded" && wasActive) {
        if (await this.loadProposal(true)) {
          this.setData({ error: "" });
          wx.showToast({ title: "修改稿已生成", icon: "success" });
        }
      }
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "修改稿进度加载失败",
      });
      if (wasActive) {
        this.scheduleDraftPoll();
      }
    }
  },

  changeFeedbackText(event: PageTextInputEvent) {
    if (
      this.data.feedbackBusy
      || this.data.draftBusy
      || this.data.feedbackVoicePhase !== "idle"
    ) {
      return;
    }
    this.setData({ feedbackText: event.detail.value });
  },

  cancelFeedbackVoice() {
    this.feedbackVoiceToken += 1;
    this.clearFeedbackVoiceTimer();
    this.feedbackVoiceStreaming.close({ force: true });
    this.feedbackVoiceBaseText = "";
    this.feedbackVoiceTranscript = "";
    this.setData({ feedbackVoicePhase: "idle" });
  },

  async toggleFeedbackVoice() {
    if (this.data.feedbackVoicePhase === "listening") {
      this.clearFeedbackVoiceTimer();
      this.feedbackVoiceStreaming.stopRecording();
      return;
    }
    if (
      this.data.busy
      || this.data.feedbackBusy
      || this.data.draftBusy
      || this.data.feedbackVoicePhase !== "idle"
    ) {
      return;
    }
    this.feedbackVoiceToken += 1;
    const token = this.feedbackVoiceToken;
    this.setData({ feedbackVoicePhase: "connecting", error: "" });
    try {
      if (!readVoiceAuthorizationConsent()) {
        wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
        this.setData({ feedbackVoicePhase: "idle" });
        return;
      }
      await syncVoiceAuthorizationConsent();
      await ensureRecorderPrivacyAuthorization();
      if (token !== this.feedbackVoiceToken) {
        return;
      }
      this.feedbackVoiceBaseText = this.data.feedbackText;
      this.feedbackVoiceTranscript = "";
      await this.feedbackVoiceStreaming.connect({
        voiceSessionId: this.data.proposalId,
        gatewayUrl: memoirBookRevisionDictationUrl(
          this.data.projectId,
          this.data.proposalId,
        ),
        guidanceMode: "free_narration",
        autoStopSilenceMs: 0,
        noVoiceActivityTimeoutMs: 0,
        callbacks: {
          onStatus: (status) => {
            if (token !== this.feedbackVoiceToken) {
              return;
            }
            if (status === "listening") {
              this.setData({ feedbackVoicePhase: "listening" });
            } else if (status === "stopping") {
              this.setData({ feedbackVoicePhase: "stopping" });
            } else if (status === "closed" || status === "error") {
              this.clearFeedbackVoiceTimer();
              this.setData({ feedbackVoicePhase: "idle" });
            }
          },
          onEvent: (event) => {
            if (
              token === this.feedbackVoiceToken
              && event.type === "audio.ended"
            ) {
              void this.finishFeedbackVoice(token);
            }
          },
          onPartial: (text) => {
            this.updateFeedbackVoiceTranscript(token, text);
          },
          onFinal: (event) => {
            this.updateFeedbackVoiceTranscript(token, event.text || "");
          },
          onError: (message) => {
            if (token !== this.feedbackVoiceToken) {
              return;
            }
            this.clearFeedbackVoiceTimer();
            this.setData({
              feedbackVoicePhase: "idle",
              error: message || "语音输入失败",
            });
          },
        },
      });
      if (token !== this.feedbackVoiceToken) {
        this.feedbackVoiceStreaming.close({ force: true });
        return;
      }
      if (!this.feedbackVoiceStreaming.startRecording()) {
        throw new Error("录音启动失败，请重试");
      }
      this.feedbackVoiceTimer = setTimeout(() => {
        if (token === this.feedbackVoiceToken) {
          this.feedbackVoiceTimer = null;
          this.feedbackVoiceStreaming.stopRecording();
        }
      }, FEEDBACK_VOICE_MAX_DURATION_MS);
    } catch (error) {
      if (token !== this.feedbackVoiceToken) {
        return;
      }
      this.feedbackVoiceStreaming.close({ force: true });
      this.setData({
        feedbackVoicePhase: "idle",
        error: error instanceof Error ? error.message : "语音输入失败",
      });
    }
  },

  updateFeedbackVoiceTranscript(token: number, transcript: string) {
    if (token !== this.feedbackVoiceToken) {
      return;
    }
    this.feedbackVoiceTranscript = transcript.trim();
    this.setData({
      feedbackText: feedbackTextWithTranscript(
        this.feedbackVoiceBaseText,
        this.feedbackVoiceTranscript,
      ),
    });
  },

  clearFeedbackVoiceTimer() {
    if (this.feedbackVoiceTimer) {
      clearTimeout(this.feedbackVoiceTimer);
      this.feedbackVoiceTimer = null;
    }
  },

  async finishFeedbackVoice(token: number) {
    this.clearFeedbackVoiceTimer();
    try {
      await this.feedbackVoiceStreaming.finishSession();
    } catch (error) {
      if (token === this.feedbackVoiceToken) {
        this.setData({
          error: error instanceof Error ? error.message : "语音输入结束失败",
        });
      }
    }
    if (token !== this.feedbackVoiceToken) {
      return;
    }
    const transcript = this.feedbackVoiceTranscript.trim();
    this.feedbackVoiceBaseText = "";
    this.feedbackVoiceTranscript = "";
    this.setData({
      feedbackVoicePhase: "idle",
      ...(transcript
        ? {}
        : { error: "没有识别到调整意见，请再说一次" }),
    });
    if (transcript) {
      wx.showToast({ title: "语音已转成文字", icon: "none" });
    }
  },

  async submitFeedback() {
    const feedback = this.data.feedbackText.trim();
    if (
      this.data.proposal?.status !== "ready"
      || !this.data.proposal.can_dismiss
      || this.data.busy
      || this.data.feedbackBusy
      || this.data.draftBusy
      || this.data.feedbackVoicePhase !== "idle"
    ) {
      return;
    }
    if (!feedback) {
      wx.showToast({ title: "请先输入或说出调整意见", icon: "none" });
      return;
    }
    this.feedbackBaselineUpdatedAt = this.data.proposal.updated_at;
    this.setData({ feedbackBusy: true, error: "" });
    try {
      const job = await enqueueMemoirBookRevisionFeedback(
        this.data.projectId,
        this.data.proposalId,
        feedback,
      );
      const active = job.status === "pending" || job.status === "running";
      this.setData({ feedbackBusy: active });
      if (active) {
        this.scheduleFeedbackPoll();
      } else if (job.status === "succeeded") {
        if (await this.loadProposal(true)) {
          this.setData({ feedbackText: "" });
        }
      }
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "提交调整意见失败",
      });
      this.scheduleFeedbackPoll();
    }
  },

  async generateDraft() {
    if (
      this.data.proposal?.status !== "ready"
      || this.data.proposal.phase !== "plan_ready"
      || this.data.busy
      || this.data.feedbackBusy
      || this.data.draftBusy
      || this.data.feedbackVoicePhase !== "idle"
    ) {
      return;
    }
    this.setData({ draftBusy: true, error: "" });
    try {
      const job = await enqueueMemoirBookRevisionDraft(
        this.data.projectId,
        this.data.proposalId,
      );
      const active = job.status === "pending" || job.status === "running";
      this.setData({ draftBusy: active });
      if (active) {
        this.scheduleDraftPoll();
      } else if (job.status === "succeeded") {
        await this.loadProposal(true);
      }
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        draftBusy: false,
        error: error instanceof Error ? error.message : "生成修改稿失败",
      });
      this.scheduleDraftPoll();
    }
  },

  async switchMode(event: RevisionDatasetEvent) {
    const mode = datasetString(event, "mode");
    if (mode === "changes" || mode === "whole") {
      this.setData({ mode });
      if (mode === "whole") {
        await this.loadWholePreview(this.data.previewVersion);
      }
    }
  },

  async switchPreviewVersion(event: RevisionDatasetEvent) {
    const version = datasetString(event, "version");
    const proposal = this.revisionProposal;
    if (!proposal || (version !== "before" && version !== "after")) {
      return;
    }
    this.setData({
      ...revisionViewPatch(proposal, version),
      wholeChapters: this.wholeChapterCache[version] || [],
    });
    if (this.data.mode === "whole") {
      await this.loadWholePreview(version);
    }
  },

  async applyRevision() {
    if (
      !this.data.proposal?.can_apply
      || this.data.busy
      || this.data.feedbackBusy
      || this.data.draftBusy
      || this.data.feedbackVoicePhase !== "idle"
    ) {
      return;
    }
    if (!await confirmAction(
      "确认使用这版修改？",
      "确认后会生成新的正式书稿版本，原版本仍会保留。",
    )) {
      return;
    }
    this.setData({ busy: true, error: "" });
    try {
      const proposal = await applyMemoirBookRevisionProposal(
        this.data.projectId,
        this.data.proposalId,
      );
      this.revisionProposal = proposal;
      this.setData({
        busy: false,
        ...revisionViewPatch(proposal, this.data.previewVersion),
      });
      wx.showToast({ title: "修改已写入书稿", icon: "success" });
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        busy: false,
        error: error instanceof Error ? error.message : "确认修改失败",
      });
    }
  },

  async dismissRevision() {
    if (
      !this.data.proposal?.can_dismiss
      || this.data.busy
      || this.data.feedbackBusy
      || this.data.draftBusy
      || this.data.feedbackVoicePhase !== "idle"
    ) {
      return;
    }
    if (!await confirmAction(
      "放弃这版修改？",
      "当前书稿不会改变；之后仍可重新生成修改预览。",
    )) {
      return;
    }
    this.setData({ busy: true, error: "" });
    try {
      await dismissMemoirBookRevisionProposal(
        this.data.projectId,
        this.data.proposalId,
      );
      wx.navigateBack();
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        busy: false,
        error: error instanceof Error ? error.message : "放弃修改失败",
      });
    }
  },

  openAppliedBook() {
    if (!this.data.projectId) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/book-export/index", {
        projectId: this.data.projectId,
        action: "preview",
      }),
    });
  },
});
