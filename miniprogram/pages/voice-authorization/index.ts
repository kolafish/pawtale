import {
  saveVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_VERSION,
} from "../../services/voice-authorization";

interface VoiceAuthorizationData {
  version: string;
}

Page<VoiceAuthorizationData, {
  agreeAndBack(): Promise<void>;
  disagreeAndBack(): Promise<void>;
}>({
  data: {
    version: VOICE_AUTHORIZATION_VERSION,
  },

  async agreeAndBack() {
    const consent = saveVoiceAuthorizationConsent(true);
    try {
      await syncVoiceAuthorizationConsent({ consent });
    } catch (error) {
      console.warn("sync voice authorization consent failed", error);
    }
    wx.showToast({ title: "已同意", icon: "success" });
    setTimeout(() => {
      wx.navigateBack({ delta: 1 });
    }, 250);
  },

  async disagreeAndBack() {
    const consent = saveVoiceAuthorizationConsent(false);
    try {
      await syncVoiceAuthorizationConsent({ consent });
    } catch (error) {
      console.warn("sync voice authorization disagreement failed", error);
    }
    wx.navigateBack({ delta: 1 });
  },
});
