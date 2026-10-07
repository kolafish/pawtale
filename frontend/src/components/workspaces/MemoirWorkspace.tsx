import { BookMarked, FileAudio, ListTree, Loader2, NotebookPen } from 'lucide-react';
import { lazy, Suspense, useEffect, useState } from 'react';

import type { Project } from '../../types';


const MemoirMaterialWorkspace = lazy(async () => ({
  default: (await import('../memoir/MemoirMaterialWorkspace')).MemoirMaterialWorkspace,
}));
const MemoirChapterWorkspace = lazy(async () => ({
  default: (await import('../memoir/MemoirChapterWorkspace')).MemoirChapterWorkspace,
}));
const MemoirChapterDraftWorkspace = lazy(async () => ({
  default: (await import('../memoir/MemoirChapterDraftWorkspace')).MemoirChapterDraftWorkspace,
}));
const MemoirBookWorkspace = lazy(async () => ({
  default: (await import('../memoir/MemoirBookWorkspace')).MemoirBookWorkspace,
}));

interface MemoirWorkspaceProps {
  project: Project;
}

export function MemoirWorkspace({ project }: MemoirWorkspaceProps) {
  const [view, setView] = useState<MemoirWorkspaceView>(initialView);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);

  const changeView = (nextView: MemoirWorkspaceView, historyMode: 'push' | 'replace' = 'push') => {
    if (nextView === view) return true;
    if (hasUnsavedChanges && !window.confirm('当前编辑内容尚未保存，确定离开吗？')) return false;
    setView(nextView);
    updateViewUrl(nextView, historyMode);
    return true;
  };

  useEffect(() => {
    const handlePopState = () => {
      const nextView = initialView();
      if (nextView === view) return;
      if (!changeView(nextView, 'replace')) updateViewUrl(view, 'replace');
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  });

  const openDrafts = (chapterId?: string) => {
    if (!changeView('drafts')) return;
    const url = new URL(window.location.href);
    if (chapterId) url.searchParams.set('chapter', chapterId);
    else url.searchParams.delete('chapter');
    window.history.replaceState(window.history.state, '', url);
  };

  return (
    <div className="flex h-full min-h-0 w-full flex-col bg-[#f7f8f7] text-[#202824] antialiased">
      <header className="flex h-13 flex-shrink-0 items-center gap-3 border-b border-black/6 bg-white px-3 sm:px-4">
        <div className="hidden min-w-0 sm:block sm:w-44 lg:w-64">
          <p className="truncate text-sm font-semibold text-[#29322e]">{project.title}</p>
          {project.owner_display_name && (
            <p className="truncate text-[11px] text-[#8a948f]">{project.owner_display_name}</p>
          )}
        </div>
        <nav aria-label="回忆录编辑" className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
          {WORKSPACE_VIEWS.map(item => {
            const Icon = item.icon;
            const active = view === item.value;
            return (
              <button
                key={item.value}
                type="button"
                aria-current={active ? 'page' : undefined}
                onClick={() => changeView(item.value)}
                className={`relative inline-flex h-10 flex-shrink-0 items-center justify-center gap-2 rounded-md px-3 text-sm font-medium transition-[background-color,color,transform] active:scale-[0.96] ${
                  active
                    ? 'bg-[#edf5f1] text-[#245c4b]'
                    : 'text-[#69736e] hover:bg-[#f3f5f4] hover:text-[#29322e]'
                }`}
              >
                <Icon size={16} aria-hidden="true" />
                {item.label}
              </button>
            );
          })}
        </nav>
      </header>
      <div className="min-h-0 flex-1">
        <Suspense fallback={(
          <div className="flex h-full items-center justify-center gap-2 text-sm text-[#69736e]">
            <Loader2 size={17} className="animate-spin" />
            正在打开编辑区
          </div>
        )}>
          {view === 'chapters' ? (
            <MemoirChapterWorkspace project={project} />
          ) : view === 'drafts' ? (
            <MemoirChapterDraftWorkspace
              project={project}
              onOpenChapters={() => changeView('chapters')}
            />
          ) : view === 'book' ? (
            <MemoirBookWorkspace
              project={project}
              onOpenChapters={() => changeView('chapters')}
              onOpenDrafts={openDrafts}
            />
          ) : (
            <MemoirMaterialWorkspace
              project={project}
              onUnsavedChange={setHasUnsavedChanges}
            />
          )}
        </Suspense>
      </div>
    </div>
  );
}

type MemoirWorkspaceView = 'materials' | 'chapters' | 'drafts' | 'book';

const WORKSPACE_VIEWS = [
  { value: 'materials' as const, label: '素材校对', icon: FileAudio },
  { value: 'chapters' as const, label: '章节目录', icon: ListTree },
  { value: 'drafts' as const, label: '章节稿', icon: NotebookPen },
  { value: 'book' as const, label: '全书', icon: BookMarked },
];

function initialView(): MemoirWorkspaceView {
  if (typeof window === 'undefined') return 'materials';
  const view = new URL(window.location.href).searchParams.get('view');
  if (view === 'chapters' || view === 'drafts' || view === 'book') return view;
  return 'materials';
}

function updateViewUrl(view: MemoirWorkspaceView, mode: 'push' | 'replace') {
  if (typeof window === 'undefined') return;
  const url = new URL(window.location.href);
  url.searchParams.set('view', view);
  if (view !== 'materials') url.searchParams.delete('material');
  if (view !== 'drafts') url.searchParams.delete('chapter');
  const method = mode === 'push' ? 'pushState' : 'replaceState';
  window.history[method](window.history.state, '', url);
}
