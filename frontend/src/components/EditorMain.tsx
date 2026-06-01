import { RefreshCw, Save, Wand2 } from 'lucide-react';
import { DiffReviewView } from './DiffReviewView';
import { MultiModelTabBar } from './MultiModelTabBar';
import type { DiffChunk, SelectionRange, MultiReviseResultItem } from '../types';
import type { SaveStatus } from '../hooks/useProjectState';

interface EditorMainProps {
  title: string;
  setTitle: (v: string) => void;
  editorContent: string;
  setEditorContent: (v: string) => void;
  saveStatus: SaveStatus;
  wordCount: number;
  totalWordCount: number;
  isSaving: boolean;
  onSave: () => void;
  onCloseProject: () => void;
  // Selection tracking
  onSelectionChange: (s: SelectionRange | null) => void;
  // Diff review
  diffChunks: DiffChunk[] | null;
  onAcceptChunk: (id: string) => void;
  onRejectChunk: (id: string) => void;
  onToggleChunk: (id: string) => void;
  onAcceptAll: () => void;
  onRejectAll: () => void;
  onApplyRevision: () => void;
  onCancelRevision: () => void;
  // Multi-model
  multiResults: MultiReviseResultItem[] | null;
  activeTabIndex: number;
  onSwitchTab: (index: number) => void;
  // AI Panel toggle
  isAIPanelOpen: boolean;
  onToggleAIPanel: () => void;
}

export function EditorMain({
  title,
  setTitle,
  editorContent,
  setEditorContent,
  saveStatus,
  wordCount,
  totalWordCount,
  isSaving,
  onSave,
  onCloseProject,
  onSelectionChange,
  diffChunks,
  onAcceptChunk,
  onRejectChunk,
  onToggleChunk,
  onAcceptAll,
  onRejectAll,
  onApplyRevision,
  onCancelRevision,
  multiResults,
  activeTabIndex,
  onSwitchTab,
  isAIPanelOpen,
  onToggleAIPanel,
}: EditorMainProps) {
  const saveStatusLabel = saveStatus === 'saved' ? '已保存' : saveStatus === 'saving' ? '保存中...' : '未保存';
  const saveStatusColor = saveStatus === 'saved' ? 'text-green-500' : saveStatus === 'saving' ? 'text-yellow-500' : 'text-gray-400';

  const handleSelect = (e: React.SyntheticEvent<HTMLTextAreaElement>) => {
    const target = e.target as HTMLTextAreaElement;
    const start = target.selectionStart;
    const end = target.selectionEnd;
    if (start !== end) {
      onSelectionChange({
        start,
        end,
        text: editorContent.substring(start, end),
      });
    } else {
      onSelectionChange(null);
    }
  };

  return (
    <div className="h-full flex flex-col">
      {/* Top bar */}
      <div className="px-2 md:px-4 py-2 md:py-3 border-b border-pink-100 bg-white flex items-center gap-2 md:gap-3">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="文档名称"
          className="flex-1 min-w-0 px-2 md:px-3 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-500 text-sm md:text-base"
          disabled={diffChunks !== null || multiResults !== null}
        />
        <button
          onClick={onSave}
          disabled={isSaving || diffChunks !== null || multiResults !== null}
          className="inline-flex items-center gap-1 px-2 md:px-3 py-2 rounded-lg border border-gray-200 hover:bg-gray-50 disabled:opacity-60 flex-shrink-0"
        >
          {isSaving ? <RefreshCw size={16} className="animate-spin" /> : <Save size={16} />}
          <span className="hidden md:inline">保存</span>
        </button>
        <button
          onClick={onToggleAIPanel}
          className={`inline-flex items-center gap-1 px-2 md:px-3 py-2 rounded-lg border transition-colors flex-shrink-0 ${
            isAIPanelOpen
              ? 'border-pink-300 bg-pink-50 text-pink-600'
              : 'border-gray-200 hover:bg-gray-50 text-gray-600'
          }`}
        >
          <Wand2 size={16} />
          <span className="hidden md:inline">AI</span>
        </button>
        <button
          onClick={onCloseProject}
          className="text-sm px-2 md:px-3 py-2 rounded-lg border border-gray-200 hover:bg-gray-50 flex-shrink-0"
        >
          返回
        </button>
      </div>

      {/* Editor area */}
      <div className="flex-1 min-h-0 p-4 flex flex-col">
        {(diffChunks || multiResults) ? (
          <div className="flex-1 rounded-xl border border-amber-200 overflow-hidden flex flex-col">
            {multiResults && (
              <MultiModelTabBar
                results={multiResults}
                activeIndex={activeTabIndex}
                onSwitchTab={onSwitchTab}
              />
            )}
            {diffChunks ? (
              <DiffReviewView
                chunks={diffChunks}
                onAcceptChunk={onAcceptChunk}
                onRejectChunk={onRejectChunk}
                onToggleChunk={onToggleChunk}
                onAcceptAll={onAcceptAll}
                onRejectAll={onRejectAll}
                onApply={onApplyRevision}
                onCancel={onCancelRevision}
                applyLabel={multiResults ? '采纳此版本' : undefined}
                cancelLabel={multiResults ? '全部放弃' : undefined}
              />
            ) : (
              <div className="flex-1 flex items-center justify-center text-gray-400 p-8">
                <p>{multiResults?.[activeTabIndex]?.error || 'AI 修改失败'}</p>
              </div>
            )}
          </div>
        ) : (
          <textarea
            value={editorContent}
            onChange={(e) => setEditorContent(e.target.value)}
            onSelect={handleSelect}
            onMouseUp={handleSelect}
            placeholder="开始写作..."
            className="flex-1 w-full px-4 py-4 rounded-xl border border-pink-100 focus:outline-none focus:ring-2 focus:ring-pink-500 leading-7 resize-none"
          />
        )}
      </div>

      {/* Status bar */}
      <div className="px-2 md:px-4 py-2 border-t border-pink-100 bg-white flex items-center justify-between text-xs text-gray-500">
        <div className="flex items-center gap-2 md:gap-4">
          <span>{wordCount} 字 <span className="hidden md:inline">/ 全文 {totalWordCount} 字</span></span>
          <span className={saveStatusColor}>{saveStatusLabel}</span>
        </div>
        <span className="text-gray-400 hidden md:inline">Cmd+S 保存</span>
      </div>
    </div>
  );
}
