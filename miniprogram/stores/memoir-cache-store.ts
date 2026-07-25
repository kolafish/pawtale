const CACHE_STORE_PREFIX = "pawtale_memoir_snapshot:";
const CACHE_ENVELOPE_VERSION = 1;

export type MemoirCacheResource =
  | "identity"
  | "voice_session"
  | "home"
  | "book"
  | "chapter_workspace";

export interface MemoirCacheScope {
  userId: string;
  storageOwnerId?: string;
  projectId?: string;
  resource: MemoirCacheResource;
  resourceId?: string;
}

export interface MemoirCacheDescriptor<T> {
  scope: MemoirCacheScope;
  cacheSchemaVersion: number;
  maxAgeMs: number;
  isPayload(value: unknown): value is T;
}

interface MemoirCacheEnvelope<T> {
  envelope_version: number;
  cache_schema_version: number;
  server_revision?: number;
  local_invalidation_revision?: number;
  cached_at: string;
  stale?: boolean;
  scope: {
    user_id: string;
    storage_owner_id: string;
    project_id: string;
    resource: MemoirCacheResource;
    resource_id: string;
  };
  payload: T;
}

export interface MemoirCacheReadResult<T> {
  status: "miss" | "fresh" | "stale";
  payload?: T;
  serverRevision?: number;
  localInvalidationRevision?: number;
  cachedAt?: string;
}

export interface MemoirCacheWriteOptions {
  serverRevision?: number;
  stale?: boolean;
  cachedAtMs?: number;
}

export type MemoirCacheWriteStatus = "written" | "ignored_older" | "failed";

function normalizedScope(scope: MemoirCacheScope): MemoirCacheEnvelope<unknown>["scope"] {
  return {
    user_id: scope.userId.trim(),
    storage_owner_id: (scope.storageOwnerId || "").trim(),
    project_id: (scope.projectId || "").trim(),
    resource: scope.resource,
    resource_id: (scope.resourceId || "").trim(),
  };
}

function encodedKeyPart(value: string): string {
  return encodeURIComponent(value || "-");
}

function cacheStoreKey(scope: MemoirCacheScope): string {
  const normalized = normalizedScope(scope);
  return [
    CACHE_STORE_PREFIX.slice(0, -1),
    encodedKeyPart(normalized.user_id),
    encodedKeyPart(normalized.storage_owner_id),
    encodedKeyPart(normalized.project_id),
    encodedKeyPart(normalized.resource),
    encodedKeyPart(normalized.resource_id),
  ].join(":");
}

function validDescriptor<T>(descriptor: MemoirCacheDescriptor<T>): boolean {
  return Boolean(
    descriptor.scope.userId.trim()
    && descriptor.scope.resource
    && Number.isInteger(descriptor.cacheSchemaVersion)
    && descriptor.cacheSchemaVersion > 0
    && Number.isFinite(descriptor.maxAgeMs)
    && descriptor.maxAgeMs >= 0,
  );
}

function validTimestamp(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(new Date(value).getTime());
}

function validEnvelope(value: unknown): value is MemoirCacheEnvelope<unknown> {
  if (!value || typeof value !== "object") {
    return false;
  }
  const envelope = value as Partial<MemoirCacheEnvelope<unknown>>;
  const scope = envelope.scope;
  return envelope.envelope_version === CACHE_ENVELOPE_VERSION
    && Number.isInteger(envelope.cache_schema_version)
    && (
      envelope.server_revision === undefined
      || (Number.isInteger(envelope.server_revision) && Number(envelope.server_revision) >= 0)
    )
    && (
      envelope.local_invalidation_revision === undefined
      || (
        Number.isInteger(envelope.local_invalidation_revision)
        && Number(envelope.local_invalidation_revision) >= 0
      )
    )
    && validTimestamp(envelope.cached_at)
    && (envelope.stale === undefined || typeof envelope.stale === "boolean")
    && Boolean(
      scope
      && typeof scope.user_id === "string"
      && typeof scope.storage_owner_id === "string"
      && typeof scope.project_id === "string"
      && typeof scope.resource === "string"
      && typeof scope.resource_id === "string",
    )
    && "payload" in envelope;
}

function scopeMatches(
  envelopeScope: MemoirCacheEnvelope<unknown>["scope"],
  scope: MemoirCacheScope,
): boolean {
  const expected = normalizedScope(scope);
  return envelopeScope.user_id === expected.user_id
    && envelopeScope.storage_owner_id === expected.storage_owner_id
    && envelopeScope.project_id === expected.project_id
    && envelopeScope.resource === expected.resource
    && envelopeScope.resource_id === expected.resource_id;
}

function removeKey(key: string): void {
  try {
    wx.removeStorageSync(key);
  } catch (error) {
    console.warn("remove memoir cache entry failed", error);
  }
}

function envelopeFor<T>(descriptor: MemoirCacheDescriptor<T>): MemoirCacheEnvelope<T> | null {
  const key = cacheStoreKey(descriptor.scope);
  let raw: unknown;
  try {
    raw = wx.getStorageSync<unknown>(key);
  } catch (error) {
    console.warn("read memoir cache entry failed", error);
    return null;
  }
  if (!raw) {
    return null;
  }
  if (
    !validEnvelope(raw)
    || raw.cache_schema_version !== descriptor.cacheSchemaVersion
    || !scopeMatches(raw.scope, descriptor.scope)
    || !descriptor.isPayload(raw.payload)
  ) {
    removeKey(key);
    return null;
  }
  return raw as MemoirCacheEnvelope<T>;
}

