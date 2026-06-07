import type {
  MemoirAnswer,
  MemoirCoverageItem,
  MemoirDraft,
  MemoirEventMemory,
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
} from "../domain/memoir";

const STORE_KEY = "pawtale_memoir_session";

export interface MemoirSessionStore {
  dashboard_cache?: MemoirCacheMetadata;
  stories_cache?: MemoirCacheMetadata;
  story_detail_cache?: MemoirStoryDetailCache;
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

export function writeMemoirStore(patch: MemoirSessionStore): MemoirSessionStore {
  const next = {
    ...readMemoirStore(),
    ...patch,
  };
  wx.setStorageSync(STORE_KEY, next);
  return next;
}

export function clearMemoirDraft(): void {
  const current = readMemoirStore();
  wx.setStorageSync(STORE_KEY, {
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
