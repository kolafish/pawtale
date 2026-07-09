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

export interface ShareVideoBackgroundSettings {
  backgroundStyle: string;
  backgroundMode: string;
}

const STORAGE_KEY = "pawtale_share_video_background_settings";

export const DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE = "old_album";
export const DEFAULT_SHARE_VIDEO_BACKGROUND_MODE = "auto";

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

function defaultShareVideoBackgroundSettings(): ShareVideoBackgroundSettings {
  return {
    backgroundStyle: DEFAULT_SHARE_VIDEO_BACKGROUND_STYLE,
    backgroundMode: DEFAULT_SHARE_VIDEO_BACKGROUND_MODE,
  };
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

export function normalizeShareVideoBackgroundSettings(value: unknown): ShareVideoBackgroundSettings {
  if (!value || typeof value !== "object") {
    return defaultShareVideoBackgroundSettings();
  }
  const candidate = value as Partial<ShareVideoBackgroundSettings>;
  return {
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

export function readSelectedShareVideoBackgroundMode(): string {
  return readShareVideoBackgroundSettings().backgroundMode;
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
