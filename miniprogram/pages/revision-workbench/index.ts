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
  type MemoirBookRevisionProposal,
  type MemoirBookRevisionPreviewVersion,
  type MemoirBookRevisionSectionComparison,
} from "../../services/memoir-book-completion-api";
import { memoirProjectDictationUrl } from "../../services/memoir-api-client";
import {
  applyMemoirChapterSectionRevision,
  dismissMemoirChapterSectionRevision,
  enqueueMemoirChapterSectionRevision,
  getMemoirChapterSectionRevisionJob,
  getMemoirChapterSectionRevisionProposal,
} from "../../services/memoir-chapter-workspace-api";
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
import { setPageNavigationTitle } from "../../services/page-navigation";


type RevisionMode = "changes" | "whole";
type PreviewVersion = "before" | "after";
type RevisionScope = "book" | "chapter_section";
const FEEDBACK_POLL_INTERVAL_MS = 1500;
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

interface BookMetadataChangeView {
  field: string;
  label: string;
  before_value: string;
  after_value: string;
  display_value: string;
}

const BOOK_METADATA_LABELS: Record<string, string> = {
  title: "书名",
  subtitle: "副标题",
  narrator_name: "口述人",
  prepared_by: "整理人",
  preface: "前言",
  afterword: "后记",
  back_cover_quote: "封底文案",
};

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
    | "metadata_changes"
  >;
}

interface RevisionWorkbenchData {
  pageClass: string;
  scope: RevisionScope;
  projectId: string;
  chapterId: string;
  sectionId: string;
  sectionTitle: string;
  proposalId: string;
  loading: boolean;
  busy: boolean;
  error: string;
  proposal: RevisionProposalView | null;
  mode: RevisionMode;
  previewVersion: PreviewVersion;
  changedChapters: RevisionChapterView[];
  metadataChanges: BookMetadataChangeView[];
  wholeLoading: boolean;
  wholeChapters: WholeChapterView[];
  feedbackText: string;
  feedbackDictationUrl: string;
  feedbackBusy: boolean;
  draftBusy: boolean;
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

function metadataChangeViews(
  proposal: MemoirBookRevisionProposal,
  previewVersion: PreviewVersion,
): BookMetadataChangeView[] {
  const changes = proposal.phase === "plan_ready"
    ? Object.entries(proposal.revision_plan?.book_metadata_patch || {}).map(
      ([field, afterValue]) => ({
        field,
        before_value: "",
        after_value: afterValue || "",
      }),
    )
    : (proposal.comparison.metadata_changes || []);
  return changes.map((change) => ({
    ...change,
    label: BOOK_METADATA_LABELS[change.field] || "成书信息",
    display_value: (
      proposal.phase === "plan_ready" || previewVersion === "after"
        ? change.after_value
        : change.before_value
    ) || "（留空）",
  }));
}

export function revisionViewPatch(
  proposal: MemoirBookRevisionProposal,
  previewVersion: PreviewVersion = "after",
): Pick<
  RevisionWorkbenchData,
  "proposal" | "previewVersion" | "changedChapters" | "metadataChanges"
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
        metadata_changes: proposal.comparison.metadata_changes || [],
      },
    },
    previewVersion,
    metadataChanges: metadataChangeViews(proposal, previewVersion),
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

function matchesSectionOperation(
  job: object,
  operation: "feedback" | "draft",
): boolean {
  return !("operation" in job) || job.operation === operation;
}

