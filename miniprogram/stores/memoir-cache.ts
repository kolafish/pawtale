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
  MemoirQuestionCandidate,
  MemoirSessionSummary,
  MemoirStoryCard,
  MemoirStoryDetail,
  MemoirSummary,
  MemoirThemeMemory,
  Project,
} from "../domain/memoir";
import {
  readMemoirStore,
  writeMemoirStore,
  type MemoirCachedStoryDetail,
  type MemoirCacheMetadata,
  type MemoirSessionStore,
  type MemoirStoryDetailCache,
} from "./session";

const MEMOIR_CACHE_VERSION = 2;
const DASHBOARD_CACHE_MAX_AGE_MS = 10 * 60 * 1000;
const STORIES_CACHE_MAX_AGE_MS = 10 * 60 * 1000;
const STORY_DETAIL_CACHE_MAX_AGE_MS = 30 * 60 * 1000;
const STORY_DETAIL_CACHE_MAX_ITEMS = 20;

export interface MemoirDashboardSnapshot {
  project?: Project;
  interview?: MemoirInterviewState;
  summary?: MemoirSummary;
  sessionSummary?: MemoirSessionSummary | null;
  people: MemoirPersonMemory[];
  events: MemoirEventMemory[];
  places: MemoirPlaceMemory[];
  themes: MemoirThemeMemory[];
  openQuestions: MemoirOpenQuestion[];
  coverage: MemoirCoverageItem[];
  candidates: MemoirQuestionCandidate[];
  outputs: MemoirOutput[];
  memoryStats?: MemoirMemoryStats;
  stories: MemoirStoryCard[];
  hasDashboard: boolean;
  hasStories: boolean;
  cachedAt?: string;
  isFresh: boolean;
}

export interface MemoirStoriesSnapshot {
  stories: MemoirStoryCard[];
  total: number;
  hasStories: boolean;
  cachedAt?: string;
  isFresh: boolean;
}

export interface MemoirStoryDetailSnapshot {
  detail?: MemoirStoryDetail;
  hasDetail: boolean;
  cachedAt?: string;
  isFresh: boolean;
}

export function answeredQuestionCount(interview: MemoirInterviewState): number {
  return interview.questions.filter((question) => question.status === "answered").length;
}

export function fallbackSummary(interview: MemoirInterviewState): MemoirSummary {
  const totalQuestionCount = interview.questions.length;
  const answeredCount = answeredQuestionCount(interview);
  return {
    total_story_count: answeredCount,
    weekly_story_count: answeredCount,
    pending_review_count: interview.latest_draft?.status === "draft" ? 1 : 0,
    confirmed_story_count: interview.latest_draft?.status === "accepted" ? 1 : 0,
    total_question_count: totalQuestionCount,
    completed_question_count: interview.completed_question_count,
    progress_percent: totalQuestionCount
      ? Math.round((interview.completed_question_count * 100) / totalQuestionCount)
      : 0,
  };
}

function freshCacheMeta(): MemoirCacheMetadata {
  return {
    version: MEMOIR_CACHE_VERSION,
    cached_at: new Date().toISOString(),
    stale: false,
  };
}

function validCacheMeta(meta?: MemoirCacheMetadata): meta is MemoirCacheMetadata {
  return Boolean(meta && meta.version === MEMOIR_CACHE_VERSION && meta.cached_at);
}

function validStoryDetailCache(cache?: MemoirStoryDetailCache): cache is MemoirStoryDetailCache {
  return Boolean(cache && cache.version === MEMOIR_CACHE_VERSION && cache.details);
}

function cacheAgeMs(cachedAt?: string): number {
  if (!cachedAt) {
    return Number.POSITIVE_INFINITY;
  }
  const timestamp = new Date(cachedAt).getTime();
  if (Number.isNaN(timestamp)) {
    return Number.POSITIVE_INFINITY;
  }
  return Date.now() - timestamp;
}

function isMetaFresh(meta: MemoirCacheMetadata | undefined, maxAgeMs: number): boolean {
  return Boolean(validCacheMeta(meta) && !meta.stale && cacheAgeMs(meta.cached_at) <= maxAgeMs);
}

