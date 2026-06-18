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

const STORAGE_KEY = "pawtale_memoir_tts_settings";
const DEFAULT_TTS_SPEED_RATIO = 1;

export const TTS_VOICE_OPTIONS: TTSVoiceOption[] = [
  {
    value: "",
    label: "默认音色",
    description: "使用后端当前默认配置",
  },
  {
    value: "zh_female_vv_uranus_bigtts",
    label: "通用女声",
    description: "火山 V3 大模型音色，支持文字高亮时间轴",
  },
  {
    value: "zh_female_xiaohe_uranus_bigtts",
    label: "小荷女声",
    description: "清晰自然，适合陪伴式交流",
  },
  {
    value: "zh_male_m191_uranus_bigtts",
    label: "通用男声",
    description: "火山 V3 大模型音色，适合访谈引导",
  },
];

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
const SPEED_VALUES = new Set(TTS_SPEED_OPTIONS.map((option) => option.value));

export function normalizeTTSVoice(value: unknown): string {
  if (typeof value !== "string") {
    return "";
  }
  const voice = value.trim();
  return VOICE_VALUES.has(voice) ? voice : "";
}

export function normalizeTTSSpeedRatio(value: unknown): number {
  const ratio = Number(value);
  return SPEED_VALUES.has(ratio) ? ratio : DEFAULT_TTS_SPEED_RATIO;
}

export function normalizeTTSSettings(value: unknown): TTSSettings {
  if (!value || typeof value !== "object") {
    return { voice: "", speedRatio: DEFAULT_TTS_SPEED_RATIO };
  }
  const candidate = value as Partial<TTSSettings>;
  return {
    voice: normalizeTTSVoice(candidate.voice),
    speedRatio: normalizeTTSSpeedRatio(candidate.speedRatio),
  };
}

export function ttsVoiceLabel(value: string): string {
  return TTS_VOICE_OPTIONS.find((option) => option.value === value)?.label || TTS_VOICE_OPTIONS[0].label;
}

export function ttsSpeedLabel(value: number): string {
  return TTS_SPEED_OPTIONS.find((option) => option.value === value)?.label || TTS_SPEED_OPTIONS[1].label;
}

export function readTTSSettings(): TTSSettings {
  try {
    return normalizeTTSSettings(wx.getStorageSync<TTSSettings>(STORAGE_KEY));
  } catch (error) {
    console.warn("read TTS settings failed", error);
    return { voice: "", speedRatio: DEFAULT_TTS_SPEED_RATIO };
  }
}

export function saveTTSSettings(settings: TTSSettings): TTSSettings {
  const normalized = normalizeTTSSettings(settings);
  wx.setStorageSync(STORAGE_KEY, normalized);
  return normalized;
}
