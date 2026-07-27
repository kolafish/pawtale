import type {
  MemoirCompletionIssue,
  MemoirCompletionIssueOutcome,
} from "../../domain/voice-chat";
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

    confirmRequired(event: IssueEvent) {
      const issue = this.issue(
        this.properties.requiredIssues as MemoirCompletionIssue[],
        event,
      );
      if (!issue || this.data.busyIssueId || this.properties.disabled) {
        return;
      }
      wx.showModal({
        title: "确认处理决定",
        content: issue.question,
        confirmText: "确认",
        cancelText: "再看看",
        success: ({ confirm }) => {
          if (confirm) {
            void this.closeIssue(
              issue.id,
              "confirmed",
              "用户已确认处理决定",
            );
          }
        },
      });
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
