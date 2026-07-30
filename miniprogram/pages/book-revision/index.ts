import type {
  MemoirBookManuscriptChapter,
  MemoirBookOutputSection,
} from "../../domain/memoir-book";
import { definePage } from "../../services/page-definition";
import {
  applyMemoirBookRevisionProposal,
  dismissMemoirBookRevisionProposal,
  enqueueMemoirBookRevisionFeedback,
  getMemoirBookRevisionFeedbackJob,
  getMemoirBookRevisionProposal,
  type MemoirBookRevisionProposal,
  type MemoirBookRevisionSectionComparison,
} from "../../services/memoir-voice-api";
import {
  recordMemoirVoiceSearchQuery,
  stopMemoirVoiceSearchRecording,
} from "../../services/memoir-voice-search";
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


type RevisionMode = "changes" | "whole";
type PreviewVersion = "before" | "after";
type FeedbackVoicePhase = "idle" | "listening" | "transcribing";
const FEEDBACK_POLL_INTERVAL_MS = 1500;
const FEEDBACK_VOICE_MAX_DURATION_MS = 30000;

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

interface BookRevisionData {
  projectId: string;
  proposalId: string;
  loading: boolean;
  busy: boolean;
  error: string;
  proposal: MemoirBookRevisionProposal | null;
  mode: RevisionMode;
  previewVersion: PreviewVersion;
  changedChapters: RevisionChapterView[];
  wholeChapters: WholeChapterView[];
  feedbackText: string;
  feedbackBusy: boolean;
  feedbackVoicePhase: FeedbackVoicePhase;
}

type RevisionDatasetEvent = PageDatasetEvent<{
  mode?: string;
  version?: string;
}>;

function wholeBookChapters(
  proposal: MemoirBookRevisionProposal,
  version: PreviewVersion,
): WholeChapterView[] {
  const chapters = proposal[version].manuscript?.chapters || [];
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
  "proposal" | "previewVersion" | "changedChapters" | "wholeChapters"
> {
  return {
    proposal,
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
    wholeChapters: wholeBookChapters(proposal, previewVersion),
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

definePage<BookRevisionData>()({
  feedbackPoller: new PollingTimer(FEEDBACK_POLL_INTERVAL_MS),
  feedbackVoiceToken: 0,
  pageActive: false,

  data: {
    projectId: "",
    proposalId: "",
    loading: true,
    busy: false,
    error: "",
    proposal: null,
    mode: "changes",
    previewVersion: "after",
    changedChapters: [],
    wholeChapters: [],
    feedbackText: "",
    feedbackBusy: false,
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
      await this.refreshFeedbackJob();
    }
  },

  onShow() {
    this.pageActive = true;
    if (
      !this.data.loading
      && this.data.proposal
      && this.data.projectId
      && this.data.proposalId
    ) {
      void this.refreshFeedbackJob();
    }
  },

  onHide() {
    this.pageActive = false;
    this.stopFeedbackPolling();
    this.cancelFeedbackVoice();
  },

  onUnload() {
    this.pageActive = false;
    this.stopFeedbackPolling();
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
      this.setData({
        loading: false,
        ...revisionViewPatch(proposal, this.data.previewVersion),
      });
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

  stopFeedbackPolling() {
    this.feedbackPoller.cancel();
  },

  scheduleFeedbackPoll() {
    if (!this.pageActive) {
      return;
    }
    this.feedbackPoller.schedule(() => {
      void this.refreshFeedbackJob();
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
        error: job.status === "failed"
          ? job.error_message || "按意见调整失败"
          : "",
      });
      if (active) {
        this.scheduleFeedbackPoll();
      } else if (job.status === "succeeded" && wasActive) {
        if (await this.loadProposal(true)) {
          this.setData({ feedbackText: "" });
          wx.showToast({ title: "修改预览已更新", icon: "success" });
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

  changeFeedbackText(event: PageTextInputEvent) {
    if (this.data.feedbackBusy || this.data.feedbackVoicePhase !== "idle") {
      return;
    }
    this.setData({ feedbackText: event.detail.value });
  },

  cancelFeedbackVoice() {
    if (this.data.feedbackVoicePhase === "idle") {
      return;
    }
    this.feedbackVoiceToken += 1;
    if (this.data.feedbackVoicePhase === "listening") {
      stopMemoirVoiceSearchRecording();
    }
    this.setData({ feedbackVoicePhase: "idle" });
  },

  async toggleFeedbackVoice() {
    if (this.data.feedbackVoicePhase === "listening") {
      stopMemoirVoiceSearchRecording();
      return;
    }
    if (
      this.data.busy
      || this.data.feedbackBusy
      || this.data.feedbackVoicePhase !== "idle"
    ) {
      return;
    }
    this.feedbackVoiceToken += 1;
    const token = this.feedbackVoiceToken;
    this.setData({ feedbackVoicePhase: "listening", error: "" });
    try {
      const transcript = await recordMemoirVoiceSearchQuery({
        stopMode: "manual",
        maxDurationMs: FEEDBACK_VOICE_MAX_DURATION_MS,
        onRecordingEnd: () => {
          if (token === this.feedbackVoiceToken) {
            this.setData({ feedbackVoicePhase: "transcribing" });
          }
        },
        emptyTranscriptMessage: "没有识别到调整意见，请再说一次",
        noVoiceTranscriptMessage: "没有听到调整意见，请再说一次",
      });
      if (token !== this.feedbackVoiceToken) {
        return;
      }
      const existing = this.data.feedbackText.trim();
      this.setData({
        feedbackText: `${existing ? `${existing}\n` : ""}${transcript}`.slice(
          0,
          1000,
        ),
        feedbackVoicePhase: "idle",
      });
      wx.showToast({ title: "已识别调整意见", icon: "none" });
    } catch (error) {
      if (token !== this.feedbackVoiceToken) {
        return;
      }
      this.setData({
        feedbackVoicePhase: "idle",
        error: error instanceof Error ? error.message : "语音输入失败",
      });
    }
  },

  async submitFeedback() {
    const feedback = this.data.feedbackText.trim();
    if (
      !this.data.proposal?.can_apply
      || this.data.busy
      || this.data.feedbackBusy
      || this.data.feedbackVoicePhase !== "idle"
    ) {
      return;
    }
    if (!feedback) {
      wx.showToast({ title: "请先输入或说出调整意见", icon: "none" });
      return;
    }
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

  switchMode(event: RevisionDatasetEvent) {
    const mode = datasetString(event, "mode");
    if (mode === "changes" || mode === "whole") {
      this.setData({ mode });
    }
  },

  switchPreviewVersion(event: RevisionDatasetEvent) {
    const version = datasetString(event, "version");
    const proposal = this.data.proposal;
    if (!proposal || (version !== "before" && version !== "after")) {
      return;
    }
    this.setData(revisionViewPatch(proposal, version));
  },

  async applyRevision() {
    if (
      !this.data.proposal?.can_apply
      || this.data.busy
      || this.data.feedbackBusy
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
