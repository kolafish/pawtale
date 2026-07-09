export interface ShareVideoBackgroundStyleOption {
  value: string;
  label: string;
  description: string;
}

export interface ShareVideoBackgroundModeOption {
  value: string;
  label: string;
  description: string;
}

export interface ShareVideoBackgroundSourceOption {
  value: string;
  label: string;
  description: string;
}

export interface ShareVideoBackgroundSettings {
  backgroundSource: string;
  backgroundStyle: string;
  backgroundMode: string;
}

const STORAGE_KEY = "pawtale_share_video_background_settings";

export const DEFAULT_SHARE_VIDEO_BACKGROUND_SOURCE = "ai";
export const DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE = "old_album";
export const DEFAULT_SHARE_VIDEO_BACKGROUND_MODE = "auto";

export const SHARE_VIDEO_BACKGROUND_SOURCE_OPTIONS: ShareVideoBackgroundSourceOption[] = [
  {
    value: "ai",
    label: "AI生成",
    description: "根据文字自动生成回忆录背景图，可选择单图或智能多图",
  },
  {
    value: "custom",
    label: "自定义图片",
    description: "上传自己的图片，自动处理成适合叠加文字的视频背景",
  },
  {
    value: "classic",
    label: "经典背景",
    description: "使用米白纸张质感背景，不生成也不上传图片",
  },
];

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

export const SHARE_VIDEO_BACKGROUND_MODE_OPTIONS: ShareVideoBackgroundModeOption[] = [
  {
    value: "auto",
    label: "自动",
    description: "20 秒以上自动使用多图，短片段使用单图",
  },
  {
    value: "single",
    label: "单张背景",
    description: "整段视频使用一张背景图，生成更快",
  },
  {
    value: "smart_multi",
    label: "智能多图",
    description: "按口述内容拆分画面，生成多张背景自动切换",
  },
];

const SHARE_VIDEO_BACKGROUND_STYLE_VALUES = new Set(
  SHARE_VIDEO_BACKGROUND_STYLE_OPTIONS.map((option) => option.value),
);
const SHARE_VIDEO_BACKGROUND_MODE_VALUES = new Set(
  SHARE_VIDEO_BACKGROUND_MODE_OPTIONS.map((option) => option.value),
);
const SHARE_VIDEO_BACKGROUND_SOURCE_VALUES = new Set(
  SHARE_VIDEO_BACKGROUND_SOURCE_OPTIONS.map((option) => option.value),
);

function defaultShareVideoBackgroundSettings(): ShareVideoBackgroundSettings {
  return {
    backgroundSource: DEFAULT_SHARE_VIDEO_BACKGROUND_SOURCE,
    backgroundStyle: DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE,
    backgroundMode: DEFAULT_SHARE_VIDEO_BACKGROUND_MODE,
  };
}

export function normalizeShareVideoBackgroundSource(value: unknown): string {
  if (typeof value !== "string") {
    return DEFAULT_SHARE_VIDEO_BACKGROUND_SOURCE;
  }
  const normalized = value.trim();
  return SHARE_VIDEO_BACKGROUND_SOURCE_VALUES.has(normalized)
    ? normalized
    : DEFAULT_SHARE_VIDEO_BACKGROUND_SOURCE;
}

export function normalizeShareVideoBackgroundStyle(value: unknown): string {
  if (typeof value !== "string") {
    return DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE;
  }
  const normalized = value.trim();
  return SHARE_VIDEO_BACKGROUND_STYLE_VALUES.has(normalized)
    ? normalized
    : DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE;
}

export function normalizeShareVideoBackgroundMode(value: unknown): string {
  if (typeof value !== "string") {
    return DEFAULT_SHARE_VIDEO_BACKGROUND_MODE;
  }
  const normalized = value.trim();
  return SHARE_VIDEO_BACKGROUND_MODE_VALUES.has(normalized)
    ? normalized
    : DEFAULT_SHARE_VIDEO_BACKGROUND_MODE;
}

export function shareVideoBackgroundStyleLabel(value: string): string {
  return SHARE_VIDEO_BACKGROUND_STYLE_OPTIONS.find((option) => option.value === value)?.label
    || SHARE_VIDEO_BACKGROUND_STYLE_OPTIONS[0].label;
}

export function shareVideoBackgroundModeLabel(value: string): string {
  return SHARE_VIDEO_BACKGROUND_MODE_OPTIONS.find((option) => option.value === value)?.label
    || SHARE_VIDEO_BACKGROUND_MODE_OPTIONS[0].label;
}

export function shareVideoBackgroundSourceLabel(value: string): string {
  return SHARE_VIDEO_BACKGROUND_SOURCE_OPTIONS.find((option) => option.value === value)?.label
    || SHARE_VIDEO_BACKGROUND_SOURCE_OPTIONS[0].label;
}

export function normalizeShareVideoBackgroundSettings(value: unknown): ShareVideoBackgroundSettings {
  if (!value || typeof value !== "object") {
    return defaultShareVideoBackgroundSettings();
  }
  const candidate = value as Partial<ShareVideoBackgroundSettings>;
  return {
    backgroundSource: normalizeShareVideoBackgroundSource(candidate.backgroundSource),
    backgroundStyle: normalizeShareVideoBackgroundStyle(candidate.backgroundStyle),
    backgroundMode: normalizeShareVideoBackgroundMode(candidate.backgroundMode),
  };
}

export function readShareVideoBackgroundSettings(): ShareVideoBackgroundSettings {
  if (typeof wx === "undefined" || typeof wx.getStorageSync !== "function") {
    return defaultShareVideoBackgroundSettings();
  }
  try {
    return normalizeShareVideoBackgroundSettings(
      wx.getStorageSync<ShareVideoBackgroundSettings>(STORAGE_KEY),
    );
  } catch (error) {
    console.warn("read share video background settings failed", error);
    return defaultShareVideoBackgroundSettings();
  }
}

export function readSelectedShareVideoBackgroundStyle(): string {
  return readShareVideoBackgroundSettings().backgroundStyle;
}

export function readSelectedShareVideoBackgroundSource(): string {
  return readShareVideoBackgroundSettings().backgroundSource;
}

export function readSelectedShareVideoBackgroundMode(): string {
  return readShareVideoBackgroundSettings().backgroundMode;
}

export function saveSelectedShareVideoBackgroundSource(backgroundSource: string): string {
  const normalized = normalizeShareVideoBackgroundSource(backgroundSource);
  const settings = readShareVideoBackgroundSettings();
  if (typeof wx !== "undefined" && typeof wx.setStorageSync === "function") {
    wx.setStorageSync(STORAGE_KEY, { ...settings, backgroundSource: normalized });
  }
  return normalized;
}

export function saveSelectedShareVideoBackgroundStyle(backgroundStyle: string): string {
  const normalized = normalizeShareVideoBackgroundStyle(backgroundStyle);
  const settings = readShareVideoBackgroundSettings();
  if (typeof wx !== "undefined" && typeof wx.setStorageSync === "function") {
    wx.setStorageSync(STORAGE_KEY, { ...settings, backgroundStyle: normalized });
  }
  return normalized;
}

export function saveSelectedShareVideoBackgroundMode(backgroundMode: string): string {
  const normalized = normalizeShareVideoBackgroundMode(backgroundMode);
  const settings = readShareVideoBackgroundSettings();
  if (typeof wx !== "undefined" && typeof wx.setStorageSync === "function") {
    wx.setStorageSync(STORAGE_KEY, { ...settings, backgroundMode: normalized });
  }
  return normalized;
}