definePage<RevisionWorkbenchData>()({
  feedbackPoller: new PollingTimer(FEEDBACK_POLL_INTERVAL_MS),
  draftPoller: new PollingTimer(FEEDBACK_POLL_INTERVAL_MS),
  feedbackBaselineUpdatedAt: "",
  revisionProposal: null as MemoirBookRevisionProposal | null,
  wholeChapterCache: {} as Partial<Record<PreviewVersion, WholeChapterView[]>>,
  wholeLoadSequence: 0,
  pageActive: false,

  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    scope: "book",
    projectId: "",
    chapterId: "",
    sectionId: "",
    sectionTitle: "",
    proposalId: "",
    loading: true,
    busy: false,
    error: "",
    proposal: null,
    mode: "changes",
    previewVersion: "after",
    changedChapters: [],
    metadataChanges: [],
    wholeLoading: false,
    wholeChapters: [],
    feedbackText: "",
    feedbackDictationUrl: "",
    feedbackBusy: false,
    draftBusy: false,
  },

  async onLoad(query: PageQuery = {}) {
    const scope: RevisionScope = queryParam(query, "scope") === "chapter_section"
      ? "chapter_section"
      : "book";
    const projectId = queryParam(query, "projectId");
    const chapterId = queryParam(query, "chapterId");
    const sectionId = queryParam(query, "sectionId");
    const sectionTitle = queryParam(query, "sectionTitle");
    const proposalId = queryParam(query, "proposalId");
    this.setData({
      scope,
      projectId,
      chapterId,
      sectionId,
      sectionTitle,
      proposalId,
      feedbackDictationUrl: projectId
        ? memoirProjectDictationUrl(projectId)
        : "",
    });
    setPageNavigationTitle(
      scope === "chapter_section" ? "调整小节正文" : "确认书稿修改",
    );
    if (
      !projectId
      || (scope === "book" && !proposalId)
      || (scope === "chapter_section" && (!chapterId || !sectionId))
    ) {
      this.setData({ loading: false, error: "缺少修改预览信息" });
      return;
    }
    if (scope === "chapter_section" && !proposalId) {
      this.setData({ loading: false });
      await this.refreshFeedbackJob();
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
      && this.data.projectId
      && (
        (this.data.proposal && this.data.proposalId)
        || (this.data.scope === "chapter_section" && !this.data.proposalId)
      )
    ) {
      void this.refreshFeedbackJob();
      void this.refreshDraftJob();
    }
  },

  onHide() {
    this.pageActive = false;
    this.stopFeedbackPolling();
    this.stopDraftPolling();
  },

  onUnload() {
    this.pageActive = false;
    this.stopFeedbackPolling();
    this.stopDraftPolling();
  },

  async loadProposal(silent = false) {
    if (!silent) {
      this.setData({ loading: true, error: "" });
    }
    try {
      const proposal = this.data.scope === "chapter_section"
        ? await getMemoirChapterSectionRevisionProposal(
          this.data.projectId,
          this.data.chapterId,
          this.data.sectionId,
          this.data.proposalId,
        )
        : await getMemoirBookRevisionProposal(
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
    if (this.data.scope !== "book") {
      return false;
    }
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

  async refreshInitialJob() {
    try {
      const job = await getMemoirChapterSectionRevisionJob(
        this.data.projectId,
        this.data.chapterId,
        this.data.sectionId,
      );
      if (job.proposal_id) {
        this.setData({ proposalId: job.proposal_id, feedbackText: "" });
        if (await this.loadProposal(true)) {
          await Promise.all([
            this.refreshFeedbackJob(),
            this.refreshDraftJob(),
          ]);
        }
        return;
      }
      const active = job.status === "pending" || job.status === "running";
      this.stopFeedbackPolling();
      this.setData({
        feedbackBusy: active,
        ...(job.status === "failed"
          ? { error: job.error_message || "生成修改思路失败" }
          : {}),
      });
      if (active) {
        this.scheduleFeedbackPoll();
      }
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        feedbackBusy: false,
        error: error instanceof Error ? error.message : "修改进度加载失败",
      });
    }
  },

  async refreshFeedbackJob() {
    if (this.data.scope === "chapter_section" && !this.data.proposalId) {
      await this.refreshInitialJob();
      return;
    }
    const wasActive = this.data.feedbackBusy;
    try {
      const job = this.data.scope === "chapter_section"
        ? await getMemoirChapterSectionRevisionJob(
          this.data.projectId,
          this.data.chapterId,
          this.data.sectionId,
        )
        : await getMemoirBookRevisionFeedbackJob(
          this.data.projectId,
          this.data.proposalId,
        );
      const active = (
        job.status === "pending" || job.status === "running"
      ) && matchesSectionOperation(job, "feedback");
      const failed = job.status === "failed"
        && matchesSectionOperation(job, "feedback");
      this.stopFeedbackPolling();
      this.setData({
        feedbackBusy: active,
        ...(failed
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
    if (!this.data.proposalId) {
      return;
    }
    const wasActive = this.data.draftBusy;
    try {
      const job = this.data.scope === "chapter_section"
        ? await getMemoirChapterSectionRevisionJob(
          this.data.projectId,
          this.data.chapterId,
          this.data.sectionId,
        )
        : await getMemoirBookRevisionDraftJob(
          this.data.projectId,
          this.data.proposalId,
        );
      const active = (
        job.status === "pending" || job.status === "running"
      ) && matchesSectionOperation(job, "draft");
      const failed = job.status === "failed"
        && matchesSectionOperation(job, "draft");
      this.stopDraftPolling();
      this.setData({
        draftBusy: active,
        ...(failed
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
    if (this.data.feedbackBusy || this.data.draftBusy) {
      return;
    }
    this.setData({ feedbackText: event.detail.value });
  },

  async submitFeedback(event?: PageTextInputEvent) {
    const feedback = String(
      event?.detail.value ?? this.data.feedbackText,
    ).trim();
    if (this.data.busy || this.data.feedbackBusy || this.data.draftBusy) {
      return;
    }
    if (!feedback) {
      wx.showToast({ title: "请先输入或说出调整意见", icon: "none" });
      return;
    }
    if (!this.data.proposal) {
      if (this.data.scope !== "chapter_section") {
        return;
      }
      this.setData({ feedbackBusy: true, error: "" });
      try {
        const job = await enqueueMemoirChapterSectionRevision(
          this.data.projectId,
          this.data.chapterId,
          this.data.sectionId,
          "plan",
          null,
          feedback,
        );
        const active = job.status === "pending" || job.status === "running";
        this.setData({ feedbackBusy: active });
        if (active) {
          this.scheduleFeedbackPoll();
        } else if (job.status === "succeeded" && job.proposal_id) {
          this.setData({ proposalId: job.proposal_id, feedbackText: "" });
          await this.loadProposal(true);
        }
      } catch (error) {
        if (routeAuthError(error)) {
          return;
        }
        this.setData({
          feedbackBusy: false,
          error: error instanceof Error ? error.message : "提交修改意见失败",
        });
      }
      return;
    }
    if (
      this.data.proposal.status !== "ready"
      || !this.data.proposal.can_dismiss
    ) {
      return;
    }
    this.feedbackBaselineUpdatedAt = this.data.proposal.updated_at;
    this.setData({ feedbackBusy: true, error: "" });
    try {
      const job = this.data.scope === "chapter_section"
        ? await enqueueMemoirChapterSectionRevision(
          this.data.projectId,
          this.data.chapterId,
          this.data.sectionId,
          "feedback",
          this.data.proposalId,
          feedback,
        )
        : await enqueueMemoirBookRevisionFeedback(
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
    ) {
      return;
    }
    this.setData({ draftBusy: true, error: "" });
    try {
      const job = this.data.scope === "chapter_section"
        ? await enqueueMemoirChapterSectionRevision(
          this.data.projectId,
          this.data.chapterId,
          this.data.sectionId,
          "draft",
          this.data.proposalId,
          null,
        )
        : await enqueueMemoirBookRevisionDraft(
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
    if (this.data.scope !== "book" && mode === "whole") {
      return;
    }
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
    ) {
      return;
    }
    if (!await confirmAction(
      "确认使用这版修改？",
      this.data.scope === "chapter_section"
        ? "确认后会生成新的章节正文版本，修改前版本仍会保留。"
        : "确认后会生成新的正式书稿版本，原版本仍会保留。",
    )) {
      return;
    }
    this.setData({ busy: true, error: "" });
    try {
      const proposal = this.data.scope === "chapter_section"
        ? await applyMemoirChapterSectionRevision(
          this.data.projectId,
          this.data.chapterId,
          this.data.sectionId,
          this.data.proposalId,
        )
        : await applyMemoirBookRevisionProposal(
          this.data.projectId,
          this.data.proposalId,
        );
      this.revisionProposal = proposal;
      this.setData({
        busy: false,
        ...revisionViewPatch(proposal, this.data.previewVersion),
      });
      wx.showToast({
        title: this.data.scope === "chapter_section"
          ? "修改已写入本章"
          : "修改已写入书稿",
        icon: "success",
      });
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
      if (this.data.scope === "chapter_section") {
        await dismissMemoirChapterSectionRevision(
          this.data.projectId,
          this.data.chapterId,
          this.data.sectionId,
          this.data.proposalId,
        );
      } else {
        await dismissMemoirBookRevisionProposal(
          this.data.projectId,
          this.data.proposalId,
        );
      }
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
    if (this.data.scope === "chapter_section") {
      wx.navigateBack();
      return;
    }
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
