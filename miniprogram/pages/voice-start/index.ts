import type { MemoirGuidanceMode } from "../../domain/voice-chat";
import { getMemoirIdentity } from "../../services/auth";
import { createMemoirVoiceSession } from "../../services/memoir-voice-api";
import { guidanceModeLabel, memoirProjectProfile } from "../../services/memoir-project-profile";

interface VoiceStartData {
  loading: boolean;
  error: string;
  selectedMode: MemoirGuidanceMode;
  selectedModule: string;
  projectTitle: string;
  guidanceLabel: string;
}

Page<VoiceStartData, {
  onShow(): Promise<void>;
  startVoiceChat(): Promise<void>;
  backHome(): void;
}>({
  data: {
    loading: false,
    error: "",
    selectedMode: "free_narration",
    selectedModule: "event",
    projectTitle: "我的回忆录",
    guidanceLabel: "自由讲述",
  },

  async onShow() {
    try {
      const identity = await getMemoirIdentity();
      const profile = memoirProjectProfile(identity.active_project);
      this.setData({
        selectedMode: profile.defaultGuidanceMode,
        projectTitle: identity.active_project?.title || "我的回忆录",
        guidanceLabel: guidanceModeLabel(profile.defaultGuidanceMode),
      });
    } catch (error) {
      console.warn("load memoir project default guidance mode failed", error);
    }
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
    wx.switchTab({ url: "/pages/home/index" });
  },
});
