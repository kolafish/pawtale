import { useRef, useState, type KeyboardEvent, type SyntheticEvent } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Bold,
  Columns2,
  Heading1,
  Heading2,
  Italic,
  List,
  ListOrdered,
  Minus,
  Pencil,
  Quote,
  RefreshCw,
  Save,
  Wand2,
  Eye,
} from 'lucide-react';
import { DiffReviewView } from './DiffReviewView';
import { MultiModelTabBar } from './MultiModelTabBar';
import type { DiffChunk, SelectionRange, MultiReviseResultItem } from '../types';
import type { SaveStatus } from '../hooks/useProjectState';
import {
  applyMarkdownFormat,
  applyMarkdownIndent,
  continueMarkdownLine,
  getMarkdownKeyboardCommand,
  type MarkdownEditResult,
  type MarkdownFormat,
} from '../utils/markdownEditor';

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

type EditorViewMode = 'edit' | 'preview' | 'split';

interface MarkdownTool {
  format: MarkdownFormat;
  title: string;
  icon: typeof Bold;
}

const MARKDOWN_TOOLS: MarkdownTool[] = [
  { format: 'heading-1', title: '一级标题 Cmd/Ctrl+Alt+1', icon: Heading1 },
  { format: 'heading-2', title: '二级标题 Cmd/Ctrl+Alt+2', icon: Heading2 },
  { format: 'bold', title: '加粗 Cmd/Ctrl+B', icon: Bold },
  { format: 'italic', title: '斜体 Cmd/Ctrl+I', icon: Italic },
  { format: 'quote', title: '引用 Cmd/Ctrl+Shift+>', icon: Quote },
  { format: 'unordered-list', title: '无序列表 Cmd/Ctrl+Shift+8', icon: List },
  { format: 'ordered-list', title: '有序列表 Cmd/Ctrl+Shift+7', icon: ListOrdered },
  { format: 'divider', title: '分隔线', icon: Minus },
];

const VIEW_MODES: { value: EditorViewMode; label: string; icon: typeof Pencil }[] = [
  { value: 'edit', label: '编辑', icon: Pencil },
  { value: 'preview', label: '预览', icon: Eye },
  { value: 'split', label: '分屏', icon: Columns2 },
];

function MarkdownPreview({ content }: { content: string }) {
  if (!content.trim()) {
    return (
      <div className="flex h-full items-center justify-center rounded-xl border border-dashed border-gray-200 bg-white text-sm text-gray-400">
        暂无可预览内容
      </div>
    );
  }

  return (
    <div className="markdown-preview h-full overflow-y-auto rounded-xl border border-pink-100 bg-white px-5 py-5 leading-7">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {content}
      </ReactMarkdown>
    </div>
  );
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
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [viewMode, setViewMode] = useState<EditorViewMode>('edit');
  const saveStatusLabel = saveStatus === 'saved'
    ? '已保存'
    : saveStatus === 'saving'
      ? '保存中...'
      : saveStatus === 'conflict'
        ? '内容已在其他位置更新'
        : '未保存';
  const saveStatusColor = saveStatus === 'saved'
    ? 'text-green-500'
    : saveStatus === 'conflict'
      ? 'text-red-500'
      : saveStatus === 'saving'
        ? 'text-yellow-500'
        : 'text-gray-400';

  const handleSelect = (e: SyntheticEvent<HTMLTextAreaElement>) => {
    const target = e.currentTarget;
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

  const applyEditResult = (result: MarkdownEditResult) => {
    setEditorContent(result.text);
    window.requestAnimationFrame(() => {
      if (!textareaRef.current) return;
      textareaRef.current.focus();
      textareaRef.current.setSelectionRange(result.start, result.end);
      if (result.start !== result.end) {
        onSelectionChange({
          start: result.start,
          end: result.end,
          text: result.text.substring(result.start, result.end),
        });
      }
    });
  };

  const currentTextareaSelection = () => {
    const target = textareaRef.current;
    return target
      ? { start: target.selectionStart, end: target.selectionEnd }
      : { start: editorContent.length, end: editorContent.length };
  };

  const applyFormat = (format: MarkdownFormat) => {
    if (viewMode === 'preview') return;
    applyEditResult(applyMarkdownFormat(editorContent, currentTextareaSelection(), format));
  };

  const handleEditorKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const command = getMarkdownKeyboardCommand({
      key: event.key,
      code: event.code,
      metaKey: event.metaKey,
      ctrlKey: event.ctrlKey,
      altKey: event.altKey,
      shiftKey: event.shiftKey,
      isComposing: event.nativeEvent.isComposing,
    });
    if (!command) return;

    const selection = {
      start: event.currentTarget.selectionStart,
      end: event.currentTarget.selectionEnd,
    };
    if (command.type === 'format') {
      event.preventDefault();
      applyEditResult(applyMarkdownFormat(editorContent, selection, command.format));
      return;
    }
    if (command.type === 'indent') {
      event.preventDefault();
      applyEditResult(applyMarkdownIndent(editorContent, selection, command.direction));
      return;
    }

    const result = continueMarkdownLine(editorContent, selection);
    if (!result) return;
    event.preventDefault();
    applyEditResult(result);
  };

  const renderTextarea = () => (
    <textarea
      ref={textareaRef}
      value={editorContent}
      onChange={(e) => setEditorContent(e.target.value)}
      onKeyDown={handleEditorKeyDown}
      onSelect={handleSelect}
      onMouseUp={handleSelect}
      placeholder="开始写作..."
      className="min-h-0 flex-1 w-full px-4 py-4 rounded-xl border border-pink-100 focus:outline-none focus:ring-2 focus:ring-pink-500 leading-7 resize-none bg-white"
    />
  );

  const switchViewMode = (mode: EditorViewMode) => {
    setViewMode(mode);
    if (mode === 'preview') {
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
          <div className="flex min-h-0 flex-1 flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2 rounded-xl border border-pink-100 bg-white px-2 py-2">
              <div className="flex items-center gap-1">
                {MARKDOWN_TOOLS.map((tool) => {
                  const Icon = tool.icon;
                  return (
                    <button
                      key={tool.format}
                      type="button"
                      onClick={() => applyFormat(tool.format)}
                      disabled={viewMode === 'preview'}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-600 hover:bg-pink-50 hover:text-pink-600 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-gray-600"
                      title={tool.title}
                    >
                      <Icon size={16} />
                    </button>
                  );
                })}
              </div>
              <div className="ml-auto flex rounded-lg bg-gray-100 p-1">
                {VIEW_MODES.map((mode) => {
                  const Icon = mode.icon;
                  return (
                    <button
                      key={mode.value}
                      type="button"
                      onClick={() => switchViewMode(mode.value)}
                      className={`inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs font-medium transition-colors ${
                        viewMode === mode.value
                          ? 'bg-white text-pink-600 shadow-sm'
                          : 'text-gray-500 hover:text-gray-700'
                      }`}
                      title={mode.label}
                    >
                      <Icon size={14} />
                      <span>{mode.label}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            {viewMode === 'edit' && renderTextarea()}
            {viewMode === 'preview' && (
              <MarkdownPreview content={editorContent} />
            )}
            {viewMode === 'split' && (
              <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-2">
                {renderTextarea()}
                <MarkdownPreview content={editorContent} />
              </div>
            )}
          </div>
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
