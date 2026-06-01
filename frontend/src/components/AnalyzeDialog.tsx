import { useState } from 'react';
import { X, Loader2, User, Map, StickyNote, Check } from 'lucide-react';
import { projectsApi } from '../api';
import { REFERENCE_TYPE_LABELS } from '../domain/references';
import type {
  ProjectAnalyzeResponse,
  AnalyzedCharacter,
  AnalyzedOutline,
  AnalyzedSetting,
} from '../types';
import { useAppStore } from '../stores/appStore';
import { AI_MODEL_OPTIONS, type AiModelValue } from '../constants/aiModels';
import { getErrorMessage } from '../utils/apiError';

interface AnalyzeDialogProps {
  projectId: string;
  onClose: () => void;
  onComplete: () => void;
}

type Stage = 'idle' | 'analyzing' | 'preview' | 'applying' | 'done';

export function AnalyzeDialog({ projectId, onClose, onComplete }: AnalyzeDialogProps) {
  const { defaultAiModel } = useAppStore();
  const [stage, setStage] = useState<Stage>('idle');
  const [error, setError] = useState<string | null>(null);
  const [aiModel, setAiModel] = useState<AiModelValue>(defaultAiModel);

  // Analysis result
  const [result, setResult] = useState<ProjectAnalyzeResponse | null>(null);

  // Selection state
  const [selectedChars, setSelectedChars] = useState<Set<number>>(new Set());
  const [selectedOutlines, setSelectedOutlines] = useState<Set<number>>(new Set());
  const [selectedSettings, setSelectedSettings] = useState<Set<number>>(new Set());

  const startAnalysis = async () => {
    setStage('analyzing');
    setError(null);
    try {
      const data = await projectsApi.analyzeProject(projectId, { ai_model: aiModel });
      setResult(data);
      // Select all by default
      setSelectedChars(new Set(data.characters.map((_, i) => i)));
      setSelectedOutlines(new Set(data.outlines.map((_, i) => i)));
      setSelectedSettings(new Set(data.settings.map((_, i) => i)));
      setStage('preview');
    } catch (e: unknown) {
      setError(getErrorMessage(e, '项目分析失败'));
      setStage('idle');
    }
  };

  const toggleChar = (i: number) => {
    setSelectedChars((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i); else next.add(i);
      return next;
    });
  };

  const toggleOutline = (i: number) => {
    setSelectedOutlines((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i); else next.add(i);
      return next;
    });
  };

  const toggleSetting = (i: number) => {
    setSelectedSettings((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i); else next.add(i);
      return next;
    });
  };

  const totalSelected = selectedChars.size + selectedOutlines.size + selectedSettings.size;

  const handleApply = async () => {
    if (!result) return;
    setStage('applying');
    setError(null);

    const chars: AnalyzedCharacter[] = result.characters.filter((_, i) => selectedChars.has(i));
    const outlines: AnalyzedOutline[] = result.outlines.filter((_, i) => selectedOutlines.has(i));
    const settings: AnalyzedSetting[] = result.settings.filter((_, i) => selectedSettings.has(i));

    try {
      await projectsApi.applyAnalysis(projectId, { characters: chars, outlines, settings });
      setStage('done');
      onComplete();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      setStage('preview');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <h2 className="text-lg font-semibold text-gray-800">
            {stage === 'done' ? 'AI 分析完成' : 'AI 分析项目'}
          </h2>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-gray-100 text-gray-400">
            <X size={18} />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {/* Idle */}
          {stage === 'idle' && (
            <div className="text-center py-8 space-y-4">
              <p className="text-gray-600">
                AI 将分析项目中所有手稿内容，自动提取人物、大纲和设定信息。
              </p>
              <div className="flex items-center justify-center gap-2">
                <span className="text-sm text-gray-500">模型</span>
                <select
                  value={aiModel}
                  onChange={(e) => setAiModel(e.target.value as AiModelValue)}
                  className="text-sm border border-gray-200 rounded-lg px-2 py-1 text-gray-700"
                >
                  {AI_MODEL_OPTIONS.map((m) => (
                    <option key={m.value} value={m.value}>{m.label}</option>
                  ))}
                </select>
              </div>
              {error && (
                <p className="text-sm text-red-500 bg-red-50 rounded-lg px-3 py-2">{error}</p>
              )}
              <button
                onClick={startAnalysis}
                className="px-6 py-2.5 rounded-lg bg-gradient-to-r from-pink-500 to-purple-500 text-white font-medium hover:opacity-90 transition"
              >
                开始分析
              </button>
            </div>
          )}

          {/* Analyzing */}
          {stage === 'analyzing' && (
            <div className="text-center py-12 space-y-4">
              <Loader2 size={32} className="mx-auto text-pink-500 animate-spin" />
              <p className="text-gray-600">AI 正在分析项目手稿...</p>
              <p className="text-xs text-gray-400">这可能需要 10-60 秒</p>
            </div>
          )}

          {/* Preview */}
          {(stage === 'preview' || stage === 'applying') && result && (
            <div className="space-y-5">
              <p className="text-sm text-gray-500">
                分析了 {result.total_words_analyzed.toLocaleString()} 字的手稿内容
              </p>

              {error && (
                <p className="text-sm text-red-500 bg-red-50 rounded-lg px-3 py-2">{error}</p>
              )}

              {/* Characters */}
              {result.characters.length > 0 && (
                <section>
                  <div className="flex items-center gap-2 mb-2">
                    <User size={14} className="text-pink-500" />
                    <h3 className="text-sm font-semibold text-gray-700">人物 ({result.characters.length})</h3>
                  </div>
                  <div className="space-y-1.5">
                    {result.characters.map((c, i) => (
                      <label
                        key={i}
                        className={`flex items-start gap-3 px-3 py-2 rounded-lg cursor-pointer transition ${
                          selectedChars.has(i) ? 'bg-pink-50' : 'hover:bg-gray-50'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={selectedChars.has(i)}
                          onChange={() => toggleChar(i)}
                          className="mt-1 rounded text-pink-500 focus:ring-pink-400"
                        />
                        <div className="flex-1 min-w-0">
                          <span className="text-sm font-medium text-gray-800">{c.name}</span>
                          {c.identity && (
                            <span className="ml-2 text-xs text-gray-400">{c.gender ? `${c.gender}，` : ''}{c.identity}</span>
                          )}
                          {c.summary && (
                            <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{c.summary}</p>
                          )}
                        </div>
                      </label>
                    ))}
                  </div>
                </section>
              )}

              {/* Outlines */}
              {result.outlines.length > 0 && (
                <section>
                  <div className="flex items-center gap-2 mb-2">
                    <Map size={14} className="text-purple-500" />
                    <h3 className="text-sm font-semibold text-gray-700">大纲 ({result.outlines.length})</h3>
                  </div>
                  <div className="space-y-1.5">
                    {result.outlines.map((o, i) => (
                      <label
                        key={i}
                        className={`flex items-start gap-3 px-3 py-2 rounded-lg cursor-pointer transition ${
                          selectedOutlines.has(i) ? 'bg-purple-50' : 'hover:bg-gray-50'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={selectedOutlines.has(i)}
                          onChange={() => toggleOutline(i)}
                          className="mt-1 rounded text-purple-500 focus:ring-purple-400"
                        />
                        <div className="flex-1 min-w-0">
                          <span className="text-sm font-medium text-gray-800">{o.name}</span>
                          <p className="text-xs text-gray-500 mt-0.5 line-clamp-3">{o.content}</p>
                        </div>
                      </label>
                    ))}
                  </div>
                </section>
              )}

              {/* Settings */}
              {result.settings.length > 0 && (
                <section>
                  <div className="flex items-center gap-2 mb-2">
                    <StickyNote size={14} className="text-amber-500" />
                    <h3 className="text-sm font-semibold text-gray-700">设定 ({result.settings.length})</h3>
                  </div>
                  <div className="space-y-1.5">
                    {result.settings.map((s, i) => (
                      <label
                        key={i}
                        className={`flex items-start gap-3 px-3 py-2 rounded-lg cursor-pointer transition ${
                          selectedSettings.has(i) ? 'bg-amber-50' : 'hover:bg-gray-50'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={selectedSettings.has(i)}
                          onChange={() => toggleSetting(i)}
                          className="mt-1 rounded text-amber-500 focus:ring-amber-400"
                        />
                        <div className="flex-1 min-w-0">
                          <span className="text-sm font-medium text-gray-800">{s.name}</span>
                          <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-400">
                            {REFERENCE_TYPE_LABELS[s.type]}
                          </span>
                          <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{s.content}</p>
                        </div>
                      </label>
                    ))}
                  </div>
                </section>
              )}

              {result.characters.length === 0 && result.outlines.length === 0 && result.settings.length === 0 && (
                <p className="text-center text-gray-400 py-8">AI 未能从手稿中提取到有效信息</p>
              )}
            </div>
          )}

          {/* Done */}
          {stage === 'done' && (
            <div className="text-center py-8 space-y-4">
              <div className="w-12 h-12 mx-auto rounded-full bg-green-100 flex items-center justify-center">
                <Check size={24} className="text-green-600" />
              </div>
              <p className="text-gray-600">已成功导入到项目中，可在 Binder 各分区查看。</p>
            </div>
          )}
        </div>

        {/* Footer */}
        {(stage === 'preview' || stage === 'applying') && (
          <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-200">
            <button
              onClick={onClose}
              className="px-4 py-2 text-sm rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50"
            >
              取消
            </button>
            <button
              onClick={handleApply}
              disabled={totalSelected === 0 || stage === 'applying'}
              className="px-5 py-2 text-sm rounded-lg bg-pink-500 text-white hover:bg-pink-600 disabled:opacity-50 inline-flex items-center gap-2"
            >
              {stage === 'applying' && <Loader2 size={14} className="animate-spin" />}
              确认导入 ({totalSelected})
            </button>
          </div>
        )}

        {stage === 'done' && (
          <div className="flex items-center justify-end px-6 py-4 border-t border-gray-200">
            <button
              onClick={onClose}
              className="px-5 py-2 text-sm rounded-lg bg-pink-500 text-white hover:bg-pink-600"
            >
              完成
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
