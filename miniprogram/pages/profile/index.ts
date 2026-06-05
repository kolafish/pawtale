import { loadMemoirDashboard } from "../../services/memoir-dashboard";

type FontSize = "standard" | "large" | "extra";

interface ProfileData {
  loading: boolean;
  storyCount: number;
  progressPercent: number;
  fontSize: FontSize;
}

const SETTINGS_KEY = "pawtale_display_settings";

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
      const { summary } = await loadMemoirDashboard();
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
