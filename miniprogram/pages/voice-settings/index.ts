import { definePage } from "../../services/page-definition";
import { getMemoirIdentity } from "../../services/auth";
import {
  AI_MODEL_OPTIONS,
  aiModelLabel,
  readSelectedAIModel,
  saveSelectedAIModel,
  type AIModelOption,
} from "../../services/ai-model-settings";
import {
  accessibilityPageClass,
  readAccessibilitySettings,
  saveAutoBargeInEnabled,
} from "../../services/accessibility-settings";
import {
  datasetChoice,
  datasetNumber,
  datasetString,
  type PageDatasetEvent,
} from "../../services/page-input";
import {
  readScopedTTSSettings,
  readTTSSettingsProfiles,
  saveScopedTTSSettings,
  TTS_SPEED_OPTIONS,
  TTS_VOICE_OPTIONS,
  type TTSSpeedOption,
  type TTSSettingsScope,
  type TTSVoiceOption,
} from "../../services/tts-settings";
import { updateMemoirProject } from "../../services/memoir";
import {
  readSelectedTranscriptPolishLevel,
  transcriptPolishLevel,
  TRANSCRIPT_POLISH_LEVELS,
  TRANSCRIPT_POLISH_OPTIONS,
  type TranscriptPolishOption,
} from "../../services/transcript-polish-settings";
import type { MemoirTranscriptPolishLevel } from "../../domain/memoir";

interface VoiceSettingsData {
  pageClass: string;
  autoBargeInEnabled: boolean;
  aiModel: string;
  aiModelLabel: string;
  aiModelOptions: AIModelOption[];
  polishProjectId: string;
  transcriptPolishLevel: MemoirTranscriptPolishLevel;
  transcriptPolishOptions: TranscriptPolishOption[];
  chatTTSVoice: string;
  chatTTSSpeedRatio: number;
  bookTTSVoice: string;
  bookTTSSpeedRatio: number;
  ttsVoiceOptions: TTSVoiceOption[];
  ttsSpeedOptions: TTSSpeedOption[];
}

interface VoiceSettingsDataset {
  model?: string;
  scope?: string;
  voice?: string;
  speed?: string | number;
  polish?: string;
}

type SettingsEvent = PageDatasetEvent<VoiceSettingsDataset>;

interface SwitchChangeEvent {
  detail: {
    value: boolean;
  };
}

const TTS_SCOPE_VALUES: readonly TTSSettingsScope[] = ["chat", "book"];

function voiceSettingsData(): VoiceSettingsData {
  const accessibility = readAccessibilitySettings();
  const profiles = readTTSSettingsProfiles();
  const aiModel = readSelectedAIModel();
  return {
    pageClass: accessibilityPageClass("voice-settings-page", accessibility.largeTextEnabled),
    autoBargeInEnabled: accessibility.autoBargeInEnabled,
    aiModel,
    aiModelLabel: aiModelLabel(aiModel),
    aiModelOptions: AI_MODEL_OPTIONS,
    polishProjectId: "",
    transcriptPolishLevel: readSelectedTranscriptPolishLevel() || "low",
    transcriptPolishOptions: TRANSCRIPT_POLISH_OPTIONS,
    chatTTSVoice: profiles.chat.voice,
    chatTTSSpeedRatio: profiles.chat.speedRatio,
    bookTTSVoice: profiles.book.voice,
    bookTTSSpeedRatio: profiles.book.speedRatio,
    ttsVoiceOptions: TTS_VOICE_OPTIONS,
    ttsSpeedOptions: TTS_SPEED_OPTIONS,
  };
}

definePage<VoiceSettingsData>()({
  data: voiceSettingsData(),

  async onShow() {
    this.setData(voiceSettingsData());
    try {
      const identity = await getMemoirIdentity();
      this.setData({
        polishProjectId: identity.active_project_id,
        transcriptPolishLevel: transcriptPolishLevel(identity.active_project),
      });
    } catch (error) {
      console.warn("load transcript polish setting failed", error);
    }
  },

  changeAutoBargeIn(event: SwitchChangeEvent) {
    const settings = saveAutoBargeInEnabled(event.detail.value);
    this.setData({
      pageClass: accessibilityPageClass("voice-settings-page", settings.largeTextEnabled),
      autoBargeInEnabled: settings.autoBargeInEnabled,
    });
    wx.showToast({
      title: settings.autoBargeInEnabled ? "自动检测已打开" : "自动检测已关闭",
      icon: "none",
    });
  },

  selectAIModel(event: SettingsEvent) {
    const model = saveSelectedAIModel(datasetString(event, "model"));
    this.setData({
      aiModel: model,
      aiModelLabel: aiModelLabel(model),
    });
    wx.showToast({ title: "已保存", icon: "success" });
  },

  async selectTranscriptPolishLevel(event: SettingsEvent) {
    if (!this.data.polishProjectId) {
      wx.showToast({ title: "请先创建回忆录", icon: "none" });
      return;
    }
    const level = datasetChoice(
      event,
      "polish",
      TRANSCRIPT_POLISH_LEVELS,
      "low",
    );
    try {
      const project = await updateMemoirProject(this.data.polishProjectId, {
        transcript_polish_level: level,
      });
      this.setData({ transcriptPolishLevel: transcriptPolishLevel(project) });
      await getMemoirIdentity().catch((error) => {
        console.warn("refresh transcript polish setting failed", error);
      });
      wx.showToast({ title: "已保存", icon: "success" });
    } catch (error) {
      console.warn("save transcript polish setting failed", error);
      wx.showToast({ title: "保存失败，请重试", icon: "none" });
    }
  },

  selectTTSVoice(event: SettingsEvent) {
    const scope = datasetChoice(event, "scope", TTS_SCOPE_VALUES, "chat");
    const voice = datasetString(event, "voice");
    const settings = saveScopedTTSSettings(scope, {
      ...readScopedTTSSettings(scope),
      voice,
    });
    this.setData(scope === "book"
      ? { bookTTSVoice: settings.voice }
      : { chatTTSVoice: settings.voice });
    wx.showToast({ title: "已保存", icon: "success" });
  },

  selectTTSSpeed(event: SettingsEvent) {
    const scope = datasetChoice(event, "scope", TTS_SCOPE_VALUES, "chat");
    const current = readScopedTTSSettings(scope);
    const speedRatio = datasetNumber(event, "speed", current.speedRatio);
    const settings = saveScopedTTSSettings(scope, {
      ...current,
      speedRatio,
    });
    this.setData(scope === "book"
      ? { bookTTSSpeedRatio: settings.speedRatio }
      : { chatTTSSpeedRatio: settings.speedRatio });
    wx.showToast({ title: "已保存", icon: "success" });
  },
});
