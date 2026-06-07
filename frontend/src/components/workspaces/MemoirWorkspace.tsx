import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Check,
  FileText,
  Gauge,
  HelpCircle,
  MapPin,
  RefreshCw,
  Sparkles,
  Users,
  Wand2,
  X,
} from 'lucide-react';
import { memoirApi } from '../../api';
import type {
  MemoirEventMemory,
  MemoirMemoryStatus,
  MemoirOpenQuestion,
  MemoirOutput,
  MemoirPersonMemory,
  MemoirPlaceMemory,
  MemoirThemeMemory,
  Project,
} from '../../types';
import { ProjectDraftWorkspace } from './ProjectDraftWorkspace';

interface MemoirWorkspaceProps {
  project: Project;
}

type MemoryCard =
  | { type: 'person'; id: string; title: string; detail: string; status: MemoirMemoryStatus }
  | { type: 'event'; id: string; title: string; detail: string; status: MemoirMemoryStatus }
  | { type: 'place'; id: string; title: string; detail: string; status: MemoirMemoryStatus }
  | { type: 'theme'; id: string; title: string; detail: string; status: MemoirMemoryStatus };

const statusText: Record<MemoirMemoryStatus, string> = {
  draft: '待确认',
  confirmed: '已确认',
  rejected: '已拒绝',
};

export function MemoirWorkspace({ project }: MemoirWorkspaceProps) {
  return (
    <ProjectDraftWorkspace
      project={project}
      workspacePanel={<MemoirReviewPanel projectId={project.id} />}
    />
  );
}

