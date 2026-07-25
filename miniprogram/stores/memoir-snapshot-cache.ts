const SNAPSHOT_STORE_PREFIX = "pawtale_memoir_snapshot:";
const SNAPSHOT_ENVELOPE_VERSION = 1;

export type MemoirSnapshotResource = "home" | "book" | "chapter_workspace";

export interface MemoirSnapshotScope {
  userId: string;
  storageOwnerId?: string;
  projectId?: string;
  resource: MemoirSnapshotResource;
  resourceId?: string;
}

export interface MemoirSnapshotDescriptor<T> {
  scope: MemoirSnapshotScope;
  cacheSchemaVersion: number;
  maxAgeMs: number;
  isPayload(value: unknown): value is T;
}

export interface MemoirSnapshotEnvelope<T> {
  envelope_version: number;
  cache_schema_version: number;
  server_revision: number;
  cached_at: string;
  stale?: boolean;
  scope: {
    user_id: string;
    storage_owner_id: string;
    project_id: string;
    resource: MemoirSnapshotResource;
    resource_id: string;
  };
  payload: T;
}

export interface MemoirSnapshotReadResult<T> {
  status: "miss" | "fresh" | "stale";
  payload?: T;
  serverRevision?: number;
  cachedAt?: string;
}

export type MemoirSnapshotWriteStatus = "written" | "ignored_older" | "failed";

