import type { MemoirShareVideoBackgroundStyle } from "../domain/memoir-transcript";

const STYLE_PREVIEWS: Record<string, string> = {
  watercolor: "/assets/share-video-styles/watercolor.jpg",
  ink_paper: "/assets/share-video-styles/ink-paper.jpg",
  old_album: "/assets/share-video-styles/old-album.jpg",
};

export function illustrationStyleViews(options: MemoirShareVideoBackgroundStyle[]) {
  return options.map((option) => ({ ...option, previewImage: STYLE_PREVIEWS[option.value] || "" }));
}

export interface ShareVideoBackgroundSettings {
  backgroundStyle: string;
  backgroundMode: string;
}

const STORAGE_KEY = "pawtale_share_video_background_settings";

function emptyShareVideoBackgroundSettings(): ShareVideoBackgroundSettings {
  return {
    backgroundStyle: "",
    backgroundMode: "",
  };
}

function storedSelection(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function normalizeStoredSettings(value: unknown): ShareVideoBackgroundSettings {
  if (!value || typeof value !== "object") {
    return emptyShareVideoBackgroundSettings();
  }
  const candidate = value as Partial<ShareVideoBackgroundSettings>;
  return {
    backgroundStyle: storedSelection(candidate.backgroundStyle),
    backgroundMode: storedSelection(candidate.backgroundMode),
  };
}

export function readShareVideoBackgroundSettings(): ShareVideoBackgroundSettings {
  if (typeof wx === "undefined" || typeof wx.getStorageSync !== "function") {
    return emptyShareVideoBackgroundSettings();
  }
  try {
    return normalizeStoredSettings(
      wx.getStorageSync<ShareVideoBackgroundSettings>(STORAGE_KEY),
    );
  } catch (error) {
    console.warn("read share video background settings failed", error);
    return emptyShareVideoBackgroundSettings();
  }
}

export function saveShareVideoBackgroundSettings(
  value: ShareVideoBackgroundSettings,
): ShareVideoBackgroundSettings {
  const settings = normalizeStoredSettings(value);
  if (typeof wx !== "undefined" && typeof wx.setStorageSync === "function") {
    wx.setStorageSync(STORAGE_KEY, settings);
  }
  return settings;
}
