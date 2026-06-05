import type { MemoirInterviewState, MemoirStoryCard, MemoirSummary } from "../../domain/memoir";
import { ensureLocalDevSession } from "../../services/auth";
import { getCurrentSession, getMemoirStories, getMemoirSummary } from "../../services/memoir";
import { writeMemoirStore } from "../../stores/session";

interface WorkbenchData {
  loading: boolean;
  error: string;
  weeklyStoryCount: number;
  pendingReviewCount: number;
  confirmedStoryCount: number;
  progressPercent: number;
  pendingCount: number;
  stories: MemoirStoryCard[];
}

function answeredQuestionCount(interview: MemoirInterviewState): number {
  return interview.questions.filter((question) => question.status === "answered").length;
}

function fallbackSummary(interview: MemoirInterviewState): MemoirSummary {
  const totalQuestionCount = interview.questions.length;
  const answeredCount = answeredQuestionCount(interview);
  return {
    total_story_count: answeredCount,
    weekly_story_count: answeredCount,
    pending_review_count: interview.latest_draft?.status === "draft" ? 1 : 0,
    confirmed_story_count: interview.latest_draft?.status === "accepted" ? 1 : 0,
    total_question_count: totalQuestionCount,
    completed_question_count: interview.completed_question_count,
    progress_percent: totalQuestionCount
      ? Math.round((interview.completed_question_count * 100) / totalQuestionCount)
      : 0,
  };
}

async function loadSummary(interview: MemoirInterviewState): Promise<MemoirSummary> {
  try {
    return await getMemoirSummary();
  } catch {
    return fallbackSummary(interview);
  }
}

async function loadStories(): Promise<MemoirStoryCard[]> {
  try {
    return (await getMemoirStories()).stories;
  } catch {
    return [];
  }
}

Page<WorkbenchData, {
  onLoad(): Promise<void>;
  onShow(): Promise<void>;
  loadWorkbench(): Promise<void>;
  continueInterview(): void;
  backHome(): void;
}>({
  data: {
    loading: false,
    error: "",
    weeklyStoryCount: 0,
    pendingReviewCount: 0,
    confirmedStoryCount: 0,
    progressPercent: 0,
    pendingCount: 0,
    stories: [],
  },

  async onLoad() {
    await this.loadWorkbench();
  },

  async onShow() {
    await this.loadWorkbench();
  },

  async loadWorkbench() {
    this.setData({ loading: true, error: "" });
    try {
      await ensureLocalDevSession();
      const interview = await getCurrentSession();
      const summary = await loadSummary(interview);
      const stories = await loadStories();
      writeMemoirStore({ interview });
      this.setData({
        weeklyStoryCount: summary.weekly_story_count,
        pendingReviewCount: summary.pending_review_count,
        confirmedStoryCount: summary.confirmed_story_count,
        progressPercent: summary.progress_percent,
        pendingCount: interview.pending_question_count,
        stories,
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "工作台加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  continueInterview() {
    wx.navigateTo({ url: "/pages/question/index" });
  },

  backHome() {
    wx.redirectTo({ url: "/pages/home/index" });
  },
});
