import { Loader2, Plus, Save, Trash2 } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { memoirTranscriptCleanupApi } from '../../api';
import type { MemoirDialectGlossaryEntry } from '../../types';
import { getErrorMessage } from '../../utils/apiError';


interface MemoirDialectGlossaryToolProps {
  projectId: string;
  canEdit: boolean;
}

function normalizeEntries(entries: MemoirDialectGlossaryEntry[]) {
  const seen = new Set<string>();
  return entries.flatMap((entry) => {
    const source = entry.source.trim();
    const target = entry.target.trim();
    const note = (entry.note ?? '').trim();
    if (!source || !target) return [];
    const key = `${source}\u0000${target}`;
    if (seen.has(key)) return [];
    seen.add(key);
    return [{ source, target, note: note || undefined }];
  });
}

export function MemoirDialectGlossaryTool({ projectId, canEdit }: MemoirDialectGlossaryToolProps) {
  const queryClient = useQueryClient();
  const [draftOverride, setDraftOverride] = useState<MemoirDialectGlossaryEntry[] | null>(null);
  const glossaryQuery = useQuery({
    queryKey: ['memoirTranscriptCleanupGlossary', projectId],
    queryFn: () => memoirTranscriptCleanupApi.getGlossary(projectId),
  });
  const savedEntries = useMemo(
    () => glossaryQuery.data?.entries ?? [],
    [glossaryQuery.data?.entries],
  );
  const draft = draftOverride ?? savedEntries;
  const hasChanges = JSON.stringify(normalizeEntries(draft)) !== JSON.stringify(normalizeEntries(savedEntries));

  const saveMutation = useMutation({
    mutationFn: () => memoirTranscriptCleanupApi.updateGlossary(projectId, normalizeEntries(draft)),
    onSuccess: (data) => {
      queryClient.setQueryData(['memoirTranscriptCleanupGlossary', projectId], data);
      setDraftOverride(null);
    },
  });

  const updateEntry = (index: number, patch: Partial<MemoirDialectGlossaryEntry>) => {
    saveMutation.reset();
    setDraftOverride((current) => (current ?? draft).map((entry, entryIndex) => (
      entryIndex === index ? { ...entry, ...patch } : entry
    )));
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-black/6 bg-[#f7f8f7] px-4 py-3 sm:px-5">
        <p className="text-xs leading-5 text-[#69736e]">
          记录四川话、家族称呼和常见误识别，批量校正时会优先参考。
        </p>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-5">
        {glossaryQuery.isLoading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-[#69736e]">
            <Loader2 size={16} className="animate-spin" />
            正在读取词表
          </div>
        ) : glossaryQuery.isError ? (
          <div className="py-12 text-center text-sm text-[#9b4646]">词表读取失败</div>
        ) : draft.length === 0 ? (
          <div className="bg-white px-5 py-12 text-center text-sm text-[#69736e] shadow-[0_1px_7px_rgba(23,43,35,0.07)]">
            暂无词条
          </div>
        ) : (
          <div className="space-y-3">
            {draft.map((entry, index) => (
              <section
                key={`${index}-${entry.source}-${entry.target}`}
                className="rounded-md bg-white p-3 shadow-[0_1px_7px_rgba(23,43,35,0.08)]"
              >
                <div className="grid grid-cols-[minmax(0,1fr)_28px] gap-2">
                  <div className="grid min-w-0 grid-cols-2 gap-2">
                    <label className="min-w-0 text-[11px] text-[#69736e]">
                      方言或误识别
                      <input
                        value={entry.source}
                        disabled={!canEdit}
                        onChange={(event) => updateEntry(index, { source: event.target.value })}
                        className="mt-1 h-10 w-full rounded-md border border-black/10 bg-white px-3 text-sm text-[#29322e] outline-none transition-[border-color,box-shadow] focus:border-[#3c7a67] focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)] disabled:bg-[#f5f6f5]"
                      />
                    </label>
                    <label className="min-w-0 text-[11px] text-[#69736e]">
                      标准写法
                      <input
                        value={entry.target}
                        disabled={!canEdit}
                        onChange={(event) => updateEntry(index, { target: event.target.value })}
                        className="mt-1 h-10 w-full rounded-md border border-black/10 bg-white px-3 text-sm text-[#29322e] outline-none transition-[border-color,box-shadow] focus:border-[#3c7a67] focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)] disabled:bg-[#f5f6f5]"
                      />
                    </label>
                  </div>
                  <button
                    type="button"
                    disabled={!canEdit}
                    onClick={() => {
                      saveMutation.reset();
                      setDraftOverride((current) => (current ?? draft).filter((_, itemIndex) => itemIndex !== index));
                    }}
                    className="mt-5 inline-flex h-9 w-7 items-center justify-center rounded-md text-[#69736e] transition-[background-color,color,opacity,transform] hover:bg-[#fff0ee] hover:text-[#9b4646] active:scale-[0.96] disabled:opacity-40"
                    title="删除词条"
                    aria-label="删除词条"
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
                <label className="mt-2 block text-[11px] text-[#69736e]">
                  备注
                  <input
                    value={entry.note ?? ''}
                    disabled={!canEdit}
                    onChange={(event) => updateEntry(index, { note: event.target.value })}
                    placeholder="人物、地点或使用场景，可不填"
                    className="mt-1 h-10 w-full rounded-md border border-black/10 bg-white px-3 text-sm text-[#29322e] outline-none transition-[border-color,box-shadow] placeholder:text-[#69736e] focus:border-[#3c7a67] focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)] disabled:bg-[#f5f6f5]"
                  />
                </label>
              </section>
            ))}
          </div>
        )}
      </div>

      <footer className="border-t border-black/6 bg-white px-4 py-3 sm:px-5">
        {saveMutation.isError && (
          <p className="mb-2 text-xs text-[#9b4646]">
            {getErrorMessage(saveMutation.error, '词表保存失败。')}
          </p>
        )}
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            disabled={!canEdit || saveMutation.isPending}
            onClick={() => {
              saveMutation.reset();
              setDraftOverride((current) => [...(current ?? draft), { source: '', target: '', note: '' }]);
            }}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[#edf1ef] text-sm font-medium text-[#46504b] transition-[background-color,opacity,transform] hover:bg-[#e2e8e5] active:scale-[0.98] disabled:opacity-45"
          >
            <Plus size={16} />
            添加词条
          </button>
          <button
            type="button"
            disabled={!canEdit || !hasChanges || saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[#2f6d59] text-sm font-medium text-white transition-[background-color,opacity,transform] hover:bg-[#275d4c] active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-45"
          >
            {saveMutation.isPending ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
            保存词表
          </button>
        </div>
      </footer>
    </div>
  );
}
