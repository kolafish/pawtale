import { Check, Loader2, Pencil, Plus, UserRound, X } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { memoirInterviewEditorApi } from '../../api';
import type {
  MemoirInterviewEditorAnswer,
  MemoirInterviewEditorQuestion,
  MemoirInterviewEditorSessionDetail,
  MemoirInterviewEditorSessionSummary,
} from '../../types';
import { getErrorMessage } from '../../utils/apiError';
import { formatBeijingDateTimeShort } from '../../utils/time';


interface MemoirInterviewRelationsToolProps {
  projectId: string;
  canEdit: boolean;
}

function compactText(value: string, limit = 88) {
  const text = value.replace(/\s+/g, ' ').trim();
  return text.length > limit ? `${text.slice(0, limit)}...` : text;
}

function sessionLabel(session: MemoirInterviewEditorSessionSummary) {
  const time = formatBeijingDateTimeShort(session.started_at || session.created_at) || '无时间';
  const summary = compactText(session.summary_text || session.last_message_text || '', 28);
  return `${time}${summary ? ` · ${summary}` : ''}`;
}

function allAnswers(detail: MemoirInterviewEditorSessionDetail) {
  return [
    ...detail.questions.flatMap((question) => question.answers),
    ...detail.unassigned_answers,
  ];
}

