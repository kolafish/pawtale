import type {
  MemoirBookImagePlacement,
  MemoirBookImagePlacementCreateRequest,
  MemoirBookPlan,
  MemoirBookPlanChapter,
  MemoirImageAsset,
} from "../../domain/memoir-book";
import {
  accessibilityPageClass,
  accessibilityPagePatch,
} from "../../services/accessibility-settings";
import { definePage } from "../../services/page-definition";
import { getMemoirIdentity } from "../../services/auth";
import {
  createMemoirBookImagePlacement,
  deleteMemoirBookImagePlacement,
  deleteMemoirImageAsset,
  downloadMemoirImageAssetFile,
  getMemoirBookPlan,
  listMemoirImageAssets,
} from "../../services/memoir-book-api";
import { getMemoirBookSnapshot } from "../../services/memoir-snapshot-api";
import {
  downloadMemoirShareVideoJob,
  getMemoirShareVideoJob,
} from "../../services/memoir-transcript-api";
import { optionalApiResource } from "../../services/request";
import { routeAuthError } from "../../services/navigation";
import {
  datasetString,
  pageUrl,
  pickerIndex,
  queryParam,
  type PageDatasetEvent,
  type PagePickerEvent,
  type PageQuery,
} from "../../services/page-input";
import { setPageNavigationTitle } from "../../services/page-navigation";
import { initialVideoPlaybackData, videoPlaybackMethods, type VideoPlaybackData } from "../../services/video-playback";
import {
  buildStoryMediaIndex,
  type StoryVideoAsset,
} from "../../services/story-media-index";

interface PlacementOption {
  label: string;
  value: string;
}

type ThumbnailState = "idle" | "loading" | "ready" | "failed";

interface ImageAssetView extends MemoirImageAsset {
  localPath: string;
  thumbnailState: ThumbnailState;
  thumbnailError: string;
  titleText: string;
  statusText: string;
  placementText: string;
  detailsExpanded: boolean;
  inlinePositionIndex: number;
  sourceIds: string[];
  enabledPlacements: MemoirBookImagePlacement[];
}

interface VideoAssetView {
  id: string;
  utteranceId: string;
  title: string;
  detail: string;
  isCurrent: boolean;
}

type StoryMaterialScope = "" | "chapter" | "section" | "unassigned";

interface ImageAssetsData extends VideoPlaybackData {
  pageClass: string;
  loading: boolean;
  error: string;
  projectId: string;
  total: number;
  includedCount: number;
  unusedCount: number;
  items: ImageAssetView[];
  chapterOptions: PlacementOption[];
  materialOptions: PlacementOption[];
  placingTarget: string;
  deletingTarget: string;
  isStoryMaterialView: boolean;
  scope: StoryMaterialScope;
  chapterId: string;
  sectionId: string;
  contextText: string;
  videos: VideoAssetView[];
  previewingVideoId: string;
  videoPreviewPath: string;
  videoPreviewTitle: string;
  videoPreviewPortrait: boolean;
}

interface ImageAssetDataset {
  id?: string;
}

type DatasetEvent = PageDatasetEvent<ImageAssetDataset>;
type PickerEvent = PagePickerEvent<ImageAssetDataset>;

const THUMBNAIL_BATCH_SIZE = 4;
const MAX_AUTO_DOWNLOADED_THUMBNAILS = 24;
const PAGE_CLASS = "image-assets-page ui-page-shell ui-accessible-page";

function trimText(value: string | null | undefined): string {
  return (value || "").trim();
}

