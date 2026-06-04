import { ensureLocalDevSession } from "../../services/auth";
import { getCurrentSession, getMemoirProject, getMemoirSummary } from "../../services/memoir";
import { writeMemoirStore } from "../../stores/session";

interface HomeData {
  loading: boolean;
  error: string;
  projectTitle: string;
  sessionTitle: string;
  currentPrompt: string;
  completedCount: number;
  pendingCount: number;
  weeklyStoryCount: number;
  pendingReviewCount: number;
  confirmedStoryCount: number;
  progressPercent: number;
  latestDraftPreview: string;
}

Page<HomeData, {
  onLoad(): Promise<void>;
  loadHome(): Promise<void>;
  continueInterview(): Promise<void>;
}>({
  data: {
    loading: false,
    error: "",
    projectTitle: "",
    sessionTitle: "",
    currentPrompt: "",
    completedCount: 0,
    pendingCount: 0,
    weeklyStoryCount: 0,
    pendingReviewCount: 0,
    confirmedStoryCount: 0,
    progressPercent: 0,
    latestDraftPreview: "",
  },

  async onLoad() {
    await this.loadHome();
  },

  async loadHome() {
    this.setData({ loading: true, error: "" });
    try {
      await ensureLocalDevSession();
      const project = await getMemoirProject();
      const interview = await getCurrentSession();
      const summary = await getMemoirSummary();
      writeMemoirStore({ project, interview });
      this.setData({
        projectTitle: project.title,
        sessionTitle: interview.session.title,
        currentPrompt: interview.current_question?.prompt || "本轮访谈已经完成，可以先看看整理好的故事。",
        completedCount: interview.completed_question_count,
        pendingCount: interview.pending_question_count,
        weeklyStoryCount: summary.weekly_story_count,
        pendingReviewCount: summary.pending_review_count,
        confirmedStoryCount: summary.confirmed_story_count,
        progressPercent: summary.progress_percent,
        latestDraftPreview: interview.latest_draft?.content_edited || "",
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async continueInterview() {
    if (!this.data.projectTitle) {
      await this.loadHome();
    }
    wx.navigateTo({ url: "/pages/question/index" });
  },
});
