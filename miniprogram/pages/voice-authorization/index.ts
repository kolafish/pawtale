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
  saving: boolean;
  error: string;
}

definePage<VoiceAuthorizationData>()({
  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    version: VOICE_AUTHORIZATION_VERSION,
    saving: false,
    error: "",
  },

  onShow() {
    this.setData(accessibilityPagePatch(PAGE_CLASS));
  },

  async agreeAndBack() {
    if (this.data.saving) return;
    this.setData({ saving: true, error: "" });
    const consent = saveVoiceAuthorizationConsent(true);
    try {
      await syncVoiceAuthorizationConsent({ consent });
      wx.showToast({ title: "已同意", icon: "success" });
      setTimeout(() => {
        wx.navigateBack({ delta: 1 });
      }, 250);
    } catch (error) {
      saveVoiceAuthorizationConsent(false);
      this.setData({ error: error instanceof Error ? error.message : "授权保存失败，请重试" });
    } finally {
      this.setData({ saving: false });
    }
  },

  async disagreeAndBack() {
    if (this.data.saving) return;
    this.setData({ saving: true, error: "" });
    const consent = saveVoiceAuthorizationConsent(false);
    try {
      await syncVoiceAuthorizationConsent({ consent });
      wx.navigateBack({ delta: 1 });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "撤回授权失败，请重试" });
    } finally {
      this.setData({ saving: false });
    }
  },
});