function normalizedScope(scope: MemoirSnapshotScope): MemoirSnapshotEnvelope<unknown>["scope"] {
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

function validDescriptor<T>(descriptor: MemoirSnapshotDescriptor<T>): boolean {
  return Boolean(
    descriptor.scope.userId.trim()
    && descriptor.scope.resource
    && Number.isInteger(descriptor.cacheSchemaVersion)
    && descriptor.cacheSchemaVersion > 0
    && Number.isFinite(descriptor.maxAgeMs)
    && descriptor.maxAgeMs >= 0,
  );
}

function snapshotStoreKey(scope: MemoirSnapshotScope): string {
  const normalized = normalizedScope(scope);
  return [
    SNAPSHOT_STORE_PREFIX.slice(0, -1),
    encodedKeyPart(normalized.user_id),
    encodedKeyPart(normalized.storage_owner_id),
    encodedKeyPart(normalized.project_id),
    encodedKeyPart(normalized.resource),
    encodedKeyPart(normalized.resource_id),
  ].join(":");
}

function scopeMatches(
  envelopeScope: MemoirSnapshotEnvelope<unknown>["scope"],
  scope: MemoirSnapshotScope,
): boolean {
  const expected = normalizedScope(scope);
  return envelopeScope.user_id === expected.user_id
    && envelopeScope.storage_owner_id === expected.storage_owner_id
    && envelopeScope.project_id === expected.project_id
    && envelopeScope.resource === expected.resource
    && envelopeScope.resource_id === expected.resource_id;
}

function validTimestamp(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(new Date(value).getTime());
}

function validEnvelope(value: unknown): value is MemoirSnapshotEnvelope<unknown> {
  if (!value || typeof value !== "object") {
    return false;
  }
  const envelope = value as Partial<MemoirSnapshotEnvelope<unknown>>;
  const scope = envelope.scope;
  return envelope.envelope_version === SNAPSHOT_ENVELOPE_VERSION
    && Number.isInteger(envelope.cache_schema_version)
    && Number.isInteger(envelope.server_revision)
    && Number(envelope.server_revision) >= 0
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

function removeSnapshotKey(key: string): void {
  try {
    wx.removeStorageSync(key);
  } catch (error) {
    console.warn("remove memoir snapshot cache failed", error);
  }
}

function snapshotAgeMs(cachedAt: string, nowMs: number): number {
  return Math.max(0, nowMs - new Date(cachedAt).getTime());
}

export function readMemoirSnapshot<T>(
  descriptor: MemoirSnapshotDescriptor<T>,
  nowMs = Date.now(),
): MemoirSnapshotReadResult<T> {
  if (!validDescriptor(descriptor)) {
    return { status: "miss" };
  }
  const key = snapshotStoreKey(descriptor.scope);
  let raw: unknown;
  try {
    raw = wx.getStorageSync<unknown>(key);
  } catch (error) {
    console.warn("read memoir snapshot cache failed", error);
    return { status: "miss" };
  }
  if (!raw) {
    return { status: "miss" };
  }
  if (
    !validEnvelope(raw)
    || raw.cache_schema_version !== descriptor.cacheSchemaVersion
    || !scopeMatches(raw.scope, descriptor.scope)
    || !descriptor.isPayload(raw.payload)
  ) {
    removeSnapshotKey(key);
    return { status: "miss" };
  }
  const stale = Boolean(raw.stale)
    || snapshotAgeMs(raw.cached_at, nowMs) > descriptor.maxAgeMs;
  return {
    status: stale ? "stale" : "fresh",
    payload: raw.payload,
    serverRevision: raw.server_revision,
    cachedAt: raw.cached_at,
  };
}

export function writeMemoirSnapshot<T>(
  descriptor: MemoirSnapshotDescriptor<T>,
  payload: T,
  serverRevision: number,
  nowMs = Date.now(),
): MemoirSnapshotWriteStatus {
  if (
    !validDescriptor(descriptor)
    || !descriptor.isPayload(payload)
    || !Number.isInteger(serverRevision)
    || serverRevision < 0
  ) {
    return "failed";
  }
  const current = readMemoirSnapshot(descriptor, nowMs);
  if (
    current.status !== "miss"
    && typeof current.serverRevision === "number"
    && current.serverRevision > serverRevision
  ) {
    return "ignored_older";
  }
  const envelope: MemoirSnapshotEnvelope<T> = {
    envelope_version: SNAPSHOT_ENVELOPE_VERSION,
    cache_schema_version: descriptor.cacheSchemaVersion,
    server_revision: serverRevision,
    cached_at: new Date(nowMs).toISOString(),
    stale: false,
    scope: normalizedScope(descriptor.scope),
    payload,
  };
  try {
    wx.setStorageSync(snapshotStoreKey(descriptor.scope), envelope);
    return "written";
  } catch (error) {
    console.warn("write memoir snapshot cache failed", error);
    return "failed";
  }
}

export function markMemoirSnapshotStale<T>(
  descriptor: MemoirSnapshotDescriptor<T>,
): void {
  if (!validDescriptor(descriptor)) {
    return;
  }
  const snapshot = readMemoirSnapshot(descriptor);
  if (snapshot.status === "miss" || snapshot.payload === undefined) {
    return;
  }
  const envelope: MemoirSnapshotEnvelope<T> = {
    envelope_version: SNAPSHOT_ENVELOPE_VERSION,
    cache_schema_version: descriptor.cacheSchemaVersion,
    server_revision: snapshot.serverRevision || 0,
    cached_at: snapshot.cachedAt || new Date().toISOString(),
    stale: true,
    scope: normalizedScope(descriptor.scope),
    payload: snapshot.payload,
  };
  try {
    wx.setStorageSync(snapshotStoreKey(descriptor.scope), envelope);
  } catch (error) {
    console.warn("mark memoir snapshot cache stale failed", error);
  }
}

export function removeMemoirSnapshot(scope: MemoirSnapshotScope): void {
  removeSnapshotKey(snapshotStoreKey(scope));
}

export function clearMemoirSnapshotsForUser(userId: string): void {
  const normalizedUserId = userId.trim();
  if (!normalizedUserId) {
    return;
  }
  const userPrefix = `${SNAPSHOT_STORE_PREFIX}${encodedKeyPart(normalizedUserId)}:`;
  try {
    const storage = wx.getStorageInfoSync();
    storage.keys
      .filter((key) => key.startsWith(userPrefix))
      .forEach((key) => removeSnapshotKey(key));
  } catch (error) {
    console.warn("clear memoir snapshot caches failed", error);
  }
}
