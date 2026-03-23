import { Check, X, CheckCheck, XCircle, ArrowLeft } from 'lucide-react';
import type { DiffChunk } from '../types';

interface DiffReviewViewProps {
  chunks: DiffChunk[];
  onAcceptChunk: (id: string) => void;
  onRejectChunk: (id: string) => void;
  onToggleChunk: (id: string) => void;
  onAcceptAll: () => void;
  onRejectAll: () => void;
  onApply: () => void;
  onCancel: () => void;
  applyLabel?: string;
  cancelLabel?: string;
}

export function DiffReviewView({
  chunks,
  onAcceptChunk,
  onRejectChunk,
  onToggleChunk,
  onAcceptAll,
  onRejectAll,
  onApply,
  onCancel,
  applyLabel = '应用修改',
  cancelLabel = '取消',
}: DiffReviewViewProps) {
  const pendingCount = chunks.filter(c => c.type !== 'equal' && c.accepted === null).length;
  const changeCount = chunks.filter(c => c.type !== 'equal').length;

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-4 py-2 bg-amber-50 border-b border-amber-200">
        <span className="text-sm font-medium text-amber-700">
          审阅模式
        </span>
        <span className="text-xs text-amber-600">
          {changeCount} 处修改{pendingCount > 0 ? `，${pendingCount} 处待定` : ''}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={onAcceptAll}
            className="inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-md bg-green-500 text-white hover:bg-green-600"
          >
            <CheckCheck size={12} />
            全部接受
          </button>
          <button
            onClick={onRejectAll}
            className="inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-md bg-red-500 text-white hover:bg-red-600"
          >
            <XCircle size={12} />
            全部拒绝
          </button>
          <button
            onClick={onApply}
            className="inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-md bg-pink-500 text-white hover:bg-pink-600"
          >
            <Check size={12} />
            {applyLabel}
          </button>
          <button
            onClick={onCancel}
            className="inline-flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-md border border-gray-200 text-gray-600 hover:bg-gray-50"
          >
            <ArrowLeft size={12} />
            {cancelLabel}
          </button>
        </div>
      </div>

      {/* Diff content */}
      <div className="flex-1 overflow-y-auto px-4 py-4 leading-7 whitespace-pre-wrap">
        {chunks.map((chunk) => {
          if (chunk.type === 'equal') {
            return <span key={chunk.id}>{chunk.text}</span>;
          }

          if (chunk.type === 'insert') {
            const isAccepted = chunk.accepted === true;
            const isRejected = chunk.accepted === false;
            return (
              <span
                key={chunk.id}
                className={`relative group cursor-pointer rounded-sm ${
                  isRejected
                    ? 'bg-gray-100 text-gray-400 line-through'
                    : isAccepted
                    ? 'bg-green-200 text-green-900'
                    : 'bg-green-100 text-green-800'
                }`}
                onClick={() => onToggleChunk(chunk.id)}
              >
                {chunk.text}
                <span className="hidden group-hover:inline-flex items-center gap-0.5 absolute -top-6 left-0 bg-white rounded shadow-md border border-gray-200 px-1 py-0.5 z-10">
                  <button
                    onClick={(e) => { e.stopPropagation(); onAcceptChunk(chunk.id); }}
                    className="p-0.5 text-green-600 hover:bg-green-50 rounded"
                    title="接受"
                  >
                    <Check size={12} />
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); onRejectChunk(chunk.id); }}
                    className="p-0.5 text-red-600 hover:bg-red-50 rounded"
                    title="拒绝"
                  >
                    <X size={12} />
                  </button>
                </span>
              </span>
            );
          }

          // delete
          const isAccepted = chunk.accepted === true; // agree to delete
          const isRejected = chunk.accepted === false; // keep original
          return (
            <span
              key={chunk.id}
              className={`relative group cursor-pointer rounded-sm ${
                isRejected
                  ? 'bg-yellow-50 text-gray-700'
                  : isAccepted
                  ? 'bg-red-200 text-red-400 line-through opacity-60'
                  : 'bg-red-100 text-red-700 line-through'
              }`}
              onClick={() => onToggleChunk(chunk.id)}
            >
              {chunk.text}
              <span className="hidden group-hover:inline-flex items-center gap-0.5 absolute -top-6 left-0 bg-white rounded shadow-md border border-gray-200 px-1 py-0.5 z-10">
                <button
                  onClick={(e) => { e.stopPropagation(); onAcceptChunk(chunk.id); }}
                  className="p-0.5 text-green-600 hover:bg-green-50 rounded"
                  title="删除此段"
                >
                  <Check size={12} />
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); onRejectChunk(chunk.id); }}
                  className="p-0.5 text-red-600 hover:bg-red-50 rounded"
                  title="保留此段"
                >
                  <X size={12} />
                </button>
              </span>
            </span>
          );
        })}
      </div>
    </div>
  );
}
