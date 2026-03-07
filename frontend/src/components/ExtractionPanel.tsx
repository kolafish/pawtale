import { useCallback } from 'react';
import { Plus, RefreshCw, Save, Trash2, BookmarkPlus } from 'lucide-react';
import { splitTokens } from '../hooks/useWorkspaceState';
import { AIDebugPanel } from './AIDebugPanel';
import type { AIDebugInfo, ExtractedCharacter, ExtractedScene, ExtractedOutlinePoint } from '../types';

interface ExtractionPanelProps {
  extractedCharacters: ExtractedCharacter[];
  setExtractedCharacters: (v: ExtractedCharacter[]) => void;
  extractedScenes: ExtractedScene[];
  setExtractedScenes: (v: ExtractedScene[]) => void;
  extractedOutlinePoints: ExtractedOutlinePoint[];
  setExtractedOutlinePoints: (v: ExtractedOutlinePoint[]) => void;
  extractionNotesText: string;
  setExtractionNotesText: (v: string) => void;
  debugMode: boolean;
  extractDebugInfo: AIDebugInfo | null;
  isSavingExtraction: boolean;
  onSaveExtraction: () => void;
  onSaveCharacterToLibrary: (char: ExtractedCharacter) => Promise<void>;
  onSaveSceneToLibrary: (scene: ExtractedScene) => Promise<void>;
  emptyCharacter: () => ExtractedCharacter;
  emptyScene: () => ExtractedScene;
  emptyOutlinePoint: () => ExtractedOutlinePoint;
}

function AutoTextarea({
  value,
  onChange,
  placeholder,
  className = '',
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  className?: string;
}) {
  const handleInput = useCallback((el: HTMLTextAreaElement) => {
    el.style.height = 'auto';
    el.style.height = el.scrollHeight + 'px';
  }, []);

  return (
    <textarea
      value={value}
      onChange={(e) => {
        onChange(e.target.value);
        handleInput(e.target);
      }}
      onFocus={(e) => handleInput(e.target)}
      placeholder={placeholder}
      rows={1}
      className={`w-full px-2 py-1.5 text-sm rounded-lg border border-gray-200 focus:outline-none focus:ring-1 focus:ring-pink-300 overflow-hidden resize-none ${className}`}
    />
  );
}