function activeProjectId(store: MemoirSessionStore): string {
  return store.cache_identity?.project_id || "";
}

function cacheMatchesActiveProject(store: MemoirSessionStore): boolean {
  const projectId = activeProjectId(store);
  if (!projectId) {
    return false;
  }
  if (store.project?.id && store.project.id !== projectId) {
    return false;
  }
  if (store.interview?.session.project_id && store.interview.session.project_id !== projectId) {
    return false;
  }
  return true;
}

function cacheIdentityPatch(store: MemoirSessionStore, project?: Project): Partial<MemoirSessionStore> {
  if (!project || !store.cache_identity || store.cache_identity.project_id === project.id) {
    return {};
  }
  return {
    cache_identity: {
      ...store.cache_identity,
      project_id: project.id,
    },
  };
}

function previewText(text: string): string {
  const value = text.trim().replace(/\s+/g, " ");
  return value.length > 80 ? `${value.slice(0, 80)}...` : value;
}

function storyPreviewFromAnswer(answer: MemoirAnswer): string {
  return previewText(answer.transcript_edited || answer.transcript_raw);
}

function storyPreviewFromDraft(draft: MemoirDraft): string {
  return previewText(draft.content_edited || draft.content_raw);
}

function sortStories(stories: MemoirStoryCard[]): MemoirStoryCard[] {
  return [...stories].sort((left, right) => String(right.updated_at).localeCompare(String(left.updated_at)));
}

function cachedStories(store: MemoirSessionStore): MemoirStoryCard[] | null {
  return cacheMatchesActiveProject(store) && isMetaFresh(store.stories_cache, STORIES_CACHE_MAX_AGE_MS)
    ? store.stories || []
    : null;
}

function compactStoryDetailEntries(
  details: Record<string, MemoirCachedStoryDetail>,
): Record<string, MemoirCachedStoryDetail> {
  return Object.fromEntries(
    Object.entries(details)
      .filter(([, entry]) => cacheAgeMs(entry.cached_at) <= STORY_DETAIL_CACHE_MAX_AGE_MS)
      .sort(([, left], [, right]) => String(right.cached_at).localeCompare(String(left.cached_at)))
      .slice(0, STORY_DETAIL_CACHE_MAX_ITEMS),
  );
}

function storyDetailCacheWith(details: Record<string, MemoirCachedStoryDetail>): MemoirStoryDetailCache {
  return {
    version: MEMOIR_CACHE_VERSION,
    details: compactStoryDetailEntries(details),
  };
}

function storySummary(stories: MemoirStoryCard[], summary?: MemoirSummary): MemoirSummary | undefined {
  if (!summary) {
    return undefined;
  }
  return {
    ...summary,
    total_story_count: stories.length,
    pending_review_count: stories.filter((story) => story.draft_status === "draft").length,
    confirmed_story_count: stories.filter((story) => story.draft_status === "accepted").length,
  };
}

export function readMemoirDashboardSnapshot(): MemoirDashboardSnapshot {
  const store = readMemoirStore();
  const matchesProject = cacheMatchesActiveProject(store);
  const hasDashboard = matchesProject && validCacheMeta(store.dashboard_cache) && Boolean(store.interview);
  const hasStories = matchesProject && validCacheMeta(store.stories_cache);
  const interview = hasDashboard ? store.interview : undefined;
  return {
    project: hasDashboard ? store.project : undefined,
    interview,
    summary: hasDashboard ? (store.summary || (interview ? fallbackSummary(interview) : undefined)) : undefined,
    sessionSummary: hasDashboard ? store.sessionSummary : undefined,
    people: hasDashboard ? store.people || [] : [],
    events: hasDashboard ? store.events || [] : [],
    places: hasDashboard ? store.places || [] : [],
    themes: hasDashboard ? store.themes || [] : [],
    openQuestions: hasDashboard ? store.openQuestions || [] : [],
    coverage: hasDashboard ? store.coverage || [] : [],
    candidates: hasDashboard ? store.candidates || [] : [],
    outputs: hasDashboard ? store.outputs || [] : [],
    memoryStats: hasDashboard ? store.memoryStats : undefined,
    stories: hasStories ? store.stories || [] : [],
    hasDashboard,
    hasStories,
    cachedAt: hasDashboard ? store.dashboard_cache?.cached_at : undefined,
    isFresh: isMetaFresh(store.dashboard_cache, DASHBOARD_CACHE_MAX_AGE_MS),
  };
}

