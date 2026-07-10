import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BookOpen,
  Download,
  FileText,
  Loader2,
  RefreshCw,
  Settings2,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { memoirBookEditorApi } from '../../api';
import { useAppStore } from '../../stores/appStore';
import type {
  MemoirBookEditorData,
  MemoirBookOutput,
  MemoirBookOutputForm,
  Project,
} from '../../types';
import { getErrorMessage } from '../../utils/apiError';
import { MemoirBookPreview } from './MemoirBookPreview';
import { MemoirBookSettings } from './MemoirBookSettings';
import {
  bookOutputForm,
  bookOutputFormsEqual,
  saveBrowserDownload,
} from './memoirBookOutputView';


interface MemoirBookWorkspaceProps {
  project: Project;
  onUnsavedChange?: (hasUnsavedChanges: boolean) => void;
  onOpenChapters: () => void;
  onOpenDrafts: (chapterId?: string) => void;
}

type FormState = { outputId: string; form: MemoirBookOutputForm } | null;

export function MemoirBookWorkspace({
  project,
  onUnsavedChange,
  onOpenChapters,
  onOpenDrafts,
}: MemoirBookWorkspaceProps) {
  const queryClient = useQueryClient();
  const defaultAiModel = useAppStore(state => state.defaultAiModel);
  const canEdit = project.can_edit_memoir_materials !== false;
  const queryKey = useMemo(() => ['memoirBookEditor', project.id] as const, [project.id]);
  const [formState, setFormState] = useState<FormState>(null);
  const [titleSuggestions, setTitleSuggestions] = useState<string[]>([]);
  const [mobilePanel, setMobilePanel] = useState<'settings' | 'preview'>('preview');

  const editorQuery = useQuery({
    queryKey,
    queryFn: () => memoirBookEditorApi.get(project.id),
  });
  const data = editorQuery.data;
  const output = data?.output ?? null;
  const storedForm = useMemo(() => bookOutputForm(output), [output]);
  const form = formState && formState.outputId === output?.id ? formState.form : storedForm;
  const hasUnsavedChanges = Boolean(
    output
    && formState
    && formState.outputId === output.id
    && !bookOutputFormsEqual(formState.form, storedForm),
  );
  const draftsByChapterId = useMemo(
    () => new Map((data?.chapter_drafts ?? []).map(draft => [draft.chapter_id, draft])),
    [data?.chapter_drafts],
  );
  const chapters = data?.plan?.chapters ?? [];
  const unconfirmedChapters = chapters.filter(chapter => {
    const draft = draftsByChapterId.get(chapter.id);
    return draft?.status !== 'confirmed' || !draft.latest_version;
  });
  const readyToGenerate = Boolean(
    data?.plan?.status === 'confirmed'
    && chapters.length > 0
    && unconfirmedChapters.length === 0,
  );

  useEffect(() => {
    onUnsavedChange?.(hasUnsavedChanges);
  }, [hasUnsavedChanges, onUnsavedChange]);

  useEffect(() => () => {
    onUnsavedChange?.(false);
  }, [onUnsavedChange]);

  useEffect(() => {
    if (!hasUnsavedChanges) return undefined;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [hasUnsavedChanges]);

  const updateOutputCache = (nextOutput: MemoirBookOutput, resetForm = true) => {
    queryClient.setQueryData<MemoirBookEditorData>(queryKey, current => (
      current ? { ...current, output: nextOutput } : current
    ));
    if (resetForm) setFormState(null);
  };

  const generateMutation = useMutation({
    mutationFn: () => memoirBookEditorApi.generateOutput(
      project.id,
      output
        ? { ...form, aiModel: defaultAiModel, forceRegenerate: true }
        : { aiModel: defaultAiModel },
    ),
    onSuccess: nextOutput => updateOutputCache(nextOutput),
  });
  const saveMutation = useMutation({
    mutationFn: () => memoirBookEditorApi.updateOutput(project.id, {
      ...form,
      title: form.title.trim(),
    }),
    onSuccess: nextOutput => updateOutputCache(nextOutput),
  });
  const copyMutation = useMutation({
    mutationFn: () => memoirBookEditorApi.regenerateOutputCopy(project.id, defaultAiModel),
    onSuccess: nextOutput => updateOutputCache(nextOutput),
  });
  const titleMutation = useMutation({
    mutationFn: () => memoirBookEditorApi.suggestOutputTitles(project.id, defaultAiModel),
    onSuccess: response => setTitleSuggestions(response.suggestions),
  });
  const chapterCopyMutation = useMutation({
    mutationFn: ({
      chapterId,
      copy,
    }: {
      chapterId: string;
      copy: { intro: string; pull_quote: string };
    }) => memoirBookEditorApi.updateOutputChapterCopy(project.id, chapterId, copy),
    onSuccess: nextOutput => updateOutputCache(nextOutput, false),
  });
  const downloadMutation = useMutation({
    mutationFn: (format: 'pdf' | 'txt') => memoirBookEditorApi.downloadOutput(project.id, format),
    onSuccess: (download, format) => {
      saveBrowserDownload(download.blob, download.filename || `${output?.title || project.title}.${format}`);
    },
  });

  const mutations = [
    generateMutation,
    saveMutation,
    copyMutation,
    titleMutation,
    chapterCopyMutation,
    downloadMutation,
  ];
  const mutationError = mutations.find(mutation => mutation.isError)?.error;

  if (editorQuery.isLoading) {
    return <WorkspaceMessage icon={<Loader2 size={20} className="animate-spin" />} text="正在读取全书" />;
  }
  if (editorQuery.isError || !data) {
    return (
      <WorkspaceMessage
        icon={<BookOpen size={24} />}
        text="全书读取失败"
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
  if (!data.plan || chapters.length === 0) {
    return (
      <WorkspaceMessage
        icon={<BookOpen size={26} />}
        text="请先建立章节目录"
        action={<PrimaryAction onClick={onOpenChapters}>打开章节目录</PrimaryAction>}
      />
    );
  }
  if (!output) {
    return (
      <div className="flex h-full items-center justify-center bg-[#f7f8f7] px-6">
        <div className="w-full max-w-lg text-center">
          <span className="mx-auto inline-flex h-12 w-12 items-center justify-center rounded-md bg-white text-[#3c7a67] shadow-[0_1px_8px_rgba(23,43,35,0.1)]">
            <BookOpen size={24} strokeWidth={1.6} />
          </span>
          <h1 className="mt-5 text-xl font-semibold text-[#202824] [text-wrap:balance]">
            {readyToGenerate ? '章节已准备好，可以生成全书' : '确认所有章节后生成全书'}
          </h1>
          <p className="mt-2 text-sm leading-6 text-[#69736e] tabular-nums">
            已确认 {chapters.length - unconfirmedChapters.length}/{chapters.length} 章
          </p>
          {unconfirmedChapters.length > 0 && (
            <div className="mx-auto mt-5 max-w-sm divide-y divide-black/5 rounded-md bg-white text-left shadow-[0_1px_6px_rgba(23,43,35,0.08)]">
              {unconfirmedChapters.map(chapter => (
                <button
                  key={chapter.id}
                  type="button"
                  onClick={() => onOpenDrafts(chapter.id)}
                  className="flex min-h-11 w-full items-center px-3 text-sm text-[#59635e] transition-[background-color,transform] hover:bg-[#f7f8f7] active:scale-[0.995]"
                >
                  <span className="truncate">{chapter.title}</span>
                  <span className="ml-auto text-xs text-[#9a6b2f]">待确认</span>
                </button>
              ))}
            </div>
          )}
          <button
            type="button"
            onClick={() => readyToGenerate ? generateMutation.mutate() : onOpenDrafts()}
            disabled={generateMutation.isPending}
            className="mt-6 inline-flex h-11 items-center justify-center gap-2 rounded-md bg-[#3c7a67] px-4 text-sm font-medium text-white transition-[background-color,transform] hover:bg-[#326956] active:scale-[0.96] disabled:opacity-50"
          >
            {generateMutation.isPending && <Loader2 size={16} className="animate-spin" />}
            {readyToGenerate ? '生成全书' : '打开章节稿'}
          </button>
          {mutationError && (
            <p className="mt-3 text-sm text-[#7c3f3f]">
              {getErrorMessage(mutationError, '全书生成失败，请稍后重试。')}
            </p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#f7f8f7]">
      <header className="flex min-h-14 flex-wrap items-center gap-2 border-b border-black/6 bg-white px-3 py-2 sm:px-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-base font-semibold text-[#202824]">{output.title}</h1>
            <span className="rounded bg-[#eef0ef] px-2 py-0.5 text-[11px] text-[#69736e] tabular-nums">
              版本 {output.latest_version?.version ?? 1}
            </span>
          </div>
          <p className="mt-0.5 truncate text-xs text-[#8a948f]">
            {output.is_stale ? `需刷新：${output.stale_chapter_titles.join('、')}` : '全书内容与章节稿一致'}
          </p>
        </div>
        <div className="inline-flex rounded-md bg-[#eef0ef] p-0.5 lg:hidden">
          <PanelButton active={mobilePanel === 'settings'} onClick={() => setMobilePanel('settings')} icon={<Settings2 size={14} />}>
            设置
          </PanelButton>
          <PanelButton active={mobilePanel === 'preview'} onClick={() => setMobilePanel('preview')} icon={<BookOpen size={14} />}>
            预览
          </PanelButton>
        </div>
        <button
          type="button"
          onClick={() => generateMutation.mutate()}
          disabled={!canEdit || !readyToGenerate || generateMutation.isPending}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[#f1f3f2] px-3 text-sm text-[#59635e] transition-[background-color,color,transform] hover:bg-[#e8ecea] hover:text-[#29322e] active:scale-[0.96] disabled:opacity-45"
        >
          {generateMutation.isPending ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
          刷新全书
        </button>
        <ExportButton
          label="PDF"
          pending={downloadMutation.isPending && downloadMutation.variables === 'pdf'}
          disabled={downloadMutation.isPending}
          onClick={() => downloadMutation.mutate('pdf')}
        />
        <ExportButton
          label="TXT"
          pending={downloadMutation.isPending && downloadMutation.variables === 'txt'}
          disabled={downloadMutation.isPending}
          onClick={() => downloadMutation.mutate('txt')}
        />
      </header>

      {output.is_stale && (
        <div className="flex flex-wrap items-center gap-2 border-b border-[#dfd1b7] bg-[#faf5e9] px-4 py-2 text-sm text-[#765a2d]">
          <span className="min-w-0 flex-1">章节稿已更新，刷新全书后再导出最新内容。</span>
          <button
            type="button"
            onClick={() => onOpenDrafts()}
            className="inline-flex h-9 items-center rounded-md px-2 text-xs font-medium transition-[background-color,transform] hover:bg-[#f1e7d3] active:scale-[0.96]"
          >
            查看章节稿
          </button>
        </div>
      )}

      {mutationError && (
        <div className="border-b border-[#ead1d1] bg-[#fbf2f2] px-4 py-2 text-sm text-[#7c3f3f]">
          {getErrorMessage(mutationError, '操作失败，请稍后重试。')}
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[340px_minmax(0,1fr)]">
        <div className={`${mobilePanel === 'settings' ? 'min-h-0' : 'hidden'} lg:block lg:min-h-0 lg:border-r lg:border-black/6`}>
          <MemoirBookSettings
            form={form}
            canEdit={canEdit}
            hasUnsavedChanges={hasUnsavedChanges}
            isSaving={saveMutation.isPending}
            isGeneratingCopy={copyMutation.isPending}
            isSuggestingTitles={titleMutation.isPending}
            titleSuggestions={titleSuggestions}
            onChange={patch => setFormState({
              outputId: output.id,
              form: { ...form, ...patch },
            })}
            onSave={() => saveMutation.mutate()}
            onGenerateCopy={() => copyMutation.mutate()}
            onSuggestTitles={() => titleMutation.mutate()}
          />
        </div>
        <div className={`${mobilePanel === 'preview' ? 'min-h-0' : 'hidden'} min-w-0 lg:block lg:min-h-0`}>
          <MemoirBookPreview
            output={output}
            isUpdatingChapterCopy={chapterCopyMutation.isPending}
            chapterCopyError={chapterCopyMutation.isError
              ? getErrorMessage(chapterCopyMutation.error, '章前文案保存失败，请稍后重试。')
              : null}
            onResetChapterCopyError={chapterCopyMutation.reset}
            onOpenDraft={onOpenDrafts}
            onUpdateChapterCopy={async (chapterId, copy) => {
              try {
                await chapterCopyMutation.mutateAsync({ chapterId, copy });
                return true;
              } catch {
                return false;
              }
            }}
          />
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

function PrimaryAction({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="mt-4 inline-flex h-10 items-center rounded-md bg-[#3c7a67] px-4 text-sm font-medium text-white transition-[background-color,transform] hover:bg-[#326956] active:scale-[0.96]"
    >
      {children}
    </button>
  );
}

function PanelButton({
  active,
  onClick,
  icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex h-8 items-center gap-1 rounded px-2 text-xs transition-[background-color,color,box-shadow,transform] active:scale-[0.96] ${
        active ? 'bg-white text-[#245c4b] shadow-sm' : 'text-[#69736e]'
      }`}
    >
      {icon}
      {children}
    </button>
  );
}

function ExportButton({
  label,
  pending,
  disabled,
  onClick,
}: {
  label: string;
  pending: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex h-10 items-center justify-center gap-1.5 rounded-md px-2 text-sm text-[#59635e] transition-[background-color,color,transform] hover:bg-[#f1f3f2] hover:text-[#29322e] active:scale-[0.96] disabled:opacity-45"
    >
      {pending ? <Loader2 size={15} className="animate-spin" /> : label === 'PDF' ? <Download size={15} /> : <FileText size={15} />}
      {label}
    </button>
  );
}
