import type {
  MemoirBookImagePlacement,
  MemoirBookImagePlacementCreateRequest,
  MemoirBookPlan,
  MemoirImageAsset,
  MemoirImageAssetListResponse,
} from "../../domain/voice-chat";
import { definePage } from "../../services/page-definition";
import { getMemoirIdentity } from "../../services/auth";
import {
  createMemoirBookImagePlacement,
  deleteMemoirBookImagePlacement,
  deleteMemoirImageAsset,
  downloadMemoirImageAssetFile,
  getMemoirBookPlan,
  listMemoirImageAssets,
  updateMemoirImageAsset,
} from "../../services/memoir-voice-api";
import { ApiError } from "../../services/request";
import { routeAuthError } from "../../services/navigation";
import {
  datasetString,
  pickerIndex,
  type PageDatasetEvent,
  type PagePickerEvent,
  type PageTextInputEvent,
} from "../../services/page-input";
import { formatBeijingDateTimeShort } from "../../services/time";

interface ChapterOption {
  label: string;
  value: string;
}

interface ImageAssetView extends MemoirImageAsset {
  localPath: string;
  titleText: string;
  sourceLabel: string;
  statusText: string;
  placementText: string;
  createdAtText: string;
  captionDraft: string;
  enabledPlacements: MemoirBookImagePlacement[];
}

interface ImageAssetsData {
  loading: boolean;
  error: string;
  projectId: string;
  total: number;
  readyCount: number;
  includedCount: number;
  unusedCount: number;
  items: ImageAssetView[];
  imagePaths: Record<string, string>;
  chapterOptions: ChapterOption[];
  savingTarget: string;
  placingTarget: string;
  deletingTarget: string;
}

interface ImageAssetDataset {
  id?: string;
}

type DatasetEvent = PageDatasetEvent<ImageAssetDataset>;
type PickerEvent = PagePickerEvent<ImageAssetDataset>;
type TextInputEvent = PageTextInputEvent<ImageAssetDataset>;

function trimText(value: string | null | undefined): string {
  return (value || "").trim();
}

function sourceLabel(asset: MemoirImageAsset): string {
  const labels: Record<string, string> = {
    share_video_background: "分享视频生成图",
    chapter_illustration: "章节插图",
    utterance_illustration: "讲述插图",
    cover_candidate: "封面候选",
    manual_upload: "手动上传",
  };
  return labels[asset.source_type] || "图片素材";
}

function statusText(asset: MemoirImageAsset): string {
  if (asset.status === "ready") {
    return asset.include_in_book ? "已入书" : "未使用";
  }
  if (asset.status === "failed") {
    return "生成失败";
  }
  if (asset.status === "running" || asset.status === "pending") {
    return "生成中";
  }
  return "已删除";
}