export function shouldRefreshMemoirDashboardSnapshot(snapshot = readMemoirDashboardSnapshot()): boolean {
  return !snapshot.hasDashboard || !snapshot.isFresh;
}

export function writeMemoirDashboardSnapshot(snapshot: Partial<MemoirDashboardSnapshot>): void {
  const store = readMemoirStore();
  const patch: MemoirSessionStore = {
    ...cacheIdentityPatch(store, snapshot.project),
  };
  const hasDashboardFields = Boolean(
    snapshot.project !== undefined
    || snapshot.interview !== undefined
    || snapshot.summary !== undefined
    || snapshot.sessionSummary !== undefined
    || snapshot.people !== undefined
    || snapshot.events !== undefined
    || snapshot.places !== undefined
    || snapshot.themes !== undefined
    || snapshot.openQuestions !== undefined
    || snapshot.coverage !== undefined
    || snapshot.candidates !== undefined
    || snapshot.outputs !== undefined
    || snapshot.memoryStats !== undefined,
  );
  if (snapshot.project !== undefined) {
    patch.project = snapshot.project;
  }
  if (snapshot.interview !== undefined) {
    patch.interview = snapshot.interview;
  }
  if (snapshot.summary !== undefined) {
    patch.summary = snapshot.summary;
  }
  if (snapshot.sessionSummary !== undefined) {
    patch.sessionSummary = snapshot.sessionSummary;
  }
  if (snapshot.people !== undefined) {
    patch.people = snapshot.people;
  }
  if (snapshot.events !== undefined) {
    patch.events = snapshot.events;
  }
  if (snapshot.places !== undefined) {
    patch.places = snapshot.places;
  }
  if (snapshot.themes !== undefined) {
    patch.themes = snapshot.themes;
  }
  if (snapshot.openQuestions !== undefined) {
    patch.openQuestions = snapshot.openQuestions;
  }
  if (snapshot.coverage !== undefined) {
    patch.coverage = snapshot.coverage;
  }
  if (snapshot.candidates !== undefined) {
    patch.candidates = snapshot.candidates;
  }
  if (snapshot.outputs !== undefined) {
    patch.outputs = snapshot.outputs;
  }
  if (snapshot.memoryStats !== undefined) {
    patch.memoryStats = snapshot.memoryStats;
  }
  if (snapshot.interview !== undefined && snapshot.summary !== undefined) {
    patch.dashboard_cache = freshCacheMeta();
  } else if (hasDashboardFields && validCacheMeta(store.dashboard_cache)) {
    patch.dashboard_cache = {
      ...store.dashboard_cache,
      stale: true,
    };
  }
  if (snapshot.stories !== undefined) {
    patch.stories = snapshot.stories;
    patch.stories_cache = freshCacheMeta();
  }
  writeMemoirStore(patch);
}

export function markMemoirDashboardSnapshotStale(): void {
  const store = readMemoirStore();
  if (!validCacheMeta(store.dashboard_cache)) {
    return;
  }
  writeMemoirStore({
    dashboard_cache: {
      ...store.dashboard_cache,
      stale: true,
    },
  });
}

export function readMemoirStoriesSnapshot(): MemoirStoriesSnapshot {
  const store = readMemoirStore();
  const hasStories = cacheMatchesActiveProject(store) && validCacheMeta(store.stories_cache);
  const stories = hasStories ? store.stories || [] : [];
  return {
    stories,
    total: stories.length,
    hasStories,
    cachedAt: hasStories ? store.stories_cache?.cached_at : undefined,
    isFresh: isMetaFresh(store.stories_cache, STORIES_CACHE_MAX_AGE_MS),
  };
}

export function shouldRefreshMemoirStoriesSnapshot(snapshot = readMemoirStoriesSnapshot()): boolean {
  return !snapshot.hasStories || !snapshot.isFresh;
}

