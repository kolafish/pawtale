import { loadMemoirDashboard } from "../../services/memoir-dashboard";
import { getMemoirIdentity, loginWithWechat, updateWechatUserProfile, uploadWechatAvatar } from "../../services/auth";
import {
  markMemoirDataSnapshotsStale,
  readMemoirDashboardSnapshot,
  shouldRefreshMemoirDashboardSnapshot,
} from "../../stores/memoir-cache";
import type { MemoirIdentity } from "../../domain/memoir";

interface ProfileData {
  loading: boolean;
  loadingIdentity: boolean;
  identityReady: boolean;
  storyCount: number;
  progressPercent: number;
  avatarUrl: string;
  profileNameDraft: string;
  avatarDraftUrl: string;
  avatarChanged: boolean;
  canSaveProfile: boolean;
  activeProjectTitle: string;
  projectCount: number;
  syncingProfile: boolean;
  error: string;
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

const DEFAULT_WECHAT_DISPLAY_NAME = "微信用户";

Page<ProfileData, {
  onLoad(): Promise<void>;
  hydrateFromCache(): boolean;
  applyIdentity(identity: MemoirIdentity): void;
  loadIdentity(forceLogin?: boolean): Promise<boolean>;
  loadProfile(): Promise<void>;
  loginWechat(): Promise<void>;
  chooseAvatar(event: AvatarEvent): void;
  onNicknameInput(event: InputEvent): void;
  saveWechatProfile(): Promise<void>;
  openProjects(): void;
  openHome(): void;
  openWorkbench(): void;
  openQa(): void;
  openProfile(): void;
}>({
  data: {
    loading: false,
    loadingIdentity: false,
    identityReady: false,
    storyCount: 0,
    progressPercent: 0,
    avatarUrl: "",
    profileNameDraft: "",
    avatarDraftUrl: "",
    avatarChanged: false,
    canSaveProfile: false,
    activeProjectTitle: "",
    projectCount: 0,
    syncingProfile: false,
    error: "",
  },

  async onLoad() {
    const identityLoaded = await this.loadIdentity();
    if (!identityLoaded) {
      return;
    }
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

  applyIdentity(identity) {
    const displayName = identity.display_name || identity.wechat_nickname || DEFAULT_WECHAT_DISPLAY_NAME;
    const savedName = displayName === DEFAULT_WECHAT_DISPLAY_NAME ? "" : displayName;
    const avatarUrl = identity.wechat_avatar_url || identity.avatar_url || "";
    this.setData({
      avatarUrl,
      profileNameDraft: savedName,
      avatarDraftUrl: avatarUrl,
      avatarChanged: false,
      canSaveProfile: false,
      activeProjectTitle: identity.active_project?.title || "",
      projectCount: identity.projects.length,
      identityReady: true,
      error: "",
    });
  },

  async loadIdentity(forceLogin = false) {
    if (this.data.loadingIdentity) {
      return false;
    }
    this.setData({ loadingIdentity: true, error: "" });
    try {
      const identity = forceLogin ? await loginWithWechat() : await getMemoirIdentity();
      this.applyIdentity(identity);
      return true;
    } catch (error) {
      this.setData({
        identityReady: false,
        error: error instanceof Error ? error.message : "身份加载失败",
      });
      return false;
    } finally {
      this.setData({ loadingIdentity: false });
    }
  },

  async loadProfile() {
    this.setData({ loading: true, error: "" });
    try {
      const { project, summary } = await loadMemoirDashboard();
      this.setData({
        storyCount: summary.total_story_count,
        progressPercent: summary.progress_percent,
        activeProjectTitle: project?.title || this.data.activeProjectTitle,
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "资料加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async loginWechat() {
    const ok = await this.loadIdentity(true);
    if (ok) {
      wx.showToast({ title: "已登录", icon: "success" });
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
      canSaveProfile: true,
      error: "",
    });
  },

  onNicknameInput(event) {
    const value = event.detail.value;
    this.setData({
      profileNameDraft: value,
      canSaveProfile: Boolean(value.trim()) || this.data.avatarChanged,
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
      const hasAvatarChange = Boolean(this.data.avatarChanged && this.data.avatarDraftUrl);
      if (!nickName && !hasAvatarChange) {
        this.setData({ error: "请先选择头像或填写昵称" });
        return;
      }
      let identity: MemoirIdentity;
      if (!this.data.identityReady) {
        const ok = await this.loadIdentity(true);
        if (!ok) {
          return;
        }
      }
      identity = await getMemoirIdentity();
      const avatarUrl = hasAvatarChange
        ? await uploadWechatAvatar(this.data.avatarDraftUrl, identity.storage_owner_id || identity.openid || identity.id)
        : this.data.avatarDraftUrl || identity.wechat_avatar_url || "";
      const nextIdentity = await updateWechatUserProfile({
        ...(nickName ? {
          display_name: nickName,
          wechat_nickname: nickName,
        } : {}),
        ...(avatarUrl ? { wechat_avatar_url: avatarUrl } : {}),
      });
      this.applyIdentity(nextIdentity);
      markMemoirDataSnapshotsStale();
      wx.showToast({ title: "已更新", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "微信资料同步失败" });
    } finally {
      this.setData({ syncingProfile: false });
    }
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
