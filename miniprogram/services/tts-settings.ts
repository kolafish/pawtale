import type { MemoirBookAudioRequest } from "../domain/memoir-book";

export interface TTSVoiceOption {
  value: string;
  label: string;
  description: string;
}

export interface TTSSpeedOption {
  value: number;
  label: string;
  description: string;
}

export interface TTSSettings {
  voice: string;
  speedRatio: number;
}

export type TTSSettingsScope = "chat" | "book";

export interface ScopedTTSSettings {
  chat: TTSSettings;
  book: TTSSettings;
}

const STORAGE_KEY = "pawtale_memoir_tts_settings";
const DEFAULT_TTS_SPEED_RATIO = 1;
const DEFAULT_TTS_SETTINGS: TTSSettings = { voice: "", speedRatio: DEFAULT_TTS_SPEED_RATIO };

export const TTS_VOICE_OPTIONS: TTSVoiceOption[] = [
  {
    value: "",
    label: "默认音色",
    description: "跟随系统设置",
  },
  {
    value: "zh_female_vv_uranus_bigtts",
    label: "通用女声",
    description: "支持朗读文字高亮",
  },
  {
    value: "zh_female_xiaohe_uranus_bigtts",
    label: "小荷女声",
    description: "清晰自然",
  },
  {
    value: "zh_male_m191_uranus_bigtts",
    label: "通用男声",
    description: "适合访谈引导",
  },
];

export const BOOK_TTS_VOICE_OPTIONS: TTSVoiceOption[] = [
  { value: "", label: "默认音色", description: "温柔女声" },
  { value: "wenrounvsheng", label: "温柔女声", description: "柔和舒缓" },
  { value: "linjiajiejie", label: "邻家女声", description: "亲切自然" },
  { value: "cixingnansheng", label: "磁性男声", description: "沉稳温暖" },
];

const LEGACY_BOOK_VOICES: Record<string, string> = {
  zh_female_vv_uranus_bigtts: "wenrounvsheng",
  zh_female_xiaohe_uranus_bigtts: "linjiajiejie",
  zh_male_m191_uranus_bigtts: "cixingnansheng",
};

export const TTS_SPEED_OPTIONS: TTSSpeedOption[] = [
  {
    value: 0.7,
    label: "慢速 0.7x",
    description: "更慢更稳，适合长辈跟听",
  },
  {
    value: 1,
    label: "正常 1.0x",
    description: "使用默认语速",
  },
  {
    value: 1.5,
    label: "快速 1.5x",
    description: "更快播放，适合快速浏览",
  },
];

const VOICE_VALUES = new Set(TTS_VOICE_OPTIONS.map((option) => option.value));
const BOOK_VOICE_VALUES = new Set(BOOK_TTS_VOICE_OPTIONS.map((option) => option.value));
const SPEED_VALUES = new Set(TTS_SPEED_OPTIONS.map((option) => option.value));

export function normalizeTTSVoice(value: unknown, scope: TTSSettingsScope = "chat"): string {
  if (typeof value !== "string") {
    return "";
  }
  const voice = value.trim();
  if (scope === "book") {
    const migrated = LEGACY_BOOK_VOICES[voice] || voice;
    return BOOK_VOICE_VALUES.has(migrated) ? migrated : "";
  }
  return VOICE_VALUES.has(voice) ? voice : "";
}

export function normalizeTTSSpeedRatio(value: unknown): number {
  const ratio = Number(value);
  return SPEED_VALUES.has(ratio) ? ratio : DEFAULT_TTS_SPEED_RATIO;
}

export function normalizeTTSSettings(value: unknown, scope: TTSSettingsScope = "chat"): TTSSettings {
  if (!value || typeof value !== "object") {
    return { ...DEFAULT_TTS_SETTINGS };
  }
  const candidate = value as Partial<TTSSettings>;
  return {
    voice: normalizeTTSVoice(candidate.voice, scope),
    speedRatio: normalizeTTSSpeedRatio(candidate.speedRatio),
  };
}

function hasLegacySettingsShape(value: unknown): boolean {
  return Boolean(
    value
    && typeof value === "object"
    && ("voice" in value || "speedRatio" in value)
    && !("chat" in value)
    && !("book" in value),
  );
}

export function normalizeScopedTTSSettings(value: unknown): ScopedTTSSettings {
  if (hasLegacySettingsShape(value)) {
    return {
      chat: normalizeTTSSettings(value, "chat"),
      book: normalizeTTSSettings(value, "book"),
    };
  }
  const candidate = (value && typeof value === "object" ? value : {}) as Partial<ScopedTTSSettings>;
  return {
    chat: normalizeTTSSettings(candidate.chat),
    book: normalizeTTSSettings(candidate.book, "book"),
  };
}

export function readScopedTTSSettings(scope: TTSSettingsScope): TTSSettings {
  try {
    return normalizeScopedTTSSettings(wx.getStorageSync<ScopedTTSSettings | TTSSettings>(STORAGE_KEY))[scope];
  } catch (error) {
    console.warn("read TTS settings failed", error);
    return { ...DEFAULT_TTS_SETTINGS };
  }
}

export function readTTSSettingsProfiles(): ScopedTTSSettings {
  try {
    return normalizeScopedTTSSettings(wx.getStorageSync<ScopedTTSSettings | TTSSettings>(STORAGE_KEY));
  } catch (error) {
    console.warn("read TTS settings failed", error);
    return {
      chat: { ...DEFAULT_TTS_SETTINGS },
      book: { ...DEFAULT_TTS_SETTINGS },
    };
  }
}

export function saveScopedTTSSettings(scope: TTSSettingsScope, settings: TTSSettings): TTSSettings {
  const profiles = readTTSSettingsProfiles();
  const normalized = normalizeTTSSettings(settings, scope);
  wx.setStorageSync(STORAGE_KEY, {
    ...profiles,
    [scope]: normalized,
  });
  return normalized;
}

export function readChatTTSSettings(): TTSSettings {
  return readScopedTTSSettings("chat");
}

export function readBookTTSSettings(): TTSSettings {
  return readScopedTTSSettings("book");
}

export function bookTTSRequestFromSettings(
  settings: TTSSettings,
): MemoirBookAudioRequest {
  const normalized = normalizeTTSSettings(settings, "book");
  return {
    ...(normalized.voice ? { voice: normalized.voice } : {}),
    ...(normalized.speedRatio === DEFAULT_TTS_SPEED_RATIO
      ? {}
      : { speed_ratio: normalized.speedRatio }),
  };
}

export function readBookTTSRequest(): MemoirBookAudioRequest {
  return bookTTSRequestFromSettings(readBookTTSSettings());
}