function firstSentence(text: string, maxLength = 36): string {
  const normalized = trimText(text).replace(/\s+/g, " ");
  if (!normalized) {
    return "";
  }
  for (let index = 0; index < normalized.length; index += 1) {
    if ("。！？!?".includes(normalized[index])) {
      return normalized.slice(0, Math.min(index + 1, maxLength));
    }
  }
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength)}...` : normalized;
}

function placementText(asset: MemoirImageAsset): string {
  const enabled = (asset.placements || []).filter((placement) => placement.enabled);
  if (!enabled.length) {
    return "尚未入书";
  }
  const chapterPlacements = enabled.filter((placement) => placement.chapter_id);
  const appendixCount = enabled.filter((placement) => placement.placement_type === "appendix").length;
  const parts = chapterPlacements
    .slice(0, 2)
    .map((placement) => `已放入：${placement.chapter_title || "未命名章节"}`);
  if (appendixCount) {
    parts.push("已加入书末图录");
  }
  const extra = enabled.length > parts.length ? `等 ${enabled.length} 处` : "";
  return [...parts, extra].filter(Boolean).join(" · ");
}

function assetTitle(asset: MemoirImageAsset): string {
  return (
    trimText(asset.title)
    || firstSentence(asset.caption)
    || firstSentence(asset.source_quote)
    || "未命名图片"
  );
}

function buildAssetViews(
  response: MemoirImageAssetListResponse | null,
  imagePaths: Record<string, string>,
): ImageAssetView[] {
  return (response?.items || []).map((asset) => ({
    ...asset,
    localPath: imagePaths[asset.id] || "",
    titleText: assetTitle(asset),
    sourceLabel: sourceLabel(asset),
    statusText: statusText(asset),
    placementText: placementText(asset),
    createdAtText: formatBeijingDateTimeShort(asset.created_at) || "",
    captionDraft: trimText(asset.caption),
    enabledPlacements: (asset.placements || []).filter((placement) => placement.enabled),
  }));
}

function chapterOptions(plan: MemoirBookPlan | null): ChapterOption[] {
  return [...(plan?.chapters || [])]
    .sort((left, right) => left.sort_order - right.sort_order)
    .map((chapter, index) => ({
      label: `第 ${index + 1} 章 ${chapter.title || "未命名章节"}`,
      value: chapter.id,
    }));
}

definePage<ImageAssetsData>()({
  data: {
    loading: false,
    error: "",
    projectId: "",
    total: 0,
    readyCount: 0,
    includedCount: 0,
    unusedCount: 0,
    items: [],
    imagePaths: {},
    chapterOptions: [],
    savingTarget: "",
    placingTarget: "",
    deletingTarget: "",
  },

  async onLoad(options: { projectId?: string }) {
    const projectId = trimText(options.projectId);
    if (projectId) {
      this.setData({ projectId });
    }
  },

  async onShow() {
    await this.loadImageAssets();
  },

  async ensureProjectId(): Promise<string> {
    if (this.data.projectId) {
      return this.data.projectId;
    }
    const identity = await getMemoirIdentity();
    const projectId = identity.active_project_id || "";
    if (!projectId) {
      throw new Error("请先创建回忆录");
    }
    this.setData({ projectId });
    return projectId;
  },

  async loadImageAssets() {
    this.setData({ loading: true, error: "" });
    try {
      const projectId = await this.ensureProjectId();
      const [response, plan] = await Promise.all([
        listMemoirImageAssets(projectId),
        this.loadBookPlan(projectId),
      ]);
      this.setData({
        total: response.total,
        readyCount: response.ready_count,
        includedCount: response.included_count,
        unusedCount: response.unused_count,
        chapterOptions: chapterOptions(plan),
        items: buildAssetViews(response, this.data.imagePaths),
      });
      void this.downloadVisibleImages(response.items);
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "图片素材加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async loadBookPlan(projectId: string): Promise<MemoirBookPlan | null> {
    try {
      return await getMemoirBookPlan(projectId);
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 404) {
        return null;
      }
      throw error;
    }
  },

  async downloadVisibleImages(assets: MemoirImageAsset[]) {
    const candidates = assets.filter((asset) => asset.status === "ready" && !this.data.imagePaths[asset.id]);
    await Promise.all(candidates.slice(0, 24).map(async (asset) => {
      try {
        const localPath = await downloadMemoirImageAssetFile(this.data.projectId, asset.id, true);
        const imagePaths = { ...this.data.imagePaths, [asset.id]: localPath };
        this.setData({
          imagePaths,
          items: this.data.items.map((item) => (
            item.id === asset.id ? { ...item, localPath } : item
          )),
        });
      } catch (_error) {
        // 缩略图失败时保留卡片文字信息，用户点击预览会再次显式报错。
      }
    }));
  },

  assetById(assetId: string): ImageAssetView | null {
    return this.data.items.find((item) => item.id === assetId) || null;
  },

  async previewAsset(event: DatasetEvent) {
    const assetId = datasetString(event, "id");
    const asset = this.assetById(assetId);
    if (!asset) {
      return;
    }
    try {
      const current = asset.localPath || await downloadMemoirImageAssetFile(this.data.projectId, asset.id, false);
      wx.previewImage({ current, urls: [current] });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "图片预览失败" });
    }
  },

  onCaptionInput(event: TextInputEvent) {
    const assetId = datasetString(event, "id");
    const value = event.detail.value;
    this.setData({
      items: this.data.items.map((item) => (
        item.id === assetId ? { ...item, captionDraft: value } : item
      )),
    });
  },

  async saveCaption(event: DatasetEvent) {
    const assetId = datasetString(event, "id");
    const asset = this.assetById(assetId);
    if (!asset) {
      return;
    }
    this.setData({ savingTarget: assetId, error: "" });
    try {
      const updated = await updateMemoirImageAsset(this.data.projectId, assetId, {
        caption: asset.captionDraft,
      });
      this.replaceAsset(updated);
      wx.showToast({ title: "已保存", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "图片说明保存失败" });
    } finally {
      this.setData({ savingTarget: "" });
    }
  },

  async placeInChapter(event: PickerEvent) {
    const assetId = datasetString(event, "id");
    const index = pickerIndex(event);
    const option = this.data.chapterOptions[index];
    if (!assetId || !option) {
      return;
    }
    await this.createPlacement(assetId, {
      placement_type: "chapter_opening",
      chapter_id: option.value,
      display_size: "full_width",
    });
  },

  async placeInAppendix(event: DatasetEvent) {
    const assetId = datasetString(event, "id");
    if (!assetId) {
      return;
    }
    await this.createPlacement(assetId, {
      placement_type: "appendix",
      display_size: "full_width",
    });
  },

  async createPlacement(
    assetId: string,
    data: MemoirBookImagePlacementCreateRequest,
  ) {
    this.setData({ placingTarget: assetId, error: "" });
    try {
      const updated = await createMemoirBookImagePlacement(this.data.projectId, assetId, data);
      this.replaceAsset(updated);
      wx.showToast({ title: "已加入书稿", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "设置插图失败" });
    } finally {
      this.setData({ placingTarget: "" });
    }
  },

  async removeFromBook(event: DatasetEvent) {
    const assetId = datasetString(event, "id");
    const asset = this.assetById(assetId);
    if (!asset || !asset.enabledPlacements.length) {
      return;
    }
    this.setData({ placingTarget: assetId, error: "" });
    try {
      let updated: MemoirImageAsset | null = null;
      for (const placement of asset.enabledPlacements) {
        updated = await deleteMemoirBookImagePlacement(this.data.projectId, assetId, placement.id);
      }
      if (updated) {
        this.replaceAsset(updated);
      }
      wx.showToast({ title: "已移出书稿", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "移出书稿失败" });
    } finally {
      this.setData({ placingTarget: "" });
    }
  },

  async deleteAsset(event: DatasetEvent) {
    const assetId = datasetString(event, "id");
    const asset = this.assetById(assetId);
    if (!asset) {
      return;
    }
    const confirmed = await new Promise<boolean>((resolve) => {
      wx.showModal({
        title: "删除图片素材",
        content: "删除后会从图片素材库移除；已经生成的历史书稿不会自动删除图片说明，当前最新版会移出这张图。",
        confirmText: "删除",
        confirmColor: "#b84a5a",
        success: (result) => resolve(Boolean(result.confirm)),
        fail: () => resolve(false),
      });
    });
    if (!confirmed) {
      return;
    }
    this.setData({ deletingTarget: assetId, error: "" });
    try {
      await deleteMemoirImageAsset(this.data.projectId, assetId);
      const nextItems = this.data.items.filter((item) => item.id !== assetId);
      const readyCount = nextItems.filter((item) => item.status === "ready").length;
      const includedCount = nextItems.filter((item) => item.include_in_book).length;
      this.setData({
        items: nextItems,
        total: nextItems.length,
        readyCount,
        includedCount,
        unusedCount: Math.max(0, readyCount - includedCount),
      });
      wx.showToast({ title: "已删除", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "删除图片失败" });
    } finally {
      this.setData({ deletingTarget: "" });
    }
  },

  replaceAsset(asset: MemoirImageAsset) {
    const updatedView = buildAssetViews(
      {
        items: [asset],
        total: 1,
        ready_count: asset.status === "ready" ? 1 : 0,
        included_count: asset.include_in_book ? 1 : 0,
        unused_count: asset.status === "ready" && !asset.include_in_book ? 1 : 0,
      },
      this.data.imagePaths,
    )[0];
    const nextItems = this.data.items.map((item) => (
      item.id === asset.id ? updatedView : item
    ));
    const readyCount = nextItems.filter((item) => item.status === "ready").length;
    const includedCount = nextItems.filter((item) => item.include_in_book).length;
    this.setData({
      items: nextItems,
      readyCount,
      includedCount,
      unusedCount: Math.max(0, readyCount - includedCount),
    });
  },
});
