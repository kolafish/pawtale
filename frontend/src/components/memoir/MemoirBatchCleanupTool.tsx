import { Check, Loader2, Sparkles } from 'lucide-react';
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { memoirMaterialEditorApi, memoirTranscriptCleanupApi } from '../../api';
import type { MemoirBatchTranscriptCleanupPreviewItem } from '../../types';
import { getErrorMessage } from '../../utils/apiError';
import { compactMaterialText, formatMaterialDateTime } from './memoirMaterialView';


interface MemoirBatchCleanupToolProps {
  projectId: string;
  selectedMaterialId: string | null;
  canEdit: boolean;
}

const MAX_SELECTED = 30;

export function MemoirBatchCleanupTool({
  projectId,
  selectedMaterialId,
  canEdit,
}: MemoirBatchCleanupToolProps) {
  const queryClient = useQueryClient();
  const [selectedIds, setSelectedIds] = useState<string[]>(
    selectedMaterialId ? [selectedMaterialId] : [],
  );
  const [previewItems, setPreviewItems] = useState<MemoirBatchTranscriptCleanupPreviewItem[]>([]);

  const materialsQuery = useInfiniteQuery({
    queryKey: ['memoirAdvancedCleanupMaterials', projectId],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => memoirMaterialEditorApi.list(projectId, {
      sort: 'chronological',
      limit: 100,
      offset: pageParam,
    }),
    getNextPageParam: (lastPage) => {
      const nextOffset = lastPage.offset + lastPage.items.length;
      return nextOffset < lastPage.total ? nextOffset : undefined;
    },
  });
  const materials = useMemo(
    () => materialsQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [materialsQuery.data?.pages],
  );

  const previewMutation = useMutation({
    mutationFn: () => memoirTranscriptCleanupApi.preview(projectId, selectedIds),
    onSuccess: (data) => setPreviewItems(data.items),
  });

  const confirmMutation = useMutation({
    mutationFn: () => memoirTranscriptCleanupApi.confirm(
      projectId,
      previewItems.map((item) => ({
        utterance_id: item.utterance_id,
        source_text_hash: item.source_text_hash,
        source_transcript_version_id: item.source_transcript_version_id,
        cleaned_text: item.cleaned_text,
        provider: item.provider,
        created_by: item.created_by,
      })),
    ),
    onSuccess: () => {
      setPreviewItems([]);
      setSelectedIds([]);
      queryClient.invalidateQueries({ queryKey: ['memoirMaterials', projectId] });
      queryClient.invalidateQueries({ queryKey: ['memoirBookPlan', projectId] });
      queryClient.invalidateQueries({ queryKey: ['memoirChapterDraft', projectId] });
      queryClient.invalidateQueries({ queryKey: ['memoirBookOutput', projectId] });
      queryClient.invalidateQueries({ queryKey: ['memoirAdvancedCleanupMaterials', projectId] });
    },
  });

  const toggleSelected = (materialId: string) => {
    previewMutation.reset();
    confirmMutation.reset();
    setPreviewItems([]);
    setSelectedIds((current) => (
      current.includes(materialId)
        ? current.filter((id) => id !== materialId)
        : current.length < MAX_SELECTED
          ? [...current, materialId]
          : current
    ));
  };

  const canPreview = Boolean(
    canEdit
    && selectedIds.length > 0
    && selectedIds.length <= MAX_SELECTED
    && !previewMutation.isPending
    && !confirmMutation.isPending,
  );
  const canConfirm = Boolean(
    canEdit
    && previewItems.length > 0
    && previewItems.every((item) => item.cleaned_text.trim())
    && !previewMutation.isPending
    && !confirmMutation.isPending,
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-black/6 bg-[#f7f8f7] px-4 py-3 sm:px-5">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs leading-5 text-[#69736e]">选择需要统一校正的素材，先预览再写入。</p>
          <span className="flex-shrink-0 text-xs text-[#59635e] tabular-nums">
            已选 {selectedIds.length}/{MAX_SELECTED}
          </span>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {previewItems.length === 0 ? (
          <div className="divide-y divide-black/5 bg-white">
            {materialsQuery.isLoading ? (
              <div className="flex items-center justify-center gap-2 px-4 py-16 text-sm text-[#69736e]">
                <Loader2 size={16} className="animate-spin" />
                正在读取素材
              </div>
            ) : materialsQuery.isError ? (
              <div className="px-5 py-12 text-center text-sm text-[#9b4646]">素材读取失败</div>
            ) : materials.length ? (
              <>
                {materials.map((material) => {
                const checked = selectedIds.includes(material.id);
                const selectionFull = !checked && selectedIds.length >= MAX_SELECTED;
                return (
                  <label
                    key={material.id}
                    className={`flex min-h-[82px] cursor-pointer items-start gap-3 px-4 py-3 transition-[background-color] sm:px-5 ${
                      checked ? 'bg-[#edf5f1]' : 'hover:bg-[#fafbfa]'
                    } ${selectionFull ? 'cursor-not-allowed opacity-50' : ''}`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!canEdit || selectionFull}
                      onChange={() => toggleSelected(material.id)}
                      className="mt-1 h-4 w-4 flex-shrink-0 accent-[#2f6d59]"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-[#29322e]">
                        {material.summary_title || '未命名讲述'}
                      </span>
                      <span className="mt-1 block line-clamp-2 text-xs leading-5 text-[#69736e]">
                        {compactMaterialText(material.display_text, 100) || '暂无识别文字'}
                      </span>
                      <span className="mt-1 block text-[11px] text-[#69736e]">
                        {formatMaterialDateTime(material)}
                      </span>
                    </span>
                  </label>
                );
                })}
                {materialsQuery.hasNextPage && (
                  <div className="px-4 py-4 sm:px-5">
                    <button
                      type="button"
                      disabled={materialsQuery.isFetchingNextPage}
                      onClick={() => materialsQuery.fetchNextPage()}
                      className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-[#edf1ef] text-sm text-[#59635e] transition-[background-color,opacity,transform] hover:bg-[#e2e8e5] active:scale-[0.98] disabled:opacity-50"
                    >
                      {materialsQuery.isFetchingNextPage && <Loader2 size={15} className="animate-spin" />}
                      加载更多素材
                    </button>
                  </div>
                )}
              </>
            ) : (
              <div className="px-5 py-12 text-center text-sm text-[#69736e]">暂无可校正素材</div>
            )}
          </div>
        ) : (
          <div className="space-y-3 p-4 sm:p-5">
            {previewItems.map((item, index) => (
              <section key={item.utterance_id} className="rounded-md bg-white p-3 shadow-[0_1px_7px_rgba(23,43,35,0.08)]">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="text-xs font-medium text-[#59635e] tabular-nums">预览 {index + 1}</span>
                  <span className={`text-[11px] ${item.changed ? 'text-[#2d705b]' : 'text-[#69736e]'}`}>
                    {item.changed ? '有改动' : '无明显改动'}
                  </span>
                </div>
                <p className="mb-2 line-clamp-3 text-xs leading-5 text-[#69736e]">{item.source_text}</p>
                <textarea
                  value={item.cleaned_text}
                  readOnly={!canEdit}
                  onChange={(event) => setPreviewItems((items) => items.map((previewItem) => (
                    previewItem.utterance_id === item.utterance_id
                      ? {
                        ...previewItem,
                        cleaned_text: event.target.value,
                        changed: event.target.value.trim() !== previewItem.source_text.trim(),
                        provider: null,
                        created_by: 'user',
                      }
                      : previewItem
                  )))}
                  className="min-h-28 w-full resize-y rounded-md border border-black/10 bg-white px-3 py-2 text-sm leading-6 text-[#29322e] outline-none transition-[border-color,box-shadow] focus:border-[#3c7a67] focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)] read-only:bg-[#f5f6f5]"
                />
              </section>
            ))}
          </div>
        )}
      </div>

      <footer className="border-t border-black/6 bg-white px-4 py-3 sm:px-5">
        {(previewMutation.isError || confirmMutation.isError) && (
          <p className="mb-2 text-xs text-[#9b4646]">
            {getErrorMessage(
              previewMutation.error ?? confirmMutation.error,
              previewMutation.isError ? 'AI 校正预览失败。' : '校正结果写入失败。',
            )}
          </p>
        )}
        {previewItems.length === 0 ? (
          <button
            type="button"
            disabled={!canPreview}
            onClick={() => previewMutation.mutate()}
            className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-[#2f6d59] text-sm font-medium text-white transition-[background-color,opacity,transform] hover:bg-[#275d4c] active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-45"
          >
            {previewMutation.isPending ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
            生成校正预览
          </button>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              disabled={confirmMutation.isPending}
              onClick={() => {
                confirmMutation.reset();
                setPreviewItems([]);
              }}
              className="h-10 rounded-md bg-[#edf1ef] text-sm font-medium text-[#46504b] transition-[background-color,transform] hover:bg-[#e2e8e5] active:scale-[0.98]"
            >
              返回选择
            </button>
            <button
              type="button"
              disabled={!canConfirm}
              onClick={() => confirmMutation.mutate()}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-[#2f6d59] text-sm font-medium text-white transition-[background-color,opacity,transform] hover:bg-[#275d4c] active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-45"
            >
              {confirmMutation.isPending ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
              确认写入
            </button>
          </div>
        )}
      </footer>
    </div>
  );
}
