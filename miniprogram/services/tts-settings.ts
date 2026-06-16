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
    value: "cixingnansheng",
    label: "磁性男声",
    description: "稳重清晰，适合访谈引导",
  },
  {
    value: "wenrounansheng",
    label: "温柔男声",
    description: "语气柔和，适合慢节奏追问",
  },
  {
    value: "qinqienvsheng",
    label: "亲切女声",
    description: "自然亲和，适合陪伴式交流",
  },
  {
    value: "linjiajiejie",
    label: "邻家姐姐",
    description: "轻松温暖，适合日常回忆",
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
