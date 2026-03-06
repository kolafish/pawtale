import { Bot, Bug, RefreshCw, Save, Sparkles } from 'lucide-react';
import { AI_MODEL_OPTIONS } from '../constants/aiModels';
import { AIDebugPanel } from './AIDebugPanel';
import type { AIDebugInfo } from '../types';
import type { SaveStatus } from '../hooks/useWorkspaceState';

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
  isSaving: boolean;
  isContinuing: boolean;
  isExtracting: boolean;
  onSave: () => void;
  onContinue: () => void;
  onExtract: () => void;
  onSwitchWorkspace: () => void;
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
  isSaving,
  isContinuing,
  isExtracting,
  onSave,
  onContinue,
  onExtract,
  onSwitchWorkspace,
}: EditorMainProps) {
  const saveStatusLabel = saveStatus === 'saved' ? '已保存' : saveStatus === 'saving' ? '保存中...' : '未保存';
  const saveStatusColor = saveStatus === 'saved' ? 'text-green-500' : saveStatus === 'saving' ? 'text-yellow-500' : 'text-gray-400';

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
          <span>{wordCount} 字</span>
          <span className={saveStatusColor}>{saveStatusLabel}</span>
        </div>
        <span className="text-gray-400">Cmd+S 保存</span>
      </div>
    </div>
  );
}
