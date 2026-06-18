export interface TTSVoiceOption {
  value: string;
  label: string;
  description: string;
}

export interface TTSSettings {
  voice: string;
}

const STORAGE_KEY = "pawtale_memoir_tts_settings";

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

const VOICE_VALUES = new Set(TTS_VOICE_OPTIONS.map((option) => option.value));

export function normalizeTTSVoice(value: unknown): string {
  if (typeof value !== "string") {
    return "";
  }
  const voice = value.trim();
  return VOICE_VALUES.has(voice) ? voice : "";
}

export function normalizeTTSSettings(value: unknown): TTSSettings {
  if (!value || typeof value !== "object") {
    return { voice: "" };
  }
  const candidate = value as Partial<TTSSettings>;
  return {
    voice: normalizeTTSVoice(candidate.voice),
  };
}

export function ttsVoiceLabel(value: string): string {
  return TTS_VOICE_OPTIONS.find((option) => option.value === value)?.label || TTS_VOICE_OPTIONS[0].label;
}

export function readTTSSettings(): TTSSettings {
  try {
    return normalizeTTSSettings(wx.getStorageSync<TTSSettings>(STORAGE_KEY));
  } catch (error) {
    console.warn("read TTS settings failed", error);
    return { voice: "" };
  }
}

export function saveTTSSettings(settings: TTSSettings): TTSSettings {
  const normalized = normalizeTTSSettings(settings);
  wx.setStorageSync(STORAGE_KEY, normalized);
  return normalized;
}
