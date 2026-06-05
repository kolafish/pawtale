import type { MemoirInterviewState, MemoirSummary } from "../../domain/memoir";
import { ensureLocalDevSession } from "../../services/auth";
import { getCurrentSession, getMemoirSummary } from "../../services/memoir";
import { writeMemoirStore } from "../../stores/session";

type FontSize = "standard" | "large" | "extra";

interface ProfileData {
  loading: boolean;
  storyCount: number;
  progressPercent: number;
  fontSize: FontSize;
}

const SETTINGS_KEY = "pawtale_display_settings";

function fallbackSummary(interview: MemoirInterviewState): MemoirSummary {
  const totalQuestionCount = interview.questions.length;
  const answeredCount = interview.questions.filter((question) => question.status === "answered").length;
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

function readFontSize(): FontSize {
  const settings = wx.getStorageSync<{ fontSize?: FontSize }>(SETTINGS_KEY) || {};
  return settings.fontSize || "large";
}

function writeFontSize(fontSize: FontSize): void {
  wx.setStorageSync(SETTINGS_KEY, { fontSize });
}

Page<ProfileData, {
  onLoad(): Promise<void>;
  loadProfile(): Promise<void>;
  setStandardFont(): void;
  setLargeFont(): void;
  setExtraFont(): void;
  openHome(): void;
  openWorkbench(): void;
  openFeedback(): void;
  openProfile(): void;
}>({
  data: {
    loading: false,
    storyCount: 0,
    progressPercent: 0,
    fontSize: "large",
  },

  async onLoad() {
    await this.loadProfile();
  },

  async loadProfile() {
    this.setData({ loading: true, fontSize: readFontSize() });
    try {
      await ensureLocalDevSession();
      const interview = await getCurrentSession();
      const summary = await loadSummary(interview);
      writeMemoirStore({ interview });
      this.setData({
        storyCount: summary.total_story_count,
        progressPercent: summary.progress_percent,
      });
    } finally {
      this.setData({ loading: false });
    }
  },

  setStandardFont() {
    writeFontSize("standard");
    this.setData({ fontSize: "standard" });
  },

  setLargeFont() {
    writeFontSize("large");
    this.setData({ fontSize: "large" });
  },

  setExtraFont() {
    writeFontSize("extra");
    this.setData({ fontSize: "extra" });
  },

  openHome() {
    wx.redirectTo({ url: "/pages/home/index" });
  },

  openWorkbench() {
    wx.redirectTo({ url: "/pages/workbench/index" });
  },

  openFeedback() {
    wx.redirectTo({ url: "/pages/feedback/index" });
  },

  openProfile() {
  },
});