export function readMemoirCache<T>(
  descriptor: MemoirCacheDescriptor<T>,
  nowMs = Date.now(),
): MemoirCacheReadResult<T> {
  if (!validDescriptor(descriptor)) {
    return { status: "miss" };
  }
  const envelope = envelopeFor(descriptor);
  if (!envelope) {
    return { status: "miss" };
  }
  const ageMs = Math.max(0, nowMs - new Date(envelope.cached_at).getTime());
  return {
    status: envelope.stale || ageMs > descriptor.maxAgeMs ? "stale" : "fresh",
    payload: envelope.payload,
    serverRevision: envelope.server_revision,
    localInvalidationRevision: envelope.local_invalidation_revision || 0,
    cachedAt: envelope.cached_at,
  };
}

export function writeMemoirCache<T>(
  descriptor: MemoirCacheDescriptor<T>,
  payload: T,
  options: MemoirCacheWriteOptions = {},
): MemoirCacheWriteStatus {
  if (
    !validDescriptor(descriptor)
    || !descriptor.isPayload(payload)
    || (
      options.serverRevision !== undefined
      && (!Number.isInteger(options.serverRevision) || options.serverRevision < 0)
    )
  ) {
    return "failed";
  }
  const current = readMemoirCache(descriptor, options.cachedAtMs);
  if (
    options.serverRevision !== undefined
    && current.serverRevision !== undefined
    && current.serverRevision > options.serverRevision
  ) {
    return "ignored_older";
  }
  if (
    options.cachedAtMs !== undefined
    && current.cachedAt
    && new Date(current.cachedAt).getTime() > options.cachedAtMs
  ) {
    return "ignored_older";
  }
  const envelope: MemoirCacheEnvelope<T> = {
    envelope_version: CACHE_ENVELOPE_VERSION,
    cache_schema_version: descriptor.cacheSchemaVersion,
    cached_at: new Date(options.cachedAtMs ?? Date.now()).toISOString(),
    stale: options.stale || false,
    scope: normalizedScope(descriptor.scope),
    payload,
  };
  if (options.serverRevision !== undefined) {
    envelope.server_revision = options.serverRevision;
  }
  if (current.localInvalidationRevision) {
    envelope.local_invalidation_revision = current.localInvalidationRevision;
  }
  try {
    wx.setStorageSync(cacheStoreKey(descriptor.scope), envelope);
    return "written";
  } catch (error) {
    console.warn("write memoir cache entry failed", error);
    return "failed";
  }
}

export function markMemoirCacheStale<T>(
  descriptor: MemoirCacheDescriptor<T>,
  incrementLocalRevision = false,
): void {
  if (!validDescriptor(descriptor)) {
    return;
  }
  const envelope = envelopeFor(descriptor);
  if (!envelope) {
    return;
  }
  try {
    wx.setStorageSync(cacheStoreKey(descriptor.scope), {
      ...envelope,
      stale: true,
      local_invalidation_revision: incrementLocalRevision
        ? (envelope.local_invalidation_revision || 0) + 1
        : envelope.local_invalidation_revision,
    });
  } catch (error) {
    console.warn("mark memoir cache entry stale failed", error);
  }
}

export function removeMemoirCache(scope: MemoirCacheScope): void {
  removeKey(cacheStoreKey(scope));
}

function userCacheEntries(
  userId: string,
): Array<{ key: string; envelope: MemoirCacheEnvelope<unknown> }> {
  const normalizedUserId = userId.trim();
  if (!normalizedUserId) {
    return [];
  }
  const prefix = `${CACHE_STORE_PREFIX}${encodedKeyPart(normalizedUserId)}:`;
  try {
    return wx.getStorageInfoSync().keys
      .filter((key) => key.startsWith(prefix))
      .flatMap((key) => {
        const raw = wx.getStorageSync<unknown>(key);
        if (!validEnvelope(raw)) {
          removeKey(key);
          return [];
        }
        return [{ key, envelope: raw }];
      });
  } catch (error) {
    console.warn("list memoir cache entries failed", error);
    return [];
  }
}

export function listMemoirCacheScopes(
  userId: string,
  resource: MemoirCacheResource,
): Array<{ scope: MemoirCacheScope; cachedAt: string }> {
  return userCacheEntries(userId)
    .filter(({ envelope }) => envelope.scope.resource === resource)
    .map(({ envelope }) => ({
      scope: {
        userId: envelope.scope.user_id,
        storageOwnerId: envelope.scope.storage_owner_id,
        projectId: envelope.scope.project_id,
        resource: envelope.scope.resource,
        resourceId: envelope.scope.resource_id,
      },
      cachedAt: envelope.cached_at,
    }));
}

export function clearMemoirCacheForUser(userId: string): void {
  userCacheEntries(userId).forEach(({ key }) => removeKey(key));
}

export function markMemoirCacheResourcesStaleForUser(
  userId: string,
  resources: ReadonlySet<MemoirCacheResource>,
): void {
  userCacheEntries(userId)
    .filter(({ envelope }) => resources.has(envelope.scope.resource))
    .forEach(({ key, envelope }) => {
      try {
        wx.setStorageSync(key, {
          ...envelope,
          stale: true,
          local_invalidation_revision: (envelope.local_invalidation_revision || 0) + 1,
        });
      } catch (error) {
        console.warn("mark memoir cache entries stale failed", error);
      }
    });
}
