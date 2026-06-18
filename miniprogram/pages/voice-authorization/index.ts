import {
  saveVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_VERSION,
} from "../../services/voice-authorization";

interface VoiceAuthorizationData {
  version: string;
}

Page<VoiceAuthorizationData, {
  agreeAndBack(): void;
  disagreeAndBack(): void;
}>({
  data: {
    version: VOICE_AUTHORIZATION_VERSION,
  },

  agreeAndBack() {
    saveVoiceAuthorizationConsent(true);
    wx.showToast({ title: "已同意", icon: "success" });
    setTimeout(() => {
      wx.navigateBack({ delta: 1 });
    }, 250);
  },

  disagreeAndBack() {
    saveVoiceAuthorizationConsent(false);
    wx.navigateBack({ delta: 1 });
  },
});
