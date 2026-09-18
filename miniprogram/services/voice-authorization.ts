import { ensureBackendSession } from "./auth";
import { request } from "./request";
import {
  ensureRecorderPrivacyAuthorization,
  isRecorderPermissionDeniedError,
  openRecorderPermissionSettings,
} from "./wechat-privacy";

export { isRecorderPermissionDeniedError, openRecorderPermissionSettings };

export const VOICE_AUTHORIZATION_VERSION = "2026-06-18";
export const VOICE_AUTHORIZATION_STORAGE_KEY = "pawtale_voice_authorization_consent";
export const VOICE_AUTHORIZATION_PAGE_URL = "/pages/voice-authorization/index";

export interface VoiceAuthorizationConsent {
  agreed: boolean;
  version: string;
  agreedAt: string;
}

export interface VoiceAuthorizationConsentAudit {
  id: string;
  user_id: string;
  consent_type: string;
  version: string;
  agreed: boolean;
  source: string;
  source_session_id?: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

interface SyncVoiceAuthorizationConsentOptions {
  consent?: VoiceAuthorizationConsent;
  sourceSessionId?: string;
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

export function readVoiceAuthorizationConsentRecord(): VoiceAuthorizationConsent | null {
  if (typeof wx === "undefined" || typeof wx.getStorageSync !== "function") {
    return null;
  }
  try {
    const stored = wx.getStorageSync(VOICE_AUTHORIZATION_STORAGE_KEY);
    if (!isConsent(stored) || stored.version !== VOICE_AUTHORIZATION_VERSION) {
      return null;
    }
    return stored;
  } catch {
    return null;
  }
}

export function readVoiceAuthorizationConsent(): boolean {
  return readVoiceAuthorizationConsentRecord()?.agreed === true;
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

export async function syncVoiceAuthorizationConsent(
  options: SyncVoiceAuthorizationConsentOptions = {},
): Promise<VoiceAuthorizationConsentAudit> {
  const consent = options.consent || readVoiceAuthorizationConsentRecord();
  if (!consent || consent.version !== VOICE_AUTHORIZATION_VERSION) {
    throw new Error("请先同意声纹授权协议");
  }
  await ensureBackendSession();
  return request<VoiceAuthorizationConsentAudit>("/api/mobile/memoir/voice-authorization-consent", {
    method: "POST",
    data: {
      version: consent.version,
      agreed: consent.agreed,
      source: "miniprogram",
      ...(options.sourceSessionId ? { source_session_id: options.sourceSessionId } : {}),
      metadata_json: {
        agreed_at: consent.agreedAt,
      },
    },
  });
}

export async function ensureVoiceRecordingAuthorization(
  sourceSessionId?: string,
): Promise<boolean> {
  if (!readVoiceAuthorizationConsent()) {
    wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
    return false;
  }
  await syncVoiceAuthorizationConsent({ sourceSessionId });
  await ensureRecorderPrivacyAuthorization();
  return true;
}