function AnswerRelationRow({
  answer,
  questions,
  canEdit,
  isSaving,
  onChange,
}: {
  answer: MemoirInterviewEditorAnswer;
  questions: MemoirInterviewEditorQuestion[];
  canEdit: boolean;
  isSaving: boolean;
  onChange: (parentQuestionId: string | null) => void;
}) {
  return (
    <div className="bg-[#f7f8f7] px-3 py-2.5">
      <p className="text-xs leading-5 text-[#59635e]">
        {compactText(answer.display_text) || '暂无识别文字'}
      </p>
      <label className="mt-2 flex items-center gap-2 text-[11px] text-[#69736e]">
        归属问题
        <select
          value={answer.parent_question_message_id ?? ''}
          disabled={!canEdit || isSaving}
          onChange={(event) => onChange(event.target.value || null)}
          className="h-9 min-w-0 flex-1 rounded-md border border-black/10 bg-white px-2 text-xs text-[#46504b] outline-none transition-[border-color,box-shadow] focus:border-[#3c7a67] focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)] disabled:opacity-50"
        >
          <option value="">未归属</option>
          {questions.map((question, index) => (
            <option key={question.id} value={question.id}>
              {index + 1}. {compactText(question.text, 36)}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

export function MemoirInterviewRelationsTool({
  projectId,
  canEdit,
}: MemoirInterviewRelationsToolProps) {
  const queryClient = useQueryClient();
  const [sessionOverride, setSessionOverride] = useState<string | null>(null);
  const [newQuestionText, setNewQuestionText] = useState('');
  const [editingQuestion, setEditingQuestion] = useState<{ id: string; text: string } | null>(null);

  const sessionsQuery = useQuery({
    queryKey: ['memoirInterviewEditorSessions', projectId, 'advancedRelations'],
    queryFn: () => memoirInterviewEditorApi.listSessions(projectId, {
      effectiveOnly: true,
      limit: 200,
    }),
  });
  const sessions = useMemo(() => sessionsQuery.data?.items ?? [], [sessionsQuery.data?.items]);
  const selectedSessionId = (
    sessionOverride && sessions.some((session) => session.id === sessionOverride)
      ? sessionOverride
      : sessions[0]?.id
  ) ?? null;

  const detailQuery = useQuery({
    queryKey: ['memoirInterviewEditorSession', projectId, selectedSessionId],
    queryFn: () => memoirInterviewEditorApi.getSession(projectId, selectedSessionId as string),
    enabled: Boolean(selectedSessionId),
  });
  const detail = detailQuery.data;

  const updateDetail = (updated: MemoirInterviewEditorSessionDetail) => {
    queryClient.setQueryData(
      ['memoirInterviewEditorSession', projectId, selectedSessionId],
      updated,
    );
    queryClient.invalidateQueries({ queryKey: ['memoirInterviewEditorSessions', projectId] });
  };

  const relationMutation = useMutation({
    mutationFn: ({ messageId, parentId }: { messageId: string; parentId: string | null }) =>
      memoirInterviewEditorApi.updateAnswerParent(
        projectId,
        messageId,
        parentId,
        'web_advanced_relation_tool',
      ),
    onSuccess: updateDetail,
  });

  const createQuestionMutation = useMutation({
    mutationFn: () => memoirInterviewEditorApi.createQuestion(
      projectId,
      selectedSessionId as string,
      newQuestionText.trim(),
    ),
    onSuccess: (updated) => {
      setNewQuestionText('');
      updateDetail(updated);
    },
  });

  const updateQuestionMutation = useMutation({
    mutationFn: () => memoirInterviewEditorApi.updateQuestionText(
      projectId,
      editingQuestion?.id as string,
      editingQuestion?.text.trim() as string,
    ),
    onSuccess: (updated) => {
      setEditingQuestion(null);
      updateDetail(updated);
    },
  });

  const convertQuestionMutation = useMutation({
    mutationFn: (questionId: string) => memoirInterviewEditorApi.convertQuestionToAnswer(
      projectId,
      questionId,
      null,
      'web_advanced_relation_tool',
    ),
    onSuccess: updateDetail,
  });

  const mutationError = relationMutation.error
    ?? createQuestionMutation.error
    ?? updateQuestionMutation.error
    ?? convertQuestionMutation.error;
  const isSaving = relationMutation.isPending
    || createQuestionMutation.isPending
    || updateQuestionMutation.isPending
    || convertQuestionMutation.isPending;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="space-y-3 border-b border-black/6 bg-[#f7f8f7] px-4 py-3 sm:px-5">
        <label className="block text-[11px] text-[#69736e]">
          访谈场次
          <select
            value={selectedSessionId ?? ''}
            disabled={sessionsQuery.isLoading || sessions.length === 0}
            onChange={(event) => {
              setSessionOverride(event.target.value || null);
              setEditingQuestion(null);
              setNewQuestionText('');
            }}
            className="mt-1 h-10 w-full rounded-md border border-black/10 bg-white px-3 text-sm text-[#46504b] outline-none transition-[border-color,box-shadow] focus:border-[#3c7a67] focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)] disabled:opacity-50"
          >
            {sessions.map((session) => (
              <option key={session.id} value={session.id}>{sessionLabel(session)}</option>
            ))}
          </select>
        </label>
        <div className="flex gap-2">
          <input
            value={newQuestionText}
            disabled={!canEdit || !selectedSessionId || isSaving}
            onChange={(event) => setNewQuestionText(event.target.value)}
            placeholder="补充一个整理问题"
            className="h-10 min-w-0 flex-1 rounded-md border border-black/10 bg-white px-3 text-sm text-[#29322e] outline-none transition-[border-color,box-shadow] placeholder:text-[#69736e] focus:border-[#3c7a67] focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)] disabled:opacity-50"
          />
          <button
            type="button"
            disabled={!canEdit || !selectedSessionId || !newQuestionText.trim() || isSaving}
            onClick={() => createQuestionMutation.mutate()}
            className="inline-flex h-10 flex-shrink-0 items-center justify-center gap-1.5 rounded-md bg-[#2f6d59] px-3 text-sm font-medium text-white transition-[background-color,opacity,transform] hover:bg-[#275d4c] active:scale-[0.96] disabled:opacity-45"
          >
            {createQuestionMutation.isPending ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
            添加
          </button>
        </div>
        {mutationError && (
          <p className="text-xs text-[#9b4646]">{getErrorMessage(mutationError, '问答关系保存失败。')}</p>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-5">
        {sessionsQuery.isLoading || detailQuery.isLoading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-[#69736e]">
            <Loader2 size={16} className="animate-spin" />
            正在读取问答关系
          </div>
        ) : sessionsQuery.isError || detailQuery.isError ? (
          <div className="py-12 text-center text-sm text-[#9b4646]">问答关系读取失败</div>
        ) : !detail ? (
          <div className="py-12 text-center text-sm text-[#69736e]">暂无可整理的访谈</div>
        ) : (
          <div className="space-y-4">
            {detail.questions.map((question, questionIndex) => (
              <section key={question.id} className="overflow-hidden rounded-md bg-white shadow-[0_1px_7px_rgba(23,43,35,0.08)]">
                <div className="flex items-start gap-2 px-3 py-3">
                  <span className="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded bg-[#edf1ef] text-xs font-semibold text-[#46504b] tabular-nums">
                    {questionIndex + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    {editingQuestion?.id === question.id ? (
                      <div className="flex gap-2">
                        <input
                          value={editingQuestion.text}
                          disabled={isSaving}
                          onChange={(event) => setEditingQuestion({ id: question.id, text: event.target.value })}
                          className="h-9 min-w-0 flex-1 rounded-md border border-black/10 px-2 text-sm outline-none focus:border-[#3c7a67]"
                        />
                        <button
                          type="button"
                          disabled={!editingQuestion.text.trim() || isSaving}
                          onClick={() => updateQuestionMutation.mutate()}
                          className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-[#2f6d59] text-white disabled:opacity-45"
                          title="保存问题"
                          aria-label="保存问题"
                        >
                          {updateQuestionMutation.isPending ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingQuestion(null)}
                          className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-[#edf1ef] text-[#59635e]"
                          title="取消"
                          aria-label="取消"
                        >
                          <X size={15} />
                        </button>
                      </div>
                    ) : (
                      <p className="text-sm font-medium leading-6 text-[#29322e]">{question.text}</p>
                    )}
                    <p className="mt-1 text-[11px] text-[#69736e] tabular-nums">{question.answer_count} 段回答</p>
                  </div>
                  {editingQuestion?.id !== question.id && (
                    <div className="flex flex-shrink-0 gap-1">
                      {question.answer_count === 0 && question.utterance_id && (
                        <button
                          type="button"
                          disabled={!canEdit || isSaving}
                          onClick={() => {
                            if (window.confirm('确定把这条误识别的问题改为受访者回答吗？')) {
                              convertQuestionMutation.mutate(question.id);
                            }
                          }}
                          className="inline-flex h-9 items-center justify-center gap-1 rounded-md px-2 text-xs text-[#6b5b3f] transition-[background-color,transform] hover:bg-[#fff3dc] active:scale-[0.96] disabled:opacity-40"
                        >
                          <UserRound size={14} />
                          转为回答
                        </button>
                      )}
                      <button
                        type="button"
                        disabled={!canEdit || isSaving}
                        onClick={() => setEditingQuestion({ id: question.id, text: question.text })}
                        className="inline-flex h-9 w-9 items-center justify-center rounded-md text-[#69736e] transition-[background-color,color,transform] hover:bg-[#f1f3f2] hover:text-[#29322e] active:scale-[0.96] disabled:opacity-40"
                        title="编辑问题"
                        aria-label="编辑问题"
                      >
                        <Pencil size={15} />
                      </button>
                    </div>
                  )}
                </div>
                {question.answers.length > 0 && (
                  <div className="space-y-px border-t border-black/5">
                    {question.answers.map((answer) => (
                      <AnswerRelationRow
                        key={answer.id}
                        answer={answer}
                        questions={detail.questions}
                        canEdit={canEdit}
                        isSaving={isSaving}
                        onChange={(parentId) => relationMutation.mutate({
                          messageId: answer.message_id,
                          parentId,
                        })}
                      />
                    ))}
                  </div>
                )}
              </section>
            ))}

            {detail.unassigned_answers.length > 0 && (
              <section className="overflow-hidden rounded-md bg-white shadow-[0_1px_7px_rgba(23,43,35,0.08)]">
                <div className="px-3 py-3">
                  <h3 className="text-sm font-medium text-[#8a5a20]">未归属回答</h3>
                  <p className="mt-1 text-[11px] text-[#69736e] tabular-nums">
                    {detail.unassigned_answers.length} 段
                  </p>
                </div>
                <div className="space-y-px border-t border-black/5">
                  {detail.unassigned_answers.map((answer) => (
                    <AnswerRelationRow
                      key={answer.id}
                      answer={answer}
                      questions={detail.questions}
                      canEdit={canEdit}
                      isSaving={isSaving}
                      onChange={(parentId) => relationMutation.mutate({
                        messageId: answer.message_id,
                        parentId,
                      })}
                    />
                  ))}
                </div>
              </section>
            )}

            <p className="text-center text-xs text-[#69736e]">
              共 {detail.questions.length} 个问题，{allAnswers(detail).length} 段回答
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
