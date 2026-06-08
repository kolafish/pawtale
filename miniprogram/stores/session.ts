import type {
  MemoirAnswer,
  MemoirCoverageItem,
  MemoirDraft,
  MemoirEventMemory,
  MemoirIdentity,
  MemoirInterviewState,
  MemoirMemoryStats,
  MemoirOpenQuestion,
  MemoirOutput,
  MemoirPersonMemory,
  MemoirPlaceMemory,
  MemoirQuestion,
  MemoirQuestionCandidate,
  MemoirSessionSummary,
  MemoirStoryCard,
  MemoirStoryDetail,
  MemoirSummary,
  MemoirThemeMemory,
  Project,
  User,
} from "../domain/memoir";
import { clearMemoirInputDrafts } from "./memoir-input-drafts";

const STORE_KEY = "pawtale_memoir_session";

export interface MemoirSessionStore {
  cache_identity?: MemoirCacheIdentity;
  identity_cache?: MemoirCacheMetadata;
  dashboard_cache?: MemoirCacheMetadata;
  stories_cache?: MemoirCacheMetadata;
  story_detail_cache?: MemoirStoryDetailCache;
  identity?: MemoirIdentity;
  project?: Project;
  interview?: MemoirInterviewState;
  question?: MemoirQuestion;
  answer?: MemoirAnswer;
  draft?: MemoirDraft;
  summary?: MemoirSummary;
  sessionSummary?: MemoirSessionSummary | null;
  people?: MemoirPersonMemory[];
  events?: MemoirEventMemory[];
  places?: MemoirPlaceMemory[];
  themes?: MemoirThemeMemory[];
  openQuestions?: MemoirOpenQuestion[];
  coverage?: MemoirCoverageItem[];
  candidates?: MemoirQuestionCandidate[];
  outputs?: MemoirOutput[];
  memoryStats?: MemoirMemoryStats;
  stories?: MemoirStoryCard[];
  cache_write_failed_at?: string;
}

export interface MemoirCacheIdentity {
  user_id: string;
  storage_owner_id?: string;
  project_id?: string;
}

export interface MemoirCacheMetadata {
  version: number;
  cached_at: string;
  stale?: boolean;
}

export interface MemoirCachedStoryDetail {
  detail: MemoirStoryDetail;
  cached_at: string;
  stale?: boolean;
}

export interface MemoirStoryDetailCache {
  version: number;
  details: Record<string, MemoirCachedStoryDetail>;
}

export function readMemoirStore(): MemoirSessionStore {
  return wx.getStorageSync<MemoirSessionStore>(STORE_KEY) || {};
}

function persistMemoirStore(store: MemoirSessionStore): MemoirSessionStore {
  try {
    wx.setStorageSync(STORE_KEY, store);
    return store;
  } catch {
    if (!store.story_detail_cache) {
      return {
        ...store,
        cache_write_failed_at: new Date().toISOString(),
      };
    }
    const fallback = { ...store };
    delete fallback.story_detail_cache;
    fallback.cache_write_failed_at = new Date().toISOString();
    try {
      wx.setStorageSync(STORE_KEY, fallback);
      return fallback;
    } catch {
      return fallback;
    }
  }
}

export function writeMemoirStore(patch: MemoirSessionStore): MemoirSessionStore {
  const next = {
    ...readMemoirStore(),
    ...patch,
  };
  delete next.cache_write_failed_at;
  return persistMemoirStore(next);
}

export function replaceMemoirStore(next: MemoirSessionStore): MemoirSessionStore {
  delete next.cache_write_failed_at;
  return persistMemoirStore(next);
}

export function cacheIdentityFromUser(user: Pick<User, "id" | "storage_owner_id">, project?: Pick<Project, "id">): MemoirCacheIdentity {
  return {
    user_id: user.id,
    storage_owner_id: user.storage_owner_id,
    project_id: project?.id,
  };
}

function sameCacheIdentity(current: MemoirCacheIdentity | undefined, next: MemoirCacheIdentity): boolean {
  if (!current) {
    return true;
  }
  if (current.user_id !== next.user_id) {
    return false;
  }
  if ((current.storage_owner_id || "") !== (next.storage_owner_id || "")) {
    return false;
  }
  const currentProjectId = current.project_id || "";
  const nextProjectId = next.project_id || "";
  if ((currentProjectId || nextProjectId) && currentProjectId !== nextProjectId) {
    return false;
  }
  return true;
}

export function ensureMemoirStoreIdentity(identity: MemoirCacheIdentity): MemoirSessionStore {
  const current = readMemoirStore();
  const hasCachedMemoirData = Boolean(
    current.project
    || current.identity
    || current.interview
    || current.identity_cache
    || current.dashboard_cache
    || current.stories_cache
    || current.story_detail_cache
    || current.stories,
  );
  if (!current.cache_identity && hasCachedMemoirData) {
    clearMemoirInputDrafts();
    return replaceMemoirStore({ cache_identity: identity });
  }
  if (!sameCacheIdentity(current.cache_identity, identity)) {
    clearMemoirInputDrafts();
    return replaceMemoirStore({ cache_identity: identity });
  }
  const nextIdentity = {
    user_id: identity.user_id,
    storage_owner_id: identity.storage_owner_id,
    project_id: identity.project_id ?? current.cache_identity?.project_id,
  };
  if (
    current.cache_identity?.user_id === nextIdentity.user_id
    && current.cache_identity.storage_owner_id === nextIdentity.storage_owner_id
    && current.cache_identity.project_id === nextIdentity.project_id
  ) {
    return current;
  }
  return writeMemoirStore({ cache_identity: nextIdentity });
}

export function clearMemoirDraft(): void {
  const current = readMemoirStore();
  replaceMemoirStore({
    cache_identity: current.cache_identity,
    identity: current.identity,
    identity_cache: current.identity_cache,
    project: current.project,
    interview: current.interview,
    summary: current.summary,
    sessionSummary: current.sessionSummary,
    people: current.people,
    events: current.events,
    places: current.places,
    themes: current.themes,
    openQuestions: current.openQuestions,
    coverage: current.coverage,
    candidates: current.candidates,
    outputs: current.outputs,
    memoryStats: current.memoryStats,
    stories: current.stories,
    dashboard_cache: current.dashboard_cache,
    stories_cache: current.stories_cache,
    story_detail_cache: current.story_detail_cache,
  });
}
