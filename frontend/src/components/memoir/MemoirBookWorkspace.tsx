import { useMutation, useQuery } from '@tanstack/react-query';
import { BookOpen, Download, FileText, Loader2, RefreshCw, Settings2 } from 'lucide-react';
import { useState } from 'react';

import { memoirBookApi } from '../../api';
import type { Project } from '../../types';
import { getErrorMessage } from '../../utils/apiError';
import { MemoirBookPreview } from './MemoirBookPreview';
import { MemoirBookSettings } from './MemoirBookSettings';
import { bookOutputForm, saveBrowserDownload } from './memoirBookOutputView';

interface MemoirBookWorkspaceProps {
  project: Project;
  onOpenChapters: () => void;
  onOpenDrafts: (chapterId?: string) => void;
}

export function MemoirBookWorkspace({ project, onOpenChapters, onOpenDrafts }: MemoirBookWorkspaceProps) {
  const [mobilePanel, setMobilePanel] = useState<'settings' | 'preview'>('preview');
  const outputQuery = useQuery({
    queryKey: ['memoirBookOutput', project.id],
    queryFn: () => memoirBookApi.getOutput(project.id),
  });
  const output = outputQuery.data;
  const downloadMutation = useMutation({
    mutationFn: (format: 'pdf' | 'txt') => memoirBookApi.downloadOutput(project.id, format),
    onSuccess: (download, format) => saveBrowserDownload(download.blob, download.filename || `${output?.title || project.title}.${format}`),
  });
  if (outputQuery.isLoading) {
    return <div className="flex h-full items-center justify-center gap-2 text-sm text-[#69736e]"><Loader2 size={20} className="animate-spin" />正在读取全书</div>;
  }
  if (outputQuery.isError) {
    return (
      <div className="flex h-full flex-col items-center justify-center text-sm text-[#69736e]">
        <BookOpen size={24} /><p className="mt-3">{getErrorMessage(outputQuery.error, '全书读取失败')}</p>
        <button type="button" onClick={() => outputQuery.refetch()} className="mt-4 inline-flex h-10 items-center gap-2 rounded-md bg-white px-3"><RefreshCw size={15} />重新加载</button>
      </div>
    );
  }
  if (!output?.latest_version) {
    return (
      <div className="flex h-full flex-col items-center justify-center text-sm text-[#69736e]">
        <BookOpen size={26} /><p className="mt-3">还没有全书内容</p>
        <button type="button" onClick={onOpenChapters} className="mt-4 h-10 rounded-md bg-[#3c7a67] px-4 text-white">打开章节目录</button>
      </div>
    );
  }
  return (
    <div className="flex h-full min-h-0 flex-col bg-[#f7f8f7]">
      <header className="flex min-h-14 flex-wrap items-center gap-2 border-b border-black/6 bg-white px-3 py-2 sm:px-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-base font-semibold text-[#202824]">{output.title}</h1>
            <span className="rounded bg-[#eef0ef] px-2 py-0.5 text-[11px] text-[#69736e]">版本 {output.latest_version.version}</span>
          </div>
          <p className="mt-0.5 truncate text-xs text-[#8a948f]">{output.is_stale ? `章节稿已更新：${output.stale_chapter_titles.join('、')}` : '全书内容与章节稿一致'}</p>
        </div>
        <div className="inline-flex rounded-md bg-[#eef0ef] p-0.5 lg:hidden">
          <button type="button" onClick={() => setMobilePanel('settings')} aria-pressed={mobilePanel === 'settings'} className="inline-flex h-9 items-center gap-1 rounded px-2 text-xs"><Settings2 size={14} />设置</button>
          <button type="button" onClick={() => setMobilePanel('preview')} aria-pressed={mobilePanel === 'preview'} className="inline-flex h-9 items-center gap-1 rounded px-2 text-xs"><BookOpen size={14} />预览</button>
        </div>
        <button type="button" onClick={() => outputQuery.refetch()} disabled={outputQuery.isFetching} className="inline-flex h-10 items-center gap-2 rounded-md bg-[#f1f3f2] px-3 text-sm text-[#59635e]">
          <RefreshCw size={15} className={outputQuery.isFetching ? 'animate-spin' : ''} />重新加载
        </button>
        {(['pdf', 'txt'] as const).map(format => (
          <button key={format} type="button" onClick={() => downloadMutation.mutate(format)} disabled={downloadMutation.isPending} className="inline-flex h-10 items-center gap-1.5 rounded-md px-2 text-sm text-[#59635e] disabled:opacity-45">
            {downloadMutation.isPending && downloadMutation.variables === format ? <Loader2 size={15} className="animate-spin" /> : format === 'pdf' ? <Download size={15} /> : <FileText size={15} />}
            {format.toUpperCase()}
          </button>
        ))}
      </header>
      {downloadMutation.isError && <p className="border-b border-[#ead1d1] bg-[#fbf2f2] px-4 py-2 text-sm text-[#7c3f3f]">{getErrorMessage(downloadMutation.error, '下载失败，请稍后重试。')}</p>}
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[340px_minmax(0,1fr)]">
        <div className={`${mobilePanel === 'settings' ? 'min-h-0' : 'hidden'} lg:block lg:min-h-0 lg:border-r lg:border-black/6`}>
          <MemoirBookSettings form={bookOutputForm(output)} />
        </div>
        <div className={`${mobilePanel === 'preview' ? 'min-h-0' : 'hidden'} min-w-0 lg:block lg:min-h-0`}>
          <MemoirBookPreview output={output} onOpenDraft={onOpenDrafts} />
        </div>
      </div>
    </div>
  );
}
