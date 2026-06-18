export const VOICE_AUTHORIZATION_VERSION = "2026-06-18";
export const VOICE_AUTHORIZATION_STORAGE_KEY = "pawtale_voice_authorization_consent";
export const VOICE_AUTHORIZATION_PAGE_URL = "/pages/voice-authorization/index";

export interface VoiceAuthorizationConsent {
  agreed: boolean;
  version: string;
  agreedAt: string;
}

function isConsent(value: unknown): value is VoiceAuthorizationConsent {
  if (!value || typeof value !== "object") {
    return false;
  }
  const candidate = value as Partial<VoiceAuthorizationConsent>;
  return typeof candidate.agreed === "boolean"
    && typeof candidate.version === "string"
    && typeof candidate.agreedAt === "string";
}

export function readVoiceAuthorizationConsent(): boolean {
  if (typeof wx === "undefined" || typeof wx.getStorageSync !== "function") {
    return false;
  }
  try {
    const stored = wx.getStorageSync(VOICE_AUTHORIZATION_STORAGE_KEY);
    return isConsent(stored)
      && stored.agreed
      && stored.version === VOICE_AUTHORIZATION_VERSION;
  } catch {
    return false;
  }
}

export function saveVoiceAuthorizationConsent(agreed = true): VoiceAuthorizationConsent {
  const consent: VoiceAuthorizationConsent = {
    agreed,
    version: VOICE_AUTHORIZATION_VERSION,
    agreedAt: new Date().toISOString(),
  };
  if (typeof wx !== "undefined" && typeof wx.setStorageSync === "function") {
    wx.setStorageSync(VOICE_AUTHORIZATION_STORAGE_KEY, consent);
  }
  return consent;
}
