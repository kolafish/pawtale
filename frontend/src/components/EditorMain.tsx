import { useCallback, useRef, useState } from 'react';
import { Bot, Bug, Plus, RefreshCw, Save, Sparkles, X } from 'lucide-react';
import { AI_MODEL_OPTIONS } from '../constants/aiModels';
import { AIDebugPanel } from './AIDebugPanel';
import type { AIDebugInfo, WorkspaceChapter } from '../types';
import type { SaveStatus } from '../hooks/useWorkspaceState';

function countWords(text: string): number {
  if (!text) return 0;
  const chineseChars = (text.match(/[\u4e00-\u9fff\u3400-\u4dbf]/g) || []).length;
  const stripped = text.replace(/[\u4e00-\u9fff\u3400-\u4dbf]/g, ' ');
  const englishWords = stripped.split(/\s+/).filter((w) => w.length > 0).length;
  return chineseChars + englishWords;
}

interface EditorMainProps {
  title: string;
  setTitle: (v: string) => void;
  editorContent: string;
  setEditorContent: (v: string) => void;
  aiModel: string;
  setAiModel: (v: string) => void;
  continueInstruction: string;
  setContinueInstruction: (v: string) => void;
  debugMode: boolean;
  setDebugMode: (v: boolean) => void;
  debugInfo: AIDebugInfo | null;
  saveStatus: SaveStatus;
  wordCount: number;
  totalWordCount: number;
  isSaving: boolean;
  isContinuing: boolean;
  isExtracting: boolean;
  onSave: () => void;
  onContinue: () => void;
  onExtract: () => void;
  onSwitchWorkspace: () => void;
  // Chapter props
  chapters: WorkspaceChapter[];
  currentChapterId: string;
  onAddChapter: () => void;
  onDeleteChapter: (id: string) => void;
  onRenameChapter: (id: string, title: string) => void;
  onSwitchChapter: (id: string) => void;
}

