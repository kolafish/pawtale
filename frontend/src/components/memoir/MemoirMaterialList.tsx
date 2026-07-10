import {
  Check,
  Clock3,
  Loader2,
  RefreshCw,
  Search,
  X,
} from 'lucide-react';

import type {
  MemoirMaterialCorrectionStatus,
  MemoirMaterialItem,
  MemoirMaterialSort,
} from '../../types';
import {
  compactMaterialText,
  formatDuration,
  groupMaterialsBySession,
} from './memoirMaterialView';


interface MemoirMaterialListProps {
  projectTitle: string;
  ownerName?: string | null;
  items: MemoirMaterialItem[];
  total: number;
  selectedId: string | null;
  search: string;
  correctionStatus: MemoirMaterialCorrectionStatus;
  sort: MemoirMaterialSort;
  isLoading: boolean;
  isError: boolean;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onSearchChange: (value: string) => void;
  onCorrectionStatusChange: (value: MemoirMaterialCorrectionStatus) => void;
  onSortChange: (value: MemoirMaterialSort) => void;
  onSelect: (materialId: string) => void;
  onRetry: () => void;
  onLoadMore: () => void;
}

const CORRECTION_FILTERS: Array<{
  value: MemoirMaterialCorrectionStatus;
  label: string;
}> = [
  { value: 'all', label: '全部' },
  { value: 'uncorrected', label: '待校正' },
  { value: 'corrected', label: '已校正' },
];

