export const MEMOIR_TTS_PLAYBACK_SPEEDS = [0.75, 1, 1.25, 1.5, 2] as const;

export const MEMOIR_TTS_VOICE_OPTIONS = [
  { id: "cixingnansheng", label: "磁性男声", note: "稳重清楚" },
  { id: "wenrounvsheng", label: "温柔女声", note: "柔和自然" },
  { id: "ruyananshi", label: "儒雅男声", note: "适合回忆" },
  { id: "qinqienvsheng", label: "亲切女声", note: "亲切明亮" },
  { id: "boyinnansheng", label: "播音男声", note: "字正腔圆" },
] as const;

export type MemoirTtsVoiceId = (typeof MEMOIR_TTS_VOICE_OPTIONS)[number]["id"];

export interface MemoirTtsSettings {
  voice: MemoirTtsVoiceId;
  speed: number;
}

const SETTINGS_STORAGE_KEY = "memoir_tts_settings_v1";
const DEFAULT_TTS_VOICE: MemoirTtsVoiceId = "cixingnansheng";
const DEFAULT_TTS_SPEED = 1;

let cachedSettings: MemoirTtsSettings | null = null;

export function normalizeMemoirTtsPlaybackSpeed(speed: number): number {
  if (!Number.isFinite(speed)) {
    return DEFAULT_TTS_SPEED;
  }
  return MEMOIR_TTS_PLAYBACK_SPEEDS.reduce((closest, candidate) => {
    return Math.abs(candidate - speed) < Math.abs(closest - speed) ? candidate : closest;
  }, DEFAULT_TTS_SPEED);
}

export function normalizeMemoirTtsVoice(voice?: string): MemoirTtsVoiceId {
  const matched = MEMOIR_TTS_VOICE_OPTIONS.find((option) => option.id === voice);
  return matched?.id || DEFAULT_TTS_VOICE;
}

export function memoirTtsVoiceLabel(voice?: string): string {
  const normalized = normalizeMemoirTtsVoice(voice);
  return MEMOIR_TTS_VOICE_OPTIONS.find((option) => option.id === normalized)?.label || "默认音色";
}

export function memoirTtsSpeedLabel(speed: number): string {
  return `${normalizeMemoirTtsPlaybackSpeed(speed)}x`;
}

export function memoirTtsSettingsLabel(settings: MemoirTtsSettings): string {
  return `${memoirTtsSpeedLabel(settings.speed)} · ${memoirTtsVoiceLabel(settings.voice)}`;
}

function readStoredSettings(): Partial<MemoirTtsSettings> {
  if (typeof wx === "undefined" || !wx.getStorageSync) {
    return {};
  }
  try {
    const value = wx.getStorageSync(SETTINGS_STORAGE_KEY);
    return value && typeof value === "object" ? value as Partial<MemoirTtsSettings> : {};
  } catch (_error) {
    return {};
  }
}

function persistSettings(settings: MemoirTtsSettings): void {
  if (typeof wx === "undefined" || !wx.setStorageSync) {
    return;
  }
  try {
    wx.setStorageSync(SETTINGS_STORAGE_KEY, settings);
  } catch (_error) {
    // The in-memory setting still applies for this session.
  }
}

export function readMemoirTtsSettings(): MemoirTtsSettings {
  if (cachedSettings) {
    return { ...cachedSettings };
  }
  const stored = readStoredSettings();
  cachedSettings = {
    voice: normalizeMemoirTtsVoice(stored.voice),
    speed: normalizeMemoirTtsPlaybackSpeed(Number(stored.speed)),
  };
  return { ...cachedSettings };
}

export function writeMemoirTtsSettings(patch: Partial<MemoirTtsSettings>): MemoirTtsSettings {
  const previous = readMemoirTtsSettings();
  cachedSettings = {
    voice: normalizeMemoirTtsVoice(patch.voice || previous.voice),
    speed: normalizeMemoirTtsPlaybackSpeed(Number(patch.speed ?? previous.speed)),
  };
  persistSettings(cachedSettings);
  return { ...cachedSettings };
}
