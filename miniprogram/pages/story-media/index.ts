import type {
  MemoirBookPlanChapter,
  MemoirImageAsset,
} from "../../domain/memoir-book";
import { accessibilityPageClass } from "../../services/accessibility-settings";
import {
  downloadMemoirImageAssetFile,
} from "../../services/memoir-book-api";
import { getMemoirBookSnapshot } from "../../services/memoir-snapshot-api";
import {
  downloadMemoirShareVideoJob,
  getMemoirShareVideoJob,
} from "../../services/memoir-transcript-api";
import { routeAuthError } from "../../services/navigation";
import { definePage } from "../../services/page-definition";
import {
  datasetString,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
} from "../../services/page-input";
import { setPageNavigationTitle } from "../../services/page-navigation";
import {
  buildStoryMediaIndex,
  type StoryVideoAsset,
} from "../story/story-media-index";

type StoryMediaScope = "chapter" | "section" | "unassigned";

interface StoryImageView {
  id: string;
  title: string;
  detail: string;
}

interface StoryVideoView {
  id: string;
  title: string;
  detail: string;
}

interface StoryMediaPageData {
  pageClass: string;
  loading: boolean;
  error: string;
  projectId: string;
  scope: StoryMediaScope;
  chapterId: string;
  sectionId: string;
  title: string;
  images: StoryImageView[];
  videos: StoryVideoView[];
  previewingImageId: string;
  previewingVideoId: string;
  videoPreviewPath: string;
  videoPreviewTitle: string;
}

type DatasetEvent = PageDatasetEvent<{ id?: string }>;

const PAGE_CLASS = "story-media-page ui-page-shell ui-accessible-page";

function shortText(value: string, maxLength = 42): string {
  const text = value.trim().replace(/\s+/g, " ");
  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text;
}

function imageViews(assets: MemoirImageAsset[]): StoryImageView[] {
  return assets.map((asset, index) => ({
    id: asset.id,
    title: asset.title.trim() || `图片 ${index + 1}`,
    detail: shortText(asset.caption || asset.source_quote) || "点击查看图片",
  }));
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
): StoryVideoView[] {
  return videos.map((video, index) => ({
    id: video.id,
    title: `分享视频 ${index + 1}`,
    detail: shortText(materialText.get(video.utteranceId) || "") || "点击播放视频",
  }));
}

definePage<StoryMediaPageData>()({
  imagePreviewPaths: {} as Record<string, string>,
  videoPreviewPaths: {} as Record<string, string>,

  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    loading: true,
    error: "",
    projectId: "",
    scope: "unassigned",
    chapterId: "",
    sectionId: "",
    title: "故事素材",
    images: [],
    videos: [],
    previewingImageId: "",
    previewingVideoId: "",
    videoPreviewPath: "",
    videoPreviewTitle: "",
  },

  async onLoad(query: PageQuery = {}) {
    const scopeValue = queryParam(query, "scope");
    const scope: StoryMediaScope = (
      scopeValue === "chapter" || scopeValue === "section"
        ? scopeValue
        : "unassigned"
    );
    this.setData({
      projectId: queryParam(query, "projectId"),
      scope,
      chapterId: queryParam(query, "chapterId"),
      sectionId: queryParam(query, "sectionId"),
    });
    await this.loadMedia();
  },

  async loadMedia() {
    this.setData({ loading: true, error: "" });
    try {
      const snapshot = await getMemoirBookSnapshot();
      const activeProjectId = snapshot.identity.active_project?.id || "";
      if (!activeProjectId || activeProjectId !== this.data.projectId) {
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
      const images = this.data.scope === "section"
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
      setPageNavigationTitle(title);
      this.setData({
        title,
        images: imageViews(images),
        videos: videoViews(videos, materialTextByUtterance(chapters)),
        loading: false,
      });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        loading: false,
        error: error instanceof Error ? error.message : "素材加载失败",
      });
    }
  },

  async previewImage(event: DatasetEvent) {
    const id = datasetString(event, "id");
    if (!id || this.data.previewingImageId) {
      return;
    }
    this.setData({ previewingImageId: id, error: "" });
    try {
      const path = this.imagePreviewPaths[id]
        || await downloadMemoirImageAssetFile(this.data.projectId, id);
      this.imagePreviewPaths[id] = path;
      wx.previewImage({ current: path, urls: [path] });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "图片预览失败" });
    } finally {
      this.setData({ previewingImageId: "" });
    }
  },

  async previewVideo(event: DatasetEvent) {
    const id = datasetString(event, "id");
    if (!id || this.data.previewingVideoId) {
      return;
    }
    const item = this.data.videos.find((video) => video.id === id);
    this.setData({ previewingVideoId: id, error: "" });
    try {
      let path = this.videoPreviewPaths[id];
      if (!path) {
        path = await downloadMemoirShareVideoJob(await getMemoirShareVideoJob(id));
        this.videoPreviewPaths[id] = path;
      }
      this.setData({
        videoPreviewPath: path,
        videoPreviewTitle: item?.title || "分享视频",
      });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "视频预览失败" });
    } finally {
      this.setData({ previewingVideoId: "" });
    }
  },

  closeVideoPreview() {
    this.setData({ videoPreviewPath: "", videoPreviewTitle: "" });
  },
});