function statusText(asset: MemoirImageAsset): string {
  if (asset.status === "ready") {
    return asset.include_in_book ? "已入书" : "未入书";
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
    .map((placement) => (
      placement.placement_type === "chapter_inline"
        ? `已放在素材${({ auto: "附近自动分散", before: "前面", middle: "中间", after: "后面" })[placement.inline_position || "auto"]}：${placement.chapter_title || "未命名章节"}`
        : `已放在章节开头：${placement.chapter_title || "未命名章节"}`
    ));
  if (appendixCount) {
    parts.push("已放在书末相册");
  }
  if (enabled.some((placement) => placement.placement_type === "cover")) {
    parts.push("已用于封面");
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

function buildAssetView(
  asset: MemoirImageAsset,
  current?: ImageAssetView,
): ImageAssetView {
  const localPath = current?.localPath || "";
  return {
    ...asset,
    localPath,
    thumbnailState: localPath ? "ready" : (current?.thumbnailState || "idle"),
    thumbnailError: localPath ? "" : (current?.thumbnailError || ""),
    titleText: assetTitle(asset),
    statusText: statusText(asset),
    placementText: placementText(asset),
    detailsExpanded: current?.detailsExpanded || false,
    sourceIds: asset.placements.find(item => item.enabled && item.placement_type === "chapter_inline")?.source_utterance_ids
      || asset.suggested_source_utterance_ids || (asset.source_utterance_id ? [asset.source_utterance_id] : []),
    inlinePositionIndex: ["auto", "before", "middle", "after"].indexOf(asset.placements.find(item => item.enabled && item.placement_type === "chapter_inline")?.inline_position || "auto"),
    enabledPlacements: (asset.placements || []).filter((placement) => placement.enabled),
  };
}

function buildAssetViews(
  assets: MemoirImageAsset[],
  currentItems: ImageAssetView[],
): ImageAssetView[] {
  const currentById = new Map(currentItems.map((item) => [item.id, item]));
  return assets.map((asset) => buildAssetView(asset, currentById.get(asset.id)));
}

function shortText(value: string, maxLength = 42): string {
  const text = value.trim().replace(/\s+/g, " ");
  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text;
}

function materialTextByUtterance(
  chapters: MemoirBookPlanChapter[],
): Map<string, string> {
  return new Map(
    chapters.flatMap((chapter) => chapter.sections.flatMap(
      (section) => section.materials.map(
        (material) => [material.utterance_id, material.text] as const,
      ),
    )),
  );
}

function videoViews(
  videos: StoryVideoAsset[],
  materialText: Map<string, string>,
): VideoAssetView[] {
  return videos.map((video, index) => ({
    id: video.id,
    utteranceId: video.utteranceId,
    title: video.title?.trim() || `分享视频 ${index + 1}`,
    detail: shortText(materialText.get(video.utteranceId) || "") || "点按播放视频",
    isCurrent: video.isCurrent,
  }));
}

function assetCountData(items: ImageAssetView[]): Pick<
  ImageAssetsData,
  "total" | "includedCount" | "unusedCount"
> {
  const readyCount = items.filter((item) => item.status === "ready").length;
  const includedCount = items.filter((item) => item.include_in_book).length;
  return {
    total: items.length,
    includedCount,
    unusedCount: Math.max(0, readyCount - includedCount),
  };
}

function chapterOptions(plan: MemoirBookPlan | null): PlacementOption[] {
  return [...(plan?.chapters || [])]
    .filter(chapter => chapter.include_in_book)
    .sort((left, right) => left.sort_order - right.sort_order)
    .map((chapter, index) => ({
      label: `第 ${index + 1} 章 ${chapter.title || "未命名章节"}`,
      value: chapter.id,
    }));
}

function materialOptions(plan: MemoirBookPlan | null): PlacementOption[] {
  return [...(plan?.chapters || [])].filter(chapter => chapter.include_in_book)
    .sort((a, b) => a.sort_order - b.sort_order)
    .flatMap(chapter => [...chapter.sections]
      .filter(section => section.status === "active" && section.include_in_book)
      .sort((a, b) => a.sort_order - b.sort_order)
      .flatMap(section => [...section.materials].filter(material => material.include_in_book)
        .sort((a, b) => a.sort_order - b.sort_order)
        .map(material => ({ value: material.utterance_id,
          label: `${chapter.title} · ${firstSentence(material.text, 28) || section.title}` }))),
    );
}

definePage<ImageAssetsData>()({
  loadToken: 0,
  videoPreviewPaths: {} as Record<string, string>,

  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    loading: false,
    error: "",
    projectId: "",
    total: 0,
    includedCount: 0,
    unusedCount: 0,
    items: [],
    chapterOptions: [],
    materialOptions: [],
    placingTarget: "",
    deletingTarget: "",
    isStoryMaterialView: false,
    scope: "",
    chapterId: "",
    sectionId: "",
    contextText: "统一管理生成图片，选择可入书的插图",
    videos: [],
    previewingVideoId: "",
    videoPreviewPath: "",
    videoPreviewTitle: "",
    videoPreviewPortrait: true,
    ...initialVideoPlaybackData(),
  },

  ...videoPlaybackMethods,

  onLoad(options: PageQuery = {}) {
    const scopeValue = queryParam(options, "scope");
    const scope: StoryMaterialScope = (
      scopeValue === "chapter" || scopeValue === "section" || scopeValue === "unassigned"
        ? scopeValue
        : ""
    );
    this.setData({
      projectId: queryParam(options, "projectId"),
      isStoryMaterialView: Boolean(scope),
      scope,
      chapterId: queryParam(options, "chapterId"),
      sectionId: queryParam(options, "sectionId"),
    });
  },

  async onShow() {
    this.setData(accessibilityPagePatch(PAGE_CLASS));
    await this.loadImageAssets();
  },

  onUnload() {
    this.loadToken += 1;
  },

  onPhotoCaptionSaved(event: { detail: { asset: MemoirImageAsset } }) {
    const asset = event.detail.asset;
    this.setData({ items: this.data.items.map(item => item.id === asset.id ? { ...item, caption: asset.caption } : item) });
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
    this.loadToken += 1;
    const requestToken = this.loadToken;
    this.setData({ loading: true, error: "" });
    try {
      const projectId = await this.ensureProjectId();
      if (this.data.isStoryMaterialView) {
        const snapshot = await getMemoirBookSnapshot();
        const activeProjectId = snapshot.identity.active_project?.id || "";
        if (!activeProjectId || activeProjectId !== projectId) {
          throw new Error("当前故事已经切换，请返回故事页重新进入");
        }
        const chapters = [...(snapshot.plan?.chapters || [])]
          .sort((left, right) => left.sort_order - right.sort_order);
        const media = buildStoryMediaIndex(
          chapters,
          snapshot.image_assets.items || [],
          snapshot.share_video_counts || [],
        );
        const chapter = chapters.find((item) => item.id === this.data.chapterId);
        const section = chapter?.sections.find((item) => item.id === this.data.sectionId);
        const assets = this.data.scope === "section"
          ? (media.sectionImages[this.data.sectionId] || [])
          : this.data.scope === "chapter"
            ? (media.chapterImages[this.data.chapterId] || [])
            : media.unassignedImages;
        const videos = this.data.scope === "section"
          ? (media.sectionVideos[this.data.sectionId] || [])
          : this.data.scope === "chapter"
            ? (media.chapterVideos[this.data.chapterId] || [])
            : media.unassignedVideos;
        const title = this.data.scope === "section"
          ? (section?.title || "小节素材")
          : this.data.scope === "chapter"
            ? `${chapter?.title || "本章"}的素材`
            : "待整理素材";
        const items = buildAssetViews(assets, this.data.items);
        if (requestToken !== this.loadToken || projectId !== this.data.projectId) {
          return;
        }
        setPageNavigationTitle(title);
        this.setData({
          ...assetCountData(items),
          total: items.length,
          chapterOptions: chapterOptions(snapshot.plan || null),
          materialOptions: materialOptions(snapshot.plan || null),
          contextText: "查看这里已有的图片和分享视频；点按素材后再预览",
          items,
          videos: videoViews(videos, materialTextByUtterance(chapters)),
        });
        void this.downloadVisibleImages(assets, projectId, requestToken);
        return;
      }
      const [response, plan] = await Promise.all([
        listMemoirImageAssets(projectId),
        optionalApiResource(getMemoirBookPlan(projectId)),
      ]);
      if (requestToken !== this.loadToken || projectId !== this.data.projectId) {
        return;
      }
      this.setData({
        total: response.total,
        includedCount: response.included_count,
        unusedCount: response.unused_count,
        chapterOptions: chapterOptions(plan),
        materialOptions: materialOptions(plan),
        items: buildAssetViews(response.items, this.data.items),
        videos: [],
      });
      void this.downloadVisibleImages(response.items, projectId, requestToken);
    } catch (error) {
      if (requestToken !== this.loadToken) {
        return;
      }
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "图片素材加载失败" });
    } finally {
      if (requestToken === this.loadToken) {
        this.setData({ loading: false });
      }
    }
  },

  async downloadVisibleImages(
    assets: MemoirImageAsset[],
    projectId: string,
    requestToken: number,
  ) {
    const downloadedIds = new Set(
      this.data.items.filter((item) => item.localPath).map((item) => item.id),
    );
    const candidates = assets
      .filter((asset) => asset.status === "ready" && !downloadedIds.has(asset.id))
      .slice(0, MAX_AUTO_DOWNLOADED_THUMBNAILS);
    const candidateIds = new Set(candidates.map((asset) => asset.id));
    if (candidateIds.size) {
      this.setData({
        items: this.data.items.map((item) => (
          candidateIds.has(item.id)
            ? { ...item, thumbnailState: "loading", thumbnailError: "" }
            : item
        )),
      });
    }
    for (let offset = 0; offset < candidates.length; offset += THUMBNAIL_BATCH_SIZE) {
      const batch = candidates.slice(offset, offset + THUMBNAIL_BATCH_SIZE);
      const results = await Promise.all(batch.map(async (asset) => {
        try {
          const localPath = await downloadMemoirImageAssetFile(projectId, asset.id, true);
          return {
            assetId: asset.id,
            localPath,
            error: "",
          };
        } catch (error) {
          return {
            assetId: asset.id,
            localPath: "",
            error: error instanceof Error ? error.message : "图片预览加载失败",
          };
        }
      }));
      if (requestToken !== this.loadToken || projectId !== this.data.projectId) {
        return;
      }
      const resultById = new Map(results.map((result) => [result.assetId, result]));
      this.setData({
        items: this.data.items.map((item) => {
          const result = resultById.get(item.id);
          if (!result) {
            return item;
          }
          return {
            ...item,
            localPath: result.localPath,
            thumbnailState: result.localPath ? "ready" : "failed",
            thumbnailError: result.error,
          };
        }),
      });
    }
  },

  assetById(assetId: string): ImageAssetView | null {
    return this.data.items.find((item) => item.id === assetId) || null;
  },

  async previewAsset(event: DatasetEvent) {
    const assetId = datasetString(event, "id");
    const asset = this.assetById(assetId);
    if (!asset || asset.status !== "ready") {
      return;
    }
    if (asset.localPath) {
      wx.previewImage({ current: asset.localPath, urls: [asset.localPath] });
      return;
    }
    const projectId = this.data.projectId;
    const requestToken = this.loadToken;
    this.setData({
      error: "",
      items: this.data.items.map((item) => (
        item.id === assetId
          ? { ...item, thumbnailState: "loading", thumbnailError: "" }
          : item
      )),
    });
    try {
      const localPath = await downloadMemoirImageAssetFile(projectId, asset.id, true);
      if (requestToken !== this.loadToken || projectId !== this.data.projectId) {
        return;
      }
      this.setData({
        items: this.data.items.map((item) => (
          item.id === assetId
            ? {
              ...item,
              localPath,
              thumbnailState: "ready",
              thumbnailError: "",
            }
            : item
        )),
      });
      wx.previewImage({ current: localPath, urls: [localPath] });
    } catch (error) {
      if (requestToken !== this.loadToken) {
        return;
      }
      const message = error instanceof Error ? error.message : "图片预览失败";
      this.setData({
        error: message,
        items: this.data.items.map((item) => (
          item.id === assetId
            ? {
              ...item,
              localPath: "",
              thumbnailState: "failed",
              thumbnailError: message,
            }
            : item
        )),
      });
    }
  },

  async previewVideo(event: DatasetEvent) {
    const videoId = datasetString(event, "id");
    if (!videoId || this.data.previewingVideoId) {
      return;
    }
    const video = this.data.videos.find((item) => item.id === videoId);
    this.setData({ previewingVideoId: videoId, error: "" });
    try {
      let path = this.videoPreviewPaths[videoId];
      if (!path) {
        path = await downloadMemoirShareVideoJob(await getMemoirShareVideoJob(videoId));
        this.videoPreviewPaths[videoId] = path;
      }
      this.setData({
        videoPreviewPath: path,
        videoPreviewTitle: video?.title || "分享视频",
        videoSpeedMenuOpen: false,
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "视频预览失败" });
    } finally {
      this.setData({ previewingVideoId: "" });
    }
  },

  markVideoPreviewShape(event: { detail: { width: number; height: number } }) {
    this.setData({ videoPreviewPortrait: event.detail.height >= event.detail.width });
  },

  closeVideoPreview() {
    this.setData({ videoPreviewPath: "", videoPreviewTitle: "", videoSpeedMenuOpen: false });
  },

  remakeVideo(event: DatasetEvent) {
    const video = this.data.videos.find((item) => item.id === datasetString(event, "id"));
    if (!video?.utteranceId) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/transcript-media/index", { id: video.utteranceId }),
    });
  },

  onThumbnailError(event: DatasetEvent) {
    const assetId = datasetString(event, "id");
    this.setData({
      items: this.data.items.map((item) => (
        item.id === assetId
          ? {
            ...item,
            localPath: "",
            thumbnailState: "failed",
            thumbnailError: "图片文件暂时无法显示，点按重试",
          }
          : item
      )),
    });
  },

  toggleAssetDetails(event: DatasetEvent) {
    const assetId = datasetString(event, "id");
    this.setData({
      items: this.data.items.map((item) => (
        item.id === assetId
          ? { ...item, detailsExpanded: !item.detailsExpanded }
          : item
      )),
    });
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

  async placeNearSource(event: DatasetEvent) {
    const assetId = datasetString(event, "id");
    const asset = this.assetById(assetId);
    if (!asset || asset.include_in_book || this.data.placingTarget || this.data.deletingTarget) return;
    if (!asset.sourceIds.length) {
      this.setData({ items: this.data.items.map(item => item.id === assetId ? { ...item, detailsExpanded: true } : item) });
      return;
    }
    await this.createPlacement(assetId, { placement_type: "chapter_inline", source_utterance_ids: asset.sourceIds,
      inline_position: "auto", display_size: "full_width" });
  },

  async chooseSourcePosition(event: PickerEvent) {
    const assetId = datasetString(event, "id");
    const asset = this.assetById(assetId);
    if (!asset?.sourceIds.length) return;
    await this.createPlacement(assetId, {
      placement_type: "chapter_inline",
      source_utterance_ids: asset.sourceIds,
      inline_position: (["auto", "before", "middle", "after"] as const)[pickerIndex(event)] || "auto",
      display_size: "full_width",
    });
  },

  async chooseSourceMaterial(event: PickerEvent) {
    const assetId = datasetString(event, "id");
    const option = this.data.materialOptions[pickerIndex(event)];
    if (!assetId || !option) return;
    await this.createPlacement(assetId, { placement_type: "chapter_inline",
      source_utterance_ids: [option.value], inline_position: "auto", display_size: "full_width" });
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
    if (this.data.placingTarget || this.data.deletingTarget) return;
    const asset = this.assetById(assetId);
    if (!asset || asset.status !== "ready") return;
    this.setData({ placingTarget: assetId, error: "" });
    try {
      const updated = await createMemoirBookImagePlacement(this.data.projectId, assetId, {
        ...data, replace_body_placements: true,
      });
      this.replaceAsset(updated);
      this.setData({ items: this.data.items.map(item => item.id === assetId ? { ...item, detailsExpanded: false } : item) });
      wx.showToast({ title: asset.include_in_book ? "已调整位置" : "已加入书稿", icon: "success" });
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
    if (!asset || !asset.enabledPlacements.length || this.data.placingTarget || this.data.deletingTarget) {
      return;
    }
    this.setData({ placingTarget: assetId, error: "" });
    try {
      for (const placement of asset.enabledPlacements) {
        const updated = await deleteMemoirBookImagePlacement(this.data.projectId, assetId, placement.id);
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
    if (this.data.placingTarget || this.data.deletingTarget) return;
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
        confirmColor: "#b64545",
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
      this.setData({
        items: nextItems,
        ...assetCountData(nextItems),
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
    const current = this.assetById(asset.id);
    const updatedView = buildAssetView(asset, current || undefined);
    const nextItems = this.data.items.map((item) => (
      item.id === asset.id ? updatedView : item
    ));
    this.setData({
      items: nextItems,
      ...assetCountData(nextItems),
    });
  },
});