export function EditorMain({
  title,
  setTitle,
  editorContent,
  setEditorContent,
  aiModel,
  setAiModel,
  continueInstruction,
  setContinueInstruction,
  debugMode,
  setDebugMode,
  debugInfo,
  saveStatus,
  wordCount,
  totalWordCount,
  isSaving,
  isContinuing,
  isExtracting,
  onSave,
  onContinue,
  onExtract,
  onSwitchWorkspace,
  chapters,
  currentChapterId,
  onAddChapter,
  onDeleteChapter,
  onRenameChapter,
  onSwitchChapter,
}: EditorMainProps) {
  const saveStatusLabel = saveStatus === 'saved' ? '已保存' : saveStatus === 'saving' ? '保存中...' : '未保存';
  const saveStatusColor = saveStatus === 'saved' ? 'text-green-500' : saveStatus === 'saving' ? 'text-yellow-500' : 'text-gray-400';

  // Inline rename state
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const renameInputRef = useRef<HTMLInputElement>(null);

  const startRename = useCallback((ch: WorkspaceChapter) => {
    setRenamingId(ch.id);
    setRenameValue(ch.title);
    setTimeout(() => renameInputRef.current?.select(), 0);
  }, []);

  const commitRename = useCallback(() => {
    if (renamingId && renameValue.trim()) {
      onRenameChapter(renamingId, renameValue.trim());
    }
    setRenamingId(null);
  }, [renamingId, renameValue, onRenameChapter]);

  return (
    <div className="h-full flex flex-col">
      {/* Top bar */}
      <div className="px-4 py-3 border-b border-pink-100 bg-white flex items-center gap-3">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="flex-1 px-3 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-500"
        />
        <select
          value={aiModel}
          onChange={(e) => setAiModel(e.target.value)}
          className="px-2 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-500"
        >
          {AI_MODEL_OPTIONS.map((item) => (
            <option key={item.value} value={item.value}>{item.label}</option>
          ))}
        </select>
        <button
          onClick={onSave}
          disabled={isSaving}
          className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-gray-200 hover:bg-gray-50"
        >
          {isSaving ? <RefreshCw size={16} className="animate-spin" /> : <Save size={16} />}
          保存
        </button>
        <button
          onClick={onSwitchWorkspace}
          className="text-sm px-3 py-2 rounded-lg border border-gray-200 hover:bg-gray-50"
        >
          新建/切换
        </button>
      </div>

      {/* Tool row */}
      <div className="px-4 py-2 border-b border-pink-50 bg-white flex items-center gap-2">
        <input
          value={continueInstruction}
          onChange={(e) => setContinueInstruction(e.target.value)}
          placeholder="可选：告诉 AI 接下来想怎么发展"
          className="flex-1 px-3 py-2 rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-500"
        />
        <button
          onClick={onContinue}
          disabled={isContinuing || !editorContent.trim()}
          className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-pink-500 text-white hover:bg-pink-600 disabled:opacity-60"
        >
          {isContinuing ? <RefreshCw size={16} className="animate-spin" /> : <Bot size={16} />}
          AI 续写
        </button>
        <button
          onClick={onExtract}
          disabled={isExtracting || !editorContent.trim()}
          className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-gray-200 hover:bg-gray-50 disabled:opacity-60"
        >
          {isExtracting ? <RefreshCw size={16} className="animate-spin" /> : <Sparkles size={16} />}
          自动提炼
        </button>
        <label className="inline-flex items-center gap-1 text-xs text-gray-500 pl-1">
          <input
            type="checkbox"
            checked={debugMode}
            onChange={(e) => setDebugMode(e.target.checked)}
            className="rounded"
          />
          <Bug size={14} /> Debug
        </label>
      </div>

      {/* Chapter navigation bar */}
      <div className="px-4 py-1.5 border-b border-pink-50 bg-gray-50/50 flex items-center gap-1 overflow-x-auto scrollbar-thin">
        {chapters.map((ch) => {
          const isActive = ch.id === currentChapterId;
          const chWordCount = countWords(ch.content);
          return (
            <div
              key={ch.id}
              className={`group relative flex items-center gap-1 px-3 py-1.5 rounded-lg text-sm cursor-pointer whitespace-nowrap transition-colors ${
                isActive
                  ? 'bg-white border-2 border-pink-400 text-pink-700 font-medium shadow-sm'
                  : 'bg-white border border-gray-200 text-gray-600 hover:border-pink-200 hover:text-gray-800'
              }`}
              onClick={() => onSwitchChapter(ch.id)}
              onDoubleClick={() => startRename(ch)}
            >
              {renamingId === ch.id ? (
                <input
                  ref={renameInputRef}
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onBlur={commitRename}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitRename();
                    if (e.key === 'Escape') setRenamingId(null);
                  }}
                  className="w-20 px-1 py-0 text-sm border-b border-pink-400 bg-transparent outline-none"
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <span>{ch.title}</span>
              )}
              <span className="text-xs text-gray-400 ml-0.5">{chWordCount}</span>
              {chapters.length > 1 && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteChapter(ch.id);
                  }}
                  className="hidden group-hover:flex items-center justify-center w-4 h-4 rounded-full hover:bg-red-100 text-gray-400 hover:text-red-500 ml-0.5"
                >
                  <X size={10} />
                </button>
              )}
            </div>
          );
        })}
        <button
          onClick={onAddChapter}
          className="flex items-center justify-center w-7 h-7 rounded-lg border border-dashed border-gray-300 text-gray-400 hover:border-pink-300 hover:text-pink-500 transition-colors flex-shrink-0"
          title="新建章节"
        >
          <Plus size={14} />
        </button>
      </div>

      {/* Editor area */}
      <div className="flex-1 min-h-0 p-4 flex flex-col gap-3">
        <textarea
          value={editorContent}
          onChange={(e) => setEditorContent(e.target.value)}
          placeholder="开始写作...你可以一直写，也可以随时点 AI 续写。"
          className="flex-1 w-full px-4 py-4 rounded-xl border border-pink-100 focus:outline-none focus:ring-2 focus:ring-pink-500 leading-7 resize-none"
        />

        {debugInfo && (
          <div className="rounded-xl border border-gray-200 bg-white p-3 max-h-72 overflow-auto">
            <AIDebugPanel debugInfo={debugInfo} />
          </div>
        )}
      </div>

      {/* Status bar */}
      <div className="px-4 py-2 border-t border-pink-100 bg-white flex items-center justify-between text-xs text-gray-500">
        <div className="flex items-center gap-4">
          <span>本章 {wordCount} 字 / 全文 {totalWordCount} 字</span>
          <span className={saveStatusColor}>{saveStatusLabel}</span>
        </div>
        <span className="text-gray-400">Cmd+S 保存</span>
      </div>
    </div>
  );
}
