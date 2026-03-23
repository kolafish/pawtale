import { Bot, Bug, Columns, RefreshCw, Wand2, X } from 'lucide-react';
import { AI_MODEL_OPTIONS } from '../constants/aiModels';
import { AIDebugPanel } from './AIDebugPanel';
import type { AIDebugInfo, SelectionRange } from '../types';

interface AIAssistPanelProps {
  // Panel
  onClose: () => void;
  // Revision
  revisionInstruction: string;
  setRevisionInstruction: (v: string) => void;
  revisionScope: 'full' | 'selection';
  setRevisionScope: (v: 'full' | 'selection') => void;
  selection: SelectionRange | null;
  isRevising: boolean;
  isMultiRevising: boolean;
  isReviewing: boolean;
  onRequestRevision: () => void;
  onRequestMultiRevision: () => void;
  // Continue
  continueInstruction: string;
  setContinueInstruction: (v: string) => void;
  isContinuing: boolean;
  onContinue: () => void;
  hasContent: boolean;
  // Model & debug
  aiModel: string;
  setAiModel: (v: string) => void;
  debugMode: boolean;
  setDebugMode: (v: boolean) => void;
  debugInfo: AIDebugInfo | null;
  revisionDebugInfo: AIDebugInfo | null;
}

export function AIAssistPanel({
  onClose,
  revisionInstruction,
  setRevisionInstruction,
  revisionScope,
  setRevisionScope,
  selection,
  isRevising,
  isMultiRevising,
  isReviewing,
  onRequestRevision,
  onRequestMultiRevision,
  continueInstruction,
  setContinueInstruction,
  isContinuing,
  onContinue,
  hasContent,
  aiModel,
  setAiModel,
  debugMode,
  setDebugMode,
  debugInfo,
  revisionDebugInfo,
}: AIAssistPanelProps) {
  const activeDebug = revisionDebugInfo || debugInfo;

  return (
    <div className="w-[360px] flex-shrink-0 h-full bg-white border-l border-gray-200 flex flex-col">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-2">
          <Wand2 size={16} className="text-pink-500" />
          AI 辅助
        </h3>
        <button onClick={onClose} className="p-1 rounded hover:bg-gray-100 text-gray-400">
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* AI Revision Section */}
        <div className="p-4 border-b border-gray-100">
          <h4 className="text-xs font-medium text-gray-500 mb-2">AI 修改</h4>

          {/* Scope toggle */}
          <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-1 mb-3">
            <button
              onClick={() => setRevisionScope('full')}
              className={`flex-1 text-xs py-1.5 rounded-md font-medium transition-colors ${
                revisionScope === 'full' ? 'bg-white shadow-sm text-pink-600' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              整章修改
            </button>
            <button
              onClick={() => setRevisionScope('selection')}
              disabled={!selection}
              className={`flex-1 text-xs py-1.5 rounded-md font-medium transition-colors ${
                revisionScope === 'selection' ? 'bg-white shadow-sm text-pink-600' : 'text-gray-500 hover:text-gray-700'
              } disabled:opacity-40`}
            >
              选段修改
            </button>
          </div>

          {/* Selection preview */}
          {revisionScope === 'selection' && selection && (
            <div className="mb-3 p-2 bg-gray-50 rounded-lg border border-gray-200 max-h-[120px] overflow-y-auto">
              <p className="text-xs text-gray-400 mb-1">选中文本：</p>
              <p className="text-xs text-gray-600 whitespace-pre-wrap">{selection.text}</p>
            </div>
          )}

          {revisionScope === 'selection' && !selection && (
            <div className="mb-3 p-2 bg-gray-50 rounded-lg border border-dashed border-gray-300">
              <p className="text-xs text-gray-400 text-center">在编辑器中选中文本</p>
            </div>
          )}

          {/* Instruction */}
          <textarea
            value={revisionInstruction}
            onChange={(e) => setRevisionInstruction(e.target.value)}
            placeholder="输入修改意见，如：让对话更自然、增加环境描写、加强冲突感..."
            rows={3}
            className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-500 resize-none mb-3"
          />

          {/* Revise buttons */}
          <div className="flex gap-2">
            <button
              onClick={onRequestRevision}
              disabled={isRevising || isMultiRevising || isReviewing || !revisionInstruction.trim() || (revisionScope === 'selection' && !selection)}
              className="flex-1 inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-pink-500 text-white text-sm hover:bg-pink-600 disabled:opacity-60 transition-colors"
            >
              {isRevising ? <RefreshCw size={14} className="animate-spin" /> : <Wand2 size={14} />}
              {isRevising ? '修改中...' : '单模型修改'}
            </button>
            <button
              onClick={onRequestMultiRevision}
              disabled={isRevising || isMultiRevising || isReviewing || !revisionInstruction.trim() || (revisionScope === 'selection' && !selection)}
              className="flex-1 inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-pink-300 text-pink-600 text-sm hover:bg-pink-50 disabled:opacity-60 transition-colors"
            >
              {isMultiRevising ? <RefreshCw size={14} className="animate-spin" /> : <Columns size={14} />}
              {isMultiRevising ? '修改中...' : '多模型对比'}
            </button>
          </div>
        </div>

        {/* AI Continue Section */}
        <div className="p-4 border-b border-gray-100">
          <h4 className="text-xs font-medium text-gray-500 mb-2">AI 续写</h4>
          <input
            value={continueInstruction}
            onChange={(e) => setContinueInstruction(e.target.value)}
            placeholder="可选：告诉 AI 接下来想怎么发展"
            className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-500 mb-3"
          />
          <button
            onClick={onContinue}
            disabled={isContinuing || !hasContent || isReviewing}
            className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg border border-gray-200 text-sm hover:bg-gray-50 disabled:opacity-60 transition-colors"
          >
            {isContinuing ? <RefreshCw size={14} className="animate-spin" /> : <Bot size={14} />}
            {isContinuing ? '续写中...' : 'AI 续写'}
          </button>
        </div>

        {/* Model & Debug */}
        <div className="p-4">
          <h4 className="text-xs font-medium text-gray-500 mb-2">设置</h4>
          <div className="space-y-2">
            <select
              value={aiModel}
              onChange={(e) => setAiModel(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-2 focus:ring-pink-500"
            >
              {AI_MODEL_OPTIONS.map((item) => (
                <option key={item.value} value={item.value}>{item.label}</option>
              ))}
            </select>
            <label className="inline-flex items-center gap-1.5 text-xs text-gray-500">
              <input
                type="checkbox"
                checked={debugMode}
                onChange={(e) => setDebugMode(e.target.checked)}
                className="rounded"
              />
              <Bug size={12} />
              Debug 模式
            </label>
          </div>

          {activeDebug && debugMode && (
            <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50 p-2 max-h-60 overflow-auto">
              <AIDebugPanel debugInfo={activeDebug} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
