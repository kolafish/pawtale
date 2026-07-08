import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  BookOpen,
  ChevronRight,
  Check,
  Clock3,
  Copy,
  FileAudio,
  Link2,
  Loader2,
  MessageSquareText,
  Pencil,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Save,
  SkipBack,
  SkipForward,
  Sparkles,
  Unlink,
  UserRound,
  X,
} from 'lucide-react';
import { memoirChapterSourceEditorApi, memoirInterviewEditorApi, memoirTranscriptCleanupApi } from '../../api';
import type {
  MemoirBatchTranscriptCleanupPreviewItem,
  MemoirChapterSourceEditor,
  MemoirChapterSourceEditorSource,
  MemoirDialectGlossaryEntry,
  MemoirInterviewEditorAnswer,
  MemoirInterviewEditorQuestion,
  MemoirInterviewEditorReviewIssue,
  MemoirInterviewEditorSessionSummary,
  Project,
} from '../../types';

interface MemoirInterviewEditorProps {
  project: Project;
}

const PLAYBACK_RATES = [0.75, 1, 1.25, 1.5];
const MAX_BATCH_CLEANUP_ITEMS = 30;

function formatDateTime(value?: string | null) {
  if (!value) return '无时间';
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatDuration(seconds: number | null | undefined) {
  const value = Math.max(0, Math.round(seconds || 0));
  const mins = Math.floor(value / 60);
  const secs = value % 60;
  return `${mins}:${String(secs).padStart(2, '0')}`;
}

function answerPreview(answer: MemoirInterviewEditorAnswer) {
  const text = answer.display_text.trim();
  if (!text) return '这段回答暂无文字';
  return text.length > 72 ? `${text.slice(0, 72)}...` : text;
}

function allAnswers(questions: MemoirInterviewEditorQuestion[], unassigned: MemoirInterviewEditorAnswer[]) {
  return [
    ...questions.flatMap((question) => question.answers),
    ...unassigned,
  ];
}

function sourceByUtterance(sources: MemoirChapterSourceEditorSource[] | undefined) {
  return new Map((sources ?? []).map((source) => [source.utterance_id, source]));
}

function normalizeGlossaryEntries(entries: MemoirDialectGlossaryEntry[]) {
  const normalized: MemoirDialectGlossaryEntry[] = [];
  const seen = new Set<string>();
  for (const entry of entries) {
    const source = entry.source.trim();
    const target = entry.target.trim();
    const note = (entry.note ?? '').trim();
    if (!source || !target) continue;
    const key = `${source}\u0000${target}`;
    if (seen.has(key)) continue;
    seen.add(key);
    normalized.push({ source, target, note: note || undefined });
  }
  return normalized;
}

function glossaryEquals(a: MemoirDialectGlossaryEntry[], b: MemoirDialectGlossaryEntry[]) {
  return JSON.stringify(normalizeGlossaryEntries(a)) === JSON.stringify(normalizeGlossaryEntries(b));
}

function reviewIssueClass(issue: MemoirInterviewEditorReviewIssue) {
  if (issue.severity === 'warning') return 'bg-amber-50 text-amber-800';
  if (issue.severity === 'error') return 'bg-red-50 text-red-700';
  return 'bg-slate-100 text-gray-600';
}

async function writeClipboardText(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  document.execCommand('copy');
  document.body.removeChild(textarea);
}

function SessionButton({
  session,
  selected,
  onSelect,
}: {
  session: MemoirInterviewEditorSessionSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full rounded-lg px-3 py-3 text-left transition-[background-color,box-shadow,transform] active:scale-[0.96] ${
        selected
          ? 'bg-white text-gray-900 shadow-[0_1px_10px_rgba(15,23,42,0.08)]'
          : 'text-gray-600 hover:bg-white/70'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-medium">{formatDateTime(session.started_at || session.created_at)}</span>
        <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600 tabular-nums">
          {session.answer_count}
        </span>
      </div>
      <div className="mt-2 flex items-center gap-3 text-xs text-gray-500">
        <span className="inline-flex items-center gap-1">
          <MessageSquareText size={13} />
          {session.question_count}
        </span>
        <span className="inline-flex items-center gap-1 tabular-nums">
          <Clock3 size={13} />
          {formatDuration(session.total_audio_seconds)}
        </span>
        {session.unassigned_answer_count > 0 && (
          <span className="text-amber-700 tabular-nums">未归属 {session.unassigned_answer_count}</span>
        )}
      </div>
      {session.last_message_text && (
        <p className="mt-2 line-clamp-2 text-xs leading-5 text-gray-500">{session.last_message_text}</p>
      )}
    </button>
  );
}

function AnswerRow({
  answer,
  selected,
  checked,
  chapterTitle,
  checkboxDisabled = false,
  onSelect,
  onToggleChecked,
}: {
  answer: MemoirInterviewEditorAnswer;
  selected: boolean;
  checked: boolean;
  chapterTitle?: string | null;
  checkboxDisabled?: boolean;
  onSelect: () => void;
  onToggleChecked: () => void;
}) {
  return (
    <div
      className={`w-full rounded-lg px-3 py-2.5 text-left shadow-[0_1px_8px_rgba(15,23,42,0.06)] transition-[background-color,box-shadow,transform] active:scale-[0.96] ${
        selected
          ? 'bg-emerald-50 text-emerald-950 ring-2 ring-emerald-200'
          : 'bg-white text-gray-700 hover:bg-slate-50'
      }`}
    >
      <div className="flex items-start gap-2">
        <input
          type="checkbox"
          checked={checked}
          disabled={checkboxDisabled}
          onChange={onToggleChecked}
          onClick={(event) => event.stopPropagation()}
          aria-label="选择回答"
          className="mt-1 h-4 w-4 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
        />
        <button type="button" onClick={onSelect} className="flex min-w-0 flex-1 items-start gap-2 text-left">
          <UserRound size={15} className={selected ? 'mt-0.5 flex-shrink-0 text-emerald-700' : 'mt-0.5 flex-shrink-0 text-gray-400'} />
          <div className="min-w-0 flex-1">
          <p className="line-clamp-2 text-sm leading-5">{answerPreview(answer)}</p>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-gray-500">
            <span className="tabular-nums">#{answer.turn_index}</span>
            {answer.audio_duration_seconds !== null && answer.audio_duration_seconds !== undefined && (
              <span className="inline-flex items-center gap-1 tabular-nums">
                <FileAudio size={12} />
                {formatDuration(answer.audio_duration_seconds)}
              </span>
            )}
            {chapterTitle ? (
              <span className="inline-flex min-w-0 items-center gap-1 text-emerald-700">
                <BookOpen size={12} />
                <span className="truncate">{chapterTitle}</span>
              </span>
            ) : answer.is_included_in_book && (
              <span className="inline-flex items-center gap-1 text-emerald-700">
                <Check size={12} />
                已收录
              </span>
            )}
          </div>
          </div>
        </button>
      </div>
    </div>
  );
}

export function MemoirInterviewEditor({ project }: MemoirInterviewEditorProps) {
  const queryClient = useQueryClient();
  const canEditMaterials = project.can_edit_memoir_materials !== false;
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [selectedSessionOverride, setSelectedSessionOverride] = useState<string | null>(null);
  const [selectedAnswerOverride, setSelectedAnswerOverride] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ answerId: string; text: string } | null>(null);
  const [checkedAnswerIds, setCheckedAnswerIds] = useState<string[]>([]);
  const [targetQuestionId, setTargetQuestionId] = useState('');
  const [targetChapterId, setTargetChapterId] = useState('');
  const [newQuestionText, setNewQuestionText] = useState('');
  const [editingQuestion, setEditingQuestion] = useState<{ id: string; text: string } | null>(null);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [isAudioPlaying, setIsAudioPlaying] = useState(false);
  const [glossaryDraftOverride, setGlossaryDraftOverride] = useState<MemoirDialectGlossaryEntry[] | null>(null);
  const [cleanupPreviewItems, setCleanupPreviewItems] = useState<MemoirBatchTranscriptCleanupPreviewItem[]>([]);
  const [copyReviewStatus, setCopyReviewStatus] = useState<'idle' | 'copied' | 'failed'>('idle');

  const sessionsQuery = useQuery({
    queryKey: ['memoirInterviewEditorSessions', project.id],
    queryFn: () => memoirInterviewEditorApi.listSessions(project.id, 'human_interview'),
  });

  const sessions = useMemo(() => sessionsQuery.data?.items ?? [], [sessionsQuery.data?.items]);
  const selectedSessionId = (
    selectedSessionOverride && sessions.some((session) => session.id === selectedSessionOverride)
      ? selectedSessionOverride
      : sessions[0]?.id
  ) ?? null;

  const detailQuery = useQuery({
    queryKey: ['memoirInterviewEditorSession', project.id, selectedSessionId],
    queryFn: () => memoirInterviewEditorApi.getSession(project.id, selectedSessionId as string),
    enabled: Boolean(selectedSessionId),
  });

  const chapterSourceQuery = useQuery({
    queryKey: ['memoirChapterSourceEditor', project.id],
    queryFn: () => memoirChapterSourceEditorApi.get(project.id),
  });

  const reviewQuery = useQuery({
    queryKey: ['memoirInterviewEditorReview', project.id, selectedSessionId],
    queryFn: () => memoirInterviewEditorApi.getSessionReview(project.id, selectedSessionId as string),
    enabled: Boolean(selectedSessionId),
  });

  const glossaryQuery = useQuery({
    queryKey: ['memoirTranscriptCleanupGlossary', project.id],
    queryFn: () => memoirTranscriptCleanupApi.getGlossary(project.id),
  });
  const glossaryDraft = glossaryDraftOverride ?? glossaryQuery.data?.entries ?? [];

  const detail = detailQuery.data;
  const review = reviewQuery.data;
  const chapterEditor = chapterSourceQuery.data;
  const chapterSourcesByUtterance = useMemo(
    () => sourceByUtterance(chapterEditor?.sources),
    [chapterEditor?.sources],
  );
  const answers = useMemo(
    () => detail ? allAnswers(detail.questions, detail.unassigned_answers) : [],
    [detail],
  );
  const selectedAnswerId = (
    selectedAnswerOverride && answers.some((answer) => answer.id === selectedAnswerOverride)
      ? selectedAnswerOverride
      : answers[0]?.id
  ) ?? null;
  const selectedAnswer = answers.find((answer) => answer.id === selectedAnswerId) ?? null;
  const selectedAnswerIndex = selectedAnswer ? answers.findIndex((answer) => answer.id === selectedAnswer.id) : -1;
  const nextAnswer = selectedAnswerIndex >= 0 ? answers[selectedAnswerIndex + 1] ?? null : null;
  const checkedAnswers = answers.filter((answer) => checkedAnswerIds.includes(answer.id));
  const checkedAnswerMessageIds = checkedAnswers.map((answer) => answer.message_id);
  const chapterSelectedAnswers = checkedAnswers.length > 0 ? checkedAnswers : selectedAnswer ? [selectedAnswer] : [];
  const cleanupSelectedAnswers = checkedAnswers.length > 0 ? checkedAnswers : selectedAnswer ? [selectedAnswer] : [];
  const cleanupSelectedAnswerIds = cleanupSelectedAnswers.map((answer) => answer.id);
  const chapterSelectedSources = chapterSelectedAnswers
    .map((answer) => chapterSourcesByUtterance.get(answer.id))
    .filter((source): source is MemoirChapterSourceEditorSource => Boolean(source));
  const chapterSelectedSourceIds = chapterSelectedSources.map((source) => source.utterance_id);
  const selectedAnswerSource = selectedAnswer ? chapterSourcesByUtterance.get(selectedAnswer.id) ?? null : null;
  const effectiveTargetQuestionId = detail?.questions.some((question) => question.id === targetQuestionId)
    ? targetQuestionId
    : '';
  const effectiveTargetChapterId = chapterEditor?.chapters.some((chapter) => chapter.id === targetChapterId)
    ? targetChapterId
    : '';
  const draftText = selectedAnswer
    ? (draft?.answerId === selectedAnswer.id ? draft.text : selectedAnswer.display_text)
    : '';

  const saveMutation = useMutation({
    mutationFn: ({ answerId, text }: { answerId: string; text: string; moveNext?: boolean }) =>
      memoirInterviewEditorApi.updateAnswerTranscript(answerId, text),
    onSuccess: (_data, variables) => {
      setDraft(null);
      if (variables.moveNext && nextAnswer) {
        setSelectedAnswerOverride(nextAnswer.id);
      }
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorSession', project.id, selectedSessionId] });
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorReview', project.id, selectedSessionId] });
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorSessions', project.id] });
      queryClient.invalidateQueries({ queryKey: ['memoirChapterSourceEditor', project.id] });
    },
  });

  const createQuestionMutation = useMutation({
    mutationFn: ({ voiceSessionId, text }: { voiceSessionId: string; text: string }) =>
      memoirInterviewEditorApi.createQuestion(project.id, voiceSessionId, text),
    onSuccess: (data) => {
      setNewQuestionText('');
      setTargetQuestionId(data.questions[data.questions.length - 1]?.id ?? '');
      queryClient.setQueryData(['memoirInterviewEditorSession', project.id, selectedSessionId], data);
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorReview', project.id, selectedSessionId] });
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorSessions', project.id] });
    },
  });

  const updateQuestionMutation = useMutation({
    mutationFn: ({ messageId, text }: { messageId: string; text: string }) =>
      memoirInterviewEditorApi.updateQuestionText(project.id, messageId, text),
    onSuccess: (data) => {
      setEditingQuestion(null);
      queryClient.setQueryData(['memoirInterviewEditorSession', project.id, selectedSessionId], data);
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorReview', project.id, selectedSessionId] });
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorSessions', project.id] });
    },
  });

  const updateParentMutation = useMutation({
    mutationFn: async ({ messageIds, parentQuestionId }: { messageIds: string[]; parentQuestionId: string | null }) => {
      let latest = null;
      for (const messageId of messageIds) {
        latest = await memoirInterviewEditorApi.updateAnswerParent(
          project.id,
          messageId,
          parentQuestionId,
          parentQuestionId ? 'web_batch_move_answer' : 'web_batch_unassign_answer',
        );
      }
      return latest;
    },
    onSuccess: (data) => {
      setCheckedAnswerIds([]);
      if (data) {
        queryClient.setQueryData(['memoirInterviewEditorSession', project.id, selectedSessionId], data);
      } else {
        queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorSession', project.id, selectedSessionId] });
      }
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorReview', project.id, selectedSessionId] });
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorSessions', project.id] });
    },
  });

  const chapterSourceMutation = useMutation({
    mutationFn: async (
      input:
        | { type: 'assign'; sourceIds: string[]; targetChapterId: string }
        | { type: 'move'; sourceIds: string[]; targetChapterId: string }
        | { type: 'remove'; sourceIds: string[] },
    ) => {
      let latest: MemoirChapterSourceEditor | null = null;
      if (input.type === 'assign') {
        latest = await memoirChapterSourceEditorApi.assign(
          project.id,
          [{ chapter_id: input.targetChapterId, utterance_ids: input.sourceIds }],
        );
      } else if (input.type === 'move') {
        for (const sourceId of input.sourceIds) {
          const source = chapterSourcesByUtterance.get(sourceId);
          if (!source?.current_chapter_id || source.current_chapter_id === input.targetChapterId) {
            continue;
          }
          latest = await memoirChapterSourceEditorApi.move(
            project.id,
            source.current_chapter_id,
            input.targetChapterId,
            sourceId,
          );
        }
      } else {
        const sourceIdsByChapter = new Map<string, string[]>();
        for (const sourceId of input.sourceIds) {
          const source = chapterSourcesByUtterance.get(sourceId);
          if (!source?.current_chapter_id) {
            continue;
          }
          sourceIdsByChapter.set(
            source.current_chapter_id,
            [...(sourceIdsByChapter.get(source.current_chapter_id) ?? []), sourceId],
          );
        }
        for (const [chapterId, sourceIds] of sourceIdsByChapter) {
          latest = await memoirChapterSourceEditorApi.remove(project.id, chapterId, sourceIds);
        }
      }
      return latest ?? memoirChapterSourceEditorApi.get(project.id);
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['memoirChapterSourceEditor', project.id], data);
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorSession', project.id, selectedSessionId] });
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorReview', project.id, selectedSessionId] });
    },
  });

  const saveGlossaryMutation = useMutation({
    mutationFn: (entries: MemoirDialectGlossaryEntry[]) =>
      memoirTranscriptCleanupApi.updateGlossary(project.id, normalizeGlossaryEntries(entries)),
    onSuccess: (data) => {
      setGlossaryDraftOverride(null);
      queryClient.setQueryData(['memoirTranscriptCleanupGlossary', project.id], data);
    },
  });

  const previewCleanupMutation = useMutation({
    mutationFn: (utteranceIds: string[]) =>
      memoirTranscriptCleanupApi.preview(project.id, utteranceIds),
    onSuccess: (data) => {
      setCleanupPreviewItems(data.items);
    },
  });

  const confirmCleanupMutation = useMutation({
    mutationFn: (items: MemoirBatchTranscriptCleanupPreviewItem[]) =>
      memoirTranscriptCleanupApi.confirm(
        project.id,
        items.map((item) => ({
          utterance_id: item.utterance_id,
          cleaned_text: item.cleaned_text,
          provider: item.provider,
          created_by: item.created_by,
        })),
      ),
    onSuccess: () => {
      setCleanupPreviewItems([]);
      setCheckedAnswerIds([]);
      setDraft(null);
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorSession', project.id, selectedSessionId] });
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorReview', project.id, selectedSessionId] });
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorSessions', project.id] });
      queryClient.invalidateQueries({ queryKey: ['memoirChapterSourceEditor', project.id] });
    },
  });

  const hasUnsavedChanges = Boolean(
    selectedAnswer
    && draft?.answerId === selectedAnswer.id
    && draft.text !== selectedAnswer.display_text,
  );
  const canSave = Boolean(
    canEditMaterials
    && selectedAnswer
    && draftText.trim()
    && hasUnsavedChanges
    && !saveMutation.isPending,
  );
  const canSaveAndNext = Boolean(canSave && nextAnswer);

  const handleSave = (moveNext = false) => {
    if (!selectedAnswer || !canSave) return;
    saveMutation.mutate({ answerId: selectedAnswer.id, text: draftText.trim(), moveNext });
  };

  const copyReviewMarkdown = useCallback(async () => {
    if (!review?.markdown) return;
    try {
      await writeClipboardText(review.markdown);
      setCopyReviewStatus('copied');
      window.setTimeout(() => setCopyReviewStatus('idle'), 1600);
    } catch {
      setCopyReviewStatus('failed');
      window.setTimeout(() => setCopyReviewStatus('idle'), 2400);
    }
  }, [review]);

  const confirmDiscardDraft = useCallback(() => (
    !hasUnsavedChanges || window.confirm('当前文字尚未保存，确定切换吗？')
  ), [hasUnsavedChanges]);

  const selectAnswer = useCallback((answerId: string) => {
    if (!confirmDiscardDraft()) return;
    setSelectedAnswerOverride(answerId);
    setDraft(null);
    setCleanupPreviewItems([]);
  }, [confirmDiscardDraft]);

  const seekAudioBy = useCallback((seconds: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = Math.min(
      Math.max(0, audio.currentTime + seconds),
      Number.isFinite(audio.duration) ? audio.duration : audio.currentTime + seconds,
    );
  }, []);

  const toggleAudioPlayback = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      audio.play().catch(() => {
        setIsAudioPlaying(false);
      });
    } else {
      audio.pause();
    }
  }, []);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.playbackRate = playbackRate;
  }, [playbackRate, selectedAnswer?.audio_file_id]);

  useEffect(() => {
    if (!hasUnsavedChanges) return;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [hasUnsavedChanges]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        if (selectedAnswer && canSave) {
          saveMutation.mutate({ answerId: selectedAnswer.id, text: draftText.trim(), moveNext: false });
        }
        return;
      }
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault();
        if (selectedAnswer && canSave) {
          saveMutation.mutate({ answerId: selectedAnswer.id, text: draftText.trim(), moveNext: true });
        }
        return;
      }
      const target = event.target as HTMLElement | null;
      const isTextEditingTarget = target instanceof HTMLInputElement
        || target instanceof HTMLTextAreaElement
        || Boolean(target?.isContentEditable);
      if (isTextEditingTarget) return;
      if (!event.altKey) return;
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        seekAudioBy(-3);
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        seekAudioBy(3);
      } else if (event.key === ' ') {
        event.preventDefault();
        toggleAudioPlayback();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [canSave, draftText, saveMutation, seekAudioBy, selectedAnswer, toggleAudioPlayback]);

  const toggleAnswerChecked = (answerId: string) => {
    if (!canEditMaterials) return;
    setCleanupPreviewItems([]);
    setCheckedAnswerIds((ids) => (
      ids.includes(answerId)
        ? ids.filter((id) => id !== answerId)
        : [...ids, answerId]
    ));
  };

  const selectAllVisibleAnswers = () => {
    if (!canEditMaterials) return;
    setCleanupPreviewItems([]);
    setCheckedAnswerIds(answers.map((answer) => answer.id));
  };

  const canCreateQuestion = Boolean(
    canEditMaterials
    && selectedSessionId
    && newQuestionText.trim()
    && !createQuestionMutation.isPending,
  );
  const canUpdateQuestion = Boolean(
    canEditMaterials
    && editingQuestion
    && editingQuestion.text.trim()
    && detail?.questions.some((question) => question.id === editingQuestion.id && question.text !== editingQuestion.text),
  );
  const canMoveCheckedAnswers = Boolean(
    canEditMaterials
    && checkedAnswerMessageIds.length > 0
    && effectiveTargetQuestionId
    && !updateParentMutation.isPending,
  );
  const canUnassignCheckedAnswers = Boolean(
    canEditMaterials
    && checkedAnswerMessageIds.length > 0
    && !updateParentMutation.isPending,
  );
  const hasConfirmedChapterPlan = chapterEditor?.plan_status === 'confirmed' && (chapterEditor?.chapters.length ?? 0) > 0;
  const hasChapterSourceSelection = chapterSelectedSources.length > 0
    && chapterSelectedSources.length === chapterSelectedAnswers.length;
  const selectedSourcesAreUnassigned = hasChapterSourceSelection
    && chapterSelectedSources.every((source) => !source.current_chapter_id);
  const selectedSourcesAreAssigned = hasChapterSourceSelection
    && chapterSelectedSources.every((source) => Boolean(source.current_chapter_id));
  const selectedAssignedSourceCount = chapterSelectedSources.filter((source) => source.current_chapter_id).length;
  const selectedUnassignedSourceCount = chapterSelectedSources.length - selectedAssignedSourceCount;
  const chapterSelectionCurrentLabel = checkedAnswers.length > 0
    ? `${selectedAssignedSourceCount} 段已有章节`
    : selectedAnswerSource?.current_chapter_title || '未归入章节';
  const pendingChapterSourceAction = chapterSourceMutation.isPending ? chapterSourceMutation.variables?.type : null;
  const selectedSourceCountByChapter = new Map<string, number>();
  for (const source of chapterSelectedSources) {
    if (!source.current_chapter_id) continue;
    selectedSourceCountByChapter.set(
      source.current_chapter_id,
      (selectedSourceCountByChapter.get(source.current_chapter_id) ?? 0) + 1,
    );
  }
  const moveWouldEmptySourceChapter = selectedSourcesAreAssigned
    && Boolean(effectiveTargetChapterId)
    && Boolean(chapterEditor?.chapters.some((chapter) => {
      const selectedCount = chapterSelectedSources.filter(
        (source) => source.current_chapter_id === chapter.id
          && source.current_chapter_id !== effectiveTargetChapterId,
      ).length;
      return selectedCount > 0 && selectedCount >= chapter.source_refs.length;
    }));
  const removeWouldEmptySourceChapter = selectedSourcesAreAssigned
    && Boolean(chapterEditor?.chapters.some((chapter) => {
      const selectedCount = selectedSourceCountByChapter.get(chapter.id) ?? 0;
      return selectedCount > 0 && selectedCount >= chapter.source_refs.length;
    }));
  const selectedSourcesCanMove = selectedSourcesAreAssigned
    && Boolean(effectiveTargetChapterId)
    && !moveWouldEmptySourceChapter
    && chapterSelectedSources.some((source) => source.current_chapter_id !== effectiveTargetChapterId);
  const canAssignChapterSources = Boolean(
    canEditMaterials
    && hasConfirmedChapterPlan
    && selectedSourcesAreUnassigned
    && effectiveTargetChapterId
    && !chapterSourceMutation.isPending,
  );
  const canMoveChapterSources = Boolean(
    canEditMaterials
    && hasConfirmedChapterPlan
    && selectedSourcesCanMove
    && !chapterSourceMutation.isPending,
  );
  const canRemoveChapterSources = Boolean(
    canEditMaterials
    && hasConfirmedChapterPlan
    && selectedSourcesAreAssigned
    && !removeWouldEmptySourceChapter
    && !chapterSourceMutation.isPending,
  );
  const glossaryHasChanges = !glossaryEquals(glossaryDraft, glossaryQuery.data?.entries ?? []);
  const canSaveGlossary = Boolean(
    canEditMaterials
    && glossaryHasChanges
    && !glossaryQuery.isLoading
    && !saveGlossaryMutation.isPending,
  );
  const canPreviewCleanup = Boolean(
    canEditMaterials
    && cleanupSelectedAnswerIds.length > 0
    && cleanupSelectedAnswerIds.length <= MAX_BATCH_CLEANUP_ITEMS
    && !hasUnsavedChanges
    && !previewCleanupMutation.isPending
    && !confirmCleanupMutation.isPending,
  );
  const canConfirmCleanup = Boolean(
    canEditMaterials
    && cleanupPreviewItems.length > 0
    && cleanupPreviewItems.every((item) => item.cleaned_text.trim())
    && !confirmCleanupMutation.isPending
    && !previewCleanupMutation.isPending,
  );

  return (
    <div className="h-full min-w-0 flex-1 bg-slate-100 text-gray-800">
      <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[300px_minmax(0,1fr)_390px]">
        <aside className="min-h-0 border-b border-slate-200 bg-slate-50 lg:border-b-0 lg:border-r">
          <div className="flex h-full min-h-0 flex-col">
            <div className="border-b border-slate-200 px-4 py-4">
              <h2 className="text-base font-semibold text-gray-900">访谈素材</h2>
              <p className="mt-1 text-xs text-gray-500">{project.title}</p>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-3">
              {sessionsQuery.isLoading ? (
                <div className="flex items-center gap-2 px-2 py-8 text-sm text-gray-500">
                  <Loader2 size={16} className="animate-spin" />
                  加载访谈记录
                </div>
              ) : sessionsQuery.isError ? (
                <div className="rounded-lg bg-red-50 px-3 py-3 text-sm text-red-700">
                  访谈记录加载失败
                </div>
              ) : sessions.length === 0 ? (
                <div className="rounded-lg bg-white px-3 py-6 text-sm leading-6 text-gray-500">
                  暂无真人访谈记录。小程序端完成真人访谈后会出现在这里。
                </div>
              ) : (
                <div className="space-y-2">
                  {sessions.map((session) => (
                    <SessionButton
                      key={session.id}
                      session={session}
                      selected={session.id === selectedSessionId}
                      onSelect={() => {
                        if (!confirmDiscardDraft()) return;
                        setSelectedSessionOverride(session.id);
                        setSelectedAnswerOverride(null);
                        setDraft(null);
                        setCheckedAnswerIds([]);
                        setTargetQuestionId('');
                        setTargetChapterId('');
                        setEditingQuestion(null);
                      }}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        </aside>

        <main className="min-h-0 overflow-y-auto bg-white">
          <div className="border-b border-slate-200 px-5 py-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h1 className="text-lg font-semibold text-gray-900">问答整理</h1>
                <p className="mt-1 text-xs text-gray-500">
                  {detail ? `${detail.session.question_count} 个问题，${detail.session.answer_count} 段回答` : '选择一场访谈查看内容'}
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  sessionsQuery.refetch();
                  if (selectedSessionId) {
                    detailQuery.refetch();
                    reviewQuery.refetch();
                  }
                  chapterSourceQuery.refetch();
                }}
                className="inline-flex h-10 items-center gap-2 rounded-lg bg-slate-100 px-3 text-sm text-gray-600 transition-[background-color,transform] hover:bg-slate-200 active:scale-[0.96]"
              >
                <RefreshCw
                  size={15}
                  className={sessionsQuery.isFetching || detailQuery.isFetching || reviewQuery.isFetching || chapterSourceQuery.isFetching ? 'animate-spin' : ''}
                />
                刷新
              </button>
            </div>
          </div>

          <div className="space-y-7 p-5">
            {detailQuery.isLoading && selectedSessionId ? (
              <div className="flex items-center gap-2 text-sm text-gray-500">
                <Loader2 size={16} className="animate-spin" />
                加载问答
              </div>
            ) : detailQuery.isError ? (
              <div className="flex items-center gap-2 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
                <AlertCircle size={16} />
                问答内容加载失败
              </div>
            ) : detail ? (
              <>
                <section className="space-y-3 border-b border-slate-100 pb-5">
                  <div className="flex flex-col gap-3 rounded-lg bg-slate-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-sm font-medium text-gray-900">
                        <Check size={15} className={review?.issues.length ? 'text-amber-600' : 'text-emerald-600'} />
                        整理检查
                        {reviewQuery.isFetching && <Loader2 size={14} className="animate-spin text-gray-400" />}
                      </div>
                      <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
                        <span className="rounded-md bg-white px-2 py-1 text-gray-600 tabular-nums">
                          问题 {review?.question_count ?? detail.session.question_count}
                        </span>
                        <span className="rounded-md bg-white px-2 py-1 text-gray-600 tabular-nums">
                          回答 {review?.answer_count ?? detail.session.answer_count}
                        </span>
                        <span className={`rounded-md px-2 py-1 tabular-nums ${
                          (review?.unassigned_answer_count ?? detail.session.unassigned_answer_count) > 0
                            ? 'bg-amber-100 text-amber-800'
                            : 'bg-white text-gray-600'
                        }`}
                        >
                          未归属 {review?.unassigned_answer_count ?? detail.session.unassigned_answer_count}
                        </span>
                        <span className={`rounded-md px-2 py-1 tabular-nums ${
                          (review?.not_included_answer_count ?? 0) > 0
                            ? 'bg-slate-100 text-gray-600'
                            : 'bg-white text-gray-600'
                        }`}
                        >
                          未归章 {review?.not_included_answer_count ?? 0}
                        </span>
                      </div>
                    </div>
                    <button
                      type="button"
                      disabled={!review?.markdown}
                      onClick={copyReviewMarkdown}
                      className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-slate-900 px-3 text-sm font-medium text-white transition-[background-color,opacity,transform] hover:bg-slate-700 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {copyReviewStatus === 'copied' ? <Check size={15} /> : <Copy size={15} />}
                      {copyReviewStatus === 'copied' ? '已复制' : '复制整理稿'}
                    </button>
                  </div>
                  {copyReviewStatus === 'failed' && (
                    <div className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">整理稿复制失败。</div>
                  )}
                  {review?.issues.length ? (
                    <div className="flex flex-wrap gap-2">
                      {review.issues.map((issue) => (
                        <span key={issue.code} className={`rounded-lg px-3 py-2 text-xs ${reviewIssueClass(issue)}`}>
                          {issue.message}
                          {issue.count > 0 ? ` ${issue.count}` : ''}
                        </span>
                      ))}
                    </div>
                  ) : review && (
                    <div className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
                      当前整理稿没有发现明显结构问题。
                    </div>
                  )}
                </section>

                <section className="space-y-3 border-b border-slate-100 pb-5">
                  <div className="flex flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
                    <div className="min-w-0 flex-1">
                      <label htmlFor="new-question-text" className="mb-1 block text-xs font-medium text-gray-500">
                        新建采访问题
                      </label>
                      <div className="flex gap-2">
                        <input
                          id="new-question-text"
                          value={newQuestionText}
                          disabled={!canEditMaterials}
                          onChange={(event) => setNewQuestionText(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' && canCreateQuestion && selectedSessionId) {
                              createQuestionMutation.mutate({ voiceSessionId: selectedSessionId, text: newQuestionText.trim() });
                            }
                          }}
                          placeholder="输入一个人工整理的问题"
                          className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-gray-800 outline-none transition-[border-color,box-shadow] focus:border-emerald-300 focus:shadow-[0_0_0_3px_rgba(16,185,129,0.16)] disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-gray-400"
                        />
                        <button
                          type="button"
                          disabled={!canCreateQuestion || !selectedSessionId}
                          onClick={() => selectedSessionId && createQuestionMutation.mutate({ voiceSessionId: selectedSessionId, text: newQuestionText.trim() })}
                          className="inline-flex h-10 items-center gap-2 rounded-lg bg-slate-900 px-3 text-sm font-medium text-white transition-[background-color,opacity,transform] hover:bg-slate-700 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {createQuestionMutation.isPending ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
                          新建
                        </button>
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 px-3 py-2">
                      <span className="text-xs text-gray-500 tabular-nums">已选 {checkedAnswers.length}</span>
                      <button
                        type="button"
                        onClick={selectAllVisibleAnswers}
                        disabled={!canEditMaterials || answers.length === 0}
                        className="h-9 rounded-lg px-2.5 text-xs text-gray-600 transition-[background-color,transform] hover:bg-white active:scale-[0.96] disabled:opacity-40"
                      >
                        全选
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setCheckedAnswerIds([]);
                          setCleanupPreviewItems([]);
                        }}
                        disabled={!canEditMaterials || checkedAnswers.length === 0}
                        className="h-9 rounded-lg px-2.5 text-xs text-gray-600 transition-[background-color,transform] hover:bg-white active:scale-[0.96] disabled:opacity-40"
                      >
                        清除
                      </button>
                      <select
                        value={effectiveTargetQuestionId}
                        onChange={(event) => setTargetQuestionId(event.target.value)}
                        disabled={!canEditMaterials}
                        className="h-9 max-w-52 rounded-lg border border-slate-200 bg-white px-2 text-xs text-gray-700 outline-none focus:ring-2 focus:ring-emerald-200 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <option value="">选择目标问题</option>
                        {detail.questions.map((question) => (
                          <option key={question.id} value={question.id}>
                            #{question.turn_index} {question.text}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        disabled={!canMoveCheckedAnswers}
                        onClick={() => updateParentMutation.mutate({ messageIds: checkedAnswerMessageIds, parentQuestionId: effectiveTargetQuestionId })}
                        className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-emerald-600 px-3 text-xs font-medium text-white transition-[background-color,opacity,transform] hover:bg-emerald-700 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {updateParentMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <Link2 size={14} />}
                        移动
                      </button>
                      <button
                        type="button"
                        disabled={!canUnassignCheckedAnswers}
                        onClick={() => updateParentMutation.mutate({ messageIds: checkedAnswerMessageIds, parentQuestionId: null })}
                        className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-amber-100 px-3 text-xs font-medium text-amber-800 transition-[background-color,opacity,transform] hover:bg-amber-200 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Unlink size={14} />
                        设为未归属
                      </button>
                    </div>
                  </div>
                  {(createQuestionMutation.isError || updateParentMutation.isError || updateQuestionMutation.isError) && (
                    <div className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
                      问答关系保存失败，请刷新后重试。
                    </div>
                  )}
                </section>
                {detail.questions.map((question) => (
                  <section key={question.id} className="space-y-3">
                    <div className="flex items-start gap-3">
                      <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg bg-slate-900 text-white">
                        <MessageSquareText size={16} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                          <span>采访者</span>
                          <span className="tabular-nums">#{question.turn_index}</span>
                          <span>{question.answer_count} 段回答</span>
                        </div>
                        {editingQuestion?.id === question.id ? (
                          <div className="mt-1 flex gap-2">
                            <input
                              value={editingQuestion.text}
                              disabled={!canEditMaterials}
                              onChange={(event) => setEditingQuestion({ id: question.id, text: event.target.value })}
                              className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-gray-800 outline-none focus:ring-2 focus:ring-emerald-200 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-gray-400"
                              autoFocus
                            />
                            <button
                              type="button"
                              disabled={!canUpdateQuestion || updateQuestionMutation.isPending}
                              onClick={() => updateQuestionMutation.mutate({ messageId: question.id, text: editingQuestion.text.trim() })}
                              className="inline-flex h-10 items-center justify-center rounded-lg bg-emerald-600 px-3 text-white transition-[background-color,opacity,transform] hover:bg-emerald-700 active:scale-[0.96] disabled:opacity-50"
                              title="保存问题"
                            >
                              {updateQuestionMutation.isPending ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
                            </button>
                            <button
                              type="button"
                              onClick={() => setEditingQuestion(null)}
                              className="inline-flex h-10 items-center justify-center rounded-lg bg-slate-100 px-3 text-gray-600 transition-[background-color,transform] hover:bg-slate-200 active:scale-[0.96]"
                              title="取消编辑"
                            >
                              <X size={15} />
                            </button>
                          </div>
                        ) : (
                          <div className="mt-1 flex items-start gap-2">
                            <h3 className="min-w-0 flex-1 text-base font-medium leading-7 text-gray-900">{question.text}</h3>
                            <button
                              type="button"
                              onClick={() => setEditingQuestion({ id: question.id, text: question.text })}
                              disabled={!canEditMaterials}
                              className="inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg text-gray-400 transition-[background-color,color,opacity,transform] hover:bg-slate-100 hover:text-gray-700 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-40"
                              title="编辑问题"
                            >
                              <Pencil size={15} />
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                    {question.answers.length > 0 ? (
                      <div className="ml-11 space-y-2">
                        {question.answers.map((answer) => (
                          <AnswerRow
                            key={answer.id}
                            answer={answer}
                            selected={answer.id === selectedAnswerId}
                            checked={checkedAnswerIds.includes(answer.id)}
                            chapterTitle={
                              chapterSourcesByUtterance.get(answer.id)?.current_chapter_title
                              ?? answer.included_chapters[0]?.title
                            }
                            checkboxDisabled={!canEditMaterials}
                            onSelect={() => {
                              selectAnswer(answer.id);
                            }}
                            onToggleChecked={() => toggleAnswerChecked(answer.id)}
                          />
                        ))}
                      </div>
                    ) : (
                      <div className="ml-11 rounded-lg bg-slate-50 px-3 py-3 text-sm text-gray-500">
                        这个问题下暂无回答。
                      </div>
                    )}
                  </section>
                ))}

                {detail.unassigned_answers.length > 0 && (
                  <section className="space-y-3">
                    <div className="flex items-center gap-2 text-sm font-medium text-amber-800">
                      <AlertCircle size={16} />
                      未归属回答
                    </div>
                    <div className="space-y-2">
                      {detail.unassigned_answers.map((answer) => (
                        <AnswerRow
                          key={answer.id}
                          answer={answer}
                          selected={answer.id === selectedAnswerId}
                          checked={checkedAnswerIds.includes(answer.id)}
                          chapterTitle={
                            chapterSourcesByUtterance.get(answer.id)?.current_chapter_title
                            ?? answer.included_chapters[0]?.title
                          }
                          checkboxDisabled={!canEditMaterials}
                          onSelect={() => {
                            selectAnswer(answer.id);
                          }}
                          onToggleChecked={() => toggleAnswerChecked(answer.id)}
                        />
                      ))}
                    </div>
                  </section>
                )}
              </>
            ) : (
              <div className="rounded-lg bg-slate-50 px-4 py-10 text-center text-sm text-gray-500">
                选择左侧访谈记录后开始整理。
              </div>
            )}
          </div>
        </main>

        <aside className="min-h-0 border-t border-slate-200 bg-slate-50 lg:border-l lg:border-t-0">
          <div className="flex h-full min-h-0 flex-col">
            <div className="border-b border-slate-200 px-5 py-4">
              <h2 className="text-base font-semibold text-gray-900">音频与文字</h2>
              <p className="mt-1 text-xs text-gray-500">选中回答后校正文稿</p>
            </div>
            {selectedAnswer ? (
              <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-5">
                <div>
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-gray-500">原始音频</span>
                    <span className="text-xs text-gray-400 tabular-nums">
                      {formatDuration(selectedAnswer.audio_duration_seconds)}
                    </span>
                  </div>
                  {selectedAnswer.audio_file_id ? (
                    <>
                      <audio
                        ref={audioRef}
                        key={selectedAnswer.audio_file_id}
                        controls
                        preload="metadata"
                        className="h-10 w-full"
                        src={memoirInterviewEditorApi.audioPlaybackUrl(selectedAnswer.audio_file_id)}
                        onLoadedMetadata={(event) => {
                          event.currentTarget.playbackRate = playbackRate;
                          setIsAudioPlaying(false);
                        }}
                        onPlay={() => setIsAudioPlaying(true)}
                        onPause={() => setIsAudioPlaying(false)}
                        onEnded={() => setIsAudioPlaying(false)}
                      />
                      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                        <div className="inline-flex rounded-lg bg-white shadow-[0_1px_8px_rgba(15,23,42,0.06)]">
                          <button
                            type="button"
                            onClick={() => seekAudioBy(-3)}
                            className="inline-flex h-9 w-10 items-center justify-center rounded-l-lg text-gray-600 transition-[background-color,transform] hover:bg-slate-100 active:scale-[0.96]"
                            title="后退 3 秒"
                          >
                            <SkipBack size={15} />
                          </button>
                          <button
                            type="button"
                            onClick={toggleAudioPlayback}
                            className="inline-flex h-9 w-10 items-center justify-center border-x border-slate-100 text-gray-700 transition-[background-color,transform] hover:bg-slate-100 active:scale-[0.96]"
                            title={isAudioPlaying ? '暂停' : '播放'}
                          >
                            {isAudioPlaying ? <Pause size={15} /> : <Play size={15} />}
                          </button>
                          <button
                            type="button"
                            onClick={() => seekAudioBy(3)}
                            className="inline-flex h-9 w-10 items-center justify-center rounded-r-lg text-gray-600 transition-[background-color,transform] hover:bg-slate-100 active:scale-[0.96]"
                            title="前进 3 秒"
                          >
                            <SkipForward size={15} />
                          </button>
                        </div>
                        <div className="inline-flex rounded-lg bg-slate-100 p-0.5">
                          {PLAYBACK_RATES.map((rate) => (
                            <button
                              key={rate}
                              type="button"
                              onClick={() => setPlaybackRate(rate)}
                              className={`h-8 rounded-md px-2 text-xs tabular-nums transition-[background-color,color,transform] active:scale-[0.96] ${
                                playbackRate === rate
                                  ? 'bg-white text-gray-900 shadow-[0_1px_4px_rgba(15,23,42,0.08)]'
                                  : 'text-gray-500 hover:text-gray-800'
                              }`}
                            >
                              {rate}x
                            </button>
                          ))}
                        </div>
                      </div>
                    </>
                  ) : (
                    <div className="rounded-lg bg-white px-3 py-3 text-sm text-gray-500">这段回答没有音频文件。</div>
                  )}
                </div>

                <div className="rounded-lg bg-white p-3 shadow-[0_1px_8px_rgba(15,23,42,0.06)]">
                  <div className="mb-3 flex items-center justify-between gap-2">
                    <div className="inline-flex min-w-0 items-center gap-2 text-xs font-medium text-gray-500">
                      <BookOpen size={14} />
                      <span>章节归属</span>
                    </div>
                    {chapterSourceQuery.isFetching && <Loader2 size={14} className="animate-spin text-gray-400" />}
                  </div>

                  {chapterSourceQuery.isLoading ? (
                    <div className="flex items-center gap-2 py-2 text-sm text-gray-500">
                      <Loader2 size={15} className="animate-spin" />
                      加载章节
                    </div>
                  ) : chapterSourceQuery.isError ? (
                    <div className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">章节归属加载失败。</div>
                  ) : !chapterEditor?.plan_id ? (
                    <div className="rounded-lg bg-slate-50 px-3 py-3 text-sm text-gray-500">尚未确认章节规划。</div>
                  ) : (
                    <div className="space-y-3">
                      <div className="grid grid-cols-3 gap-2">
                        <div className="rounded-lg bg-slate-50 px-2 py-2">
                          <div className="text-[11px] text-gray-500">总素材</div>
                          <div className="mt-1 text-sm font-semibold tabular-nums text-gray-900">
                            {chapterEditor.total_source_count}
                          </div>
                        </div>
                        <div className="rounded-lg bg-emerald-50 px-2 py-2">
                          <div className="text-[11px] text-emerald-700">已归入</div>
                          <div className="mt-1 text-sm font-semibold tabular-nums text-emerald-900">
                            {chapterEditor.covered_source_count}
                          </div>
                        </div>
                        <div className="rounded-lg bg-amber-50 px-2 py-2">
                          <div className="text-[11px] text-amber-700">未归入</div>
                          <div className="mt-1 text-sm font-semibold tabular-nums text-amber-900">
                            {chapterEditor.uncovered_source_count}
                          </div>
                        </div>
                      </div>

                      <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs leading-5 text-gray-600">
                        <div className="flex items-center justify-between gap-2">
                          <span>{checkedAnswers.length > 0 ? `批量 ${checkedAnswers.length} 段` : '当前回答'}</span>
                          <span className="tabular-nums">
                            已归入 {selectedAssignedSourceCount} / 未归入 {selectedUnassignedSourceCount}
                          </span>
                        </div>
                        <div className="mt-1 truncate text-gray-800">
                          {chapterSelectionCurrentLabel}
                        </div>
                      </div>

                      <select
                        value={effectiveTargetChapterId}
                        onChange={(event) => setTargetChapterId(event.target.value)}
                        disabled={!canEditMaterials || !hasConfirmedChapterPlan || chapterSourceMutation.isPending}
                        className="h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-gray-700 outline-none transition-[border-color,box-shadow] focus:border-emerald-300 focus:shadow-[0_0_0_3px_rgba(16,185,129,0.16)] disabled:opacity-50"
                      >
                        <option value="">目标章节</option>
                        {chapterEditor.chapters.map((chapter) => (
                          <option key={chapter.id} value={chapter.id}>
                            {chapter.sort_order + 1}. {chapter.title}
                          </option>
                        ))}
                      </select>

                      <div className="grid grid-cols-3 gap-2">
                        <button
                          type="button"
                          disabled={!canAssignChapterSources}
                          onClick={() => effectiveTargetChapterId && chapterSourceMutation.mutate({
                            type: 'assign',
                            sourceIds: chapterSelectedSourceIds,
                            targetChapterId: effectiveTargetChapterId,
                          })}
                          className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-emerald-600 px-2 text-xs font-medium text-white transition-[background-color,opacity,transform] hover:bg-emerald-700 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {pendingChapterSourceAction === 'assign' ? <Loader2 size={14} className="animate-spin" /> : <Link2 size={14} />}
                          归入
                        </button>
                        <button
                          type="button"
                          disabled={!canMoveChapterSources}
                          onClick={() => effectiveTargetChapterId && chapterSourceMutation.mutate({
                            type: 'move',
                            sourceIds: chapterSelectedSourceIds,
                            targetChapterId: effectiveTargetChapterId,
                          })}
                          className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-slate-900 px-2 text-xs font-medium text-white transition-[background-color,opacity,transform] hover:bg-slate-700 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {pendingChapterSourceAction === 'move' ? <Loader2 size={14} className="animate-spin" /> : <Link2 size={14} />}
                          移动
                        </button>
                        <button
                          type="button"
                          disabled={!canRemoveChapterSources}
                          onClick={() => chapterSourceMutation.mutate({ type: 'remove', sourceIds: chapterSelectedSourceIds })}
                          className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-amber-100 px-2 text-xs font-medium text-amber-800 transition-[background-color,opacity,transform] hover:bg-amber-200 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {pendingChapterSourceAction === 'remove' ? <Loader2 size={14} className="animate-spin" /> : <Unlink size={14} />}
                          移出
                        </button>
                      </div>

                      {chapterSourceMutation.isError && (
                        <div className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">章节归属保存失败。</div>
                      )}
                      {(moveWouldEmptySourceChapter || removeWouldEmptySourceChapter) && (
                        <div className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                          原章节至少保留 1 段素材。
                        </div>
                      )}
                    </div>
                  )}
                </div>

                <div className="rounded-lg bg-white p-3 shadow-[0_1px_8px_rgba(15,23,42,0.06)]">
                  <div className="mb-3 flex items-center justify-between gap-2">
                    <div className="inline-flex min-w-0 items-center gap-2 text-xs font-medium text-gray-500">
                      <Sparkles size={14} />
                      <span>AI 批量清理</span>
                    </div>
                    <span className="text-[11px] text-gray-400 tabular-nums">
                      {cleanupSelectedAnswerIds.length || 0} 段
                    </span>
                  </div>

                  <div className="space-y-3">
                    <div className="rounded-lg bg-slate-50 px-3 py-3">
                      <div className="mb-2 flex items-center justify-between gap-2">
                        <span className="text-xs font-medium text-gray-600">方言词表</span>
                        {glossaryQuery.isFetching && <Loader2 size={13} className="animate-spin text-gray-400" />}
                      </div>
                      <div className="space-y-2">
                        {glossaryDraft.length === 0 ? (
                          <div className="rounded-lg bg-white px-3 py-2 text-xs text-gray-500">暂无词条。</div>
                        ) : (
                          glossaryDraft.map((entry, index) => (
                            <div key={`${index}-${entry.source}-${entry.target}`} className="grid grid-cols-[1fr_1fr_28px] gap-1.5">
                              <input
                                value={entry.source}
                                disabled={!canEditMaterials}
                                onChange={(event) => setGlossaryDraftOverride((entries) => (entries ?? glossaryDraft).map((item, itemIndex) => (
                                  itemIndex === index ? { ...item, source: event.target.value } : item
                                )))}
                                placeholder="方言/误识别"
                                className="h-8 min-w-0 rounded-md border border-slate-200 bg-white px-2 text-xs text-gray-700 outline-none focus:ring-2 focus:ring-emerald-200 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-gray-400"
                              />
                              <input
                                value={entry.target}
                                disabled={!canEditMaterials}
                                onChange={(event) => setGlossaryDraftOverride((entries) => (entries ?? glossaryDraft).map((item, itemIndex) => (
                                  itemIndex === index ? { ...item, target: event.target.value } : item
                                )))}
                                placeholder="标准写法"
                                className="h-8 min-w-0 rounded-md border border-slate-200 bg-white px-2 text-xs text-gray-700 outline-none focus:ring-2 focus:ring-emerald-200 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-gray-400"
                              />
                              <button
                                type="button"
                                disabled={!canEditMaterials}
                                onClick={() => setGlossaryDraftOverride((entries) => (entries ?? glossaryDraft).filter((_, itemIndex) => itemIndex !== index))}
                                className="inline-flex h-8 w-7 items-center justify-center rounded-md text-gray-400 transition-[background-color,color,opacity,transform] hover:bg-white hover:text-gray-700 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-40"
                                title="删除词条"
                              >
                                <X size={13} />
                              </button>
                              <input
                                value={entry.note ?? ''}
                                disabled={!canEditMaterials}
                                onChange={(event) => setGlossaryDraftOverride((entries) => (entries ?? glossaryDraft).map((item, itemIndex) => (
                                  itemIndex === index ? { ...item, note: event.target.value } : item
                                )))}
                                placeholder="备注，可选"
                                className="col-span-3 h-8 min-w-0 rounded-md border border-slate-200 bg-white px-2 text-xs text-gray-700 outline-none focus:ring-2 focus:ring-emerald-200 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-gray-400"
                              />
                            </div>
                          ))
                        )}
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-2">
                        <button
                          type="button"
                          disabled={!canEditMaterials}
                          onClick={() => setGlossaryDraftOverride((entries) => [...(entries ?? glossaryDraft), { source: '', target: '', note: '' }])}
                          className="inline-flex h-8 items-center justify-center gap-1.5 rounded-lg bg-white px-2 text-xs text-gray-600 transition-[background-color,opacity,transform] hover:bg-slate-100 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <Plus size={13} />
                          添加词条
                        </button>
                        <button
                          type="button"
                          disabled={!canSaveGlossary}
                          onClick={() => saveGlossaryMutation.mutate(glossaryDraft)}
                          className="inline-flex h-8 items-center justify-center gap-1.5 rounded-lg bg-slate-900 px-2 text-xs font-medium text-white transition-[background-color,opacity,transform] hover:bg-slate-700 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {saveGlossaryMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                          保存词表
                        </button>
                      </div>
                      {saveGlossaryMutation.isError && (
                        <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">词表保存失败。</div>
                      )}
                    </div>

                    <div className="rounded-lg bg-slate-50 px-3 py-3">
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0 text-xs leading-5 text-gray-600">
                          {checkedAnswers.length > 0 ? `批量清理 ${checkedAnswers.length} 段已选回答` : '清理当前回答'}
                        </div>
                        <button
                          type="button"
                          disabled={!canPreviewCleanup}
                          onClick={() => previewCleanupMutation.mutate(cleanupSelectedAnswerIds)}
                          className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-emerald-600 px-2.5 text-xs font-medium text-white transition-[background-color,opacity,transform] hover:bg-emerald-700 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {previewCleanupMutation.isPending ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                          生成预览
                        </button>
                      </div>
                      {hasUnsavedChanges && (
                        <div className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">先保存当前人工修改后再生成 AI 预览。</div>
                      )}
                      {cleanupSelectedAnswerIds.length > MAX_BATCH_CLEANUP_ITEMS && (
                        <div className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                          一次最多清理 {MAX_BATCH_CLEANUP_ITEMS} 段回答。
                        </div>
                      )}
                      {previewCleanupMutation.isError && (
                        <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">AI 清理预览失败。</div>
                      )}

                      {cleanupPreviewItems.length > 0 && (
                        <div className="mt-3 space-y-2">
                          {cleanupPreviewItems.map((item, index) => (
                            <div key={item.utterance_id} className="rounded-lg bg-white p-2">
                              <div className="mb-1 flex items-center justify-between gap-2 text-[11px] text-gray-500">
                                <span className="tabular-nums">预览 {index + 1}</span>
                                <span className={item.changed ? 'text-emerald-700' : 'text-gray-400'}>
                                  {item.changed ? '有改动' : '无明显改动'}
                                </span>
                              </div>
                              <p className="mb-2 line-clamp-2 text-xs leading-5 text-gray-500">{item.source_text}</p>
                              <textarea
                                value={item.cleaned_text}
                                readOnly={!canEditMaterials}
                                onChange={(event) => setCleanupPreviewItems((items) => items.map((previewItem) => (
                                  previewItem.utterance_id === item.utterance_id
                                    ? { ...previewItem, cleaned_text: event.target.value, provider: null, created_by: 'user' }
                                    : previewItem
                                )))}
                                className="min-h-[84px] w-full resize-y rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs leading-5 text-gray-800 outline-none focus:ring-2 focus:ring-emerald-200 read-only:bg-slate-50 read-only:text-gray-500"
                              />
                            </div>
                          ))}
                          <button
                            type="button"
                            disabled={!canConfirmCleanup}
                            onClick={() => confirmCleanupMutation.mutate(cleanupPreviewItems)}
                            className="inline-flex h-9 w-full items-center justify-center gap-1.5 rounded-lg bg-slate-900 px-3 text-xs font-medium text-white transition-[background-color,opacity,transform] hover:bg-slate-700 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            {confirmCleanupMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                            确认写入
                          </button>
                        </div>
                      )}
                      {confirmCleanupMutation.isError && (
                        <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">AI 清理结果写入失败。</div>
                      )}
                    </div>
                  </div>
                </div>

                <div className="rounded-lg bg-white p-3 shadow-[0_1px_8px_rgba(15,23,42,0.06)]">
                  <div className="mb-2 text-xs font-medium text-gray-500">ASR 原文</div>
                  <p className="max-h-28 overflow-y-auto whitespace-pre-wrap text-sm leading-6 text-gray-600">
                    {selectedAnswer.transcript_raw || '暂无原文'}
                  </p>
                </div>

                <div className="flex min-h-[280px] flex-1 flex-col">
                  <label htmlFor="memoir-answer-editor" className="mb-2 text-xs font-medium text-gray-500">
                    人工校正文
                  </label>
                  <textarea
                    id="memoir-answer-editor"
                    value={draftText}
                    readOnly={!canEditMaterials}
                    onChange={(event) => {
                      if (!selectedAnswer) return;
                      setDraft({ answerId: selectedAnswer.id, text: event.target.value });
                    }}
                    className="min-h-[260px] flex-1 resize-none rounded-lg border border-slate-200 bg-white p-3 text-sm leading-7 text-gray-800 shadow-[0_1px_8px_rgba(15,23,42,0.06)] outline-none transition-[border-color,box-shadow] focus:border-emerald-300 focus:shadow-[0_0_0_3px_rgba(16,185,129,0.16)] read-only:bg-slate-50 read-only:text-gray-500"
                  />
                </div>

                <div className="space-y-2">
                  {selectedAnswer.included_chapters.length > 0 && (
                    <div className="rounded-lg bg-emerald-50 px-3 py-2 text-xs leading-5 text-emerald-800">
                      已收录到：{selectedAnswer.included_chapters.map((chapter) => chapter.title).join('、')}
                    </div>
                  )}
                  {saveMutation.isError && (
                    <div className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">保存失败，请稍后重试。</div>
                  )}
                  {hasUnsavedChanges && (
                    <div className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">有未保存修改。</div>
                  )}
                  <div className="grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      onClick={() => handleSave(false)}
                      disabled={!canSave}
                      className="inline-flex h-10 items-center justify-center gap-2 rounded-lg bg-emerald-600 px-3 text-sm font-medium text-white transition-[background-color,opacity,transform] hover:bg-emerald-700 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {saveMutation.isPending && !saveMutation.variables?.moveNext ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                      保存
                    </button>
                    <button
                      type="button"
                      onClick={() => handleSave(true)}
                      disabled={!canSaveAndNext}
                      className="inline-flex h-10 items-center justify-center gap-1.5 rounded-lg bg-slate-900 px-3 text-sm font-medium text-white transition-[background-color,opacity,transform] hover:bg-slate-700 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {saveMutation.isPending && saveMutation.variables?.moveNext ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                      <span className="min-w-0 truncate">保存并下一段</span>
                      <ChevronRight size={15} />
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <div className="p-5">
                <div className="rounded-lg bg-white px-4 py-10 text-center text-sm text-gray-500">
                  选择一段回答后可以播放音频并编辑文字。
                </div>
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
