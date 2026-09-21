import { ensureBackendSession, getMemoirIdentity } from "./auth";
import { request } from "./request";
import { ensureRecorderPrivacyAuthorization } from "./wechat-privacy";
import { readMemoirStore } from "../stores/session";

export const VOICE_AUTHORIZATION_VERSION = "2026-09-21";
export const VOICE_AUTHORIZATION_STORAGE_KEY = "pawtale_voice_authorization_consent_v2";
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

interface ScopedVoiceAuthorizationConsent {
  userId: string;
  consent: VoiceAuthorizationConsent;
}

interface VoiceAuthorizationConsentStatus {
  has_current_consent: boolean;
  consent: VoiceAuthorizationConsentAudit | null;
}

export class VoiceAuthorizationRequiredError extends Error {
  constructor() {
    super("请先同意语音授权协议");
  }
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

function activeUserId(): string {
  if (typeof wx === "undefined" || typeof wx.getStorageSync !== "function") {
    return "";
  }
  try {
    return readMemoirStore().cache_identity?.user_id || "";
  } catch {
    return "";
  }
}

function writeScopedConsent(userId: string, consent: VoiceAuthorizationConsent): void {
  if (userId && typeof wx !== "undefined" && typeof wx.setStorageSync === "function") {
    try {
      wx.setStorageSync(VOICE_AUTHORIZATION_STORAGE_KEY, { userId, consent });
    } catch {
      // The backend audit record remains authoritative when local storage is unavailable.
    }
  }
}

export function readVoiceAuthorizationConsentRecord(): VoiceAuthorizationConsent | null {
  if (typeof wx === "undefined" || typeof wx.getStorageSync !== "function") {
    return null;
  }
  try {
    const userId = activeUserId();
    const stored = wx.getStorageSync(VOICE_AUTHORIZATION_STORAGE_KEY) as ScopedVoiceAuthorizationConsent | null;
    if (!userId || !stored || stored.userId !== userId
      || !isConsent(stored.consent) || stored.consent.version !== VOICE_AUTHORIZATION_VERSION) {
      return null;
    }
    return stored.consent;
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
  writeScopedConsent(activeUserId(), consent);
  return consent;
}

export async function syncVoiceAuthorizationConsent(
  options: SyncVoiceAuthorizationConsentOptions = {},
): Promise<VoiceAuthorizationConsentAudit> {
  const consent = options.consent;
  if (consent && consent.version !== VOICE_AUTHORIZATION_VERSION) {
    throw new VoiceAuthorizationRequiredError();
  }
  await ensureBackendSession();
  const userIdBeforeRequest = activeUserId();
  const identity = await getMemoirIdentity();
  if (userIdBeforeRequest && userIdBeforeRequest !== identity.id) {
    throw new VoiceAuthorizationRequiredError();
  }
  if (consent) {
    try {
      const audit = await request<VoiceAuthorizationConsentAudit>("/api/mobile/memoir/voice-authorization-consent", {
        method: "POST",
        data: {
          version: consent.version,
          agreed: consent.agreed,
          source: "miniprogram",
          ...(options.sourceSessionId ? { source_session_id: options.sourceSessionId } : {}),
          metadata_json: { agreed_at: consent.agreedAt },
        },
      });
      writeScopedConsent(identity.id, consent);
      return audit;
    } catch (error) {
      if (consent.agreed) {
        writeScopedConsent(identity.id, { ...consent, agreed: false });
      }
      throw error;
    }
  }
  const status = await request<VoiceAuthorizationConsentStatus>(
    `/api/mobile/memoir/voice-authorization-consent?version=${encodeURIComponent(VOICE_AUTHORIZATION_VERSION)}`,
  );
  if (!status.has_current_consent || !status.consent) {
    writeScopedConsent(identity.id, {
      agreed: false,
      version: VOICE_AUTHORIZATION_VERSION,
      agreedAt: new Date().toISOString(),
    });
    throw new VoiceAuthorizationRequiredError();
  }
  writeScopedConsent(identity.id, {
    agreed: true,
    version: VOICE_AUTHORIZATION_VERSION,
    agreedAt: status.consent.created_at || new Date().toISOString(),
  });
  return status.consent;
}

export async function ensureVoiceRecordingAuthorization(
  sourceSessionId?: string,
): Promise<boolean> {
  try {
    await syncVoiceAuthorizationConsent({ sourceSessionId });
  } catch (error) {
    if (error instanceof VoiceAuthorizationRequiredError) {
      wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
      return false;
    }
    throw error;
  }
  await ensureRecorderPrivacyAuthorization();
  return true;
}
