import type {
  MemoirAnswer,
  MemoirCoverageItem,
  MemoirDraft,
  MemoirEventMemory,
  MemoirInterviewState,
  MemoirOpenQuestion,
  MemoirOutput,
  MemoirPersonMemory,
  MemoirPlaceMemory,
  MemoirQuestionCandidate,
  MemoirSessionSummary,
  MemoirStoryCard,
  MemoirSummary,
  MemoirThemeMemory,
  Project,
} from "../domain/memoir";
import { readMemoirStore, writeMemoirStore, type MemoirSessionStore } from "./session";

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
  stories: MemoirStoryCard[];
  hasStories: boolean;
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

export function readMemoirDashboardSnapshot(): MemoirDashboardSnapshot {
  const store = readMemoirStore();
  return {
    project: store.project,
    interview: store.interview,
    summary: store.summary || (store.interview ? fallbackSummary(store.interview) : undefined),
    sessionSummary: store.sessionSummary,
    people: store.people || [],
    events: store.events || [],
    places: store.places || [],
    themes: store.themes || [],
    openQuestions: store.openQuestions || [],
    coverage: store.coverage || [],
    candidates: store.candidates || [],
    outputs: store.outputs || [],
    stories: store.stories || [],
    hasStories: store.stories !== undefined,
  };
}

export function writeMemoirDashboardSnapshot(snapshot: Partial<MemoirDashboardSnapshot>): void {
  const patch: MemoirSessionStore = {
    cached_at: new Date().toISOString(),
  };
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
  if (snapshot.stories !== undefined) {
    patch.stories = snapshot.stories;
  }
  writeMemoirStore(patch);
}

function previewText(text: string): string {
  const value = text.trim().replace(/\s+/g, " ");
  return value.length > 80 ? `${value.slice(0, 80)}...` : value;
}

export function updateMemoirCachedStoryDraft(draft: MemoirDraft): void {
  const store = readMemoirStore();
  if (!store.stories) {
    return;
  }

  const stories = store.stories.map((story) => {
    if (story.answer_id !== draft.answer_id) {
      return story;
    }
    return {
      ...story,
      answer_status: draft.status === "accepted" ? "confirmed" : story.answer_status,
      draft_id: draft.id,
      draft_status: draft.status,
      draft_preview: previewText(draft.content_edited || draft.content_raw),
      updated_at: draft.updated_at,
    };
  });
  const summary = store.summary
    ? {
      ...store.summary,
      total_story_count: stories.length,
      pending_review_count: stories.filter((story) => story.draft_status === "draft").length,
      confirmed_story_count: stories.filter((story) => story.draft_status === "accepted").length,
    }
    : undefined;

  writeMemoirDashboardSnapshot({ stories, summary });
}

export function updateMemoirCachedStoryAnswer(answer: MemoirAnswer): void {
  const store = readMemoirStore();
  if (!store.stories) {
    return;
  }

  const stories = store.stories.map((story) => {
    if (story.answer_id !== answer.id) {
      return story;
    }
    return {
      ...story,
      answer_preview: previewText(answer.transcript_edited || answer.transcript_raw),
      answer_status: answer.status,
      updated_at: answer.updated_at,
    };
  });
  writeMemoirDashboardSnapshot({ stories });
}
