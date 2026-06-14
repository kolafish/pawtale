import type { MemoirGuidanceMode } from "../../domain/voice-chat";
import { createMemoirVoiceSession } from "../../services/memoir-voice-api";

interface ModeOption {
  value: MemoirGuidanceMode;
  title: string;
  description: string;
  tag: string;
}

interface VoiceStartData {
  loading: boolean;
  error: string;
  selectedMode: MemoirGuidanceMode;
  selectedModule: string;
  modeOptions: ModeOption[];
  moduleOptions: Array<{ value: string; label: string }>;
}

interface DatasetEvent {
  currentTarget: {
    dataset: {
      mode?: MemoirGuidanceMode;
      module?: string;
    };
  };
}

Page<VoiceStartData, {
  selectMode(event: DatasetEvent): void;
  selectModule(event: DatasetEvent): void;
  startVoiceChat(): Promise<void>;
  backHome(): void;
}>({
  data: {
    loading: false,
    error: "",
    selectedMode: "free_narration",
    selectedModule: "event",
    modeOptions: [
      {
        value: "free_narration",
        title: "自由讲述",
        description: "适合已经想好要讲什么的人，AI 只做轻量接话。",
        tag: "弱引导",
      },
      {
        value: "guided_interview",
        title: "AI 引导",
        description: "适合不知道从哪讲起的人，AI 会按主题一步步追问。",
        tag: "强引导",
      },
    ],
    moduleOptions: [
      { value: "person", label: "人" },
      { value: "event", label: "事" },
      { value: "place", label: "地点" },
      { value: "period", label: "时间段" },
      { value: "theme", label: "主题" },
    ],
  },

  selectMode(event) {
    const mode = event.currentTarget.dataset.mode;
    if (!mode) {
      return;
    }
    this.setData({
      selectedMode: mode,
      error: "",
    });
  },

  selectModule(event) {
    const selectedModule = event.currentTarget.dataset.module;
    if (!selectedModule) {
      return;
    }
    this.setData({ selectedModule });
  },

  async startVoiceChat() {
    if (this.data.loading) {
      return;
    }
    this.setData({ loading: true, error: "" });
    try {
      const currentGuidanceModule = this.data.selectedMode === "guided_interview"
        ? this.data.selectedModule
        : "";
      const detail = await createMemoirVoiceSession({
        guidance_mode: this.data.selectedMode,
        current_guidance_module: currentGuidanceModule || undefined,
        provider: "volc_streaming_asr",
        input_format: "pcm_s16le_16000_mono",
        metadata_json: {
          entry: "miniprogram_voice_start",
          first_version: true,
        },
      });
      const params = [
        `voiceSessionId=${encodeURIComponent(detail.session.id)}`,
        `mode=${encodeURIComponent(this.data.selectedMode)}`,
      ];
      if (currentGuidanceModule) {
        params.push(`module=${encodeURIComponent(currentGuidanceModule)}`);
      }
      wx.navigateTo({ url: `/pages/voice-chat/index?${params.join("&")}` });
    } catch (error) {
      this.setData({
        error: error instanceof Error ? error.message : "语音访谈创建失败",
      });
    } finally {
      this.setData({ loading: false });
    }
  },

  backHome() {
    wx.redirectTo({ url: "/pages/home/index" });
  },
});