export function writeMemoirStoriesSnapshot(stories: MemoirStoryCard[]): void {
  const store = readMemoirStore();
  if (!activeProjectId(store)) {
    return;
  }
  writeMemoirStore({
    stories: sortStories(stories),
    stories_cache: freshCacheMeta(),
    summary: storySummary(stories, store.summary),
  });
}

export function markMemoirStoriesSnapshotStale(): void {
  const store = readMemoirStore();
  if (!validCacheMeta(store.stories_cache)) {
    return;
  }
  writeMemoirStore({
    stories_cache: {
      ...store.stories_cache,
      stale: true,
    },
  });
}

export function markMemoirDataSnapshotsStale(): void {
  markMemoirDashboardSnapshotStale();
  markMemoirStoriesSnapshotStale();
}

export function readMemoirStoryDetailSnapshot(answerId: string): MemoirStoryDetailSnapshot {
  const store = readMemoirStore();
  if (!cacheMatchesActiveProject(store)) {
    return { hasDetail: false, isFresh: false };
  }
  const cache = store.story_detail_cache;
  if (!validStoryDetailCache(cache)) {
    return { hasDetail: false, isFresh: false };
  }
  const entry = cache.details[answerId];
  if (!entry) {
    return { hasDetail: false, isFresh: false };
  }
  return {
    detail: entry.detail,
    hasDetail: true,
    cachedAt: entry.cached_at,
    isFresh: !entry.stale && cacheAgeMs(entry.cached_at) <= STORY_DETAIL_CACHE_MAX_AGE_MS,
  };
}

export function shouldRefreshMemoirStoryDetailSnapshot(answerId: string): boolean {
  const snapshot = readMemoirStoryDetailSnapshot(answerId);
  return !snapshot.hasDetail || !snapshot.isFresh;
}

function readStoryDetailCache(): MemoirStoryDetailCache {
  const store = readMemoirStore();
  if (!cacheMatchesActiveProject(store)) {
    return {
      version: MEMOIR_CACHE_VERSION,
      details: {},
    };
  }
  const cache = store.story_detail_cache;
  return validStoryDetailCache(cache) ? storyDetailCacheWith(cache.details) : {
    version: MEMOIR_CACHE_VERSION,
    details: {},
  };
}

export function writeMemoirStoryDetailSnapshot(detail: MemoirStoryDetail): void {
  const store = readMemoirStore();
  if (!activeProjectId(store)) {
    return;
  }
  const cache = readStoryDetailCache();
  const nextCache = storyDetailCacheWith({
    ...cache.details,
    [detail.answer.id]: {
      detail,
      cached_at: new Date().toISOString(),
      stale: false,
    },
  });
  const stories = cachedStories(store);
  const patch: MemoirSessionStore = {
    question: detail.question,
    answer: detail.answer,
    draft: detail.draft,
    story_detail_cache: nextCache,
  };
  if (stories) {
    const existing = stories.filter((story) => story.answer_id !== detail.story.answer_id);
    patch.stories = sortStories([detail.story, ...existing]);
    patch.stories_cache = freshCacheMeta();
    patch.summary = storySummary(patch.stories, store.summary);
  }
  writeMemoirStore(patch);
}

export function markMemoirStoryDetailSnapshotStale(answerId: string): void {
  if (!cacheMatchesActiveProject(readMemoirStore())) {
    return;
  }
  const cache = readStoryDetailCache();
  const entry = cache.details[answerId];
  if (!entry) {
    return;
  }
  writeMemoirStore({
    story_detail_cache: storyDetailCacheWith({
      ...cache.details,
      [answerId]: {
        ...entry,
        stale: true,
      },
    }),
  });
}

