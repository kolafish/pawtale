import type {
  MemoirCompletionIssue,
  MemoirCompletionIssueOutcome,
} from "../../domain/memoir-completion-issue";
import {
  closeMemoirCompletionIssue,
} from "../../services/memoir-completion-issue-api";
import {
  openMemoirCompletionInterview,
} from "../../services/memoir-completion-interview";
import { routeAuthError } from "../../services/navigation";

type SkippableOutcome = Extract<
  MemoirCompletionIssueOutcome,
  "deferred" | "not_remembered" | "declined"
>;

interface IssueEvent {
  currentTarget: { dataset: { issueId?: string } };
}

interface IssueInputEvent {
  detail: { value?: string };
}

Component({
  properties: {
    projectId: { type: String, value: "" },
    projectTitle: { type: String, value: "" },
    chapterId: { type: String, value: "" },
    requiredIssues: { type: Array, value: [] },
    optionalIssues: { type: Array, value: [] },
    allowRequiredConfirmation: { type: Boolean, value: false },
    disabled: { type: Boolean, value: false },
  },

  data: {
    busyIssueId: "",
    answeringIssueId: "",
    requiredAnswer: "",
    requiredAnswerReady: false,
  },

  methods: {
    issue(
      collection: MemoirCompletionIssue[],
      event: IssueEvent,
    ): MemoirCompletionIssue | undefined {
      const issueId = String(event.currentTarget.dataset.issueId || "");
      return collection.find((item) => item.id === issueId);
    },

    async beginInterview(event: IssueEvent) {
      const issue = this.issue(
        this.properties.optionalIssues as MemoirCompletionIssue[],
        event,
      );
      if (!issue || this.data.busyIssueId || this.properties.disabled) {
        return;
      }
      this.setData({ busyIssueId: issue.id });
      try {
        await openMemoirCompletionInterview({
          projectId: this.properties.projectId,
          projectTitle: this.properties.projectTitle,
          issue,
          entry: this.properties.chapterId
            ? "chapter_workspace"
            : "book_completion",
          chapterId: this.properties.chapterId || undefined,
        });
      } catch (error) {
        if (!routeAuthError(error, "login")) {
          wx.showToast({
            title: error instanceof Error ? error.message : "AI 访谈创建失败",
            icon: "none",
          });
        }
      } finally {
        this.setData({ busyIssueId: "" });
      }
    },

    beginRequiredAnswer(event: IssueEvent) {
      const issue = this.issue(
        this.properties.requiredIssues as MemoirCompletionIssue[],
        event,
      );
      if (!issue || this.data.busyIssueId || this.properties.disabled) {
        return;
      }
      this.setData({
        answeringIssueId: issue.id,
        requiredAnswer: "",
        requiredAnswerReady: false,
      });
    },

    changeRequiredAnswer(event: IssueInputEvent) {
      const requiredAnswer = String(event.detail.value || "");
      this.setData({
        requiredAnswer,
        requiredAnswerReady: Boolean(requiredAnswer.trim()),
      });
    },

    cancelRequiredAnswer() {
      if (this.data.busyIssueId || this.properties.disabled) {
        return;
      }
      this.setData({
        answeringIssueId: "",
        requiredAnswer: "",
        requiredAnswerReady: false,
      });
    },

    async submitRequiredAnswer() {
      const issueId = this.data.answeringIssueId;
      const note = this.data.requiredAnswer.trim();
      if (
        !issueId
        || this.data.busyIssueId
        || this.properties.disabled
      ) {
        return;
      }
      if (!note) {
        wx.showToast({ title: "请先填写回答", icon: "none" });
        return;
      }
      await this.closeIssue(issueId, "confirmed", note);
    },

    markHandled(event: IssueEvent) {
      const issue = this.issue(
        this.properties.optionalIssues as MemoirCompletionIssue[],
        event,
      );
      if (issue) {
        void this.closeIssue(
          issue.id,
          "confirmed",
          "用户已通过修改处理",
        );
      }
    },

    skip(event: IssueEvent) {
      const issue = this.issue(
        this.properties.optionalIssues as MemoirCompletionIssue[],
        event,
      );
      if (!issue || this.data.busyIssueId || this.properties.disabled) {
        return;
      }
      const choices: Array<[SkippableOutcome, string]> = [
        ["deferred", "用户选择暂时不补"],
        ["not_remembered", "用户表示想不起来"],
        ["declined", "用户选择不补充这项内容"],
      ];
      wx.showActionSheet({
        itemList: ["暂时不补", "想不起来", "不想补充"],
        success: ({ tapIndex }) => {
          const choice = choices[tapIndex];
          if (choice) {
            void this.closeIssue(issue.id, choice[0], choice[1]);
          }
        },
      });
    },

    async closeIssue(
      issueId: string,
      outcome: "confirmed" | SkippableOutcome,
      note: string,
    ) {
      if (this.data.busyIssueId || this.properties.disabled) {
        return;
      }
      this.setData({ busyIssueId: issueId });
      try {
        await closeMemoirCompletionIssue(
          this.properties.projectId,
          issueId,
          { outcome, note },
        );
        if (this.data.answeringIssueId === issueId) {
          this.setData({
            answeringIssueId: "",
            requiredAnswer: "",
            requiredAnswerReady: false,
          });
        }
        this.triggerEvent("refresh");
        wx.showToast({ title: "已处理", icon: "success" });
      } catch (error) {
        if (!routeAuthError(error, "login")) {
          wx.showToast({
            title: error instanceof Error ? error.message : "处理失败",
            icon: "none",
          });
        }
      } finally {
        this.setData({ busyIssueId: "" });
      }
    },
  },
});
