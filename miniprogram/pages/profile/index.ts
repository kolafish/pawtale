import { loadMemoirDashboard } from "../../services/memoir-dashboard";
import { getMemoirIdentity, updateWechatUserProfile, uploadWechatAvatar } from "../../services/auth";
import {
  readMemoirDashboardSnapshot,
  shouldRefreshMemoirDashboardSnapshot,
} from "../../stores/memoir-cache";

type FontSize = "standard" | "large" | "extra";

interface ProfileData {
  loading: boolean;
  storyCount: number;
  progressPercent: number;
  fontSize: FontSize;
  userName: string;
  avatarUrl: string;
  profileNameDraft: string;
  avatarDraftUrl: string;
  avatarChanged: boolean;
  activeProjectTitle: string;
  projectCount: number;
  syncingProfile: boolean;
  error: string;
}

const SETTINGS_KEY = "pawtale_display_settings";

function readFontSize(): FontSize {
  const settings = wx.getStorageSync<{ fontSize?: FontSize }>(SETTINGS_KEY) || {};
  return settings.fontSize || "large";
}

function writeFontSize(fontSize: FontSize): void {
  wx.setStorageSync(SETTINGS_KEY, { fontSize });
}

interface AvatarEvent {
  detail: {
    avatarUrl?: string;
  };
}

interface InputEvent {
  detail: {
    value: string;
  };
}

Page<ProfileData, {
  onLoad(): Promise<void>;
  hydrateFromCache(): boolean;
  loadIdentity(): Promise<void>;
  loadProfile(): Promise<void>;
  chooseAvatar(event: AvatarEvent): void;
  onNicknameInput(event: InputEvent): void;
  saveWechatProfile(): Promise<void>;
  setStandardFont(): void;
  setLargeFont(): void;
  setExtraFont(): void;
  openProjects(): void;
  openHome(): void;
  openWorkbench(): void;
  openQa(): void;
  openProfile(): void;
}>({
  data: {
    loading: false,
    storyCount: 0,
    progressPercent: 0,
    fontSize: "large",
    userName: "微信用户",
    avatarUrl: "",
    profileNameDraft: "",
    avatarDraftUrl: "",
    avatarChanged: false,
    activeProjectTitle: "",
    projectCount: 0,
    syncingProfile: false,
    error: "",
  },

  async onLoad() {
    this.setData({ fontSize: readFontSize() });
    void this.loadIdentity();
    if (!this.hydrateFromCache()) {
      await this.loadProfile();
    }
  },

  hydrateFromCache() {
    const snapshot = readMemoirDashboardSnapshot();
    if (!snapshot.summary || shouldRefreshMemoirDashboardSnapshot(snapshot)) {
      return false;
    }
    this.setData({
      storyCount: snapshot.summary.total_story_count,
      progressPercent: snapshot.memoryStats?.coverage_average ?? snapshot.summary.progress_percent,
      activeProjectTitle: snapshot.project?.title || this.data.activeProjectTitle,
    });
    return true;
  },

  async loadIdentity() {
    try {
      const identity = await getMemoirIdentity();
      const displayName = identity.display_name || identity.wechat_nickname || "微信用户";
      const avatarUrl = identity.wechat_avatar_url || identity.avatar_url || "";
      this.setData({
        userName: displayName,
        avatarUrl,
        profileNameDraft: displayName,
        avatarDraftUrl: avatarUrl,
        avatarChanged: false,
        activeProjectTitle: identity.active_project?.title || "",
        projectCount: identity.projects.length,
        error: "",
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "身份加载失败" });
    }
  },

  async loadProfile() {
    this.setData({ loading: true, fontSize: readFontSize() });
    try {
      const { project, summary } = await loadMemoirDashboard();
      this.setData({
        storyCount: summary.total_story_count,
        progressPercent: summary.progress_percent,
        activeProjectTitle: project?.title || this.data.activeProjectTitle,
      });
    } finally {
      this.setData({ loading: false });
    }
  },

  chooseAvatar(event) {
    const avatarUrl = event.detail.avatarUrl || "";
    if (!avatarUrl) {
      return;
    }
    this.setData({
      avatarDraftUrl: avatarUrl,
      avatarChanged: true,
      error: "",
    });
  },

  onNicknameInput(event) {
    this.setData({
      profileNameDraft: event.detail.value,
      error: "",
    });
  },

  async saveWechatProfile() {
    if (this.data.syncingProfile) {
      return;
    }
    this.setData({ syncingProfile: true, error: "" });
    try {
      const nickName = this.data.profileNameDraft.trim();
      if (!nickName) {
        this.setData({ error: "昵称不能为空" });
        return;
      }
      const identity = await getMemoirIdentity();
      const avatarUrl = this.data.avatarChanged && this.data.avatarDraftUrl
        ? await uploadWechatAvatar(this.data.avatarDraftUrl, identity.storage_owner_id || identity.openid || identity.id)
        : this.data.avatarDraftUrl || identity.wechat_avatar_url || "";
      const nextIdentity = await updateWechatUserProfile({
        display_name: nickName,
        wechat_nickname: nickName,
        wechat_avatar_url: avatarUrl,
      });
      this.setData({
        userName: nextIdentity.display_name || nickName,
        avatarUrl: nextIdentity.wechat_avatar_url || avatarUrl,
        profileNameDraft: nextIdentity.display_name || nickName,
        avatarDraftUrl: nextIdentity.wechat_avatar_url || avatarUrl,
        avatarChanged: false,
        activeProjectTitle: nextIdentity.active_project?.title || this.data.activeProjectTitle,
        projectCount: nextIdentity.projects.length,
      });
      wx.showToast({ title: "已更新", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "微信资料同步失败" });
    } finally {
      this.setData({ syncingProfile: false });
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

  openProjects() {
    wx.navigateTo({ url: "/pages/projects/index" });
  },

  openHome() {
    wx.redirectTo({ url: "/pages/home/index" });
  },

  openWorkbench() {
    wx.redirectTo({ url: "/pages/workbench/index" });
  },

  openQa() {
    wx.redirectTo({ url: "/pages/qa/index" });
  },

  openProfile() {
  },
});