export function removeMemoirCachedStoryAnswers(answerIds: string[]): MemoirStoryCard[] {
  const removeIds = new Set(answerIds);
  const store = readMemoirStore();
  if (!cacheMatchesActiveProject(store)) {
    markMemoirDashboardSnapshotStale();
    return [];
  }
  const stories = cachedStories(store);
  const patch: MemoirSessionStore = {};
  let nextStories: MemoirStoryCard[] = stories || [];
  if (stories) {
    nextStories = stories.filter((story) => !removeIds.has(story.answer_id));
    patch.stories = nextStories;
    patch.stories_cache = freshCacheMeta();
    patch.summary = storySummary(nextStories, store.summary);
  }
  if (validStoryDetailCache(store.story_detail_cache)) {
    const details = { ...store.story_detail_cache.details };
    for (const answerId of answerIds) {
      delete details[answerId];
    }
    patch.story_detail_cache = storyDetailCacheWith(details);
  }
  if (Object.keys(patch).length) {
    writeMemoirStore(patch);
  }
  markMemoirDashboardSnapshotStale();
  return nextStories;
}

export function updateMemoirCachedStoryDraft(draft: MemoirDraft): void {
  const store = readMemoirStore();
  if (!cacheMatchesActiveProject(store)) {
    return;
  }
  const stories = cachedStories(store);
  const patch: MemoirSessionStore = {};

  if (stories) {
    const nextStories = stories.map((story) => {
      if (story.answer_id !== draft.answer_id) {
        return story;
      }
      return {
        ...story,
        answer_status: draft.status === "accepted" ? "confirmed" : story.answer_status,
        draft_id: draft.id,
        draft_status: draft.status,
        draft_preview: storyPreviewFromDraft(draft),
        updated_at: draft.updated_at,
      };
    });
    patch.stories = sortStories(nextStories);
    patch.stories_cache = freshCacheMeta();
    patch.summary = storySummary(patch.stories, store.summary);
  }

  if (validStoryDetailCache(store.story_detail_cache)) {
    const entry = store.story_detail_cache.details[draft.answer_id];
    if (entry) {
      patch.story_detail_cache = storyDetailCacheWith({
        ...store.story_detail_cache.details,
        [draft.answer_id]: {
          detail: {
            ...entry.detail,
            draft,
            story: {
              ...entry.detail.story,
              answer_status: draft.status === "accepted" ? "confirmed" : entry.detail.story.answer_status,
              draft_id: draft.id,
              draft_status: draft.status,
              draft_preview: storyPreviewFromDraft(draft),
              updated_at: draft.updated_at,
            },
          },
          cached_at: new Date().toISOString(),
          stale: false,
        },
      });
    }
  }

  if (Object.keys(patch).length) {
    writeMemoirStore(patch);
  }
}

export function updateMemoirCachedStoryAnswer(answer: MemoirAnswer): void {
  const store = readMemoirStore();
  if (!cacheMatchesActiveProject(store)) {
    return;
  }
  const stories = cachedStories(store);
  const patch: MemoirSessionStore = {};

  if (stories) {
    const nextStories = stories.map((story) => {
      if (story.answer_id !== answer.id) {
        return story;
      }
      return {
        ...story,
        audio_file_id: answer.audio_file_id ?? story.audio_file_id,
        answer_preview: storyPreviewFromAnswer(answer),
        answer_status: answer.status,
        duration_seconds: answer.duration_seconds ?? story.duration_seconds,
        updated_at: answer.updated_at,
      };
    });
    patch.stories = sortStories(nextStories);
    patch.stories_cache = freshCacheMeta();
  }

  if (validStoryDetailCache(store.story_detail_cache)) {
    const entry = store.story_detail_cache.details[answer.id];
    if (entry) {
      patch.story_detail_cache = storyDetailCacheWith({
        ...store.story_detail_cache.details,
        [answer.id]: {
          detail: {
            ...entry.detail,
            answer,
            story: {
              ...entry.detail.story,
              audio_file_id: answer.audio_file_id ?? entry.detail.story.audio_file_id,
              answer_preview: storyPreviewFromAnswer(answer),
              answer_status: answer.status,
              duration_seconds: answer.duration_seconds ?? entry.detail.story.duration_seconds,
              updated_at: answer.updated_at,
            },
          },
          cached_at: new Date().toISOString(),
          stale: false,
        },
      });
    }
  }

  if (Object.keys(patch).length) {
    writeMemoirStore(patch);
  }
}
