export interface ShareVideoBackgroundSettings {
  backgroundSource: string;
  backgroundStyle: string;
  backgroundMode: string;
}

const STORAGE_KEY = "pawtale_share_video_background_settings";

function emptyShareVideoBackgroundSettings(): ShareVideoBackgroundSettings {
  return {
    backgroundSource: "",
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
    backgroundSource: storedSelection(candidate.backgroundSource),
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
