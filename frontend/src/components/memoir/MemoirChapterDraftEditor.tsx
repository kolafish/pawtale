import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ChevronLeft, History, Loader2, PanelRight, Save, Volume2, X } from 'lucide-react';
import { useMemo, useState } from 'react';

import { useDialogFocus } from '../../hooks/useDialogFocus';

import { memoirBookApi, memoirMaterialEditorApi } from '../../api';
import type { MemoirBookChapterDraft, MemoirBookChapterDraftVersion, MemoirBookPlanChapter, MemoirMaterialItem } from '../../types';
import { getErrorMessage } from '../../utils/apiError';
import { useMemoirEditGuard } from './useMemoirEditGuard';

interface MemoirChapterDraftEditorProps {
  chapter: MemoirBookPlanChapter;
  draft: MemoirBookChapterDraft | null;
  sources: MemoirMaterialItem[];
  onBack: () => void;
  canEdit: boolean;
  onUnsavedChange?: (dirty: boolean) => void;
}

export function MemoirChapterDraftEditor({ chapter, draft, sources, onBack, canEdit, onUnsavedChange }: MemoirChapterDraftEditorProps) {
  const queryClient = useQueryClient();
  const [contextTab, setContextTab] = useState<'sources' | 'versions' | null>(null);
  const [viewedVersionId, setViewedVersionId] = useState<string | null>(null);
  const [edit, setEdit] = useState<{ base: MemoirBookChapterDraftVersion; contents: string[] } | null>(null);
  const version = draft?.versions.find(item => item.id === viewedVersionId) ?? edit?.base ?? draft?.latest_version;
  const historical = Boolean(viewedVersionId && version?.id !== draft?.latest_version?.id);
  const editable = canEdit && !historical && Boolean(version?.sections.length);
  const dirty = Boolean(edit && edit.contents.some((content, index) => content !== edit.base.sections[index].content));
  const valid = !edit || edit.contents.every(content => content.trim().length > 0 && content.trim().length <= 100_000)
    && edit.contents.reduce((total, content) => total + content.trim().length, 0) <= 200_000;
  const saveMutation = useMutation({
    mutationFn: () => memoirBookApi.reviseChapterDraft(chapter.project_id, chapter.id, {
      expected_version_id: edit!.base.id,
      sections: edit!.base.sections.map((section, index) => ({ section_id: section.section_id, content: edit!.contents[index].trim() })),
    }),
    onSuccess: updated => {
      queryClient.setQueryData(['memoirChapterDraft', chapter.project_id, chapter.id], updated);
      void queryClient.invalidateQueries({ queryKey: ['memoirBookPlan', chapter.project_id] });
      void queryClient.invalidateQueries({ queryKey: ['memoirBookOutput', chapter.project_id] });
      setEdit(null);
      setViewedVersionId(null);
    },
  });
  const save = async () => {
    if (!dirty) return;
    if (!editable || !valid) throw new Error('请检查小节正文后再保存');
    await saveMutation.mutateAsync();
  };
  const confirmDiscard = useMemoirEditGuard({ dirty, saving: saveMutation.isPending, save, onUnsavedChange });
  const discard = () => {
    if (!confirmDiscard()) return;
    setEdit(null);
    saveMutation.reset();
  };
  const changeSection = (index: number, content: string) => {
    if (!version || !editable || saveMutation.isPending) return;
    saveMutation.reset();
    const contents = [...(edit?.contents ?? version.sections.map(section => section.content))];
    contents[index] = content;
    const base = edit?.base ?? version;
    setEdit(contents.every((value, sectionIndex) => value === base.sections[sectionIndex].content) ? null : { base, contents });
  };
  return (
    <section className="relative flex h-full min-h-0 flex-col bg-[#f7f8f7]">
      <header className="border-b border-black/6 bg-white px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => { if (confirmDiscard()) { setEdit(null); onBack(); } }} aria-label="返回章节列表" className="inline-flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-md text-[#69736e] lg:hidden"><ChevronLeft size={18} /></button>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-lg font-semibold text-[#202824]">{chapter.title}</h2>
              <span className="rounded bg-[#eef0ef] px-2 py-0.5 text-[11px] text-[#69736e]">{draft?.status === 'confirmed' ? '已确认' : version ? '草稿' : '未生成'}</span>
            </div>
            <p className="mt-0.5 text-xs text-[#69736e] tabular-nums">{sources.length} 段素材 · {(edit ? edit.contents.join('\n\n').length : version?.content.length ?? 0).toLocaleString('zh-CN')} 字</p>
          </div>
          <button type="button" onClick={() => setContextTab('sources')} className="inline-flex h-10 items-center gap-1.5 rounded-md px-2 text-sm text-[#59635e]"><PanelRight size={15} />素材</button>
          {version && <button type="button" onClick={() => setContextTab('versions')} className="inline-flex h-10 items-center gap-1.5 rounded-md px-2 text-sm text-[#59635e]"><History size={15} />版本 {version.version}</button>}
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-3 sm:p-5">
        {version?.sections.length ? (
          <div className="space-y-5 rounded-md bg-white px-5 py-5 shadow-[0_1px_4px_rgba(23,43,35,0.08)] sm:px-8 sm:py-7">
            {version.sections.map((section, index) => (
              <label key={section.section_id} className="block">
                <span className="mb-2 block text-sm font-semibold text-[#59635e]">{section.title}</span>
                <textarea aria-label={`小节正文：${section.title}`} value={edit?.contents[index] ?? section.content} onChange={event => changeSection(index, event.target.value)} readOnly={!editable || saveMutation.isPending} rows={Math.max(6, Math.min(20, section.content.split('\n').length + Math.ceil(section.content.length / 40)))} className="w-full resize-y rounded-md p-2 text-[16px] leading-8 text-[#29322e] outline-none focus-visible:ring-2 focus-visible:ring-[#3c7a67] sm:text-[17px]" />
              </label>
            ))}
          </div>
        ) : <textarea aria-label="章节正文" value={version?.content ?? ''} readOnly placeholder="本章尚未生成正文" className="h-full min-h-[420px] w-full resize-none rounded-md bg-white px-5 py-5 text-[16px] leading-8 text-[#29322e] outline-none shadow-[0_1px_4px_rgba(23,43,35,0.08),0_0_0_1px_rgba(0,0,0,0.05)] sm:px-8 sm:py-7 sm:text-[17px]" />}
      </div>
      <footer className="border-t border-black/6 bg-white px-4 py-3 text-xs text-[#69736e] sm:px-5">
        {saveMutation.isError && <div className="mb-2 text-[#7c3f3f]"><p role="alert">{getErrorMessage(saveMutation.error, '正文保存失败，修改内容已保留。')}</p><button type="button" onClick={() => { if (confirmDiscard()) { setEdit(null); saveMutation.reset(); void queryClient.invalidateQueries({ queryKey: ['memoirChapterDraft', chapter.project_id, chapter.id] }); } }} className="min-h-11 underline">放弃修改并读取最新版</button></div>}
        {!valid && <p role="alert" className="mb-2 text-[#7c3f3f]">小节正文不能为空或超过 10 万字，整章不能超过 20 万字。</p>}
        <div className="flex flex-wrap items-center gap-2">
          <span role="status" className="min-w-0 flex-1">{historical ? `只读 · 历史版本 ${version?.version}` : saveMutation.isPending ? '正在保存' : dirty ? '有未保存的修改' : saveMutation.isSuccess ? '章节正文已保存' : editable ? '按小节修改正文，完成后点击「保存正文」' : version ? canEdit ? '当前正文缺少小节结构，请先在小程序重新整理本章' : '只读 · 请先确认章节目录或检查编辑权限' : '尚未生成稿件'}</span>
          {historical ? <button type="button" onClick={() => setViewedVersionId(null)} className="h-11 rounded-md bg-[#f1f3f2] px-3 text-sm">返回最新版</button> : editable && <>
            <button type="button" onClick={discard} disabled={!dirty || saveMutation.isPending} className="h-11 rounded-md px-3 text-sm disabled:opacity-45">放弃修改</button>
            <button type="button" onClick={() => { void save().catch(() => {}); }} disabled={!dirty || !valid || saveMutation.isPending} className="inline-flex h-11 items-center gap-2 rounded-md bg-[#3c7a67] px-4 text-sm text-white transition-transform active:scale-[0.96] disabled:opacity-45">{saveMutation.isPending ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}保存正文</button>
          </>}
        </div>
      </footer>
      {contextTab && (
        <ChapterContextDrawer
          tab={contextTab}
          chapterTitle={chapter.title}
          sources={sources}
          versions={draft?.versions ?? []}
          latestVersionId={draft?.latest_version?.id ?? null}
          onTabChange={setContextTab}
          onViewVersion={item => { if (confirmDiscard()) { setEdit(null); saveMutation.reset(); setViewedVersionId(item.id); setContextTab(null); } }}
          onClose={() => setContextTab(null)}
        />
      )}
    </section>
  );
}

function ChapterContextDrawer({
  tab,
  chapterTitle,
  sources,
  versions,
  latestVersionId,
  onTabChange,
  onViewVersion,
  onClose,
}: {
  tab: 'sources' | 'versions';
  chapterTitle: string;
  sources: MemoirMaterialItem[];
  versions: MemoirBookChapterDraftVersion[];
  latestVersionId: string | null;
  onTabChange: (tab: 'sources' | 'versions') => void;
  onViewVersion: (version: MemoirBookChapterDraftVersion) => void;
  onClose: () => void;
}) {
  const [openAudioId, setOpenAudioId] = useState<string | null>(null);
  const dialogRef = useDialogFocus({ onClose });
  const reversedVersions = useMemo(() => [...versions].reverse(), [versions]);
  return (
    <div className="absolute inset-0 z-30 flex justify-end">
      <button
        type="button"
        aria-label="关闭参考面板"
        tabIndex={-1}
        onClick={onClose}
        className="absolute inset-0 bg-black/15 backdrop-blur-[1px]"
      />
      <aside ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label="章节参考面板" className="relative flex h-full w-full max-w-[430px] flex-col bg-[#f7f8f7] shadow-[-8px_0_28px_rgba(23,43,35,0.16)]">
        <header className="border-b border-black/6 bg-white px-4 py-3">
          <div className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <h3 className="truncate text-sm font-semibold text-[#29322e]">{chapterTitle}</h3>
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭参考面板"
              title="关闭"
              className="inline-flex h-10 w-10 items-center justify-center rounded-md text-[#69736e] transition-[background-color,transform] hover:bg-black/5 active:scale-[0.96]"
            >
              <X size={17} />
            </button>
          </div>
          <div className="mt-2 inline-flex rounded-md bg-[#eef0ef] p-0.5">
            <DrawerTab active={tab === 'sources'} onClick={() => onTabChange('sources')}>本章素材</DrawerTab>
            <DrawerTab active={tab === 'versions'} onClick={() => onTabChange('versions')}>历史版本</DrawerTab>
          </div>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {tab === 'sources' ? (
            sources.length > 0 ? (
              <div className="space-y-2">
                {sources.map(source => (
                  <article
                    key={source.id}
                    className="rounded-md bg-white p-3 shadow-[0_1px_3px_rgba(23,43,35,0.08),0_0_0_1px_rgba(0,0,0,0.04)]"
                  >
                    <p className="text-sm leading-6 text-[#37413c] [text-wrap:pretty]">{source.display_text}</p>
                    {openAudioId === source.id && source.audio_file_id && (
                      <audio
                        controls
                        autoPlay
                        preload="metadata"
                        src={memoirMaterialEditorApi.audioPlaybackUrl(source.audio_file_id)}
                        className="mt-3 h-10 w-full"
                      />
                    )}
                    <button
                      type="button"
                      onClick={() => setOpenAudioId(current => (
                        current === source.id ? null : source.id
                      ))}
                      disabled={!source.audio_file_id}
                      className="mt-2 inline-flex h-10 items-center gap-1.5 rounded-md px-2 text-xs text-[#69736e] transition-[background-color,color,transform] hover:bg-black/5 hover:text-[#29322e] active:scale-[0.96] disabled:opacity-35"
                    >
                      <Volume2 size={14} />
                      {openAudioId === source.id ? '收起音频' : '听原音'}
                    </button>
                  </article>
                ))}
              </div>
            ) : (
              <p className="px-4 py-12 text-center text-sm text-[#69736e]">本章暂无素材</p>
            )
          ) : reversedVersions.length > 0 ? (
            <div className="space-y-2">
              {reversedVersions.map(version => {
                const isLatest = version.id === latestVersionId;
                return (
                  <article
                    key={version.id}
                    className="rounded-md bg-white p-3 shadow-[0_1px_3px_rgba(23,43,35,0.08),0_0_0_1px_rgba(0,0,0,0.04)]"
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-[#29322e] tabular-nums">版本 {version.version}</span>
                      {isLatest && <span className="rounded bg-[#eaf3ee] px-1.5 py-0.5 text-[10px] text-[#2d705b]">当前</span>}
                      <span className="ml-auto text-[11px] text-[#69736e]">
                        {version.created_by === 'user' ? '手工编辑' : 'AI 整理'}
                      </span>
                    </div>
                    <p className="mt-2 line-clamp-4 text-xs leading-5 text-[#69736e]">{version.content}</p>
                    {!isLatest && (
                      <button
                        type="button"
                        onClick={() => onViewVersion(version)}
                        className="mt-3 inline-flex h-10 items-center rounded-md bg-[#f1f3f2] px-3 text-xs font-medium text-[#59635e] transition-[background-color,transform] hover:bg-[#e8ecea] active:scale-[0.96]"
                      >
                        查看此版本
                      </button>
                    )}
                  </article>
                );
              })}
            </div>
          ) : (
            <p className="px-4 py-12 text-center text-sm text-[#69736e]">暂无历史版本</p>
          )}
        </div>
      </aside>
    </div>
  );
}

function DrawerTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`h-8 rounded px-3 text-xs font-medium transition-[background-color,color,box-shadow,transform] active:scale-[0.96] ${
        active
          ? 'bg-white text-[#245c4b] shadow-[0_1px_3px_rgba(23,43,35,0.12)]'
          : 'text-[#69736e] hover:text-[#202824]'
      }`}
    >
      {children}
    </button>
  );
}
