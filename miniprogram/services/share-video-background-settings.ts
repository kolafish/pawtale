export interface ShareVideoBackgroundStyleOption {
  value: string;
  label: string;
  description: string;
}

export interface ShareVideoBackgroundSettings {
  backgroundStyle: string;
}

const STORAGE_KEY = "pawtale_share_video_background_settings";

export const DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE = "old_album";

export const SHARE_VIDEO_BACKGROUND_STYLE_OPTIONS: ShareVideoBackgroundStyleOption[] = [
  {
    value: "old_album",
    label: "旧相册",
    description: "旧照片手工上色质感，适合家庭、亲人和童年生活片段",
  },
  {
    value: "ink_paper",
    label: "淡水墨",
    description: "淡水墨旧纸质感，适合故乡、晚年回看和人生总结",
  },
  {
    value: "storybook",
    label: "老故事书",
    description: "中国老连环画和旧书插图质感，适合具体故事和家庭事件",
  },
];

const SHARE_VIDEO_BACKGROUND_STYLE_VALUES = new Set(
  SHARE_VIDEO_BACKGROUND_STYLE_OPTIONS.map((option) => option.value),
);

export function normalizeShareVideoBackgroundStyle(value: unknown): string {
  if (typeof value !== "string") {
    return DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE;
  }
  const normalized = value.trim();
  return SHARE_VIDEO_BACKGROUND_STYLE_VALUES.has(normalized)
    ? normalized
    : DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE;
}

export function shareVideoBackgroundStyleLabel(value: string): string {
  return SHARE_VIDEO_BACKGROUND_STYLE_OPTIONS.find((option) => option.value === value)?.label
    || SHARE_VIDEO_BACKGROUND_STYLE_OPTIONS[0].label;
}

export function normalizeShareVideoBackgroundSettings(value: unknown): ShareVideoBackgroundSettings {
  if (!value || typeof value !== "object") {
    return { backgroundStyle: DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE };
  }
  const candidate = value as Partial<ShareVideoBackgroundSettings>;
  return {
    backgroundStyle: normalizeShareVideoBackgroundStyle(candidate.backgroundStyle),
  };
}

export function readShareVideoBackgroundSettings(): ShareVideoBackgroundSettings {
  if (typeof wx === "undefined" || typeof wx.getStorageSync !== "function") {
    return { backgroundStyle: DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE };
  }
  try {
    return normalizeShareVideoBackgroundSettings(
      wx.getStorageSync<ShareVideoBackgroundSettings>(STORAGE_KEY),
    );
  } catch (error) {
    console.warn("read share video background settings failed", error);
    return { backgroundStyle: DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE };
  }
}

export function readSelectedShareVideoBackgroundStyle(): string {
  return readShareVideoBackgroundSettings().backgroundStyle;
}

export function saveSelectedShareVideoBackgroundStyle(backgroundStyle: string): string {
  const normalized = normalizeShareVideoBackgroundStyle(backgroundStyle);
  if (typeof wx !== "undefined" && typeof wx.setStorageSync === "function") {
    wx.setStorageSync(STORAGE_KEY, { backgroundStyle: normalized });
  }
  return normalized;
}
