import {
  saveVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_VERSION,
} from "../../services/voice-authorization";
import {
  accessibilityPageClass,
  accessibilityPagePatch,
} from "../../services/accessibility-settings";
import { definePage } from "../../services/page-definition";

const PAGE_CLASS = "voice-authorization-page ui-page-shell ui-fixed-action-page ui-accessible-page";

interface VoiceAuthorizationData {
  pageClass: string;
  version: string;
}

definePage<VoiceAuthorizationData>()({
  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    version: VOICE_AUTHORIZATION_VERSION,
  },

  onShow() {
    this.setData(accessibilityPagePatch(PAGE_CLASS));
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
