export {};

interface InputEvent {
  detail: {
    value: string;
  };
}

interface FeedbackItem {
  issueType: string;
  content: string;
  createdAt: string;
}

interface FeedbackData {
  issueType: string;
  content: string;
  submitting: boolean;
}

const FEEDBACK_STORAGE_KEY = "pawtale_feedback_items";

function readFeedbackItems(): FeedbackItem[] {
  return wx.getStorageSync<FeedbackItem[]>(FEEDBACK_STORAGE_KEY) || [];
}

Page<FeedbackData, {
  selectFontIssue(): void;
  selectAudioIssue(): void;
  selectOperationIssue(): void;
  onContentInput(event: InputEvent): void;
  submitFeedback(): void;
  contactService(): void;
  openHome(): void;
  openWorkbench(): void;
  openFeedback(): void;
  openProfile(): void;
}>({
  data: {
    issueType: "",
    content: "",
    submitting: false,
  },

  selectFontIssue() {
    this.setData({ issueType: "字太小" });
  },

  selectAudioIssue() {
    this.setData({ issueType: "听不清" });
  },

  selectOperationIssue() {
    this.setData({ issueType: "不好操作" });
  },

  onContentInput(event) {
    this.setData({ content: event.detail.value });
  },

  submitFeedback() {
    const content = this.data.content.trim();
    if (!this.data.issueType && !content) {
      wx.showToast({ title: "请先说说问题", icon: "none" });
      return;
    }
    this.setData({ submitting: true });
    const items = readFeedbackItems();
    wx.setStorageSync(FEEDBACK_STORAGE_KEY, [
      {
        issueType: this.data.issueType,
        content,
        createdAt: new Date().toISOString(),
      },
      ...items,
    ]);
    this.setData({ submitting: false, issueType: "", content: "" });
    wx.showToast({ title: "已记录", icon: "success" });
  },

  contactService() {
    wx.showToast({ title: "客服稍后接入", icon: "none" });
  },

  openHome() {
    wx.redirectTo({ url: "/pages/home/index" });
  },

  openWorkbench() {
    wx.redirectTo({ url: "/pages/workbench/index" });
  },

  openFeedback() {
  },

  openProfile() {
    wx.redirectTo({ url: "/pages/profile/index" });
  },
});
