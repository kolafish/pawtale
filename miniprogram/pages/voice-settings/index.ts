import { definePage } from "../../services/page-definition";
import {
  AI_MODEL_OPTIONS,
  DEFAULT_AI_MODEL,
  aiModelLabel,
  readSelectedAIModel,
  saveSelectedAIModel,
  type AIModelOption,
} from "../../services/ai-model-settings";
import { readMemoirIdentitySnapshot } from "../../stores/memoir-cache";
import {
  MEMOIR_INTERVIEW_PROMPT_OPTIONS,
  memoirInterviewPromptVariantLabel,
  readSelectedMemoirInterviewPromptVariant,
  saveMemoirInterviewPromptVariant,
  type MemoirInterviewPromptOption,
} from "../../services/memoir-interview-prompt-settings";
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
  BOOK_TTS_VOICE_OPTIONS,
  type TTSSpeedOption,
  type TTSSettingsScope,
  type TTSVoiceOption,
} from "../../services/tts-settings";

interface VoiceSettingsData {
  pageClass: string;
  autoBargeInEnabled: boolean;
  aiModel: string;
  aiModelLabel: string;
  aiModelOptions: AIModelOption[];
  interviewPromptVariant: string;
  interviewPromptVariantLabel: string;
  interviewPromptOptions: MemoirInterviewPromptOption[];
  chatTTSVoice: string;
  chatTTSSpeedRatio: number;
  bookTTSVoice: string;
  bookTTSSpeedRatio: number;
  ttsVoiceOptions: TTSVoiceOption[];
  bookTTSVoiceOptions: TTSVoiceOption[];
  ttsSpeedOptions: TTSSpeedOption[];
}

interface VoiceSettingsDataset {
  model?: string;
  scope?: string;
  voice?: string;
  speed?: string | number;
  promptVariant?: string;
}

type SettingsEvent = PageDatasetEvent<VoiceSettingsDataset>;

interface SwitchChangeEvent {
  detail: {
    value: boolean;
  };
}

const TTS_SCOPE_VALUES: readonly TTSSettingsScope[] = ["chat", "book"];

function currentUserIsAdmin(): boolean {
  try {
    return readMemoirIdentitySnapshot().identity?.role === "admin";
  } catch {
    return false;
  }
}

function voiceSettingsData(): VoiceSettingsData {
  const accessibility = readAccessibilitySettings();
  const profiles = readTTSSettingsProfiles();
  const isAdmin = currentUserIsAdmin();
  const aiModel = isAdmin ? readSelectedAIModel() : DEFAULT_AI_MODEL;
  const interviewPromptVariant = readSelectedMemoirInterviewPromptVariant();
  return {
    pageClass: accessibilityPageClass("voice-settings-page", accessibility.largeTextEnabled),
    autoBargeInEnabled: accessibility.autoBargeInEnabled,
    aiModel,
    aiModelLabel: aiModelLabel(aiModel),
    aiModelOptions: isAdmin
      ? AI_MODEL_OPTIONS
      : AI_MODEL_OPTIONS.filter((option) => option.value === DEFAULT_AI_MODEL),
    interviewPromptVariant,
    interviewPromptVariantLabel: memoirInterviewPromptVariantLabel(interviewPromptVariant),
    interviewPromptOptions: MEMOIR_INTERVIEW_PROMPT_OPTIONS,
    chatTTSVoice: profiles.chat.voice,
    chatTTSSpeedRatio: profiles.chat.speedRatio,
    bookTTSVoice: profiles.book.voice,
    bookTTSSpeedRatio: profiles.book.speedRatio,
    ttsVoiceOptions: TTS_VOICE_OPTIONS,
    bookTTSVoiceOptions: BOOK_TTS_VOICE_OPTIONS,
    ttsSpeedOptions: TTS_SPEED_OPTIONS,
  };
}

definePage<VoiceSettingsData>()({
  data: voiceSettingsData(),

  onShow() {
    this.setData(voiceSettingsData());
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
    if (!currentUserIsAdmin()) {
      return;
    }
    const model = saveSelectedAIModel(datasetString(event, "model"));
    this.setData({
      aiModel: model,
      aiModelLabel: aiModelLabel(model),
    });
    wx.showToast({ title: "已保存", icon: "success" });
  },

  selectInterviewPromptVariant(event: SettingsEvent) {
    const variant = saveMemoirInterviewPromptVariant(datasetString(event, "promptVariant"));
    this.setData({
      interviewPromptVariant: variant,
      interviewPromptVariantLabel: memoirInterviewPromptVariantLabel(variant),
    });
    wx.showToast({ title: "访谈 Prompt 已保存", icon: "success" });
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
