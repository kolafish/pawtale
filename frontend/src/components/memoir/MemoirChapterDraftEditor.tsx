import { ChevronLeft, History, PanelRight, Volume2, X } from 'lucide-react';
import { useMemo, useState } from 'react';

import { memoirMaterialEditorApi } from '../../api';
import type { MemoirBookChapterDraft, MemoirBookChapterDraftVersion, MemoirBookPlanChapter, MemoirMaterialItem } from '../../types';

interface MemoirChapterDraftEditorProps {
  chapter: MemoirBookPlanChapter;
  draft: MemoirBookChapterDraft | null;
  sources: MemoirMaterialItem[];
  onBack: () => void;
}

export function MemoirChapterDraftEditor({ chapter, draft, sources, onBack }: MemoirChapterDraftEditorProps) {
  const [contextTab, setContextTab] = useState<'sources' | 'versions' | null>(null);
  const [viewedVersionId, setViewedVersionId] = useState<string | null>(null);
  const version = draft?.versions.find(item => item.id === viewedVersionId) ?? draft?.latest_version;
  return (
    <section className="relative flex h-full min-h-0 flex-col bg-[#f7f8f7]">
      <header className="border-b border-black/6 bg-white px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={onBack} aria-label="返回章节列表" className="inline-flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-md text-[#69736e] lg:hidden"><ChevronLeft size={18} /></button>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-lg font-semibold text-[#202824]">{chapter.title}</h2>
              <span className="rounded bg-[#eef0ef] px-2 py-0.5 text-[11px] text-[#69736e]">{draft?.status === 'confirmed' ? '已确认' : version ? '草稿' : '未生成'}</span>
            </div>
            <p className="mt-0.5 text-xs text-[#7a847f] tabular-nums">{sources.length} 段素材 · {(version?.content.length ?? 0).toLocaleString('zh-CN')} 字</p>
          </div>
          <button type="button" onClick={() => setContextTab('sources')} className="inline-flex h-10 items-center gap-1.5 rounded-md px-2 text-sm text-[#59635e]"><PanelRight size={15} />素材</button>
          {version && <button type="button" onClick={() => setContextTab('versions')} className="inline-flex h-10 items-center gap-1.5 rounded-md px-2 text-sm text-[#59635e]"><History size={15} />版本 {version.version}</button>}
        </div>
      </header>
      <div className="min-h-0 flex-1 p-3 sm:p-5">
        <textarea aria-label="章节正文" value={version?.content ?? ''} readOnly placeholder="本章尚未生成正文" className="h-full min-h-[420px] w-full resize-none rounded-md bg-white px-5 py-5 text-[16px] leading-8 text-[#29322e] outline-none shadow-[0_1px_4px_rgba(23,43,35,0.08),0_0_0_1px_rgba(0,0,0,0.05)] sm:px-8 sm:py-7 sm:text-[17px]" />
      </div>
      <footer className="border-t border-black/6 bg-white px-4 py-3 text-xs text-[#8a948f] sm:px-5">{version ? `正在查看版本 ${version.version}` : '尚未生成稿件'}</footer>
      {contextTab && (
        <ChapterContextDrawer
          tab={contextTab}
          chapterTitle={chapter.title}
          sources={sources}
          versions={draft?.versions ?? []}
          latestVersionId={draft?.latest_version?.id ?? null}
          onTabChange={setContextTab}
          onViewVersion={item => { setViewedVersionId(item.id); setContextTab(null); }}
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
  const reversedVersions = useMemo(() => [...versions].reverse(), [versions]);
  return (
    <div className="absolute inset-0 z-30 flex justify-end">
      <button
        type="button"
        aria-label="关闭参考面板"
        onClick={onClose}
        className="absolute inset-0 bg-black/15 backdrop-blur-[1px]"
      />
      <aside className="relative flex h-full w-full max-w-[430px] flex-col bg-[#f7f8f7] shadow-[-8px_0_28px_rgba(23,43,35,0.16)]">
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
              <p className="px-4 py-12 text-center text-sm text-[#8a948f]">本章暂无素材</p>
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
                      <span className="ml-auto text-[11px] text-[#8a948f]">
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
            <p className="px-4 py-12 text-center text-sm text-[#8a948f]">暂无历史版本</p>
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