function MemoirReviewPanel({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const invalidateMemoir = () => queryClient.invalidateQueries({ queryKey: ['memoir', projectId] });

  const sessionQuery = useQuery({
    queryKey: ['memoir', projectId, 'session'],
    queryFn: () => memoirApi.currentSession(projectId),
  });
  const sessionId = sessionQuery.data?.session.id;

  const summaryQuery = useQuery({
    queryKey: ['memoir', projectId, 'summary', sessionId],
    queryFn: () => memoirApi.getSessionSummary(sessionId!),
    enabled: Boolean(sessionId),
  });
  const coverageQuery = useQuery({
    queryKey: ['memoir', projectId, 'coverage'],
    queryFn: () => memoirApi.getCoverage(projectId),
  });
  const peopleQuery = useQuery({ queryKey: ['memoir', projectId, 'people'], queryFn: () => memoirApi.listPeople(projectId) });
  const eventsQuery = useQuery({ queryKey: ['memoir', projectId, 'events'], queryFn: () => memoirApi.listEvents(projectId) });
  const placesQuery = useQuery({ queryKey: ['memoir', projectId, 'places'], queryFn: () => memoirApi.listPlaces(projectId) });
  const themesQuery = useQuery({ queryKey: ['memoir', projectId, 'themes'], queryFn: () => memoirApi.listThemes(projectId) });
  const openQuestionsQuery = useQuery({
    queryKey: ['memoir', projectId, 'openQuestions'],
    queryFn: () => memoirApi.listOpenQuestions(projectId),
  });
  const candidatesQuery = useQuery({
    queryKey: ['memoir', projectId, 'candidates'],
    queryFn: () => memoirApi.listQuestionCandidates(projectId),
  });
  const outputsQuery = useQuery({ queryKey: ['memoir', projectId, 'outputs'], queryFn: () => memoirApi.listOutputs(projectId) });

  const processMutation = useMutation({
    mutationFn: () => memoirApi.processSession(sessionId!, {}),
    onSuccess: async () => {
      await Promise.all([
        memoirApi.recalculateCoverage(projectId),
        memoirApi.generateQuestionCandidates(projectId),
      ]);
      await invalidateMemoir();
    },
  });
  const updateMemoryMutation = useMutation({
    mutationFn: (input: { type: MemoryCard['type']; id: string; status: MemoirMemoryStatus }) =>
      memoirApi.updateMemory(input.type, input.id, { status: input.status }, projectId),
    onSuccess: invalidateMemoir,
  });
  const updateOpenQuestionMutation = useMutation({
    mutationFn: (input: { id: string; status: 'answered' | 'dismissed' }) =>
      memoirApi.updateOpenQuestion(input.id, { status: input.status }, projectId),
    onSuccess: invalidateMemoir,
  });
  const selectCandidateMutation = useMutation({
    mutationFn: memoirApi.selectQuestionCandidate,
    onSuccess: invalidateMemoir,
  });

  const memoryCards = buildMemoryCards(
    peopleQuery.data ?? [],
    eventsQuery.data ?? [],
    placesQuery.data ?? [],
    themesQuery.data ?? [],
  );
  const coverage = coverageQuery.data ?? [];
  const openQuestions = (openQuestionsQuery.data ?? []).filter((item) => item.status === 'open');
  const candidates = candidatesQuery.data ?? [];
  const outputs = outputsQuery.data ?? [];
  const isBusy = processMutation.isPending || updateMemoryMutation.isPending || selectCandidateMutation.isPending;

  return (
    <div className="px-3 py-3 md:px-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-gray-800">
          <Sparkles size={16} className="text-pink-500" />
          <span>回忆沉淀</span>
        </div>
        <button
          type="button"
          onClick={() => processMutation.mutate()}
          disabled={!sessionId || processMutation.isPending}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-pink-200 bg-white px-2.5 text-xs font-medium text-pink-700 hover:bg-pink-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {processMutation.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Wand2 size={14} />}
          <span>处理本次访谈</span>
        </button>
      </div>

      <div className="grid gap-3 xl:grid-cols-[1.1fr_1fr]">
        <section className="min-w-0">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
            <Gauge size={14} />
            <span>覆盖度</span>
          </div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5 xl:grid-cols-2 2xl:grid-cols-5">
            {coverage.slice(0, 10).map((item) => (
              <div key={item.topic_id} className="rounded-md border border-gray-200 bg-white px-2.5 py-2">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="truncate text-xs font-medium text-gray-700">{item.name}</span>
                  <span className="text-xs text-gray-500">{Math.round(item.coverage_score)}%</span>
                </div>
                <div className="h-1.5 rounded-full bg-gray-100">
                  <div
                    className={`h-1.5 rounded-full ${coverageColor(item.status)}`}
                    style={{ width: `${Math.min(100, Math.max(0, item.coverage_score))}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="min-w-0">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
            <FileText size={14} />
            <span>本次摘要</span>
          </div>
          <div className="min-h-[76px] rounded-md border border-gray-200 bg-white px-3 py-2 text-sm leading-6 text-gray-700">
            {summaryQuery.data?.summary || latestOutputTitle(outputs) || '暂无摘要'}
          </div>
        </section>
      </div>

      <div className="mt-3 grid gap-3 xl:grid-cols-[1.2fr_0.8fr]">
        <section className="min-w-0">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
            <Users size={14} />
            <span>记忆卡片</span>
          </div>
          <div className="grid gap-2 md:grid-cols-2 2xl:grid-cols-4">
            {memoryCards.slice(0, 8).map((item) => (
              <MemoryCardItem
                key={`${item.type}:${item.id}`}
                item={item}
                busy={isBusy}
                onStatus={(status) => updateMemoryMutation.mutate({ type: item.type, id: item.id, status })}
              />
            ))}
            {!memoryCards.length && (
              <div className="rounded-md border border-dashed border-gray-200 bg-white px-3 py-3 text-sm text-gray-500">
                暂无结构化记忆
              </div>
            )}
          </div>
        </section>

        <section className="min-w-0">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
            <HelpCircle size={14} />
            <span>待确认与下一问</span>
          </div>
          <div className="grid gap-2">
            {openQuestions.slice(0, 3).map((item) => (
              <OpenQuestionItem
                key={item.id}
                item={item}
                busy={updateOpenQuestionMutation.isPending}
                onDone={() => updateOpenQuestionMutation.mutate({ id: item.id, status: 'answered' })}
                onDismiss={() => updateOpenQuestionMutation.mutate({ id: item.id, status: 'dismissed' })}
              />
            ))}
            {candidates.slice(0, 2).map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => selectCandidateMutation.mutate(item.id)}
                disabled={selectCandidateMutation.isPending}
                className="flex items-start gap-2 rounded-md border border-pink-100 bg-pink-50/50 px-3 py-2 text-left text-sm text-gray-700 hover:bg-pink-50 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <MapPin size={14} className="mt-1 flex-shrink-0 text-pink-500" />
                <span className="line-clamp-2">{item.question}</span>
              </button>
            ))}
            {!openQuestions.length && !candidates.length && (
              <div className="rounded-md border border-dashed border-gray-200 bg-white px-3 py-3 text-sm text-gray-500">
                暂无推荐问题
              </div>
            )}
          </div>
        </section>
      </div>

      {processMutation.isError && (
        <div className="mt-2 text-xs text-red-600">本次访谈暂时无法处理</div>
      )}
    </div>
  );
}

function buildMemoryCards(
  people: MemoirPersonMemory[],
  events: MemoirEventMemory[],
  places: MemoirPlaceMemory[],
  themes: MemoirThemeMemory[],
): MemoryCard[] {
  return [
    ...people.map((item): MemoryCard => ({
      type: 'person',
      id: item.id,
      title: item.canonical_name,
      detail: item.relationship || item.description || '人物',
      status: item.status,
    })),
    ...events.map((item): MemoryCard => ({
      type: 'event',
      id: item.id,
      title: item.title,
      detail: [item.time_text, item.place_text, item.emotion].filter(Boolean).join(' · ') || '事件',
      status: item.status,
    })),
    ...places.map((item): MemoryCard => ({
      type: 'place',
      id: item.id,
      title: item.name,
      detail: item.elder_alias || item.place_type || '地点',
      status: item.status,
    })),
    ...themes.map((item): MemoryCard => ({
      type: 'theme',
      id: item.id,
      title: item.name,
      detail: item.description || `提到 ${item.mention_count} 次`,
      status: item.status,
    })),
  ];
}

function MemoryCardItem({
  item,
  busy,
  onStatus,
}: {
  item: MemoryCard;
  busy: boolean;
  onStatus: (status: MemoirMemoryStatus) => void;
}) {
  return (
    <div className="rounded-md border border-gray-200 bg-white px-3 py-2">
      <div className="mb-1 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-gray-800">{item.title}</div>
          <div className="truncate text-xs text-gray-500">{item.detail}</div>
        </div>
        <span className={`flex-shrink-0 rounded px-1.5 py-0.5 text-xs ${memoryStatusClass(item.status)}`}>
          {statusText[item.status]}
        </span>
      </div>
      <div className="flex items-center gap-1">
        <button
          type="button"
          aria-label="确认记忆"
          title="确认"
          disabled={busy || item.status === 'confirmed'}
          onClick={() => onStatus('confirmed')}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-emerald-200 text-emerald-700 hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Check size={14} />
        </button>
        <button
          type="button"
          aria-label="拒绝记忆"
          title="拒绝"
          disabled={busy || item.status === 'rejected'}
          onClick={() => onStatus('rejected')}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-rose-200 text-rose-700 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}

function OpenQuestionItem({
  item,
  busy,
  onDone,
  onDismiss,
}: {
  item: MemoirOpenQuestion;
  busy: boolean;
  onDone: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="rounded-md border border-gray-200 bg-white px-3 py-2">
      <div className="line-clamp-2 text-sm text-gray-800">{item.question}</div>
      <div className="mt-1 flex items-center justify-between gap-2">
        <span className="truncate text-xs text-gray-500">{item.reason || item.question_type}</span>
        <div className="flex flex-shrink-0 items-center gap-1">
          <button
            type="button"
            aria-label="标记已回答"
            title="已回答"
            disabled={busy}
            onClick={onDone}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-emerald-200 text-emerald-700 hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Check size={14} />
          </button>
          <button
            type="button"
            aria-label="忽略问题"
            title="忽略"
            disabled={busy}
            onClick={onDismiss}
            className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <X size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

function coverageColor(status: string) {
  if (status === 'green') return 'bg-emerald-500';
  if (status === 'yellow') return 'bg-amber-500';
  return 'bg-rose-500';
}

function memoryStatusClass(status: MemoirMemoryStatus) {
  if (status === 'confirmed') return 'bg-emerald-50 text-emerald-700';
  if (status === 'rejected') return 'bg-rose-50 text-rose-700';
  return 'bg-amber-50 text-amber-700';
}

function latestOutputTitle(outputs: MemoirOutput[]) {
  const story = outputs.find((item) => item.output_type === 'story_fragment');
  return story ? `故事片段：${story.title}` : '';
}