export function ExtractionPanel({
  extractedCharacters,
  setExtractedCharacters,
  extractedScenes,
  setExtractedScenes,
  extractedOutlinePoints,
  setExtractedOutlinePoints,
  extractionNotesText,
  setExtractionNotesText,
  debugMode,
  extractDebugInfo,
  isSavingExtraction,
  onSaveExtraction,
  onSaveCharacterToLibrary,
  onSaveSceneToLibrary,
  emptyCharacter,
  emptyScene,
  emptyOutlinePoint,
}: ExtractionPanelProps) {
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-gray-800">故事提炼（当前作品）</h3>
        <button
          onClick={onSaveExtraction}
          disabled={isSavingExtraction}
          className="inline-flex items-center gap-1 text-sm px-2 py-1 rounded border border-gray-200 hover:bg-gray-50"
        >
          {isSavingExtraction ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
          保存修改
        </button>
      </div>

      {debugMode && (
        extractDebugInfo ? (
          <AIDebugPanel debugInfo={extractDebugInfo} title="提炼 Debug" />
        ) : (
          <div className="text-xs text-amber-700 border border-amber-200 bg-amber-50 rounded-lg px-3 py-2">
            已开启 Debug。本次提炼尚未返回调试信息，请重新点击一次"自动提炼"。
          </div>
        )
      )}

      {/* Characters */}
      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-medium text-gray-700">角色</h4>
          <button
            onClick={() => setExtractedCharacters([...extractedCharacters, emptyCharacter()])}
            className="text-xs inline-flex items-center gap-1 px-2 py-1 rounded border border-gray-200 hover:bg-gray-50"
          >
            <Plus size={12} /> 添加
          </button>
        </div>
        {extractedCharacters.map((item, index) => (
          <div key={`char-${index}`} className="bg-gray-50 rounded-xl p-3 space-y-2 relative">
            <button
              onClick={() => setExtractedCharacters(extractedCharacters.filter((_, i) => i !== index))}
              className="absolute top-2 right-2 p-1 text-gray-400 hover:text-red-500 rounded-full hover:bg-red-50"
            >
              <Trash2 size={14} />
            </button>
            <div>
              <label className="text-xs font-bold text-gray-600 mb-0.5 block">角色名</label>
              <AutoTextarea
                value={item.name}
                onChange={(v) => {
                  const next = [...extractedCharacters];
                  next[index] = { ...next[index], name: v };
                  setExtractedCharacters(next);
                }}
                placeholder="角色名"
              />
            </div>
            <div>
              <label className="text-xs font-bold text-gray-600 mb-0.5 block">定位</label>
              <AutoTextarea
                value={item.role_hint || ''}
                onChange={(v) => {
                  const next = [...extractedCharacters];
                  next[index] = { ...next[index], role_hint: v };
                  setExtractedCharacters(next);
                }}
                placeholder="角色定位"
              />
            </div>
            <div>
              <label className="text-xs font-bold text-gray-600 mb-0.5 block">性格特征</label>
              <AutoTextarea
                value={(item.traits || []).join('，')}
                onChange={(v) => {
                  const next = [...extractedCharacters];
                  next[index] = { ...next[index], traits: splitTokens(v) };
                  setExtractedCharacters(next);
                }}
                placeholder="特征（逗号分隔）"
              />
            </div>
            <div>
              <label className="text-xs font-bold text-gray-600 mb-0.5 block">备注</label>
              <AutoTextarea
                value={item.summary || ''}
                onChange={(v) => {
                  const next = [...extractedCharacters];
                  next[index] = { ...next[index], summary: v };
                  setExtractedCharacters(next);
                }}
                placeholder="备注"
              />
            </div>
            <button
              onClick={() => onSaveCharacterToLibrary(item)}
              disabled={!item.name.trim()}
              className="inline-flex items-center gap-1.5 text-sm text-green-700 hover:text-green-800 disabled:opacity-40 disabled:cursor-not-allowed font-medium pt-1"
            >
              <BookmarkPlus size={14} />
              存入角色库
            </button>
          </div>
        ))}
      </section>

      {/* Scenes */}
      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-medium text-gray-700">场景</h4>
          <button
            onClick={() => setExtractedScenes([...extractedScenes, emptyScene()])}
            className="text-xs inline-flex items-center gap-1 px-2 py-1 rounded border border-gray-200 hover:bg-gray-50"
          >
            <Plus size={12} /> 添加
          </button>
        </div>
        {extractedScenes.map((item, index) => (
          <div key={`scene-${index}`} className="bg-gray-50 rounded-xl p-3 space-y-2 relative">
            <button
              onClick={() => setExtractedScenes(extractedScenes.filter((_, i) => i !== index))}
              className="absolute top-2 right-2 p-1 text-gray-400 hover:text-red-500 rounded-full hover:bg-red-50"
            >
              <Trash2 size={14} />
            </button>
            <div>
              <label className="text-xs font-bold text-gray-600 mb-0.5 block">场景名</label>
              <AutoTextarea
                value={item.name}
                onChange={(v) => {
                  const next = [...extractedScenes];
                  next[index] = { ...next[index], name: v };
                  setExtractedScenes(next);
                }}
                placeholder="场景名"
              />
            </div>
            <div>
              <label className="text-xs font-bold text-gray-600 mb-0.5 block">描述</label>
              <AutoTextarea
                value={item.description || ''}
                onChange={(v) => {
                  const next = [...extractedScenes];
                  next[index] = { ...next[index], description: v };
                  setExtractedScenes(next);
                }}
                placeholder="场景描述"
              />
            </div>
            <div>
              <label className="text-xs font-bold text-gray-600 mb-0.5 block">关键词</label>
              <AutoTextarea
                value={(item.keywords || []).join('，')}
                onChange={(v) => {
                  const next = [...extractedScenes];
                  next[index] = { ...next[index], keywords: splitTokens(v) };
                  setExtractedScenes(next);
                }}
                placeholder="关键词（逗号分隔）"
              />
            </div>
            <button
              onClick={() => onSaveSceneToLibrary(item)}
              disabled={!item.name.trim()}
              className="inline-flex items-center gap-1.5 text-sm text-green-700 hover:text-green-800 disabled:opacity-40 disabled:cursor-not-allowed font-medium pt-1"
            >
              <BookmarkPlus size={14} />
              存入场景库
            </button>
          </div>
        ))}
      </section>

      {/* Outline Points */}
      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-medium text-gray-700">情节大纲</h4>
          <button
            onClick={() => setExtractedOutlinePoints([...extractedOutlinePoints, emptyOutlinePoint()])}
            className="text-xs inline-flex items-center gap-1 px-2 py-1 rounded border border-gray-200 hover:bg-gray-50"
          >
            <Plus size={12} /> 添加
          </button>
        </div>
        {extractedOutlinePoints.map((item, index) => (
          <div key={`outline-${index}`} className="bg-gray-50 rounded-xl p-3 space-y-2 relative">
            <button
              onClick={() => setExtractedOutlinePoints(extractedOutlinePoints.filter((_, i) => i !== index))}
              className="absolute top-2 right-2 p-1 text-gray-400 hover:text-red-500 rounded-full hover:bg-red-50"
            >
              <Trash2 size={14} />
            </button>
            <div>
              <label className="text-xs font-bold text-gray-600 mb-0.5 block">阶段</label>
              <AutoTextarea
                value={item.stage}
                onChange={(v) => {
                  const next = [...extractedOutlinePoints];
                  next[index] = { ...next[index], stage: v };
                  setExtractedOutlinePoints(next);
                }}
                placeholder="阶段"
              />
            </div>
            <div>
              <label className="text-xs font-bold text-gray-600 mb-0.5 block">情节概要</label>
              <AutoTextarea
                value={item.summary}
                onChange={(v) => {
                  const next = [...extractedOutlinePoints];
                  next[index] = { ...next[index], summary: v };
                  setExtractedOutlinePoints(next);
                }}
                placeholder="该阶段情节"
              />
            </div>
          </div>
        ))}
      </section>

      {/* Consistency Notes */}
      <section className="space-y-2">
        <h4 className="text-sm font-medium text-gray-700">一致性提醒</h4>
        <AutoTextarea
          value={extractionNotesText}
          onChange={setExtractionNotesText}
          placeholder="每行一条提醒"
        />
      </section>
    </div>
  );
}
