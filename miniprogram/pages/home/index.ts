import { ensureLocalDevSession } from "../../services/auth";
import { getCurrentSession, getMemoirProject } from "../../services/memoir";
import { writeMemoirStore } from "../../stores/session";

interface HomeData {
  loading: boolean;
  error: string;
  projectTitle: string;
  sessionTitle: string;
  completedCount: number;
  pendingCount: number;
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
    completedCount: 0,
    pendingCount: 0,
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
      writeMemoirStore({ project, interview });
      this.setData({
        projectTitle: project.title,
        sessionTitle: interview.session.title,
        completedCount: interview.completed_question_count,
        pendingCount: interview.pending_question_count,
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