export function MemoirMaterialList({
  projectTitle,
  ownerName,
  items,
  total,
  selectedId,
  search,
  correctionStatus,
  sort,
  isLoading,
  isError,
  hasNextPage,
  isFetchingNextPage,
  onSearchChange,
  onCorrectionStatusChange,
  onSortChange,
  onSelect,
  onRetry,
  onLoadMore,
}: MemoirMaterialListProps) {
  const groups = groupMaterialsBySession(items);

  return (
    <aside className="flex h-full min-h-0 flex-col bg-[#f7f8f7]">
      <div className="border-b border-black/6 bg-white px-4 py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="truncate text-base font-semibold text-[#202824]">素材校对</h1>
            <p className="mt-1 truncate text-xs text-[#69736e]">
              {projectTitle}{ownerName ? ` · ${ownerName}` : ''}
            </p>
          </div>
          <span className="flex-shrink-0 text-xs text-[#69736e] tabular-nums">{total} 段</span>
        </div>

        <div className="relative mt-4">
          <Search
            aria-hidden="true"
            size={16}
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[#8a948f]"
          />
          <input
            type="search"
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="搜索识别文字或问题"
            className="h-10 w-full rounded-md border border-black/8 bg-[#f7f8f7] pl-9 pr-10 text-sm text-[#202824] outline-none transition-[border-color,box-shadow,background-color] placeholder:text-[#9aa39f] focus:border-[#3c7a67] focus:bg-white focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)]"
          />
          {search && (
            <button
              type="button"
              aria-label="清除搜索"
              title="清除搜索"
              onClick={() => onSearchChange('')}
              className="absolute right-1 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-[#7a847f] transition-[background-color,color,transform] hover:bg-black/5 hover:text-[#202824] active:scale-[0.96]"
            >
              <X size={15} />
            </button>
          )}
        </div>

        <div className="mt-3 flex items-center justify-between gap-2">
          <div className="inline-flex min-w-0 rounded-md bg-[#eef0ef] p-0.5">
            {CORRECTION_FILTERS.map((filter) => (
              <button
                key={filter.value}
                type="button"
                aria-pressed={correctionStatus === filter.value}
                onClick={() => onCorrectionStatusChange(filter.value)}
                className={`h-8 rounded px-2.5 text-xs font-medium transition-[background-color,color,box-shadow,transform] active:scale-[0.96] ${
                  correctionStatus === filter.value
                    ? 'bg-white text-[#245c4b] shadow-[0_1px_3px_rgba(23,43,35,0.12)]'
                    : 'text-[#69736e] hover:text-[#202824]'
                }`}
              >
                {filter.label}
              </button>
            ))}
          </div>
          <label className="sr-only" htmlFor="memoir-material-sort">排序</label>
          <select
            id="memoir-material-sort"
            value={sort}
            onChange={(event) => onSortChange(event.target.value as MemoirMaterialSort)}
            className="h-9 rounded-md border border-black/8 bg-white px-2 text-xs text-[#59635e] outline-none transition-[border-color,box-shadow] focus:border-[#3c7a67] focus:shadow-[0_0_0_3px_rgba(60,122,103,0.12)]"
          >
            <option value="chronological">时间从早到晚</option>
            <option value="recent">时间从晚到早</option>
          </select>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="flex items-center justify-center gap-2 px-4 py-16 text-sm text-[#69736e]">
            <Loader2 size={17} className="animate-spin" />
            正在读取素材
          </div>
        ) : isError ? (
          <div className="px-5 py-16 text-center">
            <p className="text-sm text-[#7c3f3f]">素材读取失败</p>
            <button
              type="button"
              onClick={onRetry}
              className="mt-3 inline-flex h-10 items-center justify-center gap-2 rounded-md bg-white px-3 text-sm text-[#46504b] shadow-[0_1px_6px_rgba(23,43,35,0.1)] transition-[background-color,transform] hover:bg-[#f3f5f4] active:scale-[0.96]"
            >
              <RefreshCw size={15} />
              重新加载
            </button>
          </div>
        ) : groups.length === 0 ? (
          <div className="px-6 py-16 text-center">
            <p className="text-sm font-medium text-[#46504b]">没有找到素材</p>
            <p className="mt-1 text-xs leading-5 text-[#8a948f]">调整搜索词或校正状态后再试。</p>
          </div>
        ) : (
          <div className="pb-4">
            {groups.map((group) => (
              <section key={group.sessionId} aria-label={`${group.dateLabel} ${group.timeLabel}`}>
                <div className="sticky top-0 z-10 flex h-9 items-center gap-2 border-y border-black/5 bg-[#f1f3f2]/95 px-4 text-[11px] font-medium text-[#69736e] backdrop-blur-sm">
                  <Clock3 size={13} aria-hidden="true" />
                  <span>{group.dateLabel}</span>
                  <span className="tabular-nums">{group.timeLabel}</span>
                  <span className="ml-auto tabular-nums">{group.items.length} 段</span>
                </div>
                <div className="divide-y divide-black/5 bg-white">
                  {group.items.map((material) => {
                    const selected = material.id === selectedId;
                    return (
                      <button
                        key={material.id}
                        type="button"
                        onClick={() => onSelect(material.id)}
                        aria-current={selected ? 'true' : undefined}
                        className={`group relative block min-h-[92px] w-full px-4 py-3 text-left outline-none transition-[background-color,box-shadow,transform] active:scale-[0.995] ${
                          selected
                            ? 'z-[1] bg-[#edf5f1] shadow-[inset_3px_0_0_#3c7a67]'
                            : 'hover:bg-[#fafbfa] focus-visible:shadow-[inset_0_0_0_2px_rgba(60,122,103,0.35)]'
                        }`}
                      >
                        <div className="flex items-start gap-3">
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-[#29322e]">
                                {material.summary_title || '未命名讲述'}
                              </h2>
                              {material.has_manual_correction ? (
                                <span className="inline-flex flex-shrink-0 items-center gap-1 text-[11px] text-[#2d705b]">
                                  <Check size={12} aria-hidden="true" />
                                  已校正
                                </span>
                              ) : (
                                <span className="flex-shrink-0 text-[11px] text-[#9a6b2f]">待校正</span>
                              )}
                            </div>
                            <p className="mt-1.5 line-clamp-2 text-xs leading-5 text-[#69736e]">
                              {compactMaterialText(material.summary_text || material.display_text) || '暂无识别文字'}
                            </p>
                            <div className="mt-2 flex items-center gap-3 text-[11px] text-[#929b97]">
                              <span className="tabular-nums">音频 {formatDuration(material.audio_duration_seconds)}</span>
                              <span className="tabular-nums">{material.display_char_count} 字</span>
                            </div>
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </section>
            ))}
            {hasNextPage && (
              <div className="px-4 py-4">
                <button
                  type="button"
                  disabled={isFetchingNextPage}
                  onClick={onLoadMore}
                  className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-white text-sm text-[#59635e] shadow-[0_1px_6px_rgba(23,43,35,0.08)] transition-[background-color,opacity,transform] hover:bg-[#f8faf9] active:scale-[0.98] disabled:opacity-60"
                >
                  {isFetchingNextPage && <Loader2 size={15} className="animate-spin" />}
                  加载更多
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
