import type {
  MemoirCompletionIssue,
  MemoirCompletionIssueOutcome,
} from "../../domain/memoir-completion-issue";
import {
  closeMemoirCompletionIssue,
} from "../../services/memoir-completion-issue-api";
import { memoirProjectDictationUrl } from "../../services/memoir-api-client";
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

export function requiredIssueGroups(issues: MemoirCompletionIssue[], bookMode: boolean) {
  const groups = bookMode
    ? [
      {
        id: "blocking",
        title: "必须处理",
        className: "required",
        detail: "涉及隐私或事实冲突，处理后相关章节才能收入全书。可以填写处理说明，也可以明确选择保留现状。",
        issues: issues.filter((issue) => issue.blocks_book),
      },
      {
        id: "optional",
        title: "可选调整",
        className: "optional",
        detail: "这些内容取舍不影响章节收入全书，可以按需要回答或保留现状。",
        issues: issues.filter((issue) => !issue.blocks_book),
      },
    ]
    : [{ id: "required", title: "需要确认", className: "required", detail: "", issues }];
  return groups.filter((group) => group.issues.length);
}

Component({
  properties: {
    projectId: { type: String, value: "" },
    projectTitle: { type: String, value: "" },
    chapterId: { type: String, value: "" },
    requiredIssues: { type: Array, value: [] },
    optionalIssues: { type: Array, value: [] },
    allowRequiredConfirmation: { type: Boolean, value: false },
    bookConfirmationMode: { type: Boolean, value: false },
    friendlyMode: { type: Boolean, value: false },
    disabled: { type: Boolean, value: false },
  },

  data: {
    busyIssueId: "",
    answeringIssueId: "",
    requiredAnswer: "",
    requiredAnswerReady: false,
    requiredAnswerGatewayUrl: "",
    requiredIssueGroups: [] as ReturnType<typeof requiredIssueGroups>,
  },

  observers: {
    "requiredIssues,bookConfirmationMode"() {
      this.setData({
        requiredIssueGroups: requiredIssueGroups(
          this.properties.requiredIssues as MemoirCompletionIssue[],
          this.properties.bookConfirmationMode,
        ),
      });
    },
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
      if (
        !issue
        || this.data.busyIssueId
        || this.data.answeringIssueId
        || this.properties.disabled
      ) {
        return;
      }
      this.setData({
        answeringIssueId: issue.id,
        requiredAnswer: "",
        requiredAnswerReady: false,
        requiredAnswerGatewayUrl: memoirProjectDictationUrl(
          this.properties.projectId,
        ),
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

    async submitRequiredAnswer(event?: IssueInputEvent) {
      const issueId = this.data.answeringIssueId;
      const note = String(
        event?.detail.value ?? this.data.requiredAnswer,
      ).trim();
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

    leaveBookIssueUnchanged(event: IssueEvent) {
      const issue = this.issue(
        this.properties.requiredIssues as MemoirCompletionIssue[],
        event,
      );
      if (
        !issue
        || issue.scope !== "book"
        || this.data.busyIssueId
        || this.data.answeringIssueId
        || this.properties.disabled
      ) {
        return;
      }
      const privacy = issue.issue_type === "privacy";
      wx.showModal({
        title: "不处理这项？",
        content: privacy
          ? "将结束本项待确认并保留当前表述，不做匿名化。更新书稿后，家人阅读、分享和导出可能包含这段文字。"
          : "将结束本项待确认，保留当前书稿的相关表述，不作修改。",
        confirmText: "不处理",
        cancelText: "再想想",
        success: ({ confirm }) => {
          if (confirm && !this.data.answeringIssueId) {
            void this.closeIssue(
              issue.id,
              "declined",
              privacy
                ? "用户确认不处理此项，保留当前书稿表述，不进行匿名化。"
                : "用户确认不处理此项，保留当前书稿表述。",
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
        itemList: this.properties.chapterId
          ? ["以后再聊", "想不起来", "不想聊这件事"]
          : ["暂时不补", "想不起来", "不想补充"],
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
            requiredAnswerGatewayUrl: "",
          });
        }
        this.triggerEvent("refresh");
        wx.showToast({ title: outcome === "declined" ? "已跳过" : "已处理", icon: "success" });
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
