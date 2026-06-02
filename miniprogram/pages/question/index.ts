import type { MemoirQuestion } from "../../domain/memoir";
import { createTextAnswer, getCurrentSession, skipQuestion } from "../../services/memoir";
import { clearMemoirDraft, writeMemoirStore } from "../../stores/session";

interface InputEvent {
  detail: {
    value: string;
  };
}

interface QuestionData {
  loading: boolean;
  submitting: boolean;
  error: string;
  sessionTitle: string;
  question?: MemoirQuestion;
  prompt: string;
  answerText: string;
  isCompleted: boolean;
}

Page<QuestionData, {
  onLoad(): Promise<void>;
  loadQuestion(): Promise<void>;
  onAnswerInput(event: InputEvent): void;
  submitAnswer(): Promise<void>;
  skipCurrentQuestion(): Promise<void>;
}>({
  data: {
    loading: false,
    submitting: false,
    error: "",
    sessionTitle: "回忆录访谈",
    question: undefined,
    prompt: "",
    answerText: "",
    isCompleted: false,
  },

  async onLoad() {
    await this.loadQuestion();
  },

  async loadQuestion() {
    this.setData({ loading: true, error: "", answerText: "" });
    try {
      const interview = await getCurrentSession();
      writeMemoirStore({ interview });
      const current = interview.current_question;
      this.setData({
        sessionTitle: interview.session.title,
        question: current,
        prompt: current?.prompt || "",
        isCompleted: !current,
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "问题加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  onAnswerInput(event) {
    this.setData({ answerText: event.detail.value });
  },

  async submitAnswer() {
    const text = this.data.answerText.trim();
    const question = this.data.question;
    if (!question || !text) {
      wx.showToast({ title: "请先输入回答", icon: "none" });
      return;
    }

    this.setData({ submitting: true, error: "" });
    try {
      const answer = await createTextAnswer(question.id, text);
      clearMemoirDraft();
      writeMemoirStore({ answer });
      wx.navigateTo({ url: `/pages/feedback/index?answerId=${answer.id}` });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "提交失败" });
    } finally {
      this.setData({ submitting: false });
    }
  },

  async skipCurrentQuestion() {
    const question = this.data.question;
    if (!question) {
      return;
    }
    this.setData({ submitting: true, error: "" });
    try {
      const interview = await skipQuestion(question.id);
      writeMemoirStore({ interview });
      await this.loadQuestion();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "跳过失败" });
    } finally {
      this.setData({ submitting: false });
    }
  },
});
