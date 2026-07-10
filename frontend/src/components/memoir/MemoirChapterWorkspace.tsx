import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BookOpen,
  CheckCircle2,
  Loader2,
  RefreshCw,
  Sparkles,
  X,
} from 'lucide-react';
import { useMemo, useState } from 'react';

import {
  memoirBookEditorApi,
  memoirChapterSourceEditorApi,
} from '../../api';
import { useAppStore } from '../../stores/appStore';
import type {
  MemoirBookPlan,
  MemoirBookPlanStructureCommand,
  MemoirChapterSourceEditor,
  MemoirChapterSourceEditorChapter,
  Project,
} from '../../types';
import { getErrorMessage } from '../../utils/apiError';
import { MemoirChapterList } from './MemoirChapterList';
import { MemoirChapterSources } from './MemoirChapterSources';


interface MemoirChapterWorkspaceProps {
  project: Project;
}

export function MemoirChapterWorkspace({ project }: MemoirChapterWorkspaceProps) {
  const queryClient = useQueryClient();
  const defaultAiModel = useAppStore(state => state.defaultAiModel);
  const canEdit = project.can_edit_memoir_materials !== false;
  const queryKey = useMemo(
    () => ['memoirChapterSourceEditor', project.id] as const,
    [project.id],
  );
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);

  const editorQuery = useQuery({
    queryKey,
    queryFn: () => memoirChapterSourceEditorApi.get(project.id),
  });

  const updateEditor = (editor: MemoirChapterSourceEditor) => {
    queryClient.setQueryData(queryKey, editor);
  };

  const applyPlan = (plan: MemoirBookPlan, selectFirst = false) => {
    queryClient.setQueryData<MemoirChapterSourceEditor>(queryKey, current => (
      current
        ? {
            ...current,
            plan_id: plan.id,
            plan_title: plan.title,
            plan_status: plan.status,
            chapters: plan.chapters,
          }
        : current
    ));
    if (selectFirst) setSelectedChapterId(plan.chapters[0]?.id ?? null);
    queryClient.invalidateQueries({ queryKey });
  };

  const generatePlanMutation = useMutation({
    mutationFn: () => memoirBookEditorApi.generatePlan(project.id, defaultAiModel),
    onSuccess: plan => applyPlan(plan, true),
  });
  const confirmPlanMutation = useMutation({
    mutationFn: (editor: MemoirChapterSourceEditor) => memoirBookEditorApi.confirmPlan(
      project.id,
      {
        title: editor.plan_title || `${project.title}章节规划`,
        chapters: editor.chapters,
      },
    ),
    onSuccess: plan => applyPlan(plan, true),
  });
  const structureMutation = useMutation({
    mutationFn: (command: MemoirBookPlanStructureCommand) => (
      memoirBookEditorApi.applyStructureCommand(project.id, command)
    ),
    onSuccess: (plan, command) => {
      applyPlan(plan);
      if (command.type === 'create_chapter_from_source') {
        const created = plan.chapters.find(chapter => (
          chapter.source_refs.some(ref => ref.utterance_id === command.utterance_id)
        ));
        if (created) setSelectedChapterId(created.id);
      }
      if (command.type === 'delete_chapter' && command.chapter_id === selectedChapterId) {
        setSelectedChapterId(plan.chapters[0]?.id ?? null);
      }
    },
  });
  const renameMutation = useMutation({
    mutationFn: ({ chapterId, title }: { chapterId: string; title: string }) => (
      memoirBookEditorApi.updateChapterTitle(project.id, chapterId, title)
    ),
    onSuccess: plan => applyPlan(plan),
  });
  const assignMutation = useMutation({
    mutationFn: ({ chapterId, utteranceId }: { chapterId: string; utteranceId: string }) => (
      memoirChapterSourceEditorApi.assign(
        project.id,
        [{ chapter_id: chapterId, utterance_ids: [utteranceId] }],
        defaultAiModel,
      )
    ),
    onSuccess: updateEditor,
  });
  const moveSourceMutation = useMutation({
    mutationFn: ({
      sourceChapterId,
      targetChapterId,
      utteranceId,
    }: {
      sourceChapterId: string;
      targetChapterId: string;
      utteranceId: string;
    }) => memoirChapterSourceEditorApi.move(
      project.id,
      sourceChapterId,
      targetChapterId,
      utteranceId,
      defaultAiModel,
    ),
    onSuccess: updateEditor,
  });
  const removeSourceMutation = useMutation({
    mutationFn: ({ chapterId, utteranceId }: { chapterId: string; utteranceId: string }) => (
      memoirChapterSourceEditorApi.remove(project.id, chapterId, [utteranceId], defaultAiModel)
    ),
    onSuccess: updateEditor,
  });

  const mutations = [
    generatePlanMutation,
    confirmPlanMutation,
    structureMutation,
    renameMutation,
    assignMutation,
    moveSourceMutation,
    removeSourceMutation,
  ];
  const isBusy = mutations.some(mutation => mutation.isPending);
  const mutationError = mutations.find(mutation => mutation.isError)?.error;
  const editor = editorQuery.data;
  const chapters = editor?.chapters ?? [];
  const effectiveChapterId = chapters.some(chapter => chapter.id === selectedChapterId)
    ? selectedChapterId
    : chapters[0]?.id ?? null;
  const selectedChapter = chapters.find(chapter => chapter.id === effectiveChapterId) ?? null;

  const moveChapter = (chapterId: string, targetIndex: number) => {
    if (targetIndex < 0 || targetIndex >= chapters.length) return;
    if (!selectedChapterId && effectiveChapterId) setSelectedChapterId(effectiveChapterId);
    structureMutation.mutate({
      type: 'move_chapter',
      chapter_id: chapterId,
      target_index: targetIndex + 1,
    });
  };

  const deleteChapter = (chapter: MemoirChapterSourceEditorChapter) => {
    if (!window.confirm(`删除“${chapter.title}”？其中素材会回到未归入目录。`)) return;
    structureMutation.mutate({ type: 'delete_chapter', chapter_id: chapter.id });
  };

  const regeneratePlan = () => {
    if (!window.confirm('重新规划会替换当前章节目录。确定继续吗？')) return;
    generatePlanMutation.mutate();
  };

  if (editorQuery.isLoading) {
    return <WorkspaceMessage icon={<Loader2 size={20} className="animate-spin" />} text="正在读取章节目录" />;
  }
  if (editorQuery.isError || !editor) {
    return (
      <WorkspaceMessage
        icon={<BookOpen size={24} />}
        text="章节目录读取失败"
        action={(
          <button
            type="button"
            onClick={() => editorQuery.refetch()}
            className="mt-4 inline-flex h-10 items-center gap-2 rounded-md bg-white px-3 text-sm text-[#46504b] shadow-[0_1px_6px_rgba(23,43,35,0.1)] transition-[background-color,transform] hover:bg-[#f3f5f4] active:scale-[0.96]"
          >
            <RefreshCw size={15} />
            重新加载
          </button>
        )}
      />
    );
  }

  if (!editor.plan_id || chapters.length === 0) {
    return (
      <div className="flex h-full items-center justify-center bg-[#f7f8f7] px-6">
        <div className="max-w-md text-center">
          <span className="mx-auto inline-flex h-12 w-12 items-center justify-center rounded-md bg-white text-[#3c7a67] shadow-[0_1px_8px_rgba(23,43,35,0.1)]">
            <BookOpen size={24} strokeWidth={1.6} />
          </span>
          <h1 className="mt-5 text-xl font-semibold text-[#202824] [text-wrap:balance]">先建立章节目录</h1>
          <p className="mt-2 text-sm leading-6 text-[#69736e] [text-wrap:pretty]">
            当前共有 {editor.total_source_count} 段访谈素材。系统会根据内容生成初始章节，你可以继续调整顺序、标题和素材归属。
          </p>
          <button
            type="button"
            onClick={() => generatePlanMutation.mutate()}
            disabled={!canEdit || editor.total_source_count === 0 || generatePlanMutation.isPending}
            className="mt-6 inline-flex h-11 items-center justify-center gap-2 rounded-md bg-[#3c7a67] px-4 text-sm font-medium text-white transition-[background-color,transform] hover:bg-[#326956] active:scale-[0.96] disabled:opacity-50"
          >
            {generatePlanMutation.isPending ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
            根据素材生成目录
          </button>
          {editor.total_source_count === 0 && (
            <p className="mt-3 text-xs text-[#8a948f]">项目还没有可用于规划章节的访谈素材。</p>
          )}
          {generatePlanMutation.isError && (
            <p className="mt-3 text-sm text-[#7c3f3f]">
              {getErrorMessage(generatePlanMutation.error, '目录生成失败，请稍后重试。')}
            </p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#f7f8f7]">
      <div className="flex min-h-14 flex-wrap items-center gap-3 border-b border-black/6 bg-white px-4 py-2 sm:px-5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-base font-semibold text-[#202824]">章节目录</h1>
            <span className={`rounded px-2 py-0.5 text-[11px] font-medium ${
              editor.plan_status === 'confirmed'
                ? 'bg-[#eaf3ee] text-[#2d705b]'
                : 'bg-[#f5efe5] text-[#846633]'
            }`}>
              {editor.plan_status === 'confirmed' ? '已确认' : '草稿'}
            </span>
          </div>
          <p className="mt-0.5 truncate text-xs text-[#7a847f]">
            {editor.plan_title || project.title} · {chapters.length} 章 · 已归入 {editor.covered_source_count}/{editor.total_source_count} 段
          </p>
        </div>
        {editor.plan_status !== 'confirmed' && (
          <button
            type="button"
            onClick={() => confirmPlanMutation.mutate(editor)}
            disabled={!canEdit || isBusy}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[#3c7a67] px-3 text-sm font-medium text-white transition-[background-color,transform] hover:bg-[#326956] active:scale-[0.96] disabled:opacity-50"
          >
            {confirmPlanMutation.isPending ? <Loader2 size={15} className="animate-spin" /> : <CheckCircle2 size={15} />}
            确认目录
          </button>
        )}
        <button
          type="button"
          onClick={regeneratePlan}
          disabled={!canEdit || isBusy}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[#f1f3f2] px-3 text-sm text-[#59635e] transition-[background-color,color,transform] hover:bg-[#e8ecea] hover:text-[#29322e] active:scale-[0.96] disabled:opacity-50"
        >
          {generatePlanMutation.isPending ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
          重新规划
        </button>
      </div>

      {mutationError && (
        <div className="flex min-h-10 items-center gap-3 border-b border-[#ead1d1] bg-[#fbf2f2] px-4 py-1 text-sm text-[#7c3f3f]">
          <span className="min-w-0 flex-1">{getErrorMessage(mutationError, '操作失败，请稍后重试。')}</span>
          <button
            type="button"
            onClick={() => mutations.forEach(mutation => mutation.reset())}
            aria-label="关闭错误提示"
            title="关闭"
            className="inline-flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md transition-[background-color,transform] hover:bg-[#f2dddd] active:scale-[0.96]"
          >
            <X size={15} />
          </button>
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[360px_minmax(0,1fr)] lg:overflow-hidden">
        <aside className="max-h-[42vh] min-h-[260px] overflow-y-auto border-b border-black/6 bg-white lg:max-h-none lg:min-h-0 lg:border-b-0 lg:border-r">
          <MemoirChapterList
            chapters={chapters}
            selectedChapterId={effectiveChapterId}
            disabled={!canEdit || isBusy}
            onSelect={setSelectedChapterId}
            onMove={moveChapter}
            onRename={(chapterId, title) => renameMutation.mutate({ chapterId, title })}
            onDelete={deleteChapter}
          />
        </aside>
        <div className="min-h-[520px] min-w-0 lg:min-h-0">
          {selectedChapter ? (
            <MemoirChapterSources
              editor={editor}
              chapter={selectedChapter}
              disabled={!canEdit || isBusy}
              onAssign={utteranceId => assignMutation.mutate({
                chapterId: selectedChapter.id,
                utteranceId,
              })}
              onMove={(utteranceId, targetChapterId) => moveSourceMutation.mutate({
                sourceChapterId: selectedChapter.id,
                targetChapterId,
                utteranceId,
              })}
              onRemove={utteranceId => removeSourceMutation.mutate({
                chapterId: selectedChapter.id,
                utteranceId,
              })}
              onCreateChapter={utteranceId => structureMutation.mutate({
                type: 'create_chapter_from_source',
                utterance_id: utteranceId,
              })}
            />
          ) : (
            <WorkspaceMessage icon={<BookOpen size={24} />} text="选择一个章节查看素材" />
          )}
        </div>
      </div>
    </div>
  );
}

function WorkspaceMessage({
  icon,
  text,
  action,
}: {
  icon: React.ReactNode;
  text: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center bg-[#f7f8f7] px-6 text-center text-[#7a847f]">
      {icon}
      <p className="mt-3 text-sm font-medium text-[#46504b]">{text}</p>
      {action}
    </div>
  );
}
