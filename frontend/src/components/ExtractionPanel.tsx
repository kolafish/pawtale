import { Plus, RefreshCw, Save, Trash2, ArrowDownToLine } from 'lucide-react';
import { splitTokens } from '../hooks/useWorkspaceState';
import type { ExtractedCharacter, ExtractedScene, ExtractedOutlinePoint } from '../types';

interface ExtractionPanelProps {
  extractedCharacters: ExtractedCharacter[];
  setExtractedCharacters: (v: ExtractedCharacter[]) => void;
  extractedScenes: ExtractedScene[];
  setExtractedScenes: (v: ExtractedScene[]) => void;
  extractedOutlinePoints: ExtractedOutlinePoint[];
  setExtractedOutlinePoints: (v: ExtractedOutlinePoint[]) => void;
  extractionNotesText: string;
  setExtractionNotesText: (v: string) => void;
  isSavingExtraction: boolean;
  onSaveExtraction: () => void;
  onSaveCharacterToLibrary: (char: ExtractedCharacter) => Promise<void>;
  onSaveSceneToLibrary: (scene: ExtractedScene) => Promise<void>;
  emptyCharacter: () => ExtractedCharacter;
  emptyScene: () => ExtractedScene;
  emptyOutlinePoint: () => ExtractedOutlinePoint;
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
          <div key={`char-${index}`} className="rounded-lg border border-gray-100 p-2 space-y-1">
            <div className="flex gap-1">
              <input
                value={item.name}
                onChange={(e) => {
                  const next = [...extractedCharacters];
                  next[index] = { ...next[index], name: e.target.value };
                  setExtractedCharacters(next);
                }}
                placeholder="角色名"
                className="flex-1 px-2 py-1 text-sm rounded border border-gray-200"
              />
              <button
                onClick={() => onSaveCharacterToLibrary(item)}
                disabled={!item.name.trim()}
                className="p-1 text-gray-400 hover:text-green-600 disabled:opacity-40"
                title="存入角色库"
              >
                <ArrowDownToLine size={14} />
              </button>
              <button
                onClick={() => setExtractedCharacters(extractedCharacters.filter((_, i) => i !== index))}
                className="p-1 text-gray-400 hover:text-red-500"
              >
                <Trash2 size={14} />
              </button>
            </div>
            <input
              value={item.role_hint || ''}
              onChange={(e) => {
                const next = [...extractedCharacters];
                next[index] = { ...next[index], role_hint: e.target.value };
                setExtractedCharacters(next);
              }}
              placeholder="角色定位"
              className="w-full px-2 py-1 text-sm rounded border border-gray-200"
            />
            <input
              value={(item.traits || []).join('，')}
              onChange={(e) => {
                const next = [...extractedCharacters];
                next[index] = { ...next[index], traits: splitTokens(e.target.value) };
                setExtractedCharacters(next);
              }}
              placeholder="特征（逗号分隔）"
              className="w-full px-2 py-1 text-sm rounded border border-gray-200"
            />
            <textarea
              value={item.summary || ''}
              onChange={(e) => {
                const next = [...extractedCharacters];
                next[index] = { ...next[index], summary: e.target.value };
                setExtractedCharacters(next);
              }}
              placeholder="备注"
              rows={2}
              className="w-full px-2 py-1 text-sm rounded border border-gray-200"
            />
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
          <div key={`scene-${index}`} className="rounded-lg border border-gray-100 p-2 space-y-1">
            <div className="flex gap-1">
              <input
                value={item.name}
                onChange={(e) => {
                  const next = [...extractedScenes];
                  next[index] = { ...next[index], name: e.target.value };
                  setExtractedScenes(next);
                }}
                placeholder="场景名"
                className="flex-1 px-2 py-1 text-sm rounded border border-gray-200"
              />
              <button
                onClick={() => onSaveSceneToLibrary(item)}
                disabled={!item.name.trim()}
                className="p-1 text-gray-400 hover:text-green-600 disabled:opacity-40"
                title="存入场景库"
              >
                <ArrowDownToLine size={14} />
              </button>
              <button
                onClick={() => setExtractedScenes(extractedScenes.filter((_, i) => i !== index))}
                className="p-1 text-gray-400 hover:text-red-500"
              >
                <Trash2 size={14} />
              </button>
            </div>
            <input
              value={item.description || ''}
              onChange={(e) => {
                const next = [...extractedScenes];
                next[index] = { ...next[index], description: e.target.value };
                setExtractedScenes(next);
              }}
              placeholder="场景描述"
              className="w-full px-2 py-1 text-sm rounded border border-gray-200"
            />
            <input
              value={(item.keywords || []).join('，')}
              onChange={(e) => {
                const next = [...extractedScenes];
                next[index] = { ...next[index], keywords: splitTokens(e.target.value) };
                setExtractedScenes(next);
              }}
              placeholder="关键词（逗号分隔）"
              className="w-full px-2 py-1 text-sm rounded border border-gray-200"
            />
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
          <div key={`outline-${index}`} className="rounded-lg border border-gray-100 p-2 space-y-1">
            <div className="flex gap-1">
              <input
                value={item.stage}
                onChange={(e) => {
                  const next = [...extractedOutlinePoints];
                  next[index] = { ...next[index], stage: e.target.value };
                  setExtractedOutlinePoints(next);
                }}
                placeholder="阶段"
                className="w-28 px-2 py-1 text-sm rounded border border-gray-200"
              />
              <button
                onClick={() => setExtractedOutlinePoints(extractedOutlinePoints.filter((_, i) => i !== index))}
                className="p-1 text-gray-400 hover:text-red-500"
              >
                <Trash2 size={14} />
              </button>
            </div>
            <textarea
              value={item.summary}
              onChange={(e) => {
                const next = [...extractedOutlinePoints];
                next[index] = { ...next[index], summary: e.target.value };
                setExtractedOutlinePoints(next);
              }}
              placeholder="该阶段情节"
              rows={2}
              className="w-full px-2 py-1 text-sm rounded border border-gray-200"
            />
          </div>
        ))}
      </section>

      {/* Consistency Notes */}
      <section className="space-y-2">
        <h4 className="text-sm font-medium text-gray-700">一致性提醒</h4>
        <textarea
          value={extractionNotesText}
          onChange={(e) => setExtractionNotesText(e.target.value)}
          placeholder="每行一条提醒"
          rows={5}
          className="w-full px-2 py-1 text-sm rounded border border-gray-200"
        />
      </section>
    </div>
  );
}
