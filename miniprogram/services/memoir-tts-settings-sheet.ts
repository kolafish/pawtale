import {
  MEMOIR_TTS_PLAYBACK_SPEEDS,
  MEMOIR_TTS_VOICE_OPTIONS,
  memoirTtsSpeedLabel,
  readMemoirTtsSettings,
  type MemoirTtsSettings,
} from "./memoir-tts-settings";
import {
  setMemoirTtsPlaybackSpeed,
  setMemoirTtsPlaybackVoice,
} from "./memoir-tts-player";

function showActionSheet(itemList: string[]): Promise<number | null> {
  return new Promise((resolve) => {
    wx.showActionSheet({
      itemList,
      success: (result) => resolve(result.tapIndex),
      fail: () => resolve(null),
    });
  });
}

export async function openMemoirTtsVoiceSheet(): Promise<MemoirTtsSettings> {
  const itemList = MEMOIR_TTS_VOICE_OPTIONS.map((option) => `${option.label} · ${option.note}`);
  const selectedIndex = await showActionSheet(itemList);
  if (selectedIndex === null) {
    return readMemoirTtsSettings();
  }
  const voice = MEMOIR_TTS_VOICE_OPTIONS[selectedIndex]?.id;
  if (voice) {
    setMemoirTtsPlaybackVoice(voice);
  }
  return readMemoirTtsSettings();
}

export async function openMemoirTtsSpeedSheet(): Promise<MemoirTtsSettings> {
  const itemList = MEMOIR_TTS_PLAYBACK_SPEEDS.map((speed) => memoirTtsSpeedLabel(speed));
  const selectedIndex = await showActionSheet(itemList);
  if (selectedIndex === null) {
    return readMemoirTtsSettings();
  }
  const speed = MEMOIR_TTS_PLAYBACK_SPEEDS[selectedIndex];
  if (speed) {
    setMemoirTtsPlaybackSpeed(speed);
  }
  return readMemoirTtsSettings();
}
