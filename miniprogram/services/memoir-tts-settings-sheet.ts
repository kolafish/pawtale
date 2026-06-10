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

async function selectVoice(): Promise<void> {
  const itemList = MEMOIR_TTS_VOICE_OPTIONS.map((option) => `${option.label} · ${option.note}`);
  const selectedIndex = await showActionSheet(itemList);
  if (selectedIndex === null) {
    return;
  }
  const voice = MEMOIR_TTS_VOICE_OPTIONS[selectedIndex]?.id;
  if (voice) {
    setMemoirTtsPlaybackVoice(voice);
  }
}

async function selectSpeed(): Promise<void> {
  const itemList = MEMOIR_TTS_PLAYBACK_SPEEDS.map((speed) => memoirTtsSpeedLabel(speed));
  const selectedIndex = await showActionSheet(itemList);
  if (selectedIndex === null) {
    return;
  }
  const speed = MEMOIR_TTS_PLAYBACK_SPEEDS[selectedIndex];
  if (speed) {
    setMemoirTtsPlaybackSpeed(speed);
  }
}

export async function openMemoirTtsSettingsSheet(): Promise<MemoirTtsSettings> {
  const settings = readMemoirTtsSettings();
  const selectedIndex = await showActionSheet([
    "选择朗读音色",
    `调整朗读倍速：${memoirTtsSpeedLabel(settings.speed)}`,
  ]);
  if (selectedIndex === 0) {
    await selectVoice();
  } else if (selectedIndex === 1) {
    await selectSpeed();
  }
  return readMemoirTtsSettings();
}
