import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  Check,
  Clock3,
  FileAudio,
  Link2,
  Loader2,
  MessageSquareText,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Unlink,
  UserRound,
  X,
} from 'lucide-react';
import { memoirInterviewEditorApi } from '../../api';
import type {
  MemoirInterviewEditorAnswer,
  MemoirInterviewEditorQuestion,
  MemoirInterviewEditorSessionSummary,
  Project,
} from '../../types';

interface MemoirInterviewEditorProps {
  project: Project;
}

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
  onSelect,
  onToggleChecked,
}: {
  answer: MemoirInterviewEditorAnswer;
  selected: boolean;
  checked: boolean;
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
          onChange={onToggleChecked}
          onClick={(event) => event.stopPropagation()}
          aria-label="选择回答"
          className="mt-1 h-4 w-4 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
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
            {answer.is_included_in_book && (
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
  const [selectedSessionOverride, setSelectedSessionOverride] = useState<string | null>(null);
  const [selectedAnswerOverride, setSelectedAnswerOverride] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ answerId: string; text: string } | null>(null);
  const [checkedAnswerIds, setCheckedAnswerIds] = useState<string[]>([]);
  const [targetQuestionId, setTargetQuestionId] = useState('');
  const [newQuestionText, setNewQuestionText] = useState('');
  const [editingQuestion, setEditingQuestion] = useState<{ id: string; text: string } | null>(null);

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

  const detail = detailQuery.data;
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
  const checkedAnswers = answers.filter((answer) => checkedAnswerIds.includes(answer.id));
  const checkedAnswerMessageIds = checkedAnswers.map((answer) => answer.message_id);
  const effectiveTargetQuestionId = detail?.questions.some((question) => question.id === targetQuestionId)
    ? targetQuestionId
    : '';
  const draftText = selectedAnswer
    ? (draft?.answerId === selectedAnswer.id ? draft.text : selectedAnswer.display_text)
    : '';

  const saveMutation = useMutation({
    mutationFn: ({ answerId, text }: { answerId: string; text: string }) =>
      memoirInterviewEditorApi.updateAnswerTranscript(answerId, text),
    onSuccess: () => {
      setDraft(null);
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorSession', project.id, selectedSessionId] });
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorSessions', project.id] });
    },
  });

  const createQuestionMutation = useMutation({
    mutationFn: ({ voiceSessionId, text }: { voiceSessionId: string; text: string }) =>
      memoirInterviewEditorApi.createQuestion(project.id, voiceSessionId, text),
    onSuccess: (data) => {
      setNewQuestionText('');
      setTargetQuestionId(data.questions[data.questions.length - 1]?.id ?? '');
      queryClient.setQueryData(['memoirInterviewEditorSession', project.id, selectedSessionId], data);
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorSessions', project.id] });
    },
  });

  const updateQuestionMutation = useMutation({
    mutationFn: ({ messageId, text }: { messageId: string; text: string }) =>
      memoirInterviewEditorApi.updateQuestionText(project.id, messageId, text),
    onSuccess: (data) => {
      setEditingQuestion(null);
      queryClient.setQueryData(['memoirInterviewEditorSession', project.id, selectedSessionId], data);
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
      queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorSessions', project.id] });
    },
  });

  const hasUnsavedChanges = Boolean(
    selectedAnswer
    && draft?.answerId === selectedAnswer.id
    && draft.text !== selectedAnswer.display_text,
  );
  const canSave = Boolean(selectedAnswer && draftText.trim() && hasUnsavedChanges && !saveMutation.isPending);

  const handleSave = () => {
    if (!selectedAnswer || !canSave) return;
    saveMutation.mutate({ answerId: selectedAnswer.id, text: draftText.trim() });
  };

  const toggleAnswerChecked = (answerId: string) => {
    setCheckedAnswerIds((ids) => (
      ids.includes(answerId)
        ? ids.filter((id) => id !== answerId)
        : [...ids, answerId]
    ));
  };

  const selectAllVisibleAnswers = () => {
    setCheckedAnswerIds(answers.map((answer) => answer.id));
  };

  const canCreateQuestion = Boolean(selectedSessionId && newQuestionText.trim() && !createQuestionMutation.isPending);
  const canUpdateQuestion = Boolean(
    editingQuestion
    && editingQuestion.text.trim()
    && detail?.questions.some((question) => question.id === editingQuestion.id && question.text !== editingQuestion.text),
  );
  const canMoveCheckedAnswers = Boolean(
    checkedAnswerMessageIds.length > 0
    && effectiveTargetQuestionId
    && !updateParentMutation.isPending,
  );
  const canUnassignCheckedAnswers = Boolean(
    checkedAnswerMessageIds.length > 0
    && !updateParentMutation.isPending,
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
                        setSelectedSessionOverride(session.id);
                        setSelectedAnswerOverride(null);
                        setDraft(null);
                        setCheckedAnswerIds([]);
                        setTargetQuestionId('');
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
                  }
                }}
                className="inline-flex h-10 items-center gap-2 rounded-lg bg-slate-100 px-3 text-sm text-gray-600 transition-[background-color,transform] hover:bg-slate-200 active:scale-[0.96]"
              >
                <RefreshCw size={15} className={sessionsQuery.isFetching || detailQuery.isFetching ? 'animate-spin' : ''} />
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
                  <div className="flex flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
                    <div className="min-w-0 flex-1">
                      <label htmlFor="new-question-text" className="mb-1 block text-xs font-medium text-gray-500">
                        新建采访问题
                      </label>
                      <div className="flex gap-2">
                        <input
                          id="new-question-text"
                          value={newQuestionText}
                          onChange={(event) => setNewQuestionText(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' && canCreateQuestion && selectedSessionId) {
                              createQuestionMutation.mutate({ voiceSessionId: selectedSessionId, text: newQuestionText.trim() });
                            }
                          }}
                          placeholder="输入一个人工整理的问题"
                          className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-gray-800 outline-none transition-[border-color,box-shadow] focus:border-emerald-300 focus:shadow-[0_0_0_3px_rgba(16,185,129,0.16)]"
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
                        disabled={answers.length === 0}
                        className="h-9 rounded-lg px-2.5 text-xs text-gray-600 transition-[background-color,transform] hover:bg-white active:scale-[0.96] disabled:opacity-40"
                      >
                        全选
                      </button>
                      <button
                        type="button"
                        onClick={() => setCheckedAnswerIds([])}
                        disabled={checkedAnswers.length === 0}
                        className="h-9 rounded-lg px-2.5 text-xs text-gray-600 transition-[background-color,transform] hover:bg-white active:scale-[0.96] disabled:opacity-40"
                      >
                        清除
                      </button>
                      <select
                        value={effectiveTargetQuestionId}
                        onChange={(event) => setTargetQuestionId(event.target.value)}
                        className="h-9 max-w-52 rounded-lg border border-slate-200 bg-white px-2 text-xs text-gray-700 outline-none focus:ring-2 focus:ring-emerald-200"
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
                              onChange={(event) => setEditingQuestion({ id: question.id, text: event.target.value })}
                              className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-gray-800 outline-none focus:ring-2 focus:ring-emerald-200"
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
                              className="inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg text-gray-400 transition-[background-color,color,transform] hover:bg-slate-100 hover:text-gray-700 active:scale-[0.96]"
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
                            onSelect={() => {
                              setSelectedAnswerOverride(answer.id);
                              setDraft(null);
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
                          onSelect={() => {
                            setSelectedAnswerOverride(answer.id);
                            setDraft(null);
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
                    <audio
                      key={selectedAnswer.audio_file_id}
                      controls
                      preload="metadata"
                      className="h-10 w-full"
                      src={memoirInterviewEditorApi.audioPlaybackUrl(selectedAnswer.audio_file_id)}
                    />
                  ) : (
                    <div className="rounded-lg bg-white px-3 py-3 text-sm text-gray-500">这段回答没有音频文件。</div>
                  )}
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
                    onChange={(event) => {
                      if (!selectedAnswer) return;
                      setDraft({ answerId: selectedAnswer.id, text: event.target.value });
                    }}
                    className="min-h-[260px] flex-1 resize-none rounded-lg border border-slate-200 bg-white p-3 text-sm leading-7 text-gray-800 shadow-[0_1px_8px_rgba(15,23,42,0.06)] outline-none transition-[border-color,box-shadow] focus:border-emerald-300 focus:shadow-[0_0_0_3px_rgba(16,185,129,0.16)]"
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
                  <button
                    type="button"
                    onClick={handleSave}
                    disabled={!canSave}
                    className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 text-sm font-medium text-white transition-[background-color,opacity,transform] hover:bg-emerald-700 active:scale-[0.96] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {saveMutation.isPending ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                    保存文字
                  </button>
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
